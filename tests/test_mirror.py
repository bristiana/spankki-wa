"""The app's chat mirrors the owner's WhatsApp conversation (app/mirror.py). Nothing here reaches Meta."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, mirror
from app import whatsapp as wa
from app.engine import Engine

OWNER = "358401234567"


@pytest.fixture(autouse=True)
def owner(monkeypatch):
    monkeypatch.setenv("MIRROR_PHONE", "+358 40 123 4567")
    monkeypatch.setattr(wa, "configured", lambda: False)  # never send for real
    mirror.reset()
    yield
    mirror.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    monkeypatch.setenv("EB_LINK_SECRET", "test-secret")
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    c = TestClient(main.app)
    c.cookies.set(main.enable_banking.COOKIE, main.enable_banking.sign(OWNER))  # the owner, logged in
    return c


def test_only_the_owner_is_mirrored_both_ways():
    mirror.incoming(OWNER, {"text": "How much can I spend?", "kind": "text"})
    mirror.incoming("358409999999", {"text": "someone else", "kind": "text"})
    wa.send_text(OWNER, "About *48 €* this week.")
    wa.send_text("358409999999", "not shown")
    log = mirror.since(0)
    assert [(m["role"], m["text"]) for m in log] == [("me", "How much can I spend?"), ("oiva", "About *48 €* this week.")]


def test_buttons_and_media_show_as_text():
    wa.send_reply_buttons(OWNER, "Rent is due tomorrow.", [("pay:1", "Pay now"), ("later:1", "Later")])
    wa.send_url_button(OWNER, "Ready in your app.", "Open S-mobiili", "https://x.test/s-mobiili/?ready=pay:1")
    mirror.incoming(OWNER, {"text": None, "kind": "image"})
    mirror.incoming(OWNER, {"text": None, "kind": "audio"})
    a, b, c, d = mirror.since(0)
    assert a["buttons"] == [{"label": "Pay now"}, {"label": "Later"}]
    assert b["buttons"] == [{"label": "Open S-mobiili", "url": "https://x.test/s-mobiili/?ready=pay:1"}]
    assert (c["text"], d["text"]) == ("Photo", "Voice note")


def test_poll_returns_only_what_is_new(client):
    mirror.add("me", "one")
    first = client.get("/api/mirror").json()
    assert first["on"] and [m["text"] for m in first["messages"]] == ["one"]
    mirror.add("oiva", "two")
    after = client.get("/api/mirror", params={"after": first["messages"][-1]["id"]}).json()["messages"]
    assert [m["text"] for m in after] == ["two"]


def test_app_chat_shares_the_whatsapp_history_and_never_sends(client, monkeypatch):
    sent = []
    monkeypatch.setattr(wa, "_send", lambda body: sent.append(body))
    j = client.post("/api/chat", json={"session": "x", "text": "__remind__", "mirror": True}).json()
    assert j["text"]
    assert main.coach(None).alias[mirror.APP_KEY] == OWNER  # same conversation as the owner's WhatsApp
    log = mirror.since(0)
    assert log and all(m["source"] == "app" and m["role"] == "oiva" for m in log)  # "__remind__" itself is not shown
    assert not sent


def test_chat_page_polls_when_mirroring(client, monkeypatch):
    assert "const MIRROR = true" in client.get("/chat").text
    monkeypatch.delenv("MIRROR_PHONE")
    monkeypatch.delenv("DEMO_PHONE", raising=False)
    assert "const MIRROR = false" in client.get("/chat").text


def test_nobody_else_sees_the_owners_conversation(client):
    """The owner's WhatsApp can hold real bank numbers: the demo account and strangers get their own chat."""
    mirror.add("oiva", "Across both banks you have 7 000 €")
    client.cookies.clear()
    assert client.get("/api/mirror").json() == {"on": False, "messages": []}
    assert "const MIRROR = false" in client.get("/chat").text
    client.cookies.set("oiva_demo", "1")
    assert client.get("/api/mirror").json()["messages"] == []
    client.cookies.set(main.enable_banking.COOKIE, main.enable_banking.sign("358409999999"))
    assert client.get("/api/mirror").json()["messages"] == []
    j = client.post("/api/chat", json={"session": "x", "text": "__remind__", "mirror": True}).json()
    assert j["text"] and mirror.APP_KEY not in main.coach(None).alias
