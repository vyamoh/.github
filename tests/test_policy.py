import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy", ROOT / "scripts/repo_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
gate_spec = importlib.util.spec_from_file_location("gate", ROOT / "actions/require-success/check.py")
gate = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(gate)


class FakeGitHub:
    def __init__(self, config, legacy):
        self.requests = []
        self.responses = {}
        self.responses["orgs/vyamoh/rulesets"] = []
        for name in config["repositories"]:
            base = f"repos/vyamoh/{name}"
            self.responses[base] = {"private": True, "archived": False,
                                    **config["defaults"]["settings"]}
            self.responses[base + "/actions/permissions/workflow"] = config["defaults"]["workflow_permissions"]
            self.responses[base + "/vulnerability-alerts"] = None
        for identity, rules in legacy.items():
            name, number = identity.split("/")
            self.responses[f"repos/vyamoh/{name}/rulesets/{number}"] = rules

    def request(self, endpoint, method="GET", body=None):
        self.requests.append((method, endpoint, body))
        response = self.responses[endpoint]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)

    def pages(self, endpoint, key=None):
        response = self.request(endpoint)
        return response[key] if key else response


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.config, self.legacy = policy.load_policy()
        self.api = FakeGitHub(self.config, self.legacy)

    def test_plan_is_read_only_and_retires_local_rules_last(self):
        plan = policy.make_plan(self.api, self.config, self.legacy)
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.requests))
        retire = [i for i, change in enumerate(plan["changes"])
                  if change["body"].get("enforcement") == "disabled"]
        self.assertEqual(len(retire), len(self.legacy))
        self.assertEqual(retire, list(range(min(retire), len(plan["changes"]))))
        self.assertTrue(all(change["body"]["bypass_actors"] == []
                            for change in plan["changes"] if change["method"] == "POST"))

    def test_apply_result_has_empty_next_plan(self):
        first = policy.make_plan(self.api, self.config, self.legacy)
        for number, change in enumerate(first["changes"], 1000):
            endpoint = change["endpoint"]
            if change["method"] == "POST":
                self.api.responses[endpoint].append({"id": number, "name": change["body"]["name"]})
                self.api.responses[f"{endpoint}/{number}"] = change["body"]
            else:
                self.api.responses[endpoint] = change["body"]
        self.assertEqual(policy.make_plan(self.api, self.config, self.legacy)["changes"], [])

    def test_changed_legacy_rules_are_never_retired(self):
        endpoint = "repos/vyamoh/movies/rulesets/18702756"
        self.api.responses[endpoint] = copy.deepcopy(self.api.responses[endpoint])
        self.api.responses[endpoint]["bypass_actors"].append(
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"})
        with self.assertRaisesRegex(ValueError, "changed since audit"):
            policy.make_plan(self.api, self.config, self.legacy)

    def test_disabled_legacy_rules_stay_disabled(self):
        endpoint = "repos/vyamoh/movies/rulesets/18702756"
        self.api.responses[endpoint] = copy.deepcopy(self.api.responses[endpoint])
        self.api.responses[endpoint]["enforcement"] = "disabled"
        plan = policy.make_plan(self.api, self.config, self.legacy)
        self.assertFalse(any(change["endpoint"] == endpoint for change in plan["changes"]))

    def test_permission_error_is_not_treated_as_disabled_alerts(self):
        endpoint = "repos/vyamoh/movies/vulnerability-alerts"
        self.api.responses[endpoint] = policy.ApiError(endpoint, {"status": "404", "message": "Not Found"})
        with self.assertRaises(policy.ApiError):
            policy.make_plan(self.api, self.config, self.legacy)

    def test_disabled_alerts_are_enabled_without_security_update_prs(self):
        endpoint = "repos/vyamoh/movies/vulnerability-alerts"
        self.api.responses[endpoint] = policy.ApiError(
            endpoint, {"status": "404", "message": "Vulnerability alerts are disabled."})
        plan = policy.make_plan(self.api, self.config, self.legacy)
        update = next(change for change in plan["changes"] if change["endpoint"] == endpoint)
        self.assertEqual(update["method"], "PUT")
        self.assertIsNone(update["body"])
        self.assertFalse(any("automated-security-fixes" in change["endpoint"] for change in plan["changes"]))

    def test_active_rules_require_ci_without_bypasses(self):
        rulesets = policy.desired_rulesets(self.config)
        baseline = next(rules for rules in rulesets if rules["name"] == "Vyamoh: main")
        self.assertEqual(set(baseline["conditions"]["repository_name"]["include"]), set(self.config["repositories"]))
        checks = next(rule for rule in baseline["rules"] if rule["type"] == "required_status_checks")
        self.assertEqual(checks["parameters"]["required_status_checks"], [policy.check("ci")])
        for rules in rulesets:
            self.assertEqual(rules["enforcement"], "active")
            self.assertEqual(rules["bypass_actors"], [])
            self.assertEqual(rules["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])

    def test_unrelated_org_rulesets_are_preserved(self):
        self.api.responses["orgs/vyamoh/rulesets"] = [{"id": 44, "name": "Human maintained"}]
        plan = policy.make_plan(self.api, self.config, self.legacy)
        self.assertFalse(any(change["endpoint"].endswith("/44") for change in plan["changes"]))

    def test_duplicate_managed_rulesets_stop_planning(self):
        self.api.responses["orgs/vyamoh/rulesets"] = [
            {"id": 1, "name": "Vyamoh: main"}, {"id": 2, "name": "Vyamoh: main"}]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            policy.make_plan(self.api, self.config, self.legacy)

    def test_unreviewed_targets_are_rejected(self):
        for name in ["*", "repo?", "../repo", "repo#main"]:
            config = copy.deepcopy(self.config)
            config["repositories"][name] = config["repositories"]["movies"]
            with self.assertRaises(ValueError):
                policy.validate_policy(config)

    def test_export_matches_policy(self):
        expected = json.loads((ROOT / "policy/exported-rulesets.json").read_text())
        self.assertEqual(policy.desired_rulesets(self.config), expected)


class StatusApi(policy.GitHub):
    def __init__(self, status_pages, check_runs=None):
        self.status_pages = status_pages
        self.check_runs = check_runs or []
        self.requests = []

    def request(self, endpoint, method="GET", body=None):
        self.requests.append(endpoint)
        if "/check-runs?" in endpoint:
            return {"check_runs": self.check_runs}
        if "/statuses?" in endpoint:
            return self.status_pages.get(int(endpoint.rsplit("page=", 1)[1]), [])
        if endpoint.endswith("/status"):
            return {"statuses": [{"context": "Swarm approval", "state": "success"}]}
        raise AssertionError(endpoint)


class StatusTests(unittest.TestCase):
    def status(self, state="success", creator="github-actions[bot]", context="Swarm approval"):
        return {"context": context, "state": state, "creator": {"login": creator}}

    def test_detailed_status_creator_is_required_for_swarm_approval(self):
        api = StatusApi({1: [self.status()]})
        self.assertIn(("Swarm approval", policy.ACTIONS_APP),
                      policy.successful_contexts(api, "vyamoh", "vatya", "head"))
        self.assertFalse(any(endpoint.endswith("/status") for endpoint in api.requests))

    def test_latest_failure_or_pending_status_blocks_stale_success(self):
        for state in ("failure", "pending"):
            with self.subTest(state=state):
                api = StatusApi({1: [self.status(state), self.status()]})
                self.assertNotIn(("Swarm approval", policy.ACTIONS_APP),
                                 policy.successful_contexts(api, "vyamoh", "vatya", "head"))

    def test_untrusted_latest_creator_blocks_stale_trusted_success(self):
        api = StatusApi({1: [self.status(creator="someone-else"), self.status()]})
        self.assertNotIn(("Swarm approval", policy.ACTIONS_APP),
                         policy.successful_contexts(api, "vyamoh", "vatya", "head"))

    def test_statuses_are_paginated_and_check_app_ids_are_preserved(self):
        first_page = [self.status(creator="someone-else", context=f"other-{index}")
                      for index in range(100)]
        checks = [
            {"name": "ci", "conclusion": "success", "app": {"id": policy.ACTIONS_APP}},
            {"name": policy.SOCKET_CHECKS[0], "conclusion": "success", "app": {"id": policy.SOCKET_APP}},
        ]
        api = StatusApi({1: first_page, 2: [self.status()]}, checks)
        self.assertEqual(policy.successful_contexts(api, "vyamoh", "vatya", "head"), {
            ("ci", policy.ACTIONS_APP),
            (policy.SOCKET_CHECKS[0], policy.SOCKET_APP),
            ("Swarm approval", policy.ACTIONS_APP),
        })
        self.assertTrue(any(endpoint.endswith("/statuses?per_page=100&page=2")
                            for endpoint in api.requests))


class GateTests(unittest.TestCase):
    def test_all_jobs_must_succeed(self):
        self.assertEqual(gate.failures({"test": {"result": "success"}}), [])
        for result in ["failure", "skipped", "cancelled", "", None]:
            with self.subTest(result=result):
                self.assertEqual(gate.failures({"test": {"result": result}}), ["test"])

    def test_missing_results_cannot_pass(self):
        for value in [{}, [], None, "success"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.failures(value)
        self.assertEqual(gate.failures({"test": {}}), ["test"])


if __name__ == "__main__":
    unittest.main()
