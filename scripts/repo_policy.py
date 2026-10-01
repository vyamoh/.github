import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ACTIONS_APP = 15368
SOCKET_APP = 156372
SOCKET_CHECKS = ["Socket Security: Project Report", "Socket Security: Pull Request Alerts"]


class ApiError(RuntimeError):
    def __init__(self, endpoint, response):
        self.response = response
        super().__init__(f"{endpoint}: {response.get('message', response)}")


class GitHub:
    def request(self, endpoint, method="GET", body=None):
        command = ["gh", "api", "--method", method, endpoint,
                   "-H", "X-GitHub-Api-Version: 2022-11-28"]
        if body is not None:
            command.extend(["--input", "-"])
        result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True, check=False)
        try:
            response = json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Invalid API response for {endpoint}") from error
        if result.returncode:
            raise ApiError(endpoint, response or {"message": result.stderr.strip()})
        return response

    def pages(self, endpoint, key=None):
        items = []
        for page in range(1, 101):
            separator = "&" if "?" in endpoint else "?"
            response = self.request(f"{endpoint}{separator}per_page=100&page={page}")
            batch = response[key] if key else response
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise RuntimeError(f"Pagination limit exceeded: {endpoint}")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        unmatched = list(actual)
        for item in expected:
            found = next((i for i, candidate in enumerate(unmatched) if matches(candidate, item)), None)
            if found is None:
                return False
            unmatched.pop(found)
        return True
    return type(actual) is type(expected) and actual == expected


def check(context, app=ACTIONS_APP):
    return {"context": context, "integration_id": app}


def required_checks(checks, strict=False):
    return {"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": strict,
        "do_not_enforce_on_create": False,
        "required_status_checks": checks,
    }}


def ruleset(name, repositories, rules):
    return {
        "name": f"Vyamoh: {name}", "target": "branch", "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "repository_name": {"include": sorted(repositories), "exclude": [], "protected": False},
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
        },
        "rules": rules,
    }


def desired_rulesets(policy):
    repos = policy["repositories"]
    result = [ruleset("main", repos, [
        {"type": "deletion"}, {"type": "non_fast_forward"},
        {"type": "pull_request", "parameters": {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews_on_push": False,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": True,
            "allowed_merge_methods": ["rebase"],
        }},
        required_checks([check("ci")]),
    ])]
    socket = [name for name, repo in repos.items() if repo["socket"]]
    if socket:
        result.append(ruleset("Socket", socket,
                              [required_checks([check(name, SOCKET_APP) for name in SOCKET_CHECKS])]))
    swarm = [name for name, repo in repos.items() if repo["swarm"]]
    if swarm:
        result.append(ruleset("swarm approval", swarm, [required_checks([check("Swarm approval")])]))
    strict = [name for name, repo in repos.items() if repo.get("strict")]
    if strict:
        result.append(ruleset("current base", strict, [required_checks([check("ci")], True)]))
    for name, repo in sorted(repos.items()):
        if repo["extra_checks"]:
            result.append(ruleset(f"{name} checks", [name],
                                  [required_checks([check(context) for context in repo["extra_checks"]])]))
    return result


def validate_policy(policy):
    if policy.get("organization") != "vyamoh":
        raise ValueError("This policy is scoped to the vyamoh organization")
    repos = policy.get("repositories")
    if not isinstance(repos, dict) or not repos or ".github" not in repos:
        raise ValueError("Policy must include the shared .github repository")
    for name, repo in repos.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in {".", ".."}:
            raise ValueError(f"Invalid exact repository name: {name}")
        if repo.get("ci") != "ci":
            raise ValueError(f"{name}: the shared CI contract is named ci")
        if not all(type(repo.get(key)) is bool for key in ["socket", "swarm"]):
            raise ValueError(f"{name}: socket and swarm must be booleans")
        if not isinstance(repo.get("extra_checks"), list) or not all(
                isinstance(x, str) and x for x in repo["extra_checks"]):
            raise ValueError(f"{name}: invalid extra checks")
    return policy


def writable_ruleset(value):
    return {key: value[key] for key in [
        "name", "target", "enforcement", "bypass_actors", "conditions", "rules"]}


def change(changes, method, endpoint, before, after, description):
    if not matches(before, after):
        changes.append({"method": method, "endpoint": endpoint, "before": before,
                        "body": after, "description": description})


