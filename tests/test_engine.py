from datetime import date

import pandas as pd
import pytest

from app.engine import ROOT, Engine

CSV = ROOT / "data" / "persona.csv"


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "state.json")


def raw():
    return pd.read_csv(CSV)


def test_spending_matches_plain_pandas(e):
    r = raw()
    sep = r[(r.tx_date >= "2026-09-01") & (r.tx_date <= "2026-09-30") & (r.category == "Restaurants")]
    out = e.spending("Restaurants", "last_month")
    assert out["total_eur"] == round(-sep.tx_amount_eur.sum(), 2)
    assert out["transactions"] == len(sep)


def test_baskets_add_up(e):
    b = e.baskets("last_3_months")
    assert round(sum(x["total_eur"] for x in b["baskets"]), 2) == pytest.approx(b["total_spent_eur"], abs=0.02)
    assert sum(x["share_pct"] for x in b["baskets"]) in (99, 100, 101)


def test_rent_and_salary_detected(e):
    rec = {r.transaction_type: r for r in e.recurring()}
    assert rec["housing_payment"].typical_day == 7
    assert rec["salary"].typical_day == 15
    bills = e.upcoming_bills()
    assert bills[0]["name"] == "Rent" and bills[0]["due_date"] == "2026-10-07"


def test_balance_is_last_dataset_balance(e):
    assert e.balance() == round(float(raw().balance_after_eur.iloc[-1]), 2)


def test_safe_to_spend_formula(e):
    s = e.safe_to_spend()
    after = (s["balance_eur"] - s["bills_total_eur"] - s["utilities_estimate_eur"]
             - s["groceries_and_transport_estimate_eur"] - s["surprise_buffer_eur"])
    assert s["left_after_bills_eur"] == pytest.approx(after, abs=0.05)
    # The smaller of what the balance leaves and what the pay cycle sustains; here the balance is the limit.
    assert s["safe_to_spend_until_payday_eur"] == pytest.approx(min(after, s["cycle_budget_eur"]), abs=0.05)
    assert s["next_payday"]["date"] == "2026-10-15"


def test_paying_rent_moves_balance_and_clears_bill(e):
    before = e.balance()
    bill = e.upcoming_bills()[0]
    p = e.create_payment(bill["bill_id"])
    assert e.balance() == before  # nothing moves until confirmed
    done = e.confirm(p["pending_id"])
    assert done["balance_eur"] == round(before - bill["amount_eur"], 2)
    assert e.upcoming_bills()[0]["name"] != "Rent" or e.upcoming_bills()[0]["due_date"] != bill["due_date"]
    e.confirm(p["pending_id"])  # confirming twice is a no-op
    assert e.balance() == done["balance_eur"]


def test_simulate_cut(e):
    s = e.simulate_cut("Restaurants", 20)
    assert s["saving_per_month_eur"] == pytest.approx(s["current_per_month_eur"] * 0.2, abs=0.01)
    assert all(x["value_eur"] >= x["paid_in_eur"] for x in s["if_invested"])


def test_rejects_bad_input(e):
    with pytest.raises(ValueError):
        e.spending("Coffee")
    with pytest.raises(ValueError):
        e.create_investment(50, "crypto", False)
