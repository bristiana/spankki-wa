"""The first-chat interview after Connect S-Pankki: a resumable state machine, no AI.

Voice rules: one question per message, opened by one short line that reflects the previous answer in the person's
words (never grades it), one "?" per message, under 240 characters. A skip gets no comment. "ok", "thanks" or
"later" pauses the interview. A money question mid-interview goes to the coach and the waiting question comes back
after. The risk answer only labels later investment info, it never picks a fund.
State: engine.state["onboarding"][user] = {"step", "paused", "skipped", "answers"} in data/state.json.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .engine import Engine, add_months, month_start

ORDER = ["goal", "amount", "risk", "checkin"]
ASK = {  # first wording, then the one used when the question comes back
    "goal": ["What are you saving for: a buffer for surprises, a home renovation, the kids, a trip or something else?",
             "What would you like to put money aside for?"],
    "amount": ["How much do you need, and by when, like 1,500 € by June?",
               "Roughly what amount, and is there a date, like 800 € by March?"],
    "risk": ["If you ever invest for it, here is how much up and down each style means:\n"
             "• *Prudent*: small swings, slow and steady\n"
             "• *Balanced*: some swings, a mix of safe and growth\n"
             "• *Growth*: big swings, more growth over 5+ years\n"
             "I only use it to label info, never to choose for you. Which is closest to you?",
             "In plain words: *prudent* means your money barely moves, *balanced* means it can dip for a while, "
             "*growth* means it can drop a lot in a bad year but tends to grow more over many years. "
             "Which feels closest to you?"],
    "checkin": ["Want a short check-in from me once a week on how it's going?",
                "A weekly check-in on your goal, yes or no?"],
}
BUTTONS = {
    "goal": [("ob:goal:trip", "A trip"), ("ob:goal:buffer", "Emergency buffer"), ("ob:goal:home", "Home renovation")],
    "amount": [("ob:skip", "Skip")],
    "risk": [("ob:risk:prudent", "Prudent"), ("ob:risk:balanced", "Balanced"), ("ob:risk:growth", "Growth")],
    "checkin": [("ob:checkin:yes", "Yes, weekly"), ("ob:checkin:no", "No check-ins")],
}
PRESET_GOALS = {"trip": ("Trip", "a trip"), "buffer": ("Emergency buffer", "a buffer for surprises"),
                "home": ("Home renovation", "a home renovation")}  # three buttons: WhatsApp allows no more
RISK_LINE = {"prudent": "Prudent: steady matters more than fast.",
             "balanced": "Balanced: some ups and downs are fine.",
             "growth": "Growth: you can sit through bigger swings."}
RISK_WORDS = {"prudent": r"prudent|careful|safe|low|cautious|steady",
              "balanced": r"balanced|middle|medium|mix|moderate|between",
              "growth": r"growth|high|aggressive|bold|grow"}

WIND_DOWN = re.compile(r"^\s*(ok|okay|k|thanks|thank you|thx|ty|kiitos|later|maybe later|not now|bye)\s*[.!👍🙏]*\s*$", re.I)
RESUME = re.compile(r"^\s*(continue|resume|go on|next|let'?s go|let'?s continue|jatka)\s*[.!]*\s*$", re.I)
SKIP = re.compile(r"\b(skip|pass|rather not|prefer not|no idea|don'?t know|dunno|not sure)\b", re.I)
YES = re.compile(r"\b(yes|yep|yeah|sure|ok|okay|please|kyllä|joo)\b", re.I)
NO = re.compile(r"\b(no|nope|nah|not really|ei)\b", re.I)
RESTART = re.compile(r"^\W*(start over|get started|restart|start again|aloita alusta)\W*$", re.I)  # the welcome again
# "hello" at the goal question is a hello, not a goal ("Saving for hello"): small talk gets a warm re-ask
GREETING = re.compile(r"^\W*(hi+|hey+|hello+|helo|hiya|yo|heya|howdy|moi+|moikka|hei+|terve|hej|hejsan|tjena|"
                      r"good (?:morning|afternoon|evening|day)|huomenta|morjens?|sup|wassup|what'?s up)"
                      r"(?:\s+(?:there|oiva|again|you))?\W*$", re.I)
HOW_ARE_YOU = re.compile(r"^\W*(?:(?:hi|hey|hello|moi|hei)\W+)?(?:how are (?:you|u)|how'?s it going|how r u|"
                         r"how are things|mitä kuuluu|hur mår du)\W*$", re.I)
CHATTER = re.compile(r"^[^\w?]*(?:(?:lol|haha+|hehe+|xd|cool|nice|wow|test(?:ing)?)[^\w?]*)?$",
                     re.I)  # also emoji-only messages; a lone "?" is confusion, not small talk
QUESTION_START = re.compile(r"^\s*(what|how|why|where|when|which|who|can|could|should|is|are|do|does|am)\b", re.I)
# "i don't know what it means", "explain prudent vs balanced": a question back, not a skip
CONFUSED = re.compile(r"\b(explain|mean|means|meaning|difference|vs|versus|tell me (?:about|more)|what'?s|"
                      r"what (?:is|are|does)|don'?t (?:get|understand)|confus\w*)\b", re.I)

MONTHS = {m: i + 1 for i, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"),
     ("december", "dec")]) for m in names}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
BY_MONTH = re.compile(rf"(?:\b(?:by|until|till|before|in|end of)\s+(?:the\s+|next\s+)?({_MONTH}|summer|christmas)\b(?:\s+((?:20)\d\d))?"
                      rf"|\b({_MONTH})\s+((?:20)\d\d)\b)", re.I)
IN_SPAN = re.compile(r"\bin\s+(\d+)\s*(months?|years?)\b", re.I)
BY_YEAR = re.compile(r"\b(?:by|until|till|before|in|end of)\s+((?:20)\d\d)\b", re.I)


@dataclass
class Turn:
    text: str                                   # the message to send (the question, or the final plan)
    buttons: list = field(default_factory=list)  # reply buttons [(id, label)], max 3
    lead: list = field(default_factory=list)     # messages to send before text
    done: bool = False


def _rec(engine: Engine, user: str) -> dict | None:
    return engine.state.setdefault("onboarding", {}).get(user)


def active(engine: Engine, user: str) -> bool:
    r = _rec(engine, user)
    return bool(r) and r["step"] != "done" and not r["paused"]


def risk_of(engine: Engine, user: str) -> str | None:
    return ((_rec(engine, user) or {}).get("answers") or {}).get("risk")


def forget(engine: Engine, user: str):
    engine.state.setdefault("onboarding", {}).pop(user, None)
    engine._save()


def _money(x: float) -> str:
    s = f"{x:,.0f}" if float(x).is_integer() else f"{x:,.2f}"
    return s.replace(",", " ") + " €"


def _month_end(y: int, m: int) -> date:
    return add_months(date(y, m, 1), 1) - timedelta(days=1)


def parse_deadline(text: str, today: date) -> tuple[str | None, str]:
    """('2027-06-30', text without the date part). 'by June', 'June 2027', 'in 8 months', 'by 2027', 'next summer'."""
    m = BY_MONTH.search(text)
    if m:
        word, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        word = word.lower()
        mon = 6 if word == "summer" else 12 if word == "christmas" else MONTHS[word]
        y = int(year) if year else today.year
        end = _month_end(y, mon)
        if not year and end <= today:
            end = _month_end(y + 1, mon)
        return (end.isoformat() if end > today else None), text.replace(m.group(0), " ")
    m = IN_SPAN.search(text)
    if m:
        n = int(m.group(1)) * (12 if m.group(2).lower().startswith("y") else 1)
        d = add_months(month_start(today), n)
        return _month_end(d.year, d.month).isoformat(), text.replace(m.group(0), " ")
    m = BY_YEAR.search(text)
    if m and int(m.group(1)) >= today.year:
        return f"{m.group(1)}-12-31", text.replace(m.group(0), " ")
    return None, text


def parse_amount(text: str) -> float | None:
    """Biggest euro amount in a reply: '1,500', '1 500 €', '2k', '800e'. None if there is none or it's out of range."""
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*k\b", lambda m: str(float(m.group(1).replace(",", ".")) * 1000), text, flags=re.I)
    t = re.sub(r"(\d)[ ,.](?=\d{3}(?!\d))", r"\1", t)
    nums = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", t)]
    best = max(nums, default=0)
    return best if 10 <= best <= 1_000_000 else None


