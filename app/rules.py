"""What is worth a message when Oiva wakes up on its own (the cron, or the demo panel).

Pure: reads the engine and never changes it. Every signal carries all the numbers its message may quote, computed
by the engine, plus a priority (1 = most urgent) and a dedup_key. pick() keeps the most urgent signal whose
dedup_key was not sent to this person in the last COOLDOWN_DAYS (state["sent"], written by app.main).
"""
from __future__ import annotations

import math
import time
from datetime import date, timedelta

from .engine import Engine, eur

# Most urgent first: a payment they may not recognise > a bill or the monthly investing going out tomorrow > a bill
# that went up (real data: a trend beyond the usual swing, or one utility bill well above its range) >
# something they started and left unfinished (it only lives for a few hours of the day after) > a goal falling
# behind > good news (salary) > the weekly check-in.
PRIORITY = {
    "UNUSUAL_TRANSACTION": 1,
    "RECURRING_CHARGE_UPCOMING": 2,
    "BILL_INCREASE": 3,
    "OPEN_THREAD": 4,
    "GOAL_DRIFT": 5,
    "SALARY_RECEIVED": 6,
    "WEEKLY_CHECK_IN": 7,
}
COOLDOWN_DAYS = 7
RECENT_DAYS = 7  # a payment older than this is not news any more
SALARY_DAYS = 3  # "your salary just landed" only for this long
PRICE_NEWS_DAYS = 35  # a price rise is news for one billing cycle
DRIFT_MIN_DAYS = 7  # a goal younger than this cannot be behind yet
DRIFT_MIN_EUR = 10.0
DRIFT_SHARE = 0.05  # behind by at least 5% of the target
THREAD_QUIET_H = 18  # an open thread comes back once they've been quiet this long: "the next day"...
WINDOW_H = 24  # ...and only while WhatsApp still allows a free-form message
THREAD = "thread:"  # dedup keys of open threads: each one comes back once, never again


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


def commitments(e: Engine) -> list[dict]:
    """Real regular payments: one that went up (last 3 months vs the 3 before), a utility bill well above the usual
    range, and the monthly investing or a fixed-day utility bill going out tomorrow (rent is in bills())."""
    out = []
    for x in e.commitments()["regular_payments"]:
        if x["direction"] != "out" or x["status"] != "active":
            continue
        tr = x.get("trend") or {}
        if tr.get("direction") == "up" and _days_since(e.today, x["last_payment"]["date"]) <= PRICE_NEWS_DAYS:
            extra = tr["change_per_month_eur"] * 12
            out.append(signal("BILL_INCREASE", f"increase:{x['id']}:{x['last_payment']['date']}",
                              {**x, "increase": "trend", "extra_per_year_eur": eur(extra)}, extra))
        odd = x.get("unusual_bill")
        if odd and _days_since(e.today, odd["date"]) <= PRICE_NEWS_DAYS:
            out.append(signal("BILL_INCREASE", f"bill:{x['id']}:{odd['date']}:{odd['amount_eur']}",
                              {**x, "increase": "one_bill"}, odd["amount_eur"] - odd["usual_high_eur"]))
        if x["id"] != "rent" and x["days_to_next"] == 1:
            out.append(signal("RECURRING_CHARGE_UPCOMING", f"due:{x['id']}:{x['next_expected']}",
                              {**x, "source": x["id"]}, x["per_month_eur"]))
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


def quiet_hours(e: Engine, who: str, now: float | None = None) -> float | None:
    """Hours since `who` last wrote (WhatsApp or web chat), or None when we never heard from them."""
    last = max(e.state.get("wa_last", {}).get(who, 0), e.state.get("last_msg", {}).get(who, 0))
    return ((now if now is not None else time.time()) - last) / 3600 if last else None


def _thread(key: str, data: dict, rank: int) -> dict:
    return signal("OPEN_THREAD", THREAD + key, data, rank)


