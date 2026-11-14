"""S-mobiili integration: native widget cards, chat deep links, embed mode, the concept page."""
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import Engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def cards(client) -> dict:
    r = client.get("/api/widgets")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    return {w["id"]: w for w in r.json()["widgets"]}


def question(link: str) -> str:
    u = urlparse(link)
    assert u.path == "/chat"
    return parse_qs(u.query)["q"][0]


def test_widgets_shape(client):
    j = client.get("/api/widgets").json()
    assert j["version"] == 1 and j["as_of"] == "2026-10-06"
    w = {c["id"]: c for c in j["widgets"]}
    assert list(w) == ["safe_to_spend", "next_bill", "baskets", "regular_payments"]  # no goal yet
    for c in j["widgets"]:
        assert {"title", "value", "subtitle", "deep_link"} <= set(c)
        assert question(c["deep_link"])
    assert question(w["next_bill"]["actions"][0]["deep_link"]).startswith("__btn__:pay:")


def test_widgets_match_engine(client):
    e, w = main.engine, cards(client)
    s = e.safe_to_spend()
    assert w["safe_to_spend"]["amount_eur"] == s["safe_to_spend_until_payday_eur"]
    assert w["safe_to_spend"]["per_day_eur"] == s["safe_per_day_eur"]
    assert w["safe_to_spend"]["payday"] == s["next_payday"]["date"]
    rent = next(b for b in e.upcoming_bills(45) if b["category"] == "Housing")
    assert (w["next_bill"]["name"], w["next_bill"]["amount_eur"], w["next_bill"]["due_date"]) == \
           ("Rent", rent["amount_eur"], rent["due_date"])
    b = e.baskets(w["baskets"]["period"])
    assert w["baskets"]["total_eur"] == b["total_spent_eur"] > 0
    assert [x["share_pct"] for x in w["baskets"]["baskets"]] == [x["share_pct"] for x in b["baskets"]]
    util = next(x for x in e.commitments()["regular_payments"] if x["id"] == "utilities")
    assert w["regular_payments"]["kind"] == "unusual_bill"  # the real 75 € utility bill of 28 Aug
    assert w["regular_payments"]["amount_eur"] == util["unusual_bill"]["amount_eur"] == 75.0
    assert w["regular_payments"]["usual_high_eur"] == util["unusual_bill"]["usual_high_eur"]


def test_goal_widget_after_a_goal(client):
    main.engine.create_goal("Lapland trip", 1500, "2027-06-30")
    g = cards(client)["goal"]
    plan = main.engine.goals()[0]
    assert g["progress_pct"] == plan["progress_pct"] and g["target_eur"] == 1500
    assert "Lapland trip" in question(g["deep_link"])


def test_pay_deep_link_prepares_payment_without_ai(client):
    q = question(cards(client)["next_bill"]["actions"][0]["deep_link"])
    j = client.post("/api/chat", json={"session": "m", "text": q}).json()
    assert j["buttons"][0]["url"].startswith("/s-mobiili/?ready=pay:") and "rent" in j["text"]
    assert main._coach is None  # no AI involved


def test_chat_is_an_s_mobiili_screen_and_embed_drops_the_phone(client):
    plain, embed = client.get("/chat").text, client.get("/chat?embed=1").text
    for page in (plain, embed):  # never the demo site's own bar: the chat is a screen of the app
        assert 'class="bar"' not in page and "<footer>" not in page and '<body class="embed">' in page
    assert 'class="phone" id="phone"' in plain and 'class="tabs"' in plain and ">Viestit" in plain
    assert 'class="phone bare" id="phone"' in embed and "safe-area-inset-top" in embed


def test_deep_link_question_is_escaped(client):
    evil = '</script><script>alert(1)</script>'
    html = client.get("/chat", params={"q": evil, "embed": "1"}).text
    assert evil not in html and "<script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html  # JSON-encoded for the script, not raw HTML
    assert 'const deepQ = "What is safe?"' in client.get("/chat?q=What is safe?").text
    assert "const deepQ = \"\"" in client.get("/chat").text


def test_mobile_demo_page(client):
    r = client.get("/mobile-demo")
    assert r.status_code == 200
    html = r.text
    assert "Concept" not in html and "Not the real app" not in html
    assert main.money_nb(main.engine.balance()) in html
    assert main.engine.recent(6)[0]["what"] in html
    assert "/api/widgets" in html and "embed=1" in html
    assert 'href="/mobile-demo"' in client.get("/").text
