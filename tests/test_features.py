"""Pausing monthly investing, savings goals, portfolio, alerts and the new confirm pages."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.broker import decimals_for, shares_for
from app.engine import Engine


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def test_pause_investing(e):
    before = e.balance()
    assert any(b["name"] == "Monthly investment" for b in e.upcoming_bills(14))
    p = e.create_pause_investing()
    e.confirm(p["pending_id"])
    inv = next(x for x in e.commitments()["regular_payments"] if x["id"] == "investing")
    assert inv["status"] == "paused_by_you" and inv["paused_since"] == "2026-10-06" and inv["next_expected"] is None
    assert e.balance() == before  # pausing moves no money
    assert not any(b["name"] == "Monthly investment" for b in e.upcoming_bills(45))
    with pytest.raises(ValueError):
        e.create_pause_investing()  # already paused


def test_goal_and_saving(e):
    plan = e.create_goal("Trip to Lapland", 1500, "2027-06-30")
    assert plan["needed_per_month_for_deadline_eur"] == round(1500 / 8, 2)
    assert any(o["monthly_eur"] == 100 and o["months"] == 15 for o in plan["options"])
    before = e.balance()
    spent_before = e.spending(None, "this_month")["total_eur"]
    s = e.create_saving("triptolapland", 97)
    e.confirm(s["pending_id"])
    assert e.balance() == round(before - 97, 2)
    assert e.goal_plan("triptolapland")["goal"]["saved"] == 97
    # saving is not spending
    assert e.spending(None, "this_month")["total_eur"] == spent_before


def test_portfolio_matches_data(e):
    import pandas as pd
    raw = pd.read_csv(main.Engine().csv_path)
    dep = -raw[raw.transaction_type == "investment_deposit"].tx_amount_eur.sum()
    assert e.portfolio()["invested_total_eur"] == round(dep, 2)


def test_alerts(e):
    u = e.alert("unusual")
    assert u["found"] and u["times_usual"] >= 1.5
    b = e.alert("bills")  # the 75 € utility bill on 28 Aug is above the usual range
    assert b["found"] and b["type"] == "unusual_bill" and b["unusual_bill"]["amount_eur"] == 75.0
    assert e.alert("salary")["salary_eur"] > 0


def test_share_precision():
    assert decimals_for(120.5) == 4 and decimals_for(0.5) == 1
    assert shares_for(50, 120.5) == 0.4149


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    monkeypatch.delenv("T212_API_KEY", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_stop_and_save_pages(client):
    p = main.engine.create_pause_investing()
    page = client.get(f"/stop/{p['pending_id']}").text
    assert "Pause monthly investing" in page and "Stays invested" in page
    assert client.post(f"/stop/{p['pending_id']}", data={"pin": "12"}, follow_redirects=False).status_code == 303
    assert main.engine.state["paused"] == {}  # a wrong PIN pauses nothing
    r = client.post(f"/stop/{p['pending_id']}", data={"pin": "1234"})
    assert "Investing paused" in r.text and "Sun 18 Oct" in r.text
    assert main.engine.state["paused"] == {"investment_deposit": "2026-10-06"}
    main.engine.create_goal("Laptop", 900)
    s = main.engine.create_saving("laptop", 50)
    assert "Move to savings" in client.get(f"/save/{s['pending_id']}").text
    assert "Saved" in client.post(f"/save/{s['pending_id']}", data={"pin": "1234"}).text


def test_demo_alerts_send_buttons(client):
    for kind in ("salary", "unusual", "bills"):
        r = client.post("/demo/alert", params={"kind": kind, "to": "40700000000"})
        assert r.json()["sent"], kind
        assert client.sent[-1]["interactive"]["type"] == "button"


def test_real_price_backtest(e):
    r = e.backtest([(date(2025, 1, 15), 100.0)], "growth")
    closes = e.market["symbols"]["VWCE.DE"]["monthly_close"]
    expected = 100 / closes["2025-01"] * e.market["symbols"]["VWCE.DE"]["last_price"]
    assert r["value_today_eur"] == round(expected, 2)
    past = e.simulate_cut("Restaurants", 20)["real_past_check"]
    assert len(past["results"]) == 3 and past["results"][0]["paid_in_eur"] == round(24 * past["results"][0]["paid_in_eur"] / 24, 2)


def test_official_benchmark(e):
    b = e.official_benchmark()
    rest = next(r for r in b["comparison"] if r["category"] == "Restaurants")
    assert b["group"].endswith("17-24") and rest["finnish_households_eur"] == e.statfin["by_age"]["17-24"]["Restaurants"]