def open_threads(e: Engine, who: str, now: float | None = None, force: bool = False) -> list[dict]:
    """What `who` started and left unfinished, for the "comes back a day later" message: the Learn course, the
    basket game, a PIN link not confirmed, a goal with nothing in it, a "maybe later". Only after
    THREAD_QUIET_H hours of quiet and inside the 24-hour window (force, from the demo panel, skips the wait).
    At most one per kind, the most useful first (rank). Read only."""
    from .learn import COUNT, TITLES
    hours = quiet_hours(e, who, now)
    if not who or (not force and (hours is None or not THREAD_QUIET_H <= hours < WINDOW_H)):
        return []
    out = []
    lr = e.state.get("learn", {}).get(who)
    if lr and not lr["done"]:
        out.append(_thread(f"learn:{lr.get('run', 1)}:{lr['lesson']}", {
            "thread": "learn", "lesson": lr["lesson"] + 1, "of": COUNT, "title": TITLES[lr["lesson"]],
            "done": lr["lesson"]}, 6))
    # a paused goal interview never comes back by itself: they moved on, Oiva follows their lead ("continue" resumes)
    q = e.state.get("quiz", {}).get(who)
    if q and not q["done"]:
        out.append(_thread(f"quiz:{q['game']}:{q['round']}", {"thread": "quiz", "round": q["round"] + 1,
                                                              "of": len(q["picks"])}, 4))
    due = {b["bill_id"]: b for b in e.upcoming_bills(45) if b["bill_id"]}
    waiting = [(pid, p) for pid, p in e.state.get("pending", {}).items() if p.get("status") == "pending"
               and (p["kind"] != "payment" or p.get("bill_id") in due)]
    if waiting:
        pid, p = waiting[-1]
        out.append(_thread(f"pin:{pid}", {"thread": "pin", "pending_id": pid, "kind": p["kind"], "name": p["name"],
                                          "amount_eur": p["amount"]}, 3))
    saving_for = {p.get("goal_id") for _, p in waiting}
    for gid, g in e.state.get("goals", {}).items():
        if g["saved"] > 0 or gid in saving_for:
            continue
        plan = e.goal_plan(gid)
        monthly = plan.get("needed_per_month_for_deadline_eur") or next(
            (o["monthly_eur"] for o in plan["options"]), 0)
        amount = float(min(round(monthly), int(e.safe_to_save()["now_max_eur"] // 2)))
        if amount >= 5:
            out.append(_thread(f"goal:{gid}", {"thread": "goal", "goal_id": gid, "name": g["name"],
                                               "target_eur": g["target"], "amount_eur": amount}, 2))
            break
    later = e.state.get("later", {}).get(who)
    if later:
        key = later["key"]
        if key.startswith("later:") and key[6:] in due:
            out.append(_thread(key, {"thread": "later", "kind": "bill", "bill": due[key[6:]]}, 1))
        elif key.startswith("chat:") and later.get("offer"):
            out.append(_thread(key, {"thread": "later", "kind": "chat", "offer": later["offer"]}, 1))
    return out


RULES = (unusual, bills, commitments, goal_drift, salary, weekly)


def signals(e: Engine, who: str | None = None, now: float | None = None, force: bool = False) -> list[dict]:
    """Every candidate, most urgent first. Open threads are per person, so they need `who`."""
    out = [s for rule in RULES for s in rule(e)] + (open_threads(e, who, now, force) if who else [])
    return sorted(out, key=lambda s: (s["priority"], -s["weight"]))


def cooled(sent: dict, key: str, today: date) -> bool:
    at = sent.get(key)
    if at and key.startswith(THREAD):  # an open thread comes back once, then it's theirs
        return False
    return not at or _days_since(today, at) >= COOLDOWN_DAYS


def pick(e: Engine, who: str, force: bool = False, now: float | None = None) -> dict | None:
    """The one signal to send `who` now. At most one proactive message a day unless forced (demo panel)."""
    sent = e.state.get("sent", {}).get(who, {})
    if not force and e.today.isoformat() in sent.values():
        return None
    # the weekly check-in is opt-in: only for someone who asked for it ("remind me weekly"), or forced (demo panel)
    said = (e.state.get("onboarding", {}).get(who) or {}).get("answers", {}).get("weekly_checkin")
    wants_checkin = said is True or (force and said is not False)
    return next((s for s in signals(e, who, now, force) if cooled(sent, s["dedup_key"], e.today)
                 and (wants_checkin or s["type"] != "WEEKLY_CHECK_IN")), None)
