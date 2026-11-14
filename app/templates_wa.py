"""Pre-approved WhatsApp templates: the only thing Birky may send more than 24 hours after the person last wrote.

Discreet on purpose: no amounts, no shop names, no goal titles on the lock screen. The full message is held and sent
when the person taps "Show me" or writes. Bodies must match docs/whatsapp-templates.md (what is submitted to Meta).

Meta rules: names lowercase with underscores; variables {{1}} {{2}} never at the start or end of the body and never
next to each other; a variable value has no new lines, tabs or links and here at most 40 characters.
"""
from __future__ import annotations

import re
from datetime import date

LANG = "en"
BUTTON = "Show me"
SHOW = "proactive:show"  # payload of the quick-reply button, so a tap is recognised
MAX_PARAM = 40

TEMPLATES = {
    "kurre_unusual_payment": "I noticed a payment on your account that's bigger than usual 👀 "
                             "Want to take a look at it with me?",
    "kurre_bill_due": "Your {{1}} payment is due {{2}}. Want to see what's safe to spend after it?",
    "kurre_subscription_renewal": "Your {{1}} subscription renews {{2}}. Want to check if it's still worth keeping?",
    "kurre_subscription_price_up": "Your {{1}} subscription got more expensive. "
                                   "Want to see by how much and if it's still worth it?",
    "kurre_goal_pace": "Your savings goal is a little behind plan. Want to see an easy way to catch up?",
    "kurre_salary_in": "Your salary just came in 💰 Want to put some aside before it slips through your fingers?",
    "kurre_weekly_checkin": "Hi 👋 Your weekly money check-in is ready. Want to see how your week went?",
}

_LINK = re.compile(r"(https?://|www\.)\S*|\S+\.(com|fi|net|org|io|app|me|ly)\b\S*", re.I)


def clean(value) -> str | None:
    """A safe template variable: no links, letters, digits, spaces and & ' - only, at most 40 chars. None if empty."""
    s = _LINK.sub(" ", str(value or ""))
    s = re.sub(r"[^\w &'-]|_", " ", s)
    s = re.sub(r"\s+", " ", s).strip()[:MAX_PARAM].strip()
    return s or None


def when(due: str, today: date) -> str:
    days = (date.fromisoformat(due) - today).days
    return {0: "today", 1: "tomorrow"}.get(days, "on " + date.fromisoformat(due).strftime("%a %d %b"))


def template_for(sig: dict, today: date) -> tuple[str, list[str]] | None:
    """(template name, params) for a signal, or None when there is no safe template for it."""
    d, kind = sig["data"], sig["type"]
    if kind == "RECURRING_CHARGE_UPCOMING":
        if d["source"] == "bill":
            name, params = "kurre_bill_due", [d["bill"]["name"].lower(), when(d["bill"]["due_date"], today)]
        else:
            name, params = "kurre_subscription_renewal", [d["name"], when(d["next_charge"], today)]
    elif kind == "SUBSCRIPTION_PRICE_INCREASE":
        name, params = "kurre_subscription_price_up", [d["name"]]
    else:
        name = {"UNUSUAL_TRANSACTION": "kurre_unusual_payment", "GOAL_DRIFT": "kurre_goal_pace",
                "SALARY_RECEIVED": "kurre_salary_in", "WEEKLY_CHECK_IN": "kurre_weekly_checkin"}.get(kind)
        params = []
    cleaned = [clean(p) for p in params]
    if not name or None in cleaned:  # Meta rejects an empty variable
        return None
    return name, cleaned
