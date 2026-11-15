"""The helper squirrel's pose for each reply (app/mood.py, docs/plans/squirrel-helper.md)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, mood
from app.engine import Engine
from app.onboarding import Turn


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


@pytest.fixture
def client(e, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    monkeypatch.setattr(main, "engine", e)
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


@pytest.mark.parametrize("tools", [["get_people_like_me"], ["get_spender_profile"], ["get_spending", "get_people_like_me"]])
def test_comparing_with_other_people_is_compare(tools):
    assert mood.for_reply(tools) == "compare"


@pytest.mark.parametrize("tools,want", [(["get_spending"], "spent"), (["get_baskets"], "spent"),
                                        (["get_safe_to_spend"], "safe_to_spend"), (["simulate_cut"], "what_if"),
                                        (["get_save_more_ideas"], "saving"), (["get_home_readiness"], "saving"), (["get_money_guide"], "teaching"),
                                        (["explore_my_data"], "working"), ([], "success")])
def test_not_comparing_takes_the_pose_of_what_was_done(tools, want):
    assert mood.for_reply(tools) == want


def test_a_pin_button_asks_for_approval():
    assert mood.for_reply(["get_upcoming_bills", "create_payment_link"], [{"label": "Pay", "url": "/pay/x"}]) == "needs_approval"
    assert mood.for_reply(["get_people_like_me", "create_saving_link"], [{"label": "Save", "url": "/save/x"}]) == "needs_approval"


def test_afford_is_caution_only_when_it_does_not_fit(e):
    safe = e.safe_to_spend()["safe_to_spend_until_payday_eur"]
    small = [{"tool": "can_i_afford", "args": {"amount_eur": 1, "what": "coffee"}}]
    big = [{"tool": "can_i_afford", "args": {"amount_eur": round(safe + 5000, 2), "what": "sofa"}}]
    assert mood.for_reply(["can_i_afford"], (), e, small) == "safe_to_spend"
    assert mood.for_reply(["can_i_afford"], (), e, big) == "caution"


def test_flows():
    assert mood.for_flow("quiz", Turn("*2/5* Which basket?", lead=["Right. Rent is basket 1."])) == "learned"
    assert mood.for_flow("quiz", Turn("*2/5* Which basket?", lead=["Easy to mix up: this one is ..."])) == "teaching"
    assert mood.for_flow("learn", Turn("Lesson 1/5")) == "teaching"
    assert mood.for_flow("learn", Turn("That's all five, course done!")) == "learned"
    assert mood.for_flow("interview", Turn("What are you saving for?")) == "listening"
    assert set(mood.STATES) >= {"compare", "spent", "listening", "learned", "caution", "needs_approval"}


def test_every_web_reply_names_a_pose(client):
    assert client.post("/api/chat", json={"text": "__remind__"}).json()["mood"] == "checking"
    assert client.post("/api/chat", json={"text": "game"}).json()["mood"] == "teaching"
    assert client.post("/api/chat", json={"text": "/new"}).json()["mood"] == "success"


def test_the_ai_failing_is_error(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no AI")
    monkeypatch.setattr(main, "ask", boom)
    j = client.post("/api/chat", json={"text": "how much did I spend on taxis?"}).json()
    assert j["mood"] == "error"


@pytest.mark.parametrize("lang", ["fi", "en"])
def test_chat_page_is_the_helper_only(client, lang):
    html = client.get(f"/chat?lang={lang}").text
    assert 'id="stage"' in html and 'id="stage" hidden' not in html and "/static/oiva/mascot/oiva.js" in html
    assert "data-mode" not in html and 'class="modes"' not in html and "oiva.css" not in html  # no Chat | Helper | Coin
    assert 'id="voice"' in html and 'id="photo"' in html
    js = open("app/static/oiva/helper.js").read()
    assert "coin" not in js.lower() and "setMode" not in js and "oiva-mode" not in js
    assert client.get("/static/oiva/mascot/oiva.js").status_code == 200
