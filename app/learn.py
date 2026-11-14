"""Learn in 2 minutes: five 30-second money lessons, each built on the person's own numbers. No AI.

1 The three baskets · 2 Safe to spend vs. your balance · 3 The buffer · 4 Small things add up · 5 Pay yourself first.
Each lesson is one short message (under 500 characters) with three reply buttons: the next lesson, "Ask Birky" and
one action that uses the existing handlers (the basket game, My money, a goal + savings pocket with PIN, the coach).
Started with "learn", "teach me" or the web chip. "stop" pauses, any other question pauses too (the coach answers),
"learn" picks it up where it was. Tapping a lesson's action steps away quietly; its "Next lesson" carries on.
State: engine.state["learn"][user] = {"lesson", "paused", "note", "done", "seen"}; one per user in each persona's state.
"""
from __future__ import annotations

import re
from datetime import date

from . import quiz
from .engine import BASKETS, Engine
from .lessons import eur
from .onboarding import Turn
from .tools import GUIDE

TITLES = ["The three baskets", "Safe to spend vs. your balance", "The buffer", "Small things add up",
          "Pay yourself first"]
COUNT = len(TITLES)
BUFFER_MONTHS = 3  # a first buffer: three months of the irregular basket
SCENARIO_YEARS = 10
SCENARIO_FUND = "balanced"
MAX_CHARS = 500

START = re.compile(r"^\W*(learn|teach me|teach me something|learn in 2 minutes|learn in two minutes|lessons?|"
                   r"money lessons?|start learning|continue learning|resume learning|mini course|opi)\W*$", re.I)
NEXT = re.compile(r"^\W*(next|next lesson|continue|go on)\W*$", re.I)
EXIT = quiz.EXIT
FLOW = ("learn:start", "learn:next:", "learn:ask:", "learn:finish")  # buttons that move the course itself


def _rec(engine: Engine, user: str) -> dict | None:
    return engine.state.setdefault("learn", {}).get(user)


def active(engine: Engine, user: str) -> bool:
    r = _rec(engine, user)
    return bool(r) and not r["done"] and not r["paused"]


def forget(engine: Engine, user: str):
    engine.state.setdefault("learn", {}).pop(user, None)
    engine._save()


def step_away(engine: Engine, user: str, button_id: str):
    """Another button (a lesson's action, a reminder): the course waits quietly, its "Next lesson" carries on."""
    r = _rec(engine, user)
    if r and button_id and not button_id.startswith(FLOW) and not r["done"] and not r["paused"]:
        r["paused"], r["note"] = True, False
        engine._save()


# ---------------- the numbers behind each lesson (engine only) ----------------
def numbers(engine: Engine) -> dict:
    """Every figure the five lessons use, straight from the engine (and the few products computed from them)."""
    b = engine.baskets("last_3_months")
    per = {x["basket"]: x for x in b["baskets"]}
    s = engine.safe_to_spend()
    fp = engine.full_picture()
    linked = next((a["balance_eur"] for bank in fp["banks"][1:] for a in bank["accounts"]
                   if a["type"] == "savings account"), 0.0)
    irregular = per["irregular"]["per_month_eur"]
    set_apart = round(s["reserve_eur"] + linked, 2)
    out = {"months": b["months"], "baskets": b["baskets"], "safe": s, "irregular_per_month": irregular,
           "buffer_target": round(irregular * BUFFER_MONTHS, 2), "set_apart": set_apart, "linked_savings": linked,
           "months_covered": round(set_apart / irregular, 1) if irregular else None,
           "top": _top_everyday(engine, b["months"]), "save": _saving(engine)}
    return out


def _top_everyday(engine: Engine, months: int) -> dict | None:
    """The kind of place where most 'slips through your fingers' money went over the last 3 months."""
    kinds = {}
    for c in BASKETS["everyday"]["categories"]:
        for label, total in engine.spending(c, "last_3_months")["by_merchant_type"].items():
            if not label.startswith(("Code ", "No merchant")):
                kinds[label] = kinds.get(label, 0) + total
    if not kinds:
        return None
    label = max(kinds, key=kinds.get)
    sp = engine.spending(merchant_type=label, period="last_3_months")
    per_month = round(sp["total_eur"] / months, 2)
    per_year = round(per_month * 12, 2)
    return {"kind": label, "total": sp["total_eur"], "payments": sp["transactions"], "per_month": per_month,
            "per_year": per_year, "per_day": round(per_year / 365, 2)}


