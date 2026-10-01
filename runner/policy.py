import math
from datetime import datetime, timezone


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Expected a finite nonnegative number")
    return value


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result.astimezone(timezone.utc)


def month_start(now):
    return now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def blacksmith_balance(report, config, now):
    if report["installation"]["installation_name"] != config["organization"]:
        raise ValueError("Wrong Blacksmith organization")
    if timestamp(report["window"]["start"]) != month_start(now):
        raise ValueError("Wrong Blacksmith usage month")
    age = (now - timestamp(report["window"]["end"])).total_seconds()
    if not 0 <= age <= config["state_ttl_seconds"]:
        raise ValueError("Stale Blacksmith query window")
    used = number(report["summary"]["billing_minutes"])
    return used < (config["blacksmith_allowance_billing_minutes"]
                   - config["blacksmith_reserve_billing_minutes"]), used


def github_balance(report, budgets, config, now):
    period = report["timePeriod"]
    if (period["year"], period["month"]) != (now.year, now.month):
        raise ValueError("Wrong GitHub usage month")
    if report["organization"] != config["organization"] or report["product"] != "Actions":
        raise ValueError("Wrong GitHub usage scope")
    matches = [b for b in budgets["budgets"] if b["id"] == config["budget_id"]]
    if len(matches) != 1:
        raise ValueError("Configured hard budget is missing")
    budget = matches[0]
    if (budget["budget_scope"] != "organization"
            or budget["budget_entity_name"] != config["organization"]
            or budget["budget_product_sku"] != "actions"
            or budget["budget_type"] != "ProductPricing"
            or budget["prevent_further_usage"] is not True
            or number(budget["budget_amount"]) != config["github_budget_usd"]):
        raise ValueError("GitHub hard budget changed")
    items = report["usageItems"]
    if not isinstance(items, list) or any(i["product"] != "Actions" for i in items):
        raise ValueError("Unexpected billing items")
    paid = sum(number(item["netAmount"]) for item in items)
    return paid < config["github_budget_usd"] - config["github_reserve_usd"], paid


def decide(config, now, blacksmith=None, github=None, budgets=None, override="automatic"):
    available = {}
    usage = {}
    for provider, read in (
        ("blacksmith", lambda: blacksmith_balance(blacksmith, config, now)),
        ("github", lambda: github_balance(github, budgets, config, now)),
    ):
        try:
            available[provider], usage[provider] = read()
        except (KeyError, TypeError, ValueError, AttributeError):
            available[provider] = False
            usage[provider] = None
    choices = ["blacksmith", "github"] if override == "automatic" else [override]
    backend = next((p for p in choices if available.get(p)), "blocked")
    reason = "provider allowance available" if backend != "blocked" else "exhausted, unavailable, or disabled"
    return {"schema": 1, "generated_at": int(now.timestamp()),
            "expires_at": int(now.timestamp()) + config["state_ttl_seconds"],
            "month": now.strftime("%Y-%m"), "backend": backend,
            "reason": reason, "override": override, "usage": usage}


def select(state, now, size="2vcpu"):
    if state.get("schema") != 1 or state.get("month") != now.strftime("%Y-%m"):
        raise ValueError("Missing or wrong-month routing state")
    generated = number(state["generated_at"])
    expires = number(state["expires_at"])
    if not generated <= now.timestamp() < expires or not 0 < expires - generated <= 600:
        raise ValueError("Routing state expired or invalid; check the controller")
    if size not in ("2vcpu", "4vcpu"):
        raise ValueError("Unsupported Linux runner size")
    if state["backend"] == "blacksmith":
        return [f"blacksmith-{size}-ubuntu-2404"]
    if state["backend"] == "github":
        return ["ubuntu-24.04"]
    raise ValueError("Heavy jobs blocked: provider usage unavailable or budget exhausted")
