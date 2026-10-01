import json
import os
from pathlib import Path

from providers import GitHub, load_secrets


def main():
    config = json.loads(Path("/opt/vyamoh-ci/config.json").read_text())
    api = GitHub(config, load_secrets()["GITHUB_APP_PRIVATE_KEY"])
    base = f"orgs/{api.org}/actions"
    groups = [g for g in api.pages(base + "/runner-groups", "runner_groups")
              if g["name"] == config["runner_group"]]
    if not groups:
        group = api.call(base + "/runner-groups", {
            "name": config["runner_group"], "visibility": "selected", "allows_public_repositories": False,
            "selected_repository_ids": config["repository_ids"]}, "POST")
    elif len(groups) == 1:
        group = groups[0]
    else:
        raise ValueError("Duplicate runner groups")
    if group["visibility"] != "selected" or group["allows_public_repositories"]:
        raise ValueError("Runner group access changed; refusing registration")
    repos = api.pages(base + f"/runner-groups/{group['id']}/repositories", "repositories")
    if {r["id"] for r in repos} != set(config["repository_ids"]):
        raise ValueError("Runner group repositories differ from reviewed configuration")
    for runner in api.pages(base + "/runners", "runners"):
        if runner["name"] == config["runner_name"]:
            if runner["status"] != "offline" or runner["busy"]:
                raise ValueError("Previous runner still online; retry after it disconnects")
            api.call(base + f"/runners/{runner['id']}", method="DELETE")
    result = api.call(base + "/runners/generate-jitconfig", {
        "name": config["runner_name"], "runner_group_id": group["id"],
        "labels": config["runner_labels"], "work_folder": "_work"}, "POST")
    path = Path("/var/lib/vyamoh-ci-router/jit.json")
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps({"jit": result["encoded_jit_config"]}))
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    print("One-job runner configuration prepared", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"Runner registration failed ({type(error).__name__})") from None