def _goal_from_text(text: str) -> tuple[str, str]:
    """('Trip to Lapland', 'a trip to Lapland') from 'I want 1500 for a trip to Lapland by June'."""
    m = re.search(r"\b(?:for|towards)\s+(?!about\b|around\b)([^\d,.;!?]+)", text, re.I)
    t = m.group(1) if m else re.sub(r"^\s*(i'?m |i am |i want |i'd like |we're )?(saving |to save )?(up )?", "",
                                    text, flags=re.I)
    t = re.split(r"[,.;!?]|\d|\b(?:by|until|till|before|in|about|around|next)\b", t, maxsplit=1)[0].strip()[:40].strip()
    t = t or "my savings"
    if re.match(r"(a|an|the|my)\b", t, re.I):
        t = t[0].lower() + t[1:]
    name = re.sub(r"^(a|an|the|my)\s+", "", t, flags=re.I)
    return name[:1].upper() + name[1:], t


def _when(deadline: str | None) -> str:
    return f"by {date.fromisoformat(deadline).strftime('%B %Y')}" if deadline else "no fixed date"


def _ask(step: str, reflect: str = "", again: bool = False) -> Turn:
    q = ASK[step][1 if again else 0]
    return Turn(text=f"{reflect}\n\n{q}" if reflect else q, buttons=BUTTONS[step])


