"""Which pose the helper squirrel takes for a reply (docs/plans/squirrel-helper.md).

The server tags each web chat reply with a `mood`, one of the mascot's 16 states (design/mascot/oiva.js), from what
the reply actually did: the tools Claude called, whether a purchase fits, the buttons it made, and which fixed flow
answered. No keyword matching on the person's words: a peer comparison is the reply that called a peer tool, in any
language and any phrasing. The page adds the moments between replies itself (listening, thinking).
"""
from __future__ import annotations

STATES = ("idle", "listening", "thinking", "working", "teaching", "checking", "needs_approval", "success", "error",
          "learned", "saving", "spent", "safe_to_spend", "caution", "what_if", "compare")

# Tools whose answer puts the person next to other people: S-Pankki customers of their age and area, Statistics
# Finland households, or mortgage applicants with their income.
PEER_TOOLS = {"get_people_like_me", "get_spender_profile", "get_home_readiness"}
APPROVAL_TOOLS = {"create_payment_link", "create_saving_link", "create_investment_link", "create_pause_investing_link"}
TOOL_MOOD = [  # first match wins, after peers, approvals and can_i_afford
    ({"simulate_cut"}, "what_if"),
    ({"get_safe_to_spend"}, "safe_to_spend"),
    ({"get_safe_to_save", "get_save_more_ideas", "create_goal", "get_goals"}, "saving"),
    ({"get_money_guide"}, "teaching"),
    ({"get_spending", "get_baskets", "get_regular_payments", "get_upcoming_bills"}, "spent"),
    ({"explore_my_data", "get_full_picture", "get_portfolio", "list_funds"}, "working"),
]


def afford_fits(engine, trace) -> bool | None:
    """True/False for the last can_i_afford in the trace (recomputed from its arguments), None if there was none."""
    for step in reversed(trace or []):
        if step.get("tool") == "can_i_afford":
            a = step.get("args") or {}
            try:
                return bool(engine.afford(float(a.get("amount_eur") or 0), str(a.get("what") or ""))["fits"])
            except (ValueError, TypeError, KeyError):
                return None
    return None


def for_reply(tools, buttons=(), engine=None, trace=None) -> str:
    """The pose for an answer from the coach: `tools` it called, `buttons` (PIN / Face ID links) it made."""
    used = set(tools or ())
    if used & PEER_TOOLS:
        return "compare"
    if buttons or used & APPROVAL_TOOLS:
        return "needs_approval"
    if "can_i_afford" in used:
        fits = afford_fits(engine, trace) if engine is not None else None
        return "caution" if fits is False else "safe_to_spend"
    for names, mood in TOOL_MOOD:
        if used & names:
            return mood
    return "success"


def for_flow(flow: str, turn) -> str:
    """The pose for a turn of a fixed flow: the Learn course, the basket game or the goal/risk interview."""
    said = " ".join([*(turn.lead or []), turn.text or ""])
    if flow == "learn":
        return "learned" if turn.done or "course done" in said else "teaching"
    if flow == "quiz":
        if any(x.startswith("Right.") for x in turn.lead or []) or "you know your baskets" in said:
            return "learned"
        return "teaching"
    return "listening"  # the interview asks the person something
