"""Customers unlike the demo persona: each failure class the fleet check (scripts/fleet_check.py) found, rebuilt
from a small synthetic history."""
import json
from datetime import date, timedelta

import pandas as pd
import pytest

from app.engine import Engine, add_months, on_day
from app.tools import run_tool
from scripts.fleet_check import CALLS, numbers, sanity

TODAY = date(2026, 10, 6)


def history(months=24, salary=2500.0, salary_day=15, rent=800.0, rent_day=7, start=3000.0,
            restaurants=True, salaries=None):
    """One customer's rows like the dataset: salary, rent, groceries, restaurants, oldest first."""
    rows = []
    first = add_months(date(2026, 10, 1), -months)
    for i in range(months):
        m = add_months(first, i)
        if salaries is not None or salary:
            amt = salaries[i % len(salaries)] if salaries is not None else salary
            rows.append((on_day(m, salary_day), "Salary", amt, "salary", None, None))
        if rent:
            rows.append((on_day(m, rent_day), "Housing", -rent, "housing_payment", None, None))
        for d in (3, 10, 17, 24):
            rows.append((m.replace(day=d), "Groceries", -75.0, "purchase", "MRC-GRO-0001", "5411"))
        if restaurants:
            rows.append((m.replace(day=20), "Restaurants", -40.0, "purchase", "MRC-RES-0002", "5812"))
    return frame(rows, start)


def frame(rows, start=0.0):
    rows = sorted(rows, key=lambda r: r[0])
    bal, out = start, []
    for d, cat, amt, ttype, mid, mcc in rows:
        bal += amt
        out.append({"customer_id": "CUSTTEST", "bank_account_id": "ACCTEST", "tx_date": d.isoformat(),
                    "tx_timestamp": d.strftime("%d%b%y:12:00:00").upper(), "category": cat, "tx_amount_eur": amt,
                    "balance_after_eur": round(bal, 2), "transaction_type": ttype, "merchant_id": mid, "mcc_code": mcc})
    return pd.DataFrame(out)


def engine(df, tmp_path, age=None):
    return Engine(today=TODAY, state_path=tmp_path / "s.json", frame=df, age=age)


def run_all(e) -> dict:
    """Every read-only call the tools and dashboard make: none may raise, none may return NaN/inf."""
    out = {name: fn(e) for name, fn in CALLS if name != "explore" or hasattr(e, "explore")}
    for name, res in out.items():
        assert all(pd.notna(v) and abs(v) < 1e7 for _, v in numbers(res)), name
    assert sanity(out, e.age) == []
    assert not (e.state_path.exists()), "read-only calls must not write state"
    return out


def test_regular_customer_from_frame(tmp_path):
    e = engine(history(), tmp_path)
    out = run_all(e)
    assert out["next_payday"] == {"date": "2026-10-15", "days_away": 9, "expected_eur": 2500.0}
    assert [b["name"] for b in out["upcoming_bills"]][0] == "Rent"
    assert "not_enough_data" not in out["safe_to_spend"]


def test_no_salary_degrades_without_guessing_a_payday(tmp_path):
    e = engine(history(salary=0), tmp_path)
    out = run_all(e)
    assert out["next_payday"]["date"] is None and out["next_payday"]["not_enough_data"]
    s = out["safe_to_spend"]
    assert s["horizon_days"] == 30 and s["not_enough_data"]
    assert out["afford"]["days_to_payday"] is None and out["afford"]["not_enough_data"]
    assert out["salary_nudge"]["salary_eur"] is None
    h = out["home_readiness"]
    assert h["not_enough_data"] and "at_your_income" not in h
    assert json.loads(run_tool(e, "get_home_readiness", {"price_eur": None}, []))["not_enough_data"]


def test_irregular_income_is_not_treated_as_a_salary(tmp_path):
    e = engine(history(salaries=[600.0, 3200.0, 1400.0, 2900.0, 300.0]), tmp_path)
    out = run_all(e)
    assert out["next_payday"]["date"] is None
    assert out["monthly_flow"]["income_per_month_eur"] > 0  # the money still counts as income


def test_short_history_does_not_dilute_averages(tmp_path):
    e = engine(history(months=2), tmp_path)  # August and September only
    out = run_all(e)
    assert e.monthly_avg("Groceries", 3) == 300.0  # 4 x 75 a month, not 600 / 3
    assert e.monthly_avg("Groceries", 6) == 300.0
    assert out["monthly_flow"]["not_enough_data"] == "Only 2 full month(s) of history; averages use those."
    assert out["baskets"]["months"] == 2
    assert out["next_payday"]["date"] is None  # 2 months is too few to call it regular