def _saving(engine: Engine) -> dict | None:
    """A realistic monthly saving: the steady surplus, or else the first cut in basket 3; grown over 10 years."""
    s = engine.safe_to_save()
    steady = s["monthly"]["sustainable_saving_per_month_eur"]
    cuts = s.get("where_a_cut_could_come_from") or []
    if steady > 0:
        monthly, how = steady, "what's left over in a usual month"
    elif cuts:
        monthly, how = cuts[0]["saving_per_month_eur"], f"{cuts[0]['category'].lower()} −{cuts[0]['cut_pct']:g}%"
    else:
        return None
    grow = engine.grow_monthly(monthly, SCENARIO_YEARS, SCENARIO_FUND)[0]
    start_now = min(round(monthly), int(s["now_max_eur"] // 2))  # half of what's free now, so the week still works
    return {"monthly": monthly, "how": how, "grow": grow, "start_now": start_now}


# ---------------- the five lessons ----------------
def _when(d: str, today: date) -> str:
    days = (date.fromisoformat(d) - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    x = date.fromisoformat(d)
    return f"{x.strftime('%a')} {x.day} {x.strftime('%b')}"


def lesson(engine: Engine, i: int, n: dict | None = None) -> tuple[str, tuple[str, str]]:
    """(message, action button) for lesson i (0-based)."""
    n = n or numbers(engine)
    head = f"📚 *{i + 1}/{COUNT} · {TITLES[i]}*\n"
    if i == 0:
        rows = "\n".join(f"• {x['label']}: {eur(x['per_month_eur'])} ({x['share_pct']}%)" for x in n["baskets"])
        return (head + f"Every euro lands in one of three baskets. Yours, per month over the last {n['months']} "
                f"months:\n{rows}\nPlan for the first, keep a buffer for the second. The third is the only one you "
                "can change today.", ("quiz:start", "Play basket game"))
    if i == 1:
        s = n["safe"]
        bills = s["bills_before_payday"]
        due = (f"but {bills[0]['name'].lower()} of {eur(bills[0]['amount_eur'])} is due "
               f"{_when(bills[0]['due_date'], engine.today)}, and usual needs and a small buffer come first"
               if len(bills) == 1 else
               f"but {len(bills)} bills ({eur(s['bills_total_eur'])}) come first, then usual needs and a small buffer"
               if bills else "and usual needs and a small buffer for surprises come first")
        days = s["next_payday"]["days_away"]
        until = f"until payday in {days} days" if days is not None else f"for the next {s['horizon_days']} days"
        text = (head + f"Your balance is {eur(s['balance_eur'])}, {due}. What's left, "
                f"*{eur(s['safe_to_spend_until_payday_eur'])}*, is safe to spend {until}, about "
                f"{eur(s['safe_per_day_eur'])} a day.")
        if s["reserve_eur"] > 0:
            text += (f" The other {eur(s['reserve_eur'])} has built up beyond your pay cycle: that's savings, "
                     "not spending money.")
        else:
            text += " The balance is a snapshot; safe to spend is your real budget."
        return text, ("quiz:money", "Open My money")
    if i == 2:
        src = (GUIDE.get("emergency-fund", {}).get("sources") or [""])[0]
        m = n["months_covered"]
        cover = (f"What you have set apart, {eur(n['set_apart'])}, covers more than a year of it."
                 if n["set_apart"] > 0 and m is not None and m >= 12 else
                 f"What you have set apart, {eur(n['set_apart'])}, covers about {m:g} months of it."
                 if n["set_apart"] > 0 and m is not None else
                 f"Nothing is set apart for it yet: {BUFFER_MONTHS} months of it, {eur(n['buffer_target'])}, would "
                 "be a first buffer.")
        return (head + f"Basket 2, the doctor or a broken laptop, cost you {eur(n['irregular_per_month'])} a month "
                "on average. You can't know when, so a buffer turns those surprises into non-events. "
                f"{cover} S-Pankki's rule of thumb over time: 2-3 × your monthly net pay.\nSource: {src}",
                ("learn:buffer", "Set a buffer goal") if (m or 0) < BUFFER_MONTHS else ("ask:goal", "Set a savings goal"))
    if i == 3:
        t = n["top"]
        if not t:
            return (head + "Small daily amounts × 365 add up to real money. I can't see 'slips through your "
                    "fingers' payments yet, so there is nothing to add up for you.", ("ask:everyday", "Where does it go?"))
        return (head + f"Your biggest 'slips through your fingers' place over the last {n['months']} months: "
                f"*{t['kind'].lower()}*, {t['payments']} payments, {eur(t['total'])}. That's about "
                f"{eur(t['per_month'])} a month, or *{eur(t['per_year'])} a year*, {eur(t['per_day'])} a day. "
                "Nobody decides to spend that much at once; it goes one payment at a time.",
                ("ask:everyday", "Where does it go?"))
    sv = n["save"]
    if not sv:
        return (head + "Pay yourself first: move savings on payday, before spending starts, instead of saving "
                "whatever is left at the end. Even a small amount turns it into a habit.", ("ask:goal", "Set a savings goal"))
    g = sv["grow"]
    text = (head + "Move savings on payday, before spending starts, not whatever is left at the end. A realistic "
            f"amount for you: *{eur(sv['monthly'])} a month* ({sv['how']}). For {g['years']} years that's "
            f"{eur(g['paid_in_eur'])} paid in; in the Balanced demo fund's {g['scenario_return_pct']:g}% scenario "
            f"about {eur(g['value_eur'])}, growth on growth. A scenario, not a promise: funds can lose value.")
    if sv["start_now"] >= 5:
        return text, (f"learn:save:{sv['start_now']}", f"Save {sv['start_now']:,} € now".replace(",", " "))
    return text, ("ask:goal", "Set a savings goal")


def _show(engine: Engine, r: dict) -> Turn:
    i = r["lesson"]
    text, action = lesson(engine, i)
    nxt = (f"learn:next:{i}", "Next lesson") if i < COUNT - 1 else ("learn:finish", "I'm done")
    if i not in r["seen"]:
        r["seen"].append(i)
    engine._save()
    return Turn(text, [nxt, action, (f"learn:ask:{i}", "Ask Birky")])


def _hold_game(engine: Engine, user: str):
    """One flow at a time: a running basket game waits quietly while the course is on screen."""
    q = quiz._rec(engine, user)
    if q and not q["done"]:
        q["paused"], q["note"] = True, False


def start(engine: Engine, user: str) -> Turn:
    _hold_game(engine, user)
    runs = (engine.state.setdefault("learn", {}).get(user) or {}).get("run", 0)
    r = engine.state["learn"][user] = {"lesson": 0, "paused": False, "note": False, "done": False, "seen": [],
                                       "run": runs + 1}  # run: each go at the course comes back at most once
    t = _show(engine, r)
    t.lead = [f"📚 *Learn in 2 minutes*: {COUNT} short lessons, each with your own numbers. Write *stop* to pause "
              "and *learn* to pick it up."]
    return t


def finish(engine: Engine, r: dict) -> Turn:
    r["done"] = True
    engine._save()
    return Turn("🎓 That's all five, course done! 🎉 The short version: plan for basket 1, keep a buffer for "
                "basket 2, choose basket 3, and pay yourself first. Write *learn* any time to go again.",
                [("quiz:start", "Play basket game"), ("quiz:money", "Open My money")],
                lead=[f"🌰 {COUNT} of {COUNT} done!"], done=True)


def progress(done: int) -> str:
    """The streak line between lessons: "2 of 5 done 🌰", with a nudge as the end comes closer."""
    left = COUNT - done
    tail = " Last one, 30 seconds 💪" if left == 1 else " Past halfway 🙌" if done == (COUNT + 1) // 2 else ""
    return f"✅ {done} of {COUNT} done 🌰{tail}"


def turn(engine: Engine, user: str, text: str, button_id: str = "") -> Turn | None:
    """One course step. None = not a course turn (the game, the interview or the coach answers)."""
    r = _rec(engine, user)
    text = (text or "").strip()
    if button_id == "learn:start" or (not button_id and START.match(text)):
        if r and not r["done"]:  # "learn" again: back where they left
            r["paused"] = r["note"] = False
            _hold_game(engine, user)
            t = _show(engine, r)
            t.lead = [f"📚 Back to lesson {r['lesson'] + 1}/{COUNT}."]
            return t
        return start(engine, user)
    if button_id.startswith(FLOW):
        if not r or r["done"]:
            return Turn("That course is finished 🙂 Want to go through it again?", [("learn:start", "Start again")])
        r["paused"] = r["note"] = False
        _hold_game(engine, user)
        if button_id == "learn:finish":
            return finish(engine, r) if r["lesson"] == COUNT - 1 else _show(engine, r)
        kind, i = button_id.split(":")[1], int(button_id.rsplit(":", 1)[1])
        if i != r["lesson"]:
            return _show(engine, r)  # a button from an earlier lesson: the current one again
        if kind == "ask":
            r["paused"] = True
            engine._save()
            return Turn(f"Sure, ask me anything about *{TITLES[i].lower()}*, in your own words. Write *learn* to "
                        f"pick up at lesson {i + 1}/{COUNT}.")
        return _next(engine, r)
    if button_id or not r or r["done"] or r["paused"]:
        return None
    if EXIT.match(text):
        r["paused"] = True
        engine._save()
        return Turn(f"👍 Paused at lesson {r['lesson'] + 1}/{COUNT}. Write *learn* when you want to pick it up.")
    if NEXT.match(text):
        return finish(engine, r) if r["lesson"] == COUNT - 1 else _next(engine, r)
    r["paused"] = True
    r["note"] = not quiz.START.match(text)  # another question: the coach answers, the lesson waits
    engine._save()
    return None


def _next(engine: Engine, r: dict) -> Turn:
    r["lesson"] += 1
    t = _show(engine, r)
    t.lead = [progress(r["lesson"])]
    return t


def waiting_lesson(engine: Engine, user: str) -> str | None:
    """The lesson the person just stepped away from with a question (the paused note is still to come), or None."""
    r = _rec(engine, user)
    if not r or not r.get("note") or r["done"]:
        return None
    return lesson(engine, r["lesson"])[0]


def paused_note(engine: Engine, user: str) -> str | None:
    """Once, after the coach answered a side question mid-course: how to get back."""
    r = _rec(engine, user)
    if not r or not r.get("note"):
        return None
    r["note"] = False
    engine._save()
    return f"📚 Lesson {r['lesson'] + 1}/{COUNT} is waiting. Write *learn* to pick it up."


def goal_for(engine: Engine, button_id: str) -> tuple[str, float]:
    """(goal id, amount to move now) behind a lesson's saving button. 'learn:buffer' sets up an emergency buffer of
    three months of basket 2; 'learn:save:<X>' saves into the first goal, or a new 'Pay yourself first' one."""
    goals = engine.state.get("goals", {})
    n = numbers(engine)
    if button_id == "learn:buffer":
        gid = "emergencybuffer"
        if gid not in goals:
            engine.create_goal("Emergency buffer", max(10.0, n["buffer_target"]))
        free = int(engine.safe_to_save()["now_max_eur"] // 2)
        return gid, float(min(round(n["irregular_per_month"]), free))
    amount = float(button_id.rsplit(":", 1)[1])
    if goals:
        return next(iter(goals)), amount
    sv = n["save"]
    target = max(10.0, round(sv["monthly"] * 12, 2)) if sv else max(10.0, amount * 12)
    return engine.create_goal("Pay yourself first", target)["goal"]["id"], amount
