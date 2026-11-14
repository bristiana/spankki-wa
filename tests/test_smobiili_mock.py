"""The S-mobiili mock: Birky's buttons open review screens, Face ID (simulated) runs the same confirm as the PIN page."""
import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, tools
from app.engine import Engine

HTML = {"accept": "text/html"}
LABEL = "Concept prototype · not the real S-mobiili"


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "DEMO_PHONE", "WA_DISPLAY_NUMBER", "T212_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PUBLIC_URL", "http://127.0.0.1:8806")
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    for d in (main.owner, main.inbox):
        d.clear()
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def rent():
    return next(b for b in main.engine.upcoming_bills(45) if b["category"] == "Housing")


def chat_pay(client, session="t"):
    """Pay rent from the web chat: the button the chat shows, and the pending id behind it."""
    j = client.post("/api/chat", json={"session": session, "text": f"__btn__:pay:{rent()['bill_id']}"}).json()
    url = j["buttons"][0]["url"]
    return url, url.split("?")[0].rsplit("/", 1)[-1]


def goal_saving(amount=97, name="Lapland trip"):
    g = main.engine.create_goal(name, 1500, "2027-06-30")["goal"]
    return main.engine.create_saving(g["id"], amount)


def steps(page: str, sid="guide-steps") -> list[dict]:
    m = re.search(rf'<script type="application/json" id="{sid}">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


def confirm(client, route, pid, **body):
    return client.post(f"/s-mobiili/{route}/{pid}/confirm", json=body)


# ---------------- screens ----------------
def test_home_shows_accounts_and_recent_from_the_engine(client):
    page = client.get("/s-mobiili/").text
    assert LABEL in page and "Savings pocket" in page and "Funds" in page and "Not opened yet" in page
    assert main.money_nb(main.engine.balance()) in page
    assert main.engine.recent(8)[0]["what"] in page
    assert [s["target"] for s in steps(page)] == ["acct-current", "baskets", "acct-pocket", "acct-funds", "recent", "ask"]
    for s in steps(page):
        assert f'id="{s["target"]}"' in page
    assert client.get("/s-mobiili", follow_redirects=False).headers["location"] == "/s-mobiili/"


def test_persona_override_works_like_as(client):
    other = next(p for p in main.PERSONAS if p != main.DEFAULT_PERSONA)
    page = client.get(f"/s-mobiili/?persona={other}").text
    assert f"Hi {main.PERSONAS[other]['name']}" in page
    assert main.money_nb(main.engine_for(other).balance()) in page
    assert f"Hi {main.PERSONAS[main.DEFAULT_PERSONA]['name']}" in client.get("/s-mobiili/?persona=nobody").text


def test_pay_review_is_prefilled_and_guided(client):
    _, pid = chat_pay(client)
    page = client.get(f"/s-mobiili/pay/{pid}").text
    r = rent()
    assert LABEL in page and "Pay a bill" in page and "Confirm with Face ID" in page and "no camera" in page
    for text in ("Landlord · Asunto Oy Demo", "FI00 0000 0000 0000 00 (demo)", main.nice_date(r["due_date"]),
                 main.money_nb(r["amount_eur"])):
        assert text in page
    ref = main.demo_reference(r["bill_id"])
    assert ref in page and len(ref.replace(" ", "")) == 10
    s = steps(page)
    assert s[0] == {"target": "amount", "say": f"This is the amount we planned: {main.short_money(r['amount_eur'])}",
                    "mood": "talk"}
    assert s[-1]["target"] == "faceid" and "Face ID" in s[-1]["say"]
    assert all(f'id="{x["target"]}"' in page for x in s)
    assert "?pin=1" in page  # the PIN page stays one tap away


def test_save_first_time_opens_the_pocket(client):
    p = goal_saving(97)
    page = client.get(f"/s-mobiili/save/{p['pending_id']}").text
    assert 'id="step-pocket"' in page and "Open Savings pocket" in page
    assert steps(page, "pocket-steps")[0]["target"] == "open-pocket"
    assert steps(page)[0]["say"] == "This is the amount we planned: 97 €"  # an amount never breaks
    assert "Your Savings pocket" in steps(page)[1]["say"]
    assert confirm(client, "save", p["pending_id"], open_pocket=True).json()["title"] == "Saved"
    assert main.engine.state["savings_pocket_opened"] == "2026-10-06"
    again = main.engine.create_saving(p["goal_id"], 10)
    assert 'id="step-pocket"' not in client.get(f"/s-mobiili/save/{again['pending_id']}").text  # only the first time
    assert "97.00" in client.get("/s-mobiili/").text.replace(" ", " ")


def test_invest_shows_the_chosen_fund_and_risk_in_plain_words(client):
    p = main.engine.create_investment(50, "balanced", True)
    page = client.get(f"/s-mobiili/invest/{p['pending_id']}").text
    assert "Invest monthly" in page and "Demo Fund Balanced (your choice)" in page and "4 of 7" in page
    assert main.RISK_WORDS["medium"] in page and "not a promise" in page
    before = main.engine.balance()
    j = confirm(client, "invest", p["pending_id"]).json()
    assert j["ok"] and "Monthly saving started" in j["lines"][0]
    assert main.engine.balance() == before and main.engine.state["plans"][0]["fund_id"] == "balanced"
    assert "Monthly plan" in client.get("/s-mobiili/").text


def test_pause_investing_flow(client):
    p = main.engine.create_pause_investing()
    page = client.get(f"/s-mobiili/stop/{p['pending_id']}").text
    assert "Pause monthly investing" in page and f"Day {p['usual_day']} of the month" in page
    assert main.money_nb(p["amount"]) in page and "Stays invested" in page and main.nice_date(p["next_charge"]) in page
    assert "card payment" not in page.lower() and "merchant" not in page.lower()
    assert [s["target"] for s in steps(page)] == ["amount", "row-next", "row-invested", "faceid"]
    before = main.engine.balance()
    j = confirm(client, "stop", p["pending_id"]).json()
    assert j["title"] == "Investing paused" and "stays invested" in j["lines"][0]
    assert main.engine.balance() == before
    assert main.engine.commitments()["regular_payments"] and "paused" in client.get("/app").text
    assert confirm(client, "stop", p["pending_id"]).status_code == 409


def test_pay_a_bill_photo_shows_the_bills_own_iban_and_reference(client):
    p = main.engine._pend({"kind": "payment", "source": "photo", "bill_id": "photo-x", "name": "Voltti Energia Oy",
                           "category": "Utilities", "amount": 57.08, "due_date": "2026-10-20",
                           "iban": "FI5979900020451187", "reference": "20261005837", "invoice_number": None,
                           "transaction_type": "utilities_payment"})
    page = client.get(f"/s-mobiili/pay/{p['pending_id']}").text
    assert "FI59 7990 0020 4511 87" in page and "20261005837" in page and "Voltti Energia Oy" in page
    assert main.DEMO_IBAN not in page and "copied from your bill" in page


# ---------------- the confirm: same engine logic as the PIN page ----------------
def test_face_id_confirm_moves_money_once_and_tells_the_chat(client):
    _, pid = chat_pay(client, "t")
    before, amount = main.engine.balance(), rent()["amount_eur"]
    r = confirm(client, "pay", pid)
    assert r.status_code == 200 and r.json()["title"] == "Payment sent"
    assert main.engine.balance() == round(before - amount, 2) == r.json()["balance_eur"]
    msgs = client.get("/api/inbox?session=t").json()["messages"]
    assert len(msgs) == 1 and msgs[0].startswith("✅ *Rent paid*")
    again = confirm(client, "pay", pid)
    assert again.status_code == 409 and "Already confirmed" in again.json()["detail"]
    assert main.engine.balance() == round(before - amount, 2)
    assert client.get("/api/inbox?session=t").json()["messages"] == []
    page = client.get(f"/s-mobiili/pay/{pid}").text
    assert "Already confirmed" in page and 'id="step-review" hidden' in page


def test_the_bank_saying_no_is_a_refusal_not_a_crash(client):
    a, b = main.engine.create_payment(rent()["bill_id"]), main.engine.create_payment(rent()["bill_id"])
    assert confirm(client, "pay", a["pending_id"]).status_code == 200
    r = confirm(client, "pay", b["pending_id"])
    assert r.status_code == 422 and "already paid" in r.json()["detail"]
    assert len(main.engine.state["actions"]) == 1


def test_confirm_needs_json_and_a_live_link(client):
    _, pid = chat_pay(client)
    before = main.engine.balance()
    assert client.post(f"/s-mobiili/pay/{pid}/confirm", data={"x": "1"}).status_code == 415  # no cross-site form posts
    assert confirm(client, "save", pid).status_code == 404  # a payment id is not a saving
    assert confirm(client, "nope", pid).status_code == 404
    assert main.engine.balance() == before
    page = client.get("/s-mobiili/pay/nope", headers=HTML)
    assert page.status_code == 404 and "This link has expired" in page.text


# ---------------- links and the PIN fallback ----------------
def test_whatsapp_and_chat_buttons_open_s_mobiili(client):
    d = main.button_reply("358401234567", f"pay:{rent()['bill_id']}")
    assert d["url"][0] == main.OPEN_APP == "Open in S-mobiili" and len(main.OPEN_APP) <= 20
    assert d["url"][1].startswith("http://127.0.0.1:8806/s-mobiili/pay/") and "Face ID" in d["text"]
    url, _ = chat_pay(client)
    assert url.startswith("/s-mobiili/pay/") and url.endswith("?web=1")
    buttons = []
    tools.run_tool(main.engine, "create_payment_link", {"bill_id": rent()["bill_id"]}, buttons)
    g = main.engine.create_goal("Trip", 500)["goal"]
    tools.run_tool(main.engine, "create_saving_link", {"goal_id": g["id"], "amount_eur": 20}, buttons)
    tools.run_tool(main.engine, "create_investment_link", {"amount_eur": 30, "fund_id": "growth", "monthly": True}, buttons)
    tools.run_tool(main.engine, "create_pause_investing_link", {}, buttons)
    routes = [b["url"].split("/s-mobiili/")[1].split("/")[0] for b in buttons]
    assert routes == ["pay", "save", "invest", "stop"]
    assert all(b["url"].startswith("http://127.0.0.1:8806/s-mobiili/") for b in buttons)


def test_pin_page_still_works_as_a_fallback(client):
    _, pid = chat_pay(client, "t")
    r = client.get(f"/s-mobiili/pay/{pid}?pin=1&web=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/pay/{pid}?web=1"
    assert "Enter your 4-digit PIN" in client.get(f"/pay/{pid}").text
    page = client.post(f"/pay/{pid}", data={"pin": "1234"})
    assert "Payment sent" in page.text and len(client.get("/api/inbox?session=t").json()["messages"]) == 1
    assert confirm(client, "pay", pid).status_code == 409  # PIN first, then Face ID: still once


# ---------------- safety ----------------
def test_prefilled_fields_are_escaped(client):
    evil = '<img src=x onerror=alert(1)></script>'
    p = goal_saving(20, name=evil)
    page = client.get(f"/s-mobiili/save/{p['pending_id']}").text
    assert "<img src=x" not in page and "&lt;img src=x" in page
    for sid in ("guide-steps", "pocket-steps", "flow"):
        block = re.search(rf'id="{sid}">(.*?)</script>', page, re.S).group(1)
        assert "<" not in block  # tojson escapes < > & inside the script tag
    assert "<img src=x" not in client.get("/s-mobiili/").text