def small_talk(text: str) -> str | None:
    """The warm line for a greeting or small talk ("hello", "how are you", "haha", "👋"), or None for a real answer."""
    if not (text or "").strip():
        return None
    if HOW_ARE_YOU.match(text):
        return "All good here, thanks for asking!"
    if GREETING.match(text):
        return "Hey, good to see you."
    if CHATTER.match(text):
        return "Ha, I like that."
    return None


def _is_question(text: str, step: str) -> bool:
    asked = text.strip().endswith("?") or bool(QUESTION_START.match(text)) or bool(CONFUSED.search(text))
    return asked and not (step == "amount" and parse_amount(text))


def start(engine: Engine, user: str) -> Turn:
    """Called right after the person connects S-Pankki. Starts (or restarts) the interview."""
    engine.state.setdefault("onboarding", {})[user] = {"step": "goal", "paused": False, "skipped": [], "answers": {}}
    engine._save()
    b = engine.baskets("last_3_months")
    lines = "\n".join(f"• {x['label']} (e.g. {x['example']}): {_money(x['per_month_eur'])} a month" for x in b["baskets"])
    lead = [f"Connected. Everything you spend falls in 3 baskets. Over the last 3 months, a month:\n{lines}",
            "A few short questions so I can help with *your* plan. Skip any of them."]
    t = _ask("goal")
    t.lead = lead
    return t


def reask(engine: Engine, user: str) -> Turn | None:
    """After the coach answered a side question: the waiting question again, in new words."""
    r = _rec(engine, user)
    return _ask(r["step"], again=True) if active(engine, user) else None


