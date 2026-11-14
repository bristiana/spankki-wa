"""Safe to spend is this pay cycle's flexible money, not the whole balance: savings built up on the current account
are a reserve, offered to safe-to-save, never as this week's spending money."""
from datetime import date

import pytest

from app import main
from app.engine import Engine
from tests.test_fleet import engine, frame, history, run_all


def rows_of(df):
    return [(date.fromisoformat(r.tx_date), r.category, r.tx_amount_eur, r.transaction_type, r.merchant_id, r.mcc_code)
            for r in df.itertuples()]


def test_rich_saver_gets_the_cycle_budget_not_the_balance(tmp_path):
    e = engine(history(salary=3000.0, start=50_000.0), tmp_path)  # puts ~1,860 € a month aside for 2 years
    out = run_all(e)
    s = out["safe_to_spend"]
    frac = 9 / 30.4  # payday on the 15th, 9 days away
    assert s["income_per_month_eur"] == 3000.0
    assert s["mandatory_costs_per_month_eur"] == 1100.0  # rent 800 + groceries 300
    assert s["cycle_budget_eur"] == pytest.approx(1900.0 * frac, abs=0.01)
    assert s["safe_to_spend_until_payday_eur"] == s["cycle_budget_eur"] and s["limited_by"] == "pay_cycle"
    assert s["reserve_eur"] > 90_000
    assert s["safe_to_spend_until_payday_eur"] + s["reserve_eur"] == pytest.approx(s["left_after_bills_eur"], abs=0.02)
    assert f"{s['reserve_eur']:.2f} EUR" in s["basis"] and "savings" in s["basis"]
    save = out["safe_to_save"]
    assert save["reserve_eur"] == s["reserve_eur"]
    assert save["now_max_eur"] == pytest.approx(s["safe_to_spend_until_payday_eur"] + s["reserve_eur"], abs=0.01)
    assert "reserve_note" in save
    big = e.afford(5000.0, "sofa")
    assert not big["fits_safe_to_spend"] and big["fits_with_reserve"]


def test_over_spender_with_a_temporarily_high_balance(tmp_path):
    # Earns 1,000 €, rent and groceries cost 1,100 €: the balance is only high because of a one-off withdrawal
    # from investments last month, which is savings coming back, not income the pay cycle earns.
    df = history(salary=1000.0, start=3500.0)  # loses 140 € a month
    rows = rows_of(df) + [(date(2026, 9, 20), "Invest Withdrawal", 4000.0, "investment_withdrawal", None, None)]
    e = engine(frame(rows, start=3500.0), tmp_path)
    assert e.balance() == 4140.0
    out = run_all(e)
    s = out["safe_to_spend"]
    assert s["income_per_month_eur"] == 1000.0  # the withdrawal is left out
    assert s["cycle_budget_eur"] == 0 and s["reserve_eur"] > 0
    assert s["safe_to_spend_until_payday_eur"] == 0 and s["safe_per_day_eur"] == 0 and s["safe_this_week_eur"] == 0
    assert "does not cover" in s["basis"]
    a = out["afford"]
    assert not a["fits"] and a["fits_with_reserve"]
    assert out["safe_to_save"]["now_max_eur"] == s["reserve_eur"]


def test_no_salary_uses_other_income_over_the_30_day_horizon(tmp_path):
    df = history(salary=0, start=8000.0)
    extra = [(date(2026, m, 25), "Other Income", amt, "other_income", None, None)
             for m, amt in zip(range(4, 10), (2400.0, 900.0, 1800.0, 3000.0, 600.0, 2100.0))]
    e = engine(frame(rows_of(df) + extra, start=8000.0), tmp_path)
    out = run_all(e)
    s = out["safe_to_spend"]
    assert s["next_payday"]["date"] is None and s["horizon_days"] == 30 and s["not_enough_data"]
    assert s["income_per_month_eur"] == 1800.0  # 10,800 over the last 6 full months
    assert s["cycle_budget_eur"] == pytest.approx((1800.0 - 1100.0) * 30 / 30.4, abs=0.01)
    assert s["safe_to_spend_until_payday_eur"] <= s["cycle_budget_eur"]
    assert "in the next 30 days" in s["basis"] and "until payday" not in s["basis"]


def test_demo_persona_headline_numbers_unchanged(tmp_path):
    e = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    s = e.safe_to_spend()
    assert (s["safe_to_spend_until_payday_eur"], s["safe_per_day_eur"], s["safe_this_week_eur"]) == (68.6, 7.62, 53.36)
    assert s["limited_by"] == "balance" and s["reserve_eur"] == 0 and s["cycle_budget_eur"] > 68.6
    assert e.safe_to_save()["now_max_eur"] == 68.6
    assert "beyond this pay cycle" not in main.reminder_text(e=e)[0]


def test_reminder_names_the_reserve_only_when_it_matters():
    assert main.reserve_line(0) == "" and main.reserve_line(99.99) == ""
    assert main.reserve_line(81025.0) == "\nThe other 81 025.00 € on your account is beyond this pay cycle, more like savings."
