import json
from datetime import datetime, timezone
from pathlib import Path

from policy import decide, month_start
from providers import GitHub, blacksmith_usage, load_secrets


def main():
    config = json.loads(Path("/opt/vyamoh-ci/config.json").read_text())
    secrets = load_secrets(include_blacksmith=True)
    github = GitHub(config, secrets["GITHUB_APP_PRIVATE_KEY"])
    now = datetime.now(timezone.utc)
    blacksmith = usage = budgets = None
    try:
        blacksmith = blacksmith_usage(config, secrets["BLACKSMITH_ORG_TOKEN"], month_start(now), now)
    except Exception as exc:
        print(f"Blacksmith usage unavailable ({type(exc).__name__})", flush=True)
    try:
        base = f"organizations/{github.org}/settings/billing"
        usage = github.call(f"{base}/usage/summary?year={now.year}&month={now.month}&product=Actions")
        budgets = {"budgets": github.pages(base + "/budgets", "budgets")}
    except Exception as exc:
        print(f"GitHub usage unavailable ({type(exc).__name__})", flush=True)
    override = github.variable("CI_ROUTING_OVERRIDE") or "automatic"
    state = decide(config, now, blacksmith, usage, budgets, override)
    github.publish(state)
    print(json.dumps(state), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"Controller failed ({type(error).__name__}); previous routing state will expire") from None
