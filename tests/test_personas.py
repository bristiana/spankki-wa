"""Persona switcher: judges view the app as other real customers, each with its own engine, state and coach."""
import json
import re
from datetime import date
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main, tools
from app.agent import Coach
from app.engine import Engine

INDEX = json.loads((main.PERSONA_DIR / "index.json").read_text(encoding="utf-8"))
OTHERS = [p["id"] for p in INDEX if p["id"] != main.DEFAULT_PERSONA]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = main.persona_engine(main.DEFAULT_PERSONA, date(2026, 10, 6), tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def safe(client, **params) -> float:
    w = {c["id"]: c for c in client.get("/api/widgets", params=params).json()["widgets"]}
    return w["safe_to_spend"]["amount_eur"]


def test_index_lists_the_hero_first_then_aino_and_four_real_customers():
    hero = INDEX[0]
    assert hero.get("hero") and main.HERO == main.DEFAULT_PERSONA == hero["id"] and main.DEMO_NAME == hero["name"]
    assert 38 <= hero["age"] <= 45 and hero["why"]
    assert INDEX[1]["id"] == main.AINO == "CUST04831" and INDEX[1]["name"] == "Aino" and INDEX[1]["file"] is None
    assert len(OTHERS) == 5 and len(set(p["name"] for p in INDEX)) == 6
    for p in INDEX:
        assert p["age"] and p["label"] and p["description"]
        assert p["file"] is None or (main.PERSONA_DIR / p["file"]).exists()


def test_the_hero_is_the_default_and_aino_stays_switchable(client, monkeypatch):
    monkeypatch.delenv("MIRROR_PHONE", raising=False)
    monkeypatch.delenv("DEMO_PHONE", raising=False)  # not mirroring WhatsApp: the chat follows the chosen customer
    assert main.engine.customer_id == main.HERO
    assert f"Hi {main.DEMO_NAME}!" in client.get("/chat").text
    aino = main.engine_for(main.AINO)
    assert aino is not main.engine and aino.customer_id == main.AINO
    assert aino.safe_to_spend()["safe_to_spend_until_payday_eur"] == 68.60  # the engine's own default, unchanged
    assert "Hi Aino!" in client.get("/chat", params={"as": main.AINO}).text


def test_demo_persona_env_picks_the_default():
    assert main.pick_default(None) == main.pick_default("") == main.HERO
    assert main.pick_default(main.AINO) == main.AINO and main.pick_default(OTHERS[-1]) == OTHERS[-1]
    assert main.pick_default("CUST99999") == main.pick_default("../persona") == main.HERO
    # Aino keeps data/state.json; another default gets its own file, so no saved payment lands on the wrong account
    assert main.default_state_path(main.AINO) == main.ROOT / "data" / "state.json"
    assert main.default_state_path(main.HERO) == main.STATE_DIR / f"{main.HERO}.json"


def test_each_persona_has_its_own_numbers(client):
    base = client.get("/app").text
    seen = {safe(client)}
    for pid in OTHERS:
        e = main.engine_for(pid)
        assert e.customer_id == pid and e is not main.engine
        assert safe(client, **{"as": pid}) == e.safe_to_spend()["safe_to_spend_until_payday_eur"]
        page = client.get("/app", params={"as": pid}).text
        assert page != base and f"Hi {main.name_for(pid)}" in page and main.money_nb(e.balance()) in page
        seen.add(safe(client, **{"as": pid}))
    assert len(seen) == len(INDEX) == 6


@pytest.mark.parametrize("pid", [p["id"] for p in INDEX])
def test_every_page_renders_for_every_persona(client, pid):
    for path in ("/app", "/chat", "/app/explore", "/mobile-demo", "/app/explore.csv", "/app/export.json"):
        assert client.get(path, params={"as": pid}).status_code == 200, path


def test_as_param_sets_the_cookie_and_the_cookie_sticks(client, monkeypatch):
    monkeypatch.delenv("MIRROR_PHONE", raising=False)
    monkeypatch.delenv("DEMO_PHONE", raising=False)
    pid = OTHERS[0]
    r = client.get("/chat", params={"as": pid})
    assert client.cookies.get(main.PERSONA_COOKIE) == pid
    assert f"Hi {main.name_for(pid)}" in r.text
    assert f"Viewing as <b>{main.name_for(pid)}</b>" in client.get("/app/explore").text
    assert safe(client) == main.engine_for(pid).safe_to_spend()["safe_to_spend_until_payday_eur"]


@pytest.mark.parametrize("bad", ["CUST99999", "../persona", "", "cust04831"])
def test_unknown_persona_falls_back_to_the_default(client, bad):
    aino = safe(client)
    assert safe(client, **{"as": bad}) == aino
    client.cookies.set(main.PERSONA_COOKIE, bad)
    assert safe(client) == aino and f"Viewing as <b>{main.DEMO_NAME}</b>" in client.get("/app").text
    assert main.engine_for(bad) is main.engine


def test_a_payment_in_one_persona_leaves_the_others_alone(client):
    pid = OTHERS[0]
    other = main.engine_for(pid)
    aino_before, other_before = main.engine.balance(), other.balance()
    rent = next(b for b in other.upcoming_bills(45) if b["category"] == "Housing")
    j = client.post("/api/chat", json={"session": "s1", "text": f"__btn__:pay:{rent['bill_id']}", "as": pid}).json()
    url = j["buttons"][0]["url"]
    client.cookies.set(main.PERSONA_COOKIE, main.DEFAULT_PERSONA)  # the confirm page acts on the creator, not the cookie
    route, flow = tools.ready_of(url)
    r = client.post(f"/s-mobiili/{route}/{flow}/confirm", json={})
    assert r.status_code == 200 and r.json()["title"] == "Payment sent"
    assert other.balance() == round(other_before - rent["amount_eur"], 2)
    assert main.engine.balance() == aino_before
    assert other.state_path.parent == main.STATE_DIR and other.state_path.exists()
    assert not main.engine.state["actions"]
    assert safe(client) == main.engine.safe_to_spend()["safe_to_spend_until_payday_eur"]

    client.post("/demo/reset")
    assert other.balance() == other_before and not other.state["actions"]


def test_reminder_uses_the_persona_name_and_numbers(client):
    pid = OTHERS[-1]
    e = main.engine_for(pid)
    t = client.post("/api/chat", json={"session": "s2", "text": "__remind__", "as": pid}).json()["text"]
    assert t.startswith(f"Hi {main.name_for(pid)}, ")
    assert main.money(e.safe_to_spend()["safe_to_spend_until_payday_eur"]) in t


class EngineEcho:
    """Fake API: asks for safe to spend once, then repeats the tool's number."""
    def __init__(self):
        self.systems = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.systems.append(kw["system"])
        last = kw["messages"][-1]["content"]
        if isinstance(last, str):
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="get_safe_to_spend", input={})])
        out = json.loads(last[0]["content"])
        return NS(stop_reason="end_turn", content=[NS(type="text", text=f"{out['safe_to_spend_until_payday_eur']} €")])


