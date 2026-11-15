"""The figures behind S-mobiili's Your money charts, from the same Engine Oiva answers with (one set of numbers).

The month is Engine.money_month(): this month so far, or the last full month, whole, when this one has barely started
(the demo's today is 6 Oct and the data ends 30 Sep, so the charts show September, as Oiva does). Spending leaves out
rent and utility bills, like get_spending(exclude_bills=True); budgets are Engine.budgets(); the regular payments are
Engine.commitments(), as get_regular_payments gives them. The peer comparison (compare.json) stays the fixture built
from the whole data set by scripts/build_chart_mockup_fixtures.py.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from .engine import BILL_CATEGORIES, FLEX_CATEGORIES, Engine, eur

LABEL = {"Lifestyle": "Shopping"}
COMPARE = Path(__file__).parent / "static/mockups/charts/data/compare.json"
COMPARE_PERSONA = "CUST01656"  # the peer comparison was built for Antti's own transactions


def where_you_sit(e: Engine) -> dict:
    """The peer comparison Your money draws (compare.json): this customer vs people their age in their area, monthly
    averages over the 12 full months it was built on. Only for the customer it was built for."""
    if Path(e.csv_path).stem != COMPARE_PERSONA:
        raise ValueError("the Your money comparison is only built for the demo customer; use get_people_like_me")
    c = json.loads(COMPARE.read_text())
    return {k: c[k] for k in ("cohort", "figures", "source")} | {
        "window": f"monthly averages {c['cohort']['from']} to {c['cohort']['to']}",
        "note": "These are the exact figures on S-mobiili's Your money comparison cards. Lead with the figure asked "
                "about (income: 'you' and 'median'). Income here is the 12-month average, so it differs from one "
                "month's salary."}


def spend(e: Engine) -> dict:
    d0, d1, whole = e.money_month()
    b = e.budgets()
    by = {i["category"]: i for i in b["items"]}
    total = e.spending(None, start=d0.isoformat(), end=d1.isoformat(), exclude_bills=True)
    s = e._slice(d0, d1)
    out_ = s[s["tx_amount_eur"] < 0]
    flex = out_[out_["category"].isin(FLEX_CATEGORIES)]
    daily, run = [], 0.0
    for n in range(1, d1.day + 1):
        run += -flex.loc[flex["date"] == d0 + timedelta(days=n - 1), "tx_amount_eur"].sum()
        daily.append({"day": n, "cumulative_eur": eur(run)})
    days, budget_total = b["days_in_month"], b["budget_total_eur"]
    expected = eur(budget_total * d1.day / days)
    cats = [{"category": c, "label": LABEL.get(c, c), "spent_eur": i["spent_eur"],
             "avg_3m_eur": i["average_of_the_last_3_full_months_eur"], "budget_eur": i["limit_eur"]}
            for c, i in by.items()]
    bills = [{"name": "Rent" if r.category == "Housing" else "Utility bill", "date": r.date.isoformat(),
              "amount_eur": eur(-r.tx_amount_eur)}
             for r in out_[out_["category"].isin(BILL_CATEGORIES)].sort_values("date").itertuples()]
    paid = {k: [{"date": r.date.isoformat(), "amount_eur": eur(abs(r.tx_amount_eur))}
                for r in s[s["transaction_type"] == t].sort_values("date").itertuples()]
            for k, t in (("rent", "housing_payment"), ("investing", "investment_deposit"), ("salary", "salary"),
                         ("utilities", "utilities_payment"))}
    return {"as_of": e.today.isoformat(), "paid": paid, "month": d0.isoformat()[:7], "whole_month": whole, "day": d1.day,
            "total_eur": total["total_eur"], "prev_total_eur": total["previous_period"]["total_eur"],
            "budget_total_eur": budget_total,
            "pace": {"expected_by_today_eur": expected, "spent_by_today_eur": total["total_eur"],
                     "status": "over" if total["total_eur"] > expected else "under"},
            "categories": cats, "daily": daily, "bills": bills,
            "source": "Engine.spending(exclude_bills=True) and Engine.budgets(), the figures Oiva answers with"}


def budget(e: Engine) -> dict:
    b = e.budgets()
    items = [{"category": i["category"], "label": LABEL.get(i["category"], i["category"]), "limit_eur": i["limit_eur"],
              "spent_eur": i["spent_eur"], "left_eur": i["left_eur"],
              "status": "over" if i["status"] == "over" else "on_track" if i["limit_eur"] else "none"}
             for i in b["items"]]
    return {**{k: v for k, v in b.items() if k != "items"}, "items": items}


def recurring(e: Engine) -> dict:
    return e.commitments()


BUILD = {"spend": spend, "budget": budget, "recurring": recurring}
