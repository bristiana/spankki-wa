"""Charts for the web chat, built from a tool's own result: every figure on a chart is a number the engine returned,
never one the model wrote. tools.run_tool attaches them as {"chart": spec}; app/static/kchart.js draws them inside
the reply bubble (WhatsApp gets the words only).

Each spec carries English and Finnish text (title / title_fi, labels / labels_fi, ...): the page picks its language.
Types: "baskets" (one stacked bar of the three baskets + money in vs out), "waterfall" (balance -> safe to spend),
and the explorer's "bar" (rows or columns, drawn by kchart's own rules).
"""
from __future__ import annotations

from datetime import date

from .engine import CATEGORY_FI, eur

BASKET_FI = {"regular": "Laskut ja perusmenot", "irregular": "Satunnaiset menot", "everyday": "Valinnaiset menot"}
MONTH_FI = ("tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu", "kesäkuu", "heinäkuu", "elokuu", "syyskuu",
            "lokakuu", "marraskuu", "joulukuu")
CATEGORY_EN = {"Housing": "Rent", "Utilities": "Utility bills", "Groceries": "Groceries", "Transportation": "Transport",
               "Health": "Health", "Home & Tech": "Home & tech", "Travel": "Travel", "Restaurants": "Eating out",
               "Entertainment": "Entertainment", "Lifestyle": "Shopping"}


def _m(x: float, fi: bool = False) -> str:
    """2 178.44 € / 2 178,44 € (a non-breaking space between thousands, as the bubbles write them)."""
    s = f"{x:,.2f}".replace(",", "\u00a0")
    return (s.replace(".", ",") if fi else s) + "\u00a0€"


def _cat(c: str) -> tuple[str, str]:
    fi = CATEGORY_FI.get(c, c)
    return CATEGORY_EN.get(c, c), fi[:1].upper() + fi[1:]


def _span(d0: str, d1: str) -> tuple[str, str]:
    """'September 2026' / 'syyskuu 2026' for one calendar month, else '1 Jul – 30 Sep 2026' / '1.7.–30.9.2026'."""
    a, b = date.fromisoformat(d0), date.fromisoformat(d1)
    if a.day == 1 and a.month == b.month and a.year == b.year:
        return f"{a:%B %Y}", f"{MONTH_FI[a.month - 1]} {a.year}"
    return f"{a.day} {a:%b} – {b.day} {b:%b %Y}", f"{a.day}.{a.month}.–{b.day}.{b.month}.{b.year}"


def baskets(out: dict) -> dict | None:
    """get_baskets: the three baskets as one stacked bar, a month's average when the period is longer (that is the
    figure Oiva says), then the period's money in vs money out."""
    rows = out.get("baskets") or []
    if not rows or not out.get("total_spent_eur"):
        return None
    n = out.get("months") or 1
    per = n > 1
    en, fi = _span(out["from"], out["to"])
    spec = {
        "type": "baskets", "unit": "€", "decimals": 2,
        "title": "Where your money goes", "title_fi": "Mihin rahasi menevät",
        "subtitle": f"Average month · last {n} months" if per else en,
        "subtitle_fi": f"Kuukausikeskiarvo · viimeiset {n} kuukautta" if per else fi,
        "keys": [r["basket"] for r in rows],
        "labels": [r["label"] for r in rows],
        "labels_fi": [BASKET_FI.get(r["basket"], r["label"]) for r in rows],
        "values": [r["per_month_eur"] if per else r["total_eur"] for r in rows],
        "shares": [r["share_pct"] for r in rows],
    }
    inc, inv = out.get("income_eur"), out.get("invested_eur") or 0.0
    if inc is not None:
        spent = out["total_spent_eur"]
        spec["inout"] = {
            "title": f"Money in vs out · {'last ' + str(n) + ' months' if per else en}",
            "title_fi": f"Tulot ja menot · {'viimeiset ' + str(n) + ' kuukautta' if per else fi}",
            "labels": ["Money in", "Spent"] + (["Invested"] if inv else []),
            "labels_fi": ["Tulot", "Kulutus"] + (["Sijoitettu"] if inv else []),
            "values": [inc, spent] + ([inv] if inv else []),
            "left": out.get("left_over_eur"),
        }
    return spec


def safe_to_spend(out: dict) -> dict | None:
    """get_safe_to_spend: balance, minus what is already spoken for until payday, down to what is safe to spend."""
    if out.get("balance_eur") is None or out.get("safe_to_spend_until_payday_eur") is None:
        return None
    needs = eur((out.get("utilities_estimate_eur") or 0) + (out.get("groceries_and_transport_estimate_eur") or 0))
    steps = [("start", "Balance now", "Saldo nyt", out["balance_eur"]),
             ("minus", "Bills before payday", "Laskut ennen palkkapäivää", out.get("bills_total_eur") or 0.0),
             ("minus", "Food, transport, utilities", "Ruoka, liikkuminen, sähkö", needs),
             ("minus", "Buffer for surprises", "Puskuri yllätyksiin", out.get("surprise_buffer_eur") or 0.0)]
    if out.get("reserve_eur"):
        steps.append(("minus", "Kept as savings", "Jää säästöön", out["reserve_eur"]))
    steps.append(("end", "Safe to spend", "Turvallisesti käytettävissä", out["safe_to_spend_until_payday_eur"]))
    steps = [s for s in steps if s[0] != "minus" or s[3]]  # a zero step says nothing
    pay = (out.get("next_payday") or {}).get("date")
    if pay:
        d = date.fromisoformat(pay)
        sub, sub_fi = f"Until payday, {d:%a} {d.day} {d:%b}", f"Palkkapäivään {d.day}.{d.month}. asti"
    else:
        days = out.get("horizon_days") or 30
        sub, sub_fi = f"Next {days} days", f"Seuraavat {days} päivää"
    return {"type": "waterfall", "unit": "€", "decimals": 2,
            "title": "What's safe to spend", "title_fi": "Mitä voit käyttää turvallisesti",
            "subtitle": sub, "subtitle_fi": sub_fi,
            "kinds": [s[0] for s in steps], "labels": [s[1] for s in steps], "labels_fi": [s[2] for s in steps],
            "values": [s[3] for s in steps]}