def test_coach_runs_on_the_session_persona(client):
    fake = EngineEcho()
    main._coach = Coach(main.engine, client=fake)
    pid = OTHERS[1]
    ask = {"session": "s3", "text": "What is safe for me to spend this week?"}
    mine = client.post("/api/chat", json={**ask, "as": pid}).json()
    theirs = client.post("/api/chat", json=ask).json()
    assert mine["text"] == f"{main.engine_for(pid).safe_to_spend()['safe_to_spend_until_payday_eur']:.2f} €"
    assert theirs["text"] == f"{main.engine.safe_to_spend()['safe_to_spend_until_payday_eur']:.2f} €"
    assert f"first name is {main.name_for(pid)}." in fake.systems[0] and f"first name is {main.DEMO_NAME}." in fake.systems[-1]
    assert main.coach(pid).engine is main.engine_for(pid) and main.coach(pid).client is fake
    # one history per persona for the same web session
    assert len(main.coach(pid).histories["web:s3"]) == len(main._coach.histories["web:s3"]) == 4


def test_whatsapp_stays_on_the_default_persona(client):
    assert main.coach() is main.coach(None) and main.coach().engine is main.engine
    assert main.coach("CUST99999") is main.coach()


def test_extract_script_picks_by_rule():
    src = (main.ROOT / "scripts" / "extract_personas.py").read_text(encoding="utf-8")
    assert not set(re.findall(r"CUST\d{5}", src)) - {main.AINO}  # no hand-picked customer ids
    assert not set(re.findall(r"CUST\d{5}", (main.ROOT / "app" / "main.py").read_text(encoding="utf-8"))) - {main.AINO}  # hero by rule
