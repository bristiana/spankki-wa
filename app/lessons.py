"""'Why it matters': one line that teaches the idea behind a tool's numbers, with the person's own figures.

Deterministic, no AI. run_tool adds it to the tool result as "lesson"; the system prompt asks the coach to use at
most one such line per reply, when it fits. Every figure in a lesson is in the same tool result (or computed here
from it and written into it), so the number guard accepts it.
The ideas are the challenge's three baskets and a few core habits: plan for basket 1, buffer basket 2, choose
basket 3; safe to spend is not the balance; pay yourself first; small amounts × 12 or × 365.
"""
from __future__ import annotations

from datetime import date, timedelta


def eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ")


SLIPS = "Slips through your fingers"
IRREGULAR = "Mandatory & irregular"


def _one_month(r: dict) -> bool:
    """The period is one whole calendar month (so × 12 reads as 'a year like this')."""
    try:
        d0, d1 = date.fromisoformat(r["from"]), date.fromisoformat(r["to"])
    except (KeyError, TypeError, ValueError):
        return False
    return d0.day == 1 and d0.month == d1.month and (d1 + timedelta(days=1)).day == 1


def _spending(r: dict) -> str | None:
    total, n = r.get("total_eur") or 0, r.get("transactions") or 0
    if not total:
        return None
    basket = r.get("basket")
    if basket == SLIPS or (r.get("within_category") in ("Restaurants", "Lifestyle", "Entertainment")):
        if _one_month(r):
            r["per_year_if_every_month_eur"] = year = round(total * 12, 2)
            return (f"Small things add up: {n} payments came to {eur(total)} in a month, about "
                    f"{eur(year)} a year if every month looked like this.")
        return f"Small things add up: {n} payments of about {eur(r['average_per_transaction_eur'])} made {eur(total)}."
    if basket == IRREGULAR:
        return (f"Mandatory & irregular: needed, but you can't know when. A small buffer turns {eur(total)} like "
                "this into a non-event instead of a scramble.")
    if basket:
        return "Mandatory & regular: it comes back on a schedule, so it can be planned for before payday."
    return None


def _baskets(r: dict) -> str | None:
    b = {x["basket"]: x for x in r.get("baskets") or []}
    if "everyday" not in b:
        return None
    e = b["everyday"]
    return (f"Basket 1 you plan for, basket 2 you keep a buffer for, and basket 3 ({eur(e['per_month_eur'])} a "
            f"month, {e['share_pct']}%) is the only one you can change today.")


def _safe_to_spend(r: dict) -> str | None:
    bal, safe = r.get("balance_eur"), r.get("safe_to_spend_until_payday_eur")
    if bal is None or safe is None:
        return None
    if r.get("reserve_eur", 0) >= 300:
        return (f"Your balance is not your budget: {eur(r['reserve_eur'])} of it has built up beyond the pay cycle. "
                "Moving savings apart before they get spent is 'pay yourself first'.")
    return (f"Your balance is not your budget: of {eur(bal)}, {eur(safe)} is free once bills, usual needs and a "
            "buffer for surprises are kept aside.")


def _safe_to_save(r: dict) -> str | None:
    m = r.get("monthly") or {}
    steady = m.get("sustainable_saving_per_month_eur") or 0
    if steady > 0:
        return (f"Pay yourself first: {eur(steady)} moved on payday, before spending starts, makes saving a habit "
                "instead of whatever is left at the end.")
    cuts = r.get("where_a_cut_could_come_from") or []
    if cuts:
        c = cuts[0]
        return (f"With no steady surplus, saving starts in basket 3: {c['category'].lower()} −{c['cut_pct']:g}% "
                f"frees {eur(c['saving_per_month_eur'])} a month to pay yourself first.")
    return None


def _afford(r: dict) -> str | None:
    budget, price = r.get("flexible_budget_per_month_eur") or 0, r.get("price_eur") or 0
    if not budget or not price:
        return None
    pct = round(price / budget * 100)
    r["price_share_of_basket_3_pct"] = pct
    return (f"Wants come out of basket 3, the one you choose: this is {pct}% of your usual "
            f"{eur(budget)} a month there.")


def _cut(r: dict) -> str | None:
    month, year = r.get("saving_per_month_eur") or 0, r.get("saving_per_year_eur") or 0
    if not month:
        return None
    day = round(year / 365, 2)
    r["saving_per_day_eur"] = day
    return (f"Small amounts × 365: {eur(day)} a day is {eur(month)} a month and {eur(year)} a year. Cuts in basket 3 "
            "are the quickest to make.")


def _profile(r: dict) -> str | None:
    share = r.get("everyday_share_pct")
    if share is None:
        return None
    return f"{share}% of your spending is basket 3, slips through your fingers: the part you can change today."


def _regular(r: dict) -> str | None:
    month, year = r.get("bills_per_month_eur") or 0, r.get("bills_per_year_eur") or 0
    if not month:
        return None
    return (f"Rent and bills are basket 1 on autopilot: {eur(month)} a month, {eur(year)} a year, gone before you "
            "choose anything. Safe to spend starts after them.")


def _full_picture(r: dict) -> str | None:
    months = r.get("savings_cover_irregular_months")
    if not r.get("linked") or months is None:
        return None
    return (f"A buffer turns surprises from basket 2 into non-events: your savings cover about {months} months of "
            "them.")


def _goal(r: dict) -> str | None:
    opts = r.get("options") or []
    if not opts:
        return None
    o = opts[0]
    return (f"Pay yourself first: {eur(o['monthly_eur'])} set aside on payday, before spending starts, gets there in "
            f"{o['months']} months.")


LESSONS = {"get_spending": _spending, "get_baskets": _baskets, "get_safe_to_spend": _safe_to_spend,
           "get_safe_to_save": _safe_to_save, "can_i_afford": _afford, "simulate_cut": _cut,
           "get_spender_profile": _profile, "get_regular_payments": _regular, "get_full_picture": _full_picture,
           "create_goal": _goal}


def lesson_for(tool_name: str, result) -> str | None:
    """One short, data-specific teaching line for this tool result, or None. May add the derived figure it uses
    (e.g. saving_per_day_eur) to `result`, so the reply's number is backed by the tool result."""
    fn = LESSONS.get(tool_name)
    if not fn or not isinstance(result, dict):
        return None
    try:
        return fn(result)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
