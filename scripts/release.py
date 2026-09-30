import json
import os
import re
import subprocess
import sys

from repo_policy import ApiError, GitHub


def version_tuple(version):
    match = re.fullmatch(r"v([1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    if not match:
        raise ValueError("Release must be a stable version such as v1.0.0")
    return tuple(map(int, match.groups()))


def release_plan(version, sha, refs):
    desired = version_tuple(version)
    exact = next((ref for ref in refs if ref["ref"] == f"refs/tags/{version}"), None)
    if exact and (exact["object"]["type"] != "commit" or exact["object"]["sha"] != sha):
        raise ValueError(f"{version} already exists at a different object; version tags are immutable")
    for ref in refs:
        tag = ref["ref"].removeprefix("refs/tags/")
        if re.fullmatch(r"v[1-9][0-9]*\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
            candidate = version_tuple(tag)
            if candidate[0] == desired[0] and candidate > desired:
                raise ValueError("Refusing to move a major channel backwards")
    major = f"v{desired[0]}"
    channel = next((ref for ref in refs if ref["ref"] == f"refs/tags/{major}"), None)
    return {"version": version, "channel": major, "sha": sha,
            "create_version": exact is None, "create_channel": channel is None}


def main():
    if os.environ.get("GITHUB_REPOSITORY") != "vyamoh/.github" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Releases run only from vyamoh/.github main")
    api = GitHub()
    base = "repos/vyamoh/.github"
    refs = api.pages(base + "/git/matching-refs/tags/v")
    plan = release_plan(os.environ["VERSION"], os.environ["GITHUB_SHA"], refs)
    if plan["create_version"]:
        api.request(base + "/git/refs", "POST", {"ref": "refs/tags/" + plan["version"], "sha": plan["sha"]})
    try:
        api.request(base + "/releases/tags/" + plan["version"])
    except ApiError as error:
        if str(error.response.get("status")) != "404":
            raise
        subprocess.run(["gh", "release", "create", plan["version"], "--repo", "vyamoh/.github",
                        "--verify-tag", "--generate-notes", "--target", plan["sha"]], check=True)
    if plan["create_channel"]:
        api.request(base + "/git/refs", "POST", {"ref": "refs/tags/" + plan["channel"], "sha": plan["sha"]})
    else:
        api.request(base + "/git/refs/tags/" + plan["channel"], "PATCH", {"sha": plan["sha"], "force": True})
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ApiError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
