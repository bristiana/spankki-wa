"""'Can I afford it?'"""
import json
from datetime import date

import pytest

from app.engine import Engine
from app.tools import run_tool


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def test_afford_jacket(e):
    a = e.afford(120, "jacket")
    safe = e.safe_to_spend()
    assert a["safe_to_spend_until_payday_eur"] == safe["safe_to_spend_until_payday_eur"] == 68.6
    assert a["flexible_left_this_month_eur"] == round(a["flexible_budget_per_month_eur"] - a["spent_this_month_eur"], 2)
    assert "subscriptions_before_payday_eur" not in a  # the data has no subscriptions
    assert a["fits_flexible_budget"] and not a["fits_safe_to_spend"] and not a["fits"]
    assert a["safe_left_after_eur"] == -51.4 and a["per_day_after_eur"] == 0
    assert a["days_to_payday"] == 9 and a["goals"] == []


def test_afford_small_fits_and_goal_delay(e):
    e.create_goal("Trip to Lapland", 1500, "2027-06-30")  # needs 187.50 a month
    a = e.afford(20)
    assert a["fits"] and a["per_day_after_eur"] == round((68.6 - 20) / 9, 2)
    assert a["goals"] == [{"name": "Trip to Lapland", "monthly_pace_eur": 187.5,
                           "pushes_back_days": round(20 / 187.5 * 30.4)}]
    with pytest.raises(ValueError):
        e.afford(0)


def test_afford_tool(e):
    out = json.loads(run_tool(e, "can_i_afford", {"amount_eur": 120, "what": "jacket"}, []))
    assert out["item"] == "jacket" and out["price_eur"] == 120
