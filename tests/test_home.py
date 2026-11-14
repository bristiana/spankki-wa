"""'Ready for my first home?' over the committed data/mortgage_benchmark.json."""
import json
from datetime import date
from pathlib import Path

import pytest

from app.engine import Engine
from app.tools import TOOLS, run_tool

BENCH = json.loads((Path(__file__).resolve().parents[1] / "data" / "mortgage_benchmark.json").read_text())


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def test_benchmark_shape():
    b = BENCH
    assert b["applications"] == 200 and b["approved"] == 150 and b["approval_rate_pct"] == 75.0
    a = b["approved_applications"]
    for k in ("property_price_eur", "loan_eur", "equity_share_pct", "loan_to_value_pct", "required_monthly_income_eur"):
        assert a[k]["p25"] <= a[k]["median"] <= a[k]["p75"]
    assert 0 < b["required_income_per_loan_eur"] < 0.05 and b["required_income_per_loan_spread"] < 1e-6
    reasons = [r["reason"] for r in b["rejection_reasons"]]
    assert reasons[0] == "insufficient_collateral" and sum(r["count"] for r in b["rejection_reasons"]) == 50
    assert set(b["one_off_cost_types"]) <= set(b["expenses"]) and "transfer_tax" in b["expenses"]
    assert sum(t["applications"] for t in b["property_types"].values()) == 200
    assert sum(g["applications"] for g in b["by_age"].values()) == 200


def test_home_readiness_from_income(e):
    h = e.home_readiness()
    income = h["your_money"]["monthly_salary_eur"]
    assert income == e.next_payday()["expected_eur"]
    loan = h["at_your_income"]["loan_eur"]
    assert loan == round(income / BENCH["required_income_per_loan_eur"], 2)
    p = h["at_your_income"]["price_range_eur"]
    assert loan < p["with_small_deposit"] < p["typical"] < p["with_big_deposit"]
    d = h["deposit"]
    assert d["price_eur"] == p["typical"] and d["loan_eur"] == pytest.approx(loan, abs=0.02)
    assert d["total_needed_eur"] == pytest.approx(d["equity_eur"] + sum(d["one_off_costs_eur"].values()), abs=0.02)
    assert d["you_have_eur"] == e.balance()
    assert d["still_to_save_eur"] == pytest.approx(d["total_needed_eur"] - d["you_have_eur"], abs=0.02)
    for s in h["saving_path"]:
        assert s["months"] * s["monthly_eur"] >= d["still_to_save_eur"] > (s["months"] - 1) * s["monthly_eur"]
    assert h["what_banks_look_at"][0]["reason"] == "insufficient_collateral"
    assert h["asp"]["guide_topic"] == "asp" and "not a loan offer" in h["label"].lower()
    assert h["suggested_goal"] == {"name": "First home deposit", "target_eur": d["total_needed_eur"]}
    assert "price_check" not in h


def test_home_readiness_counts_goal_savings(e):
    before = e.home_readiness()["deposit"]["you_have_eur"]
    e.create_goal("First home deposit", 26000)
    e.state["goals"]["firsthomedeposit"]["saved"] = 500.0
    assert e.home_readiness()["deposit"]["you_have_eur"] == round(before + 500, 2)


def test_home_readiness_with_price(e):
    h = e.home_readiness(200_000)
    c = h["price_check"]
    assert h["deposit"]["price_eur"] == 200_000
    assert c["monthly_income_needed_eur"] == round(c["loan_needed_eur"] * BENCH["required_income_per_loan_eur"], 2)
    assert not c["income_enough"] and c["income_short_by_eur"] > 0
    cheap = e.home_readiness(100_000)["price_check"]
    assert cheap["income_enough"] and cheap["income_short_by_eur"] == 0
    with pytest.raises(ValueError):
        e.home_readiness(5)


def test_home_readiness_tool(e):
    assert any(t["name"] == "get_home_readiness" for t in TOOLS)
    out = json.loads(run_tool(e, "get_home_readiness", {"price_eur": None}, []))
    assert "price_check" not in out and out["asp"]["sources"]
    out = json.loads(run_tool(e, "get_home_readiness", {"price_eur": 180000}, []))
    assert out["price_check"]["price_eur"] == 180000