def turn(engine: Engine, user: str, text: str, button_id: str = "") -> Turn | None:
    """One interview step. None = not an interview turn (send the message to the coach)."""
    r = _rec(engine, user)
    text = (text or "").strip()
    ob = button_id.startswith("ob:")
    if not r or r["step"] == "done":
        if not ob:
            return None
        return start(engine, user) if not r else Turn("Your goal is already set up. Ask me anything about your money.")
    if button_id and not ob:
        return None  # another quick reply (pay, ask:...) is handled elsewhere
    if r["paused"]:
        if not (ob or RESUME.match(text)):
            return None
        r["paused"] = False
        engine._save()
        if not ob:
            return _ask(r["step"], again=True)
    step, a = r["step"], r["answers"]
    if ob and button_id.split(":")[1] not in (step, "skip"):
        return _ask(step, again=True)  # a button from an earlier question
    if not ob and step == "checkin" and YES.search(text) and not NO.search(text):
        button_id, ob = "ob:checkin:yes", True  # "ok" here means yes, not "later"
    if not ob and WIND_DOWN.match(text):
        r["paused"] = True
        engine._save()
        return Turn("Okay, later then. Write *continue* when you want to pick this up.")
    hello = None if ob else small_talk(text)
    if hello:  # never parsed as an answer: a warm word, then the same question in other words
        return _ask(step, hello, again=True)
    if not ob and _is_question(text, step):
        return None  # a question back: the coach answers it, then the waiting question comes back
    if button_id == "ob:skip" or (not ob and SKIP.search(text)):
        r["skipped"].append(step)
        return _advance(engine, r, "")

    value = button_id.split(":", 2)[2] if ob and button_id.count(":") == 2 else ""
    if step == "goal":
        if value in PRESET_GOALS:
            a["goal_name"], phrase = PRESET_GOALS[value]
        else:
            a["goal_name"], phrase = _goal_from_text(text)
            deadline, rest = parse_deadline(text, engine.today)
            amount = parse_amount(rest)
            if amount:
                a["amount"], a["deadline"] = amount, deadline
                return _advance(engine, r, f"Saving for {phrase}, {_money(amount)} {_when(deadline)}.")
        return _advance(engine, r, f"Saving for {phrase}.")
    if step == "amount":
        deadline, rest = parse_deadline(text, engine.today)
        amount = parse_amount(rest)
        a["deadline"] = deadline or a.get("deadline")
        if not amount:
            engine._save()
            return _ask("amount", again=True)
        a["amount"] = amount
        return _advance(engine, r, f"{_money(amount)} {_when(a['deadline'])}.")
    if step == "risk":
        risk = value or next((k for k, w in RISK_WORDS.items() if re.search(rf"\b({w})", text, re.I)), None)
        if risk not in RISK_LINE:
            return _ask("risk", again=True)
        a["risk"] = risk
        return _advance(engine, r, RISK_LINE[risk])
    # checkin
    if value:
        yes = value == "yes"
    elif NO.search(text) or YES.search(text):
        yes = not NO.search(text)
    else:
        return _ask("checkin", again=True)
    a["weekly_checkin"] = yes
    return _advance(engine, r, "A weekly check-in, then." if yes else "No check-ins, you ask when you want.")


def _advance(engine: Engine, r: dict, reflect: str) -> Turn:
    """Next unanswered, unskipped question, or the end with the goal plan."""
    a = r["answers"]
    done = {"goal": "goal_name" in a, "amount": "amount" in a, "risk": "risk" in a, "checkin": "weekly_checkin" in a}
    nxt = next((s for s in ORDER[ORDER.index(r["step"]) + 1:] if not done[s] and s not in r["skipped"]), None)
    if nxt:
        r["step"] = nxt
        engine._save()
        return _ask(nxt, reflect)
    r["step"] = "done"
    engine._save()
    return _finish(engine, r, reflect)


def _finish(engine: Engine, r: dict, reflect: str) -> Turn:
    a = r["answers"]
    head = f"{reflect}\n\n" if reflect else ""
    if not a.get("amount"):
        return Turn(head + "When you have an amount in mind, tell me and I'll make the plan.", done=True)
    plan = engine.create_goal(a.get("goal_name") or "Savings", a["amount"], a.get("deadline"))
    g = plan["goal"]
    lines = [f"*{g['name']}*: {_money(g['target'])} {_when(g['deadline'])}."]
    first = plan["options"][0]["monthly_eur"] if plan["options"] else 50
    if plan.get("needed_per_month_for_deadline_eur"):
        first = plan["needed_per_month_for_deadline_eur"]
        lines.append(f"That's *{_money(first)} a month* for {plan['months_to_deadline']} months.")
    cut = plan["cutting_restaurants_20pct_frees_per_month_eur"]
    for o in plan["options"]:
        what = " (dining out -20%)" if o["monthly_eur"] == cut else ""
        lines.append(f"• {_money(o['monthly_eur'])} a month{what}: there by "
                     f"{date.fromisoformat(o['reached_by'] + '-01').strftime('%b %Y')}")
    lines.append(plan["note"])
    first = round(first)
    return Turn(head + "\n".join(lines) + f"\n\nPut the first {_money(first)} aside now?",
                buttons=[(f"save:{g['id']}:{first}", f"Save {_money(first)}")], done=True)