def _rows(title, title_fi, sub, sub_fi, labels, labels_fi, values, **more) -> dict:
    return {"type": "bar", "layout": "rows", "unit": "€", "decimals": 2, "title": title, "title_fi": title_fi,
            "subtitle": sub, "subtitle_fi": sub_fi, "labels": labels, "labels_fi": labels_fi, "values": values, **more}


def spending(out: dict) -> dict | None:
    """get_spending: where it went by category (or kind of place), else this period against the one before."""
    en, fi = _span(out["from"], out["to"])
    by = out.get("by_category") or {}
    if len(by) > 1:
        cats = sorted(by, key=lambda c: -by[c])
        return _rows("Spending by category", "Kulutus luokittain", f"{en} · total {_m(out['total_eur'])}",
                     f"{fi} · yhteensä {_m(out['total_eur'], True)}",
                     [_cat(c)[0] for c in cats], [_cat(c)[1] for c in cats], [by[c] for c in cats])
    kinds = out.get("by_merchant_type") or {}
    name_en, name_fi = _cat(out.get("category") or "")
    if len(kinds) > 1:
        ks = sorted(kinds, key=lambda k: -kinds[k])
        return _rows(f"{name_en} by kind of place", f"{name_fi}: paikan tyypin mukaan", en, fi, ks, ks,
                     [kinds[k] for k in ks])
    prev = out.get("previous_period") or {}
    if prev.get("total_eur") is None or not (out.get("total_eur") or prev.get("total_eur")):
        return None
    p_en, p_fi = _span(prev["from"], prev["to"])
    title = name_en if out.get("category") not in (None, "all spending") else "Spending"
    title_fi = name_fi if out.get("category") not in (None, "all spending") else "Kulutus"
    return _rows(title, title_fi, "This period and the one before", "Tämä jakso ja edellinen",
                 [en, p_en], [fi, p_fi], [out["total_eur"], prev["total_eur"]])


def simulate_cut(out: dict) -> dict | None:
    if not out.get("current_per_month_eur"):
        return None
    en, fi = _cat(out.get("category") or "")
    pct = out.get("cut_pct")
    return _rows(f"{en}: now and after −{pct:g}%", f"{fi}: nyt ja −{pct:g} % jälkeen",
                 f"A month · frees {_m(out['saving_per_year_eur'])} a year",
                 f"Kuukaudessa · vapauttaa {_m(out['saving_per_year_eur'], True)} vuodessa",
                 ["Now", f"After −{pct:g}%"], ["Nyt", f"−{pct:g} % jälkeen"],
                 [out["current_per_month_eur"], out["new_per_month_eur"]])


def upcoming_bills(out: list) -> dict | None:
    """get_upcoming_bills: the dated bills, biggest first (the 'several small bills' average is not a dated bill)."""
    bills = [b for b in out or [] if b.get("due_date") and b.get("amount_eur")]
    if not bills:
        return None
    bills = sorted(bills, key=lambda b: b["due_date"])
    def when(b, fi=False):
        d = date.fromisoformat(b["due_date"])
        return f"{d.day}.{d.month}." if fi else f"{d.day} {d:%b}"
    return _rows("Bills coming up", "Tulevat laskut", "By due date", "Eräpäivän mukaan",
                 [f"{b['name']} · {when(b)}" for b in bills], [f"{b['name']} · {when(b, True)}" for b in bills],
                 [b["amount_eur"] for b in bills])


def afford(out: dict) -> dict | None:
    """can_i_afford: the price next to what is safe to spend and what is left of the usual fun & extras."""
    if not out.get("price_eur"):
        return None
    labels = [(out.get("item") or "Price").capitalize(), "Safe to spend until payday", "Fun & extras left this month"]
    labels_fi = [(out.get("item") or "Hinta").capitalize(), "Turvallisesti käytettävissä", "Valinnaisia menoja jäljellä"]
    values = [out["price_eur"], out.get("safe_to_spend_until_payday_eur") or 0.0,
              out.get("flexible_left_this_month_eur") or 0.0]
    return _rows("Can you afford it?", "Onko sinulla varaa?", "The price next to what you have",
                 "Hinta ja mitä sinulla on", labels, labels_fi, values, highlight=0)


BUILD = {"get_baskets": baskets, "get_safe_to_spend": safe_to_spend, "get_spending": spending,
         "simulate_cut": simulate_cut, "get_upcoming_bills": upcoming_bills, "can_i_afford": afford}


def for_tool(name: str, out) -> dict | None:
    """The chart for a tool's result, or None (no chart for this tool, or nothing to draw)."""
    f = BUILD.get(name)
    if not f:
        return None
    try:
        return f(out)
    except (KeyError, TypeError, ValueError):  # a chart is a nicety: never break the answer over one
        return None