def test_month_end_payday_is_not_moved_to_the_28th(tmp_path):
    out = run_all(engine(history(salary_day=30), tmp_path))
    assert out["next_payday"]["date"] == "2026-10-30" and out["safe_to_spend"]["horizon_days"] == 24


def test_month_end_bill_without_payday(tmp_path):
    s = run_all(engine(history(salary=0, rent_day=31), tmp_path))["safe_to_spend"]  # 30-day horizon, no payday
    assert [b["due_date"] for b in s["bills_before_payday"]] == ["2026-10-30"]  # median of 30 and 31


def test_no_rent(tmp_path):
    out = run_all(engine(history(rent=0), tmp_path))
    assert all(b["name"] != "Rent" for b in out["upcoming_bills"])
    assert out["safe_to_spend"]["bills_total_eur"] == 0


def test_negative_balance_is_never_shown_as_spendable(tmp_path):
    e = engine(history(salary=900.0, start=-100.0), tmp_path)  # spends more than comes in, always overdrawn
    assert e.balance() < 0
    out = run_all(e)
    s = out["safe_to_spend"]
    assert s["safe_to_spend_until_payday_eur"] == 0 and s["safe_per_day_eur"] == 0 and s["safe_this_week_eur"] == 0
    assert s["overdrawn"] and s["status"] == "overdrawn" and s["short_by_eur"] > 0  # never a negative "safe"
    assert out["safe_to_save"]["now_max_eur"] == 0
    assert not out["afford"]["fits"]
    assert out["home_readiness"]["deposit"]["covered_pct"] == 0


def test_a_few_transactions(tmp_path):
    rows = [(date(2026, 9, 2), "Groceries", -20.0, "purchase", "MRC-GRO-0001", "5411"),
            (date(2026, 9, 9), "Restaurants", -15.0, "purchase", "MRC-RES-0001", "5812"),
            (date(2026, 9, 30), "Other Income", 100.0, "other_income", None, None)]
    out = run_all(engine(frame(rows, start=50.0), tmp_path))
    assert out["next_payday"]["date"] is None
    assert out["monthly_flow"]["not_enough_data"]
    assert out["spending"]["total_eur"] == 35.0


def test_nothing_to_cut_has_no_backtest(tmp_path):
    e = engine(history(restaurants=False), tmp_path)
    out = run_all(e)
    assert out["simulate_cut"]["saving_per_month_eur"] == 0 and "real_past_check" not in out["simulate_cut"]
    assert all(p["monthly_eur"] > 0 for p in out["home_readiness"]["saving_path"])


def test_tiny_saving_pace_has_no_date_in_year_12000(tmp_path):
    rows = [(date(2026, m, d), "Restaurants", -0.3, "purchase", "MRC-RES-0001", "5812") for m in (7, 8, 9) for d in (1,)]
    e = engine(pd.concat([history(salary=1200.0, start=0.0, restaurants=False), frame(rows)]).sort_values("tx_date", kind="stable"), tmp_path)
    path = {p["how"]: p for p in e.home_readiness()["saving_path"]}
    cut = path["Cut restaurants by 20%"]
    assert cut["monthly_eur"] == 0.06 and cut["months"] > 600 and cut["reached_by"] is None
    e.create_goal("House", 50_000)
    assert any(o["reached_by"] is None for o in e.goals()[0]["options"])


def test_no_income_has_no_savings_rate(tmp_path):
    rows = [(date(2026, m, 5), "Invest Deposit", -100.0, "investment_deposit", None, None) for m in (7, 8, 9)]
    rows += [(date(2026, m, 6), "Groceries", -50.0, "purchase", "MRC-GRO-0001", "5411") for m in (7, 8, 9)]
    out = run_all(engine(frame(rows, start=2000.0), tmp_path))
    assert out["spender_profile"]["savings_rate_pct"] is None
    assert not any("saver" in t for t in out["spender_profile"]["traits"])


def test_peers_only_for_the_same_age_band(tmp_path):
    e = engine(history(), tmp_path, age=61)
    out = run_all(e)
    peers = out["spender_profile"]["peers"]  # not benchmark.json's band: the "people like you" group of their age
    assert peers["group"] is None or peers["age_band"] in ("50-64", "50+")
    assert out["spender_profile"]["finland_official"]["group"].endswith("aged all")
    young = engine(history(), tmp_path, age=24).spender_profile()
    assert young["peers"]["group"] == "S-Pankki customers aged 20-29"
    assert young["finland_official"]["group"].endswith("17-24")
