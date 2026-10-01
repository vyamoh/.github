import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
import tempfile
from unittest.mock import patch
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runner_policy", ROOT / "runner/policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

provider_spec = importlib.util.spec_from_file_location("runner_providers", ROOT / "runner/providers.py")
providers = importlib.util.module_from_spec(provider_spec)
provider_spec.loader.exec_module(providers)

worker_spec = importlib.util.spec_from_file_location("runner_worker", ROOT / "runner/worker.py")
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class WorkerStorageTests(unittest.TestCase):
    def test_persistent_host_mount_refuses_jobs(self):
        with self.assertRaisesRegex(RuntimeError, "refusing jobs"):
            worker.require_tmpfs(
                "123 45 252:1 /var/lib/vyamoh-ci /var/lib/vyamoh-ci rw - ext4 /dev/vda1 rw",
                Path("/var/lib/vyamoh-ci"))

    def test_expected_tmpfs_accepts_jobs(self):
        worker.require_tmpfs("123 45 0:99 / /var/lib/vyamoh-ci rw - tmpfs tmpfs rw,size=1048576k",
                             Path("/var/lib/vyamoh-ci"))

    def test_missing_mount_refuses_jobs(self):
        with self.assertRaisesRegex(RuntimeError, "refusing jobs"):
            worker.require_tmpfs("123 45 0:99 / /tmp rw - tmpfs tmpfs rw", Path("/var/lib/vyamoh-ci"))

    def test_tmpfs_path_has_no_competing_mount_directive(self):
        unit = (ROOT / "runner/systemd/vyamoh-ci-worker.service").read_text()
        self.assertNotIn("ReadWritePaths=/var/lib/vyamoh-ci", unit)


