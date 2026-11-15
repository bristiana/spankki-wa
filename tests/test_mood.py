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


@pytest.mark.parametrize("tools,want", [(["get_spending"], "spent"), (["get_baskets"], "spent"), (["get_upcoming_bills"], "checking"),
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


class _Saver:
    def __init__(self, monthly): self.monthly = monthly
    def safe_to_save(self): return {"monthly": {"sustainable_saving_per_month_eur": self.monthly}}


def test_nothing_to_save_is_a_caution_not_a_happy_saving_pose():
    assert mood.for_reply(["get_safe_to_save"], (), _Saver(0.0)) == "caution"
    assert mood.for_reply(["get_safe_to_save"], (), _Saver(120.0)) == "saving"
    assert mood.for_reply(["get_safe_to_save"]) == "saving"


def test_the_helper_mascot_url_changes_with_the_file(client):
    """The chat links the Helper's oiva.js with a content hash, so a browser never keeps an older squirrel."""
    import hashlib
    import re
    sha = hashlib.sha1(open("app/static/oiva/mascot/oiva.js", "rb").read()).hexdigest()[:10]
    html = client.get("/chat").text
    assert f"/static/oiva/mascot/oiva.js?v={sha}" in html
    assert re.search(r'/static/oiva/helper\.js\?v=[0-9a-f]{10}"', html)


_HELPER_STUB = """
global.window = global;
global.document = {documentElement: {lang: %s}, getElementById: () => null, addEventListener() {}, querySelector: () => null};
global.localStorage = {getItem: () => null, setItem() {}};
global.requestAnimationFrame = () => 0;
require(%s);
const t = window.oivaHelper._test, input = %s;
process.stdout.write(JSON.stringify({bubble: input.bubble.map(t.bubble), pairs: input.pairs.map(p => t.pairFor(...p))}));
"""


def _helper(lang, bubble=(), pairs=()):
    import json, os, shutil, subprocess
    if not shutil.which("node"):
        pytest.skip("needs node to run the Helper's script")
    js = _HELPER_STUB % (json.dumps(lang), json.dumps(os.path.abspath("app/static/oiva/helper.js")),
                         json.dumps({"bubble": list(bubble), "pairs": list(pairs)}))
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", check=True).stdout)


def test_the_finnish_bubble_writes_money_with_a_decimal_comma():
    said = ["Syyskuussa meni *776.48 €*, kun elokuussa 447.80 €.", "Tililläsi on 3 345.06 € ja 12.5 % ennen 31.10.2026 klo 14.30."]
    fi = _helper("fi", said)["bubble"]
    assert fi[0] == "Syyskuussa meni *776,48 €*, kun elokuussa 447,80 €."
    assert fi[1] == "Tililläsi on 3 345,06 € ja 12,5 % ennen 31.10.2026 klo 14.30."  # dates and times keep their points
    en = _helper("en", said)["bubble"]
    assert en[0] == "Syyskuussa meni *776.48 €*, kun elokuussa 447.80 €." and "3 345.06 €" in en[1]


def _spent(now, before):
    return ["spent", [{"figures": [{"label": "Total", "value": now}, {"label": "Period before", "value": before}]}], [now, before]]


@pytest.mark.parametrize("lang", ["fi", "en"])
def test_spending_more_than_before_is_not_drawn_in_good_green(lang):
    higher, lower, same = _helper(lang, pairs=[_spent("776.48 €", "447.80 €"), _spent("447.80 €", "776.48 €"),
                                               _spent("447.80 €", "447.80 €")])["pairs"]
    assert higher["over"] is True and (higher["a"], higher["b"]) == (776.48, 447.8)
    assert lower["over"] is False and same["over"] is False
    css = open("app/static/oiva/helper.css").read()
    assert ".bars .over b{background:#8a5a00}" in css  # base.html's warning colour, as the compare bars use
