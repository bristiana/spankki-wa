"""Charts in the web chat's bubbles: every figure is the engine's own, in English and Finnish."""
import json
from datetime import date

import pytest

from app import charts
from app.engine import Engine
from app.tools import run_tool


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def chart_of(e, name, args=None):
    buttons = []
    out = json.loads(run_tool(e, name, args or {}, buttons))
    return out, [b["chart"] for b in buttons if "chart" in b]


def test_baskets_answer_draws_the_three_baskets_with_the_engine_figures(e):
    out, cs = chart_of(e, "get_baskets", {"period": "last_3_months"})
    assert len(cs) == 1
    c = cs[0]
    assert c["type"] == "baskets" and c["labels"] == ["Bills & basics", "Occasional costs", "Fun & extras"]
    assert c["labels_fi"] == ["Laskut ja perusmenot", "Satunnaiset menot", "Valinnaiset menot"]
    assert c["values"] == [b["per_month_eur"] for b in out["baskets"]]  # the a-month figure Oiva says
    assert c["shares"] == [b["share_pct"] for b in out["baskets"]]
    io = c["inout"]
    assert io["values"][:2] == [out["income_eur"], out["total_spent_eur"]] and io["left"] == out["left_over_eur"]


def test_one_month_baskets_show_the_month_total(e):
    out, (c,) = chart_of(e, "get_baskets", {"period": "last_month"})
    assert c["values"] == [b["total_eur"] for b in out["baskets"]] and "kuu" in c["subtitle_fi"]


def test_safe_to_spend_waterfall_steps_down_to_the_engine_figure(e):
    out, (c,) = chart_of(e, "get_safe_to_spend")
    assert c["type"] == "waterfall" and c["kinds"][0] == "start" and c["kinds"][-1] == "end"
    assert c["values"][0] == out["balance_eur"] and c["values"][-1] == out["safe_to_spend_until_payday_eur"]
    left = c["values"][0] - sum(v for k, v in zip(c["kinds"], c["values"]) if k == "minus")
    if out["left_after_bills_eur"] >= 0:
        assert abs(left - c["values"][-1]) < 0.03  # the steps add up (to the cent, give or take rounding)
    assert len(c["labels_fi"]) == len(c["values"])


@pytest.mark.parametrize("name,args", [("get_spending", {"category": None, "period": "last_month"}),
                                       ("simulate_cut", {"category": "Restaurants", "cut_pct": 20}),
                                       ("can_i_afford", {"amount_eur": 300, "what": "jacket"})])
def test_other_answers_get_a_bar_chart_from_their_own_figures(e, name, args):
    out, (c,) = chart_of(e, name, args)
    assert c["type"] == "bar" and c["layout"] == "rows" and len(c["labels"]) == len(c["labels_fi"]) == len(c["values"])
    if name == "get_spending":
        assert sorted(c["values"], reverse=True) == sorted(out["by_category"].values(), reverse=True)
    if name == "simulate_cut":
        assert c["values"] == [out["current_per_month_eur"], out["new_per_month_eur"]]


def test_no_chart_for_a_tool_without_one(e):
    assert chart_of(e, "get_goals")[1] == [] and charts.for_tool("get_baskets", {"baskets": []}) is None


def test_waterfall_shows_the_gap_when_money_runs_short():
    out = {"balance_eur": 100.0, "bills_total_eur": 150.0, "utilities_estimate_eur": 10.0,
           "groceries_and_transport_estimate_eur": 40.0, "surprise_buffer_eur": 5.0, "reserve_eur": 0.0,
           "safe_to_spend_until_payday_eur": 0.0, "left_after_bills_eur": -105.0,
           "next_payday": {"date": "2026-10-15"}}
    c = charts.safe_to_spend(out)
    assert c["values"][-1] == 0.0 and c["short_by"] == 105.0 and "Kept as savings" not in c["labels"]
    assert charts.safe_to_spend({**out, "safe_to_spend_until_payday_eur": 20.0, "left_after_bills_eur": 20.0})["short_by"] is None


def test_every_merchant_kind_has_a_finnish_name():
    from app.engine import MCC_LABELS
    assert set(MCC_LABELS.values()) <= set(charts.MCC_FI)