class CredentialTests(unittest.TestCase):
    def load(self, secrets, include_blacksmith=False, lifetime=3600):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "infisical.env").write_text(
                "INFISICAL_DOMAIN=https://app.infisical.com\n"
                "INFISICAL_PROJECT_ID=project\nINFISICAL_ENVIRONMENT=prod\n"
                "INFISICAL_SECRET_PATH=/runner-router\nINFISICAL_CLIENT_ID=client\n"
                "INFISICAL_CLIENT_SECRET=literal$secret\n")
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": directory}):
                with patch.object(providers, "request", side_effect=[
                    {"expiresIn": lifetime, "accessToken": "test-token"},
                    {"secrets": [{"secretKey": key, "secretValue": value} for key, value in secrets.items()]}
                ]) as request:
                    result = providers.load_secrets(include_blacksmith)
                    self.assertEqual(request.call_args_list[0].kwargs["body"]["clientSecret"], "literal$secret")
                    self.assertIn("includeImports=false", request.call_args_list[1].args[0])
                    self.assertIn("recursive=false", request.call_args_list[1].args[0])
                    return result

    def test_broker_does_not_require_or_return_blacksmith_secret(self):
        self.assertEqual(self.load({"GITHUB_APP_PRIVATE_KEY": "key"}), {"GITHUB_APP_PRIVATE_KEY": "key"})
        self.assertEqual(self.load({"GITHUB_APP_PRIVATE_KEY": "key", "BLACKSMITH_ORG_TOKEN": "token"}),
                         {"GITHUB_APP_PRIVATE_KEY": "key"})

    def test_missing_blacksmith_token_allows_github_fallback(self):
        self.assertEqual(self.load({"GITHUB_APP_PRIVATE_KEY": "key"}, True), {"GITHUB_APP_PRIVATE_KEY": "key"})

    def test_long_lived_access_token_is_rejected(self):
        with self.assertRaises(ValueError):
            self.load({"GITHUB_APP_PRIVATE_KEY": "key"}, lifetime=3601)

    def test_missing_app_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.load({"BLACKSMITH_ORG_TOKEN": "token"})


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "runner/config.json").read_text())
        self.now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        self.blacksmith = {
            "installation": {"installation_name": "vyamoh"},
            "window": {"start": "2026-10-01T00:00:00Z", "end": self.now.isoformat()},
            "summary": {"billing_minutes": 48}}
        self.github = {"timePeriod": {"year": 2026, "month": 10},
                       "organization": "vyamoh", "product": "Actions",
                       "usageItems": [{"product": "Actions", "netAmount": 0}]}
        self.budgets = {"budgets": [{
            "id": self.config["budget_id"], "budget_scope": "organization",
            "budget_entity_name": "vyamoh", "budget_product_sku": "actions",
            "budget_type": "ProductPricing", "budget_amount": 5, "prevent_further_usage": True}]}

    def decide(self, override="automatic"):
        return policy.decide(self.config, self.now, self.blacksmith, self.github, self.budgets, override)

    def exhaust_blacksmith(self):
        self.blacksmith["summary"]["billing_minutes"] = 5400

    def test_prefers_blacksmith_normalized_two_cpu_allowance(self):
        self.blacksmith["summary"]["billing_minutes"] = 5399
        self.assertEqual(self.decide()["backend"], "blacksmith")
        self.exhaust_blacksmith()
        self.assertEqual(self.decide()["backend"], "github")

    def test_github_free_and_paid_buffer_then_blocks(self):
        self.exhaust_blacksmith()
        for used, expected in [(0, "github"), (4.49, "github"), (4.5, "blocked"), (5, "blocked")]:
            self.github["usageItems"][0]["netAmount"] = used
            self.assertEqual(self.decide()["backend"], expected)

    def test_storage_spending_also_counts_against_buffer(self):
        self.exhaust_blacksmith()
        self.github["usageItems"] = [{"product": "Actions", "sku": "actions_storage", "netAmount": 4.6}]
        self.assertEqual(self.decide()["backend"], "blocked")

    def test_budget_drift_never_enables_github(self):
        self.exhaust_blacksmith()
        for field, value in [("prevent_further_usage", False), ("budget_amount", 10),
                             ("budget_scope", "repository"), ("id", "other"),
                             ("budget_product_sku", "codespaces")]:
            with self.subTest(field=field):
                budgets = copy.deepcopy(self.budgets)
                budgets["budgets"][0][field] = value
                state = policy.decide(self.config, self.now, self.blacksmith, self.github, budgets)
                self.assertEqual(state["backend"], "blocked")

    def test_provider_outage_does_not_impersonate_unused_allowance(self):
        self.blacksmith = None
        self.assertEqual(self.decide()["backend"], "github")
        self.github = None
        self.assertEqual(self.decide()["backend"], "blocked")

    def test_invalid_numbers_are_unavailable(self):
        for value in [float("nan"), float("inf"), -1, "0", False]:
            with self.subTest(value=value):
                self.blacksmith["summary"]["billing_minutes"] = value
                self.github["usageItems"][0]["netAmount"] = value
                self.assertEqual(self.decide()["backend"], "blocked")

    def test_wrong_month_and_stale_window_are_rejected(self):
        self.blacksmith["window"]["end"] = (self.now - timedelta(minutes=11)).isoformat()
        self.github["timePeriod"]["month"] = 9
        self.assertEqual(self.decide()["backend"], "blocked")

    def test_reset_uses_new_month_only(self):
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        self.assertEqual(self.decide()["backend"], "blocked")
        self.blacksmith["window"] = {"start": self.now.isoformat(), "end": self.now.isoformat()}
        self.blacksmith["summary"]["billing_minutes"] = 0
        self.assertEqual(self.decide()["backend"], "blacksmith")

    def test_override_cannot_bypass_allowances(self):
        self.assertEqual(self.decide("github")["backend"], "github")
        self.exhaust_blacksmith()
        self.assertEqual(self.decide("blacksmith")["backend"], "blocked")
        for override in ["blocked", "invalid", "do", "macos"]:
            self.assertEqual(self.decide(override)["backend"], "blocked")

    def test_selector_rejects_missing_stale_future_and_cross_month_state(self):
        state = self.decide()
        self.assertEqual(policy.select(state, self.now), ["blacksmith-2vcpu-ubuntu-2404"])
        self.assertEqual(policy.select(state, self.now, "4vcpu"), ["blacksmith-4vcpu-ubuntu-2404"])
        for now in [self.now + timedelta(seconds=600), self.now - timedelta(seconds=1),
                    datetime(2026, 11, 1, tzinfo=timezone.utc)]:
            with self.assertRaises(ValueError):
                policy.select(state, now)
        for invalid in [{}, {**state, "expires_at": state["generated_at"] + 601},
                        {**state, "backend": "blocked"}]:
            with self.assertRaises(ValueError):
                policy.select(invalid, self.now)

    def test_no_unmeasured_do_or_macos_fallback(self):
        for size in ["macos", "8vcpu", "self-hosted"]:
            with self.assertRaises(ValueError):
                policy.select(self.decide(), self.now, size)
        self.assertEqual(policy.select(self.decide("github"), self.now, "4vcpu"), ["ubuntu-24.04"])


if __name__ == "__main__":
    unittest.main()