def make_plan(api, policy, legacy):
    org = policy["organization"]
    changes = []
    current = api.pages(f"orgs/{org}/rulesets")
    for desired in desired_rulesets(policy):
        candidates = [item for item in current if item["name"] == desired["name"]]
        if len(candidates) > 1:
            raise ValueError(f"Duplicate managed ruleset: {desired['name']}")
        if candidates:
            endpoint = f"orgs/{org}/rulesets/{candidates[0]['id']}"
            before = writable_ruleset(api.request(endpoint))
            change(changes, "PUT", endpoint, before, desired, desired["name"])
        else:
            change(changes, "POST", f"orgs/{org}/rulesets", None, desired, desired["name"])
    for name, repo in sorted(policy["repositories"].items()):
        endpoint = f"repos/{org}/{name}"
        info = api.request(endpoint)
        if info.get("archived") or not info.get("private"):
            raise ValueError(f"Refusing policy rollout to archived/public repository: {name}")
        settings = policy["defaults"]["settings"] | repo.get("settings", {})
        change(changes, "PATCH", endpoint, {key: info.get(key) for key in settings}, settings,
               f"{name}: merge settings")
        permissions = policy["defaults"]["workflow_permissions"]
        path = endpoint + "/actions/permissions/workflow"
        change(changes, "PUT", path, api.request(path), permissions, f"{name}: workflow permissions")
        if policy["defaults"]["dependabot_alerts"]:
            path = endpoint + "/vulnerability-alerts"
            try:
                api.request(path)
            except ApiError as error:
                if str(error.response.get("status")) != "404" or "disabled" not in error.response.get("message", "").lower():
                    raise
                changes.append({"method": "PUT", "endpoint": path, "before": False,
                                "body": None, "description": f"{name}: enable Dependabot alerts"})
    for name, repo in sorted(policy["repositories"].items()):
        for identity in repo["retire_rulesets"]:
            endpoint = f"repos/{org}/{name}/rulesets/{identity}"
            before = writable_ruleset(api.request(endpoint))
            if before["enforcement"] == "disabled":
                continue
            expected = legacy.get(f"{name}/{identity}")
            if expected is None or not matches(before, expected) or not matches(expected, before):
                raise ValueError(f"{name}: legacy ruleset {identity} changed since audit; review its snapshot")
            after = before | {"enforcement": "disabled"}
            change(changes, "PUT", endpoint, before, after, f"{name}: retire audited local main ruleset")
    return {"organization": org, "policy_digest": digest([policy, legacy]), "changes": changes}


def successful_contexts(api, org, name, sha):
    contexts = set()
    for run in api.pages(f"repos/{org}/{name}/commits/{sha}/check-runs", "check_runs"):
        if run.get("conclusion") == "success":
            contexts.add((run["name"], run.get("app", {}).get("id")))
    seen_statuses = set()
    for status in api.pages(f"repos/{org}/{name}/commits/{sha}/statuses"):
        if status["context"] in seen_statuses:
            continue
        seen_statuses.add(status["context"])
        if status["state"] == "success" and (status.get("creator") or {}).get("login") == "github-actions[bot]":
            contexts.add((status["context"], ACTIONS_APP))
    return contexts


def verify_adoption(api, policy):
    org = policy["organization"]
    failures = []
    for name, repo in policy["repositories"].items():
        endpoint = f"repos/{org}/{name}"
        info = api.request(endpoint)
        branch = info["default_branch"]
        try:
            content = api.request(endpoint + f"/contents/renovate.json?ref={branch}")
            renovate = json.loads(base64.b64decode(content["content"]))
        except (ApiError, KeyError, ValueError):
            failures.append(f"{name}: shared Renovate adoption is not on {branch}")
            continue
        if "local>vyamoh/.github:renovate-config" not in renovate.get("extends", []):
            failures.append(f"{name}: shared Renovate adoption is not on {branch}")
            continue
        pulls = api.request(endpoint + "/pulls?state=closed&sort=updated&direction=desc&per_page=100")
        merged = [pull for pull in pulls if pull.get("merged_at") and pull["base"]["ref"] == branch]
        if not merged:
            failures.append(f"{name}: no merged PR to verify the required checks")
            continue
        latest = max(merged, key=lambda pull: pull["merged_at"])
        observed = successful_contexts(api, org, name, latest["head"]["sha"])
        required = {("ci", ACTIONS_APP)}
        required.update((context, ACTIONS_APP) for context in repo["extra_checks"])
        if repo["socket"]:
            required.update((context, SOCKET_APP) for context in SOCKET_CHECKS)
        if repo["swarm"]:
            required.add(("Swarm approval", ACTIONS_APP))
        missing = required - observed
        if missing:
            failures.append(f"{name}: latest merged PR lacks successful checks: " +
                            ", ".join(sorted(context for context, _ in missing)))
    if failures:
        raise ValueError("Rollout prerequisites are not met:\n" + "\n".join(failures))


def load_policy():
    policy = validate_policy(json.loads((ROOT / "policy/repositories.json").read_text()))
    legacy = json.loads((ROOT / "policy/legacy-rulesets.json").read_text())
    return policy, legacy


def main():
    parser = argparse.ArgumentParser(description="Plan and explicitly apply the reviewed Vyamoh repository policy")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--output", default="policy-plan.json")
    apply = commands.add_parser("apply")
    apply.add_argument("--plan", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", default="policy/exported-rulesets.json")
    args = parser.parse_args()
    policy, legacy = load_policy()
    if args.command == "export":
        Path(args.output).write_text(json.dumps(desired_rulesets(policy), indent=2) + "\n")
        print(args.output)
        return
    api = GitHub()
    current = make_plan(api, policy, legacy)
    if args.command == "plan":
        Path(args.output).write_text(json.dumps(current, indent=2) + "\n")
        print(f"{len(current['changes'])} proposed changes written to {args.output}; no writes performed")
        return
    reviewed = json.loads(Path(args.plan).read_text())
    if current != reviewed:
        raise ValueError("Policy or GitHub state changed since the plan; regenerate and review it")
    verify_adoption(api, policy)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(f"policy-backup-{timestamp}.json")
    backup.write_text(json.dumps(reviewed, indent=2) + "\n")
    for operation in current["changes"]:
        print(operation["description"], flush=True)
        api.request(operation["endpoint"], operation["method"], operation["body"])
    print(f"Applied {len(current['changes'])} changes; previous values saved in {backup}")


if __name__ == "__main__":
    try:
        main()
    except (ApiError, ValueError, OSError, RuntimeError) as error:
        sys.exit(str(error))
