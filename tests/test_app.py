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
    pid = url.rsplit("/", 1)[-1]
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
