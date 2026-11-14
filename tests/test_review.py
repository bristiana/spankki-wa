"""Regressions found in review: double spends, wrong state after actions, 500s on edge inputs."""
import threading
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Coach
from app.engine import Engine


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


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


def test_two_links_for_same_bill_pay_it_once(e):
    rent = e.upcoming_bills()[0]
    a, b = e.create_payment(rent["bill_id"]), e.create_payment(rent["bill_id"])
    before = e.balance()
    e.confirm(a["pending_id"])
    with pytest.raises(ValueError, match="already paid"):
        e.confirm(b["pending_id"])
    assert e.balance() == round(before - rent["amount_eur"], 2)


def test_paying_monthly_investment_clears_that_bill(e):
    bill = next(b for b in e.upcoming_bills() if b["name"] == "Monthly investment")
    e.confirm(e.create_payment(bill["bill_id"])["pending_id"])
    assert all(b["bill_id"] != bill["bill_id"] for b in e.upcoming_bills(45))
    with pytest.raises(ValueError):
        e.create_payment(bill["bill_id"])


def test_cannot_move_more_than_the_balance(e):
    p = e.create_investment(5000, "growth", False)
    with pytest.raises(ValueError, match="not enough money"):
        e.confirm(p["pending_id"])
    assert e.balance() > 0 and not e.state["actions"]
    e.confirm(e.create_investment(5000, "growth", True)["pending_id"])  # monthly plan moves nothing today


def test_safe_to_spend_counts_rent_twice_when_payday_is_after_next_rent(e):
    e.alert("salary")  # payday moves to 15 Nov, so rent on 7 Oct and 7 Nov both come first
    s = e.safe_to_spend()
    assert [b["due_date"] for b in s["bills_before_payday"]] == ["2026-10-07", "2026-11-07"]
    assert s["bills_total_eur"] == round(2 * 771.82, 2)


def test_recreating_goal_keeps_saved_money(e):
    e.create_goal("Trip", 500)
    e.confirm(e.create_saving("trip", 100)["pending_id"])
    plan = e.create_goal("Trip", 800)
    assert plan["goal"]["saved"] == 100 and plan["left_to_save_eur"] == 700


def test_confirm_reports_first_time(e):
    e.create_goal("Laptop", 900)
    p = e.create_saving("laptop", 10)
    assert e.confirm(p["pending_id"])["first_time"] is True
    assert e.confirm(p["pending_id"])["first_time"] is False


def test_double_submit_places_one_broker_order(client, monkeypatch):
    orders = []
    monkeypatch.setattr(main.broker, "configured", lambda: True)
    monkeypatch.setattr(main.broker, "place", lambda f, a: orders.append(a) or {"status": "placed", "legs": []})
    read = main.engine.pending
    monkeypatch.setattr(main.engine, "pending", lambda pid: (read(pid), time.sleep(0.2))[0])  # both see "pending"
    pid = main.engine.create_investment(50, "growth", False)["pending_id"]
    pages = []
    ts = [threading.Thread(target=lambda: pages.append(client.post(f"/invest/{pid}", data={"pin": "1234"})))
          for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(orders) == 1 and all(p.status_code == 200 for p in pages)
    assert main.engine.balance() == round(1001.94 - 50, 2)


def test_second_pay_link_shows_friendly_page(client):
    bill = main.engine.upcoming_bills()[0]["bill_id"]
    a, b = main.engine.create_payment(bill), main.engine.create_payment(bill)
    client.post(f"/pay/{a['pending_id']}", data={"pin": "1234"})
    r = client.post(f"/pay/{b['pending_id']}", data={"pin": "1234"})
    assert r.status_code == 200 and "Not done" in r.text and "already paid" in r.text


def test_wrong_pin_keeps_web_flag(client):
    pid = main.engine.create_payment(main.engine.upcoming_bills()[0]["bill_id"])["pending_id"]
    r = client.post(f"/pay/{pid}?web=1", data={"pin": "12"}, follow_redirects=False)
    assert r.headers["location"] == f"/pay/{pid}?err=1&web=1"


def test_bill_alert_with_nothing_to_say(client, monkeypatch):
    monkeypatch.setattr(main.engine, "commitments", lambda: {"regular_payments": []})
    assert client.post("/demo/alert", params={"kind": "bills", "to": "1"}).json() == {"sent": False,
                                                                                     "reason": "nothing to say"}
    r = client.post("/api/chat", json={"text": "__alert__:bills"})
    assert r.status_code == 200 and r.json()["text"] == "Nothing to flag right now."
    assert client.post("/api/chat", json={"text": "__alert__:renewal"}).status_code == 400  # no renewals any more


def test_bill_alert_is_the_real_utility_bill(client):
    r = client.post("/api/chat", json={"text": "__alert__:bills"}).json()
    assert "75,00 €" in r["text"] and "19,94 €" in r["text"] and "60,09 €" in r["text"]
    assert [q["send"] for q in r["quick"]] == ["__btn__:ask:bills", "__btn__:later:bills"]
    assert client.post("/api/chat", json={"text": "__btn__:later:bills"}).json()["text"].startswith("Okay")


def test_unknown_alert_kind_is_400(client):
    assert client.post("/api/chat", json={"text": "__alert__:bogus"}).status_code == 400


def test_reminder_after_rent_paid(client):
    main.engine.confirm(main.engine.create_payment(main.engine.upcoming_bills()[0]["bill_id"])["pending_id"])
    r = client.post("/api/chat", json={"text": "__remind__"})
    assert r.status_code == 200 and "Sat 07 Nov" in r.json()["text"]


def test_whatsapp_button_for_paid_bill_gets_friendly_text(client):
    main.engine.state["connected"]["407"] = {}
    main.handle_message({"id": "x", "from": "407", "name": "Åsa Ö", "text": "Pay rent now",
                         "button_id": "pay:housing_payment-2000-01-07"})
    body = client.sent[-1]["text"]["body"]
    assert body.startswith("I can't do that") and main.names["407"] == "Åsa"


def test_reset_clears_web_inbox(client):
    main._coach = Coach(main.engine, client=object())
    main.inbox["web:s"] = ["old"]
    client.post("/demo/reset")
    assert client.get("/api/inbox", params={"session": "s"}).json() == {"messages": []}
