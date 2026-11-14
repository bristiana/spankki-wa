"""What is worth a message when Birky wakes up on its own (the cron, or the demo panel).

Pure: reads the engine and never changes it. Every signal carries all the numbers its message may quote, computed
by the engine, plus a priority (1 = most urgent) and a dedup_key. pick() keeps the most urgent signal whose
dedup_key was not sent to this person in the last COOLDOWN_DAYS (state["sent"], written by app.main).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .engine import Engine, eur

# Most urgent first: a payment they may not recognise > a bill or renewal charging tomorrow > a price rise >
# a goal falling behind > good news (salary) > the weekly check-in.
PRIORITY = {
    "UNUSUAL_TRANSACTION": 1,
    "RECURRING_CHARGE_UPCOMING": 2,
    "SUBSCRIPTION_PRICE_INCREASE": 3,
    "GOAL_DRIFT": 4,
    "SALARY_RECEIVED": 5,
    "WEEKLY_CHECK_IN": 6,
}
COOLDOWN_DAYS = 7
RECENT_DAYS = 7  # a payment older than this is not news any more
SALARY_DAYS = 3  # "your salary just landed" only for this long
PRICE_NEWS_DAYS = 35  # a price rise is news for one billing cycle
DRIFT_MIN_DAYS = 7  # a goal younger than this cannot be behind yet
DRIFT_MIN_EUR = 10.0
DRIFT_SHARE = 0.05  # behind by at least 5% of the target


def signal(kind: str, dedup_key: str, data: dict, weight: float = 0.0) -> dict:
    """weight orders signals of the same priority: the bigger amount first."""
    return {"type": kind, "priority": PRIORITY[kind], "dedup_key": dedup_key, "data": data, "weight": eur(weight)}


def _days_since(today: date, iso: str) -> int:
    return (today - date.fromisoformat(iso)).days


def salary(e: Engine) -> list[dict]:
    t = e.tx
    s = t[(t["transaction_type"] == "salary") & (t["date"] >= e.today - timedelta(days=SALARY_DAYS))]
    if s.empty:
        return []
    row = s.sort_values("date").iloc[-1]
    day = row["date"].isoformat()
    data = {**e.salary_nudge(), "salary_eur": eur(row["tx_amount_eur"]), "date": day}
    return [signal("SALARY_RECEIVED", f"salary:{day[:7]}", data, data["salary_eur"])]


def unusual(e: Engine) -> list[dict]:
    a = e.alert("unusual")  # read only for this kind
    if not a["found"] or _days_since(e.today, a["date"]) > RECENT_DAYS:
        return []
    return [signal("UNUSUAL_TRANSACTION", f"unusual:{a['date']}:{a['amount_eur']}", a, a["amount_eur"])]


def subscriptions(e: Engine) -> list[dict]:
    out = []
    for x in e.subscriptions()["subscriptions"]:
        if x["status"] != "active":
            continue
        inc = x["price_increase"]
        if inc and _days_since(e.today, inc["since"]) <= PRICE_NEWS_DAYS:
            extra = (inc["to_eur"] - inc["from_eur"]) * (12 if x["cadence"] == "monthly" else 1)
            out.append(signal("SUBSCRIPTION_PRICE_INCREASE", f"price:{x['name']}:{inc['to_eur']}",
                              {**x, "extra_per_year_eur": eur(extra)}, extra))
        if x["days_to_next"] == 1:
            out.append(signal("RECURRING_CHARGE_UPCOMING", f"renewal:{x['name']}:{x['next_charge']}",
                              {**x, "source": "subscription"}, x["price_eur"]))
    return out


def bills(e: Engine) -> list[dict]:
    due = [b for b in e.upcoming_bills(1) if b["bill_id"] and b["days_away"] == 1 and b["category"] != "Invest Deposit"]
    if not due:
        return []
    s = e.safe_to_spend()
    safe = {k: s[k] for k in ("safe_to_spend_until_payday_eur", "safe_per_day_eur", "next_payday", "reserve_eur")}
    return [signal("RECURRING_CHARGE_UPCOMING", f"due:{b['bill_id']}", {"source": "bill", "bill": b, "safe": safe},
                   b["amount_eur"]) for b in due]


def goal_drift(e: Engine) -> list[dict]:
    """A goal with a deadline whose savings are behind a straight line from the day it was set."""
    out = []
    for p in e.goals():
        g = p["goal"]
        if not g.get("deadline"):
            continue
        start, end = date.fromisoformat(g["created"]), date.fromisoformat(g["deadline"])
        elapsed, total = (e.today - start).days, (end - start).days
        if elapsed < DRIFT_MIN_DAYS or total <= 0 or e.today >= end:
            continue
        expected = g["target"] * elapsed / total
        behind = expected - g["saved"]
        if behind < max(DRIFT_MIN_EUR, DRIFT_SHARE * g["target"]):
            continue
        out.append(signal("GOAL_DRIFT", f"goal:{g['id']}", {
            "goal_id": g["id"], "name": g["name"], "target_eur": g["target"], "saved_eur": eur(g["saved"]),
            "deadline": g["deadline"], "expected_by_now_eur": eur(expected), "behind_eur": eur(behind),
            "catch_up_eur": float(math.ceil(behind)), "needed_per_month_eur": p["needed_per_month_for_deadline_eur"],
        }, behind))
    return out


def weekly(e: Engine) -> list[dict]:
    w = e.spending(period="last_7_days")
    s = e.safe_to_spend()
    coming = [{"name": b["name"], "amount_eur": b["amount_eur"], "due_date": b["due_date"]}
              for b in e.upcoming_bills(7) if b["bill_id"] and b["category"] != "Invest Deposit"]
    return [signal("WEEKLY_CHECK_IN", "checkin", {
        "spent_7d_eur": w["total_eur"], "spent_prev_7d_eur": w["previous_period"]["total_eur"],
        "safe_eur": s["safe_to_spend_until_payday_eur"], "safe_per_day_eur": s["safe_per_day_eur"],
        "reserve_eur": s["reserve_eur"],
        "next_payday": s["next_payday"]["date"], "bills_next_7d": coming})]


RULES = (unusual, bills, subscriptions, goal_drift, salary, weekly)


def signals(e: Engine) -> list[dict]:
    """Every candidate, most urgent first."""
    out = [s for rule in RULES for s in rule(e)]
    return sorted(out, key=lambda s: (s["priority"], -s["weight"]))


def cooled(sent: dict, key: str, today: date) -> bool:
    at = sent.get(key)
    return not at or _days_since(today, at) >= COOLDOWN_DAYS


def pick(e: Engine, who: str, force: bool = False) -> dict | None:
    """The one signal to send `who` now. At most one proactive message a day unless forced (demo panel)."""
    sent = e.state.get("sent", {}).get(who, {})
    if not force and e.today.isoformat() in sent.values():
        return None
    no_checkin = (e.state.get("onboarding", {}).get(who) or {}).get("answers", {}).get("weekly_checkin") is False
    return next((s for s in signals(e) if cooled(sent, s["dedup_key"], e.today)
                 and not (no_checkin and s["type"] == "WEEKLY_CHECK_IN")), None)
