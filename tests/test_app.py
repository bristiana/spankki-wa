import hashlib
import hmac
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Coach
from app.engine import Engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    monkeypatch.setenv("WA_APP_SECRET", "secret")
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_webhook_verify(client):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert r.text == "42"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no"}).status_code == 403


def wa_payload(text="Hi", mid="m1", button=None):
    m = {"from": "40700000000", "id": mid, "timestamp": "1", "type": "text", "text": {"body": text}}
    if button:
        m = {"from": "40700000000", "id": mid, "timestamp": "1", "type": "interactive",
             "interactive": {"type": "button_reply", "button_reply": {"id": button, "title": "Pay rent now"}}}
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": "40700000000", "profile": {"name": "Aino Test"}}], "messages": [m]}}]}]}


def post_signed(client, payload, secret="secret"):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=raw, headers={"x-hub-signature-256": sig, "content-type": "application/json"})


def test_bad_signature_rejected(client):
    assert post_signed(client, wa_payload(), secret="wrong").status_code == 403


def test_unconnected_user_gets_connect_button(client):
    main.engine.state.setdefault("switched_off", {})["40700000000"] = "2026-10-06"  # new numbers go into the demo
    assert post_signed(client, wa_payload()).status_code == 200
    cta = [b for b in client.sent if b.get("type") == "interactive"]
    assert cta and cta[0]["interactive"]["action"]["parameters"]["url"].endswith("/connect?u=40700000000")


def test_reminder_then_pay_flow(client):
    main.engine.state["connected"]["40700000000"] = {}
    r = client.post("/demo/remind", params={"to": "40700000000"})
    assert "771.82 €" in r.json()["text"] and "tomorrow" in r.json()["text"]
    bill_id = next(b for b in main.engine.upcoming_bills() if b["name"] == "Rent")["bill_id"]
    post_signed(client, wa_payload(mid="m2", button=f"pay:{bill_id}"))
    url = client.sent[-1]["interactive"]["action"]["parameters"]["url"]
    pid = url.rsplit(":", 1)[-1]  # ...?ready=pay:<pid>
    assert "Confirm payment" in client.get(f"/pay/{pid}").text
    before = main.engine.balance()
    page = client.post(f"/pay/{pid}", data={"pin": "1234"})
    assert page.status_code == 200 and "Payment sent" in page.text
    assert main.engine.balance() == round(before - 771.82, 2)
    assert "Rent paid" in client.sent[-1]["text"]["body"]


def test_connect_page_and_chat_page(client):
    assert "Connect S-Pankki" in client.get("/connect?u=1").text
    assert "money coach" in client.get("/chat").text


class FakeClient:
    """Plays Claude: first asks for a tool, then answers with the number it got back."""
    def __init__(self):
        self.calls = 0
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[
                NS(type="tool_use", id="t1", name="get_spending", input={"category": "Restaurants", "period": "last_month"})])
        result = json.loads(kw["messages"][-1]["content"][0]["content"])
        return NS(stop_reason="end_turn", content=[NS(type="text", text=f"You spent {result['total_eur']} €")])


def test_agent_loop_uses_tool_numbers(tmp_path):
    coach = Coach(Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json"), client=FakeClient())
    r = coach.ask("u", "restaurants last month?")
    assert r.text == "You spent 644.63 €" and r.tools_used == ["get_spending"]


def test_demo_panel_needs_key(client):
    assert client.get("/demo").status_code == 404
    assert "Demo control" in client.get("/demo", params={"key": "vt"}).text


def test_chat_api_rejects_non_object(client):
    assert client.post("/api/chat", json=["x"]).status_code == 400


def test_stop_switches_off(client):
    main.engine.state["connected"]["40700000000"] = {}
    post_signed(client, wa_payload(text="STOP", mid="m9"))
    assert "40700000000" not in main.engine.state["connected"]
    assert "switched off" in client.sent[-1]["text"]["body"]


def test_quick_reaction_and_redaction():
    from app import whatsapp as w
    assert w.quick_reaction("How much did I spend?") == "👀" and w.quick_reaction("ok") is None
    assert "[card number removed]" in w.redact("card 4242 4242 4242 4242")
    assert "[personal ID removed]" in w.redact("my id is 131052-308T")
    assert w.redact("I spent 120 euros") == "I spent 120 euros"
    assert w.bubbles("a\n\nb\n\nc\n\nd") == ["a", "b", "c\n\nd"]


def test_mydata_export(client):
    j = client.get("/app/export.json").json()
    assert j["transactions"] and j["balance_eur"] == main.engine.balance()
    csv_text = client.get("/app/export.csv").text
    assert csv_text.startswith("date,category,basket") and csv_text.count("\n") == len(j["transactions"]) + 1


def test_my_data_command(client):
    main.engine.state["connected"]["40700000000"] = {}
    post_signed(client, wa_payload(text="my data", mid="m10"))
    assert client.sent[-1]["interactive"]["action"]["parameters"]["url"].endswith("/app/export.json")


def test_window_and_failed_status(client, monkeypatch):
    monkeypatch.setenv("DEMO_PHONE", "40700000000")
    main.last_seen.clear()
    assert not main.window_open("40700000000")
    post_signed(client, wa_payload(text="Hi", mid="m11"))
    assert main.window_open("40700000000")
    assert "window open" in client.get("/demo", params={"key": "vt"}).text
    from app.whatsapp import failed_statuses
    st = {"entry": [{"changes": [{"value": {"statuses": [{"status": "failed", "recipient_id": "4070",
                                                           "errors": [{"code": 131047, "title": "Re-engagement"}]}]}}]}]}
    assert failed_statuses(st) == [{"to": "4070", "code": 131047, "title": "Re-engagement"}]


def test_demo_phone_stays_connected_until_stop(monkeypatch):
    from app import main
    monkeypatch.setenv("DEMO_PHONE", "40711111111")
    main.engine.state.get("connected", {}).pop("40711111111", None)
    main.engine.state.get("switched_off", {}).pop("40711111111", None)
    assert main.connected("40711111111")          # fresh container: no reconnect needed
    assert not main.connected("40722222222")      # anyone else still connects first
    main.switch_off("40711111111")
    assert not main.connected("40711111111")      # STOP is respected
    main.engine.state.get("switched_off", {}).pop("40711111111", None)
