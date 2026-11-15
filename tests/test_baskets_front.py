"""The three baskets up front: S-mobiili home, the chat chip and the WhatsApp welcome."""
import json
import re
from datetime import date
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import main, onboarding
from app.engine import CATEGORY_WORDS, Engine

NAMES = {"regular": "Bills & basics", "irregular": "Occasional costs", "everyday": "Fun & extras"}
NAMES_FI = {"regular": "Laskut ja perusmenot", "irregular": "Satunnaiset menot", "everyday": "Valinnaiset menot"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "DEMO_PHONE", "WA_DISPLAY_NUMBER", "T212_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PUBLIC_URL", "http://127.0.0.1:8806")
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def baskets():
    return main.engine.baskets("last_3_months")["baskets"]


def test_engine_baskets_carry_plain_names_and_their_own_examples(client):
    assert {b["basket"]: b["label"] for b in baskets()} == NAMES
    for b in baskets():  # the example is the person's own top one or two categories in that basket, in words
        assert b["top_categories"] == list(b["by_category"])[:2]
        assert b["example"] == " and ".join(CATEGORY_WORDS[c] for c in b["top_categories"])


def test_smobiili_home_shows_the_baskets_card_with_engine_numbers(client):
    page = client.get("/s-mobiili/?lang=en").text
    card = page[page.index('id="baskets"'):page.index('id="acct-pocket"')]
    assert "Where your money goes" in card and "Average month · last 3 months" in card
    assert card.count('class="k1"') + card.count('class="k2"') + card.count('class="k3"') >= 6  # bar + dots
    for b in baskets():
        row = card[card.index(f'id="basket-{b["basket"]}"'):]
        row = row[:re.search(r'class="(bk-row|ask-o)', row).start()]  # up to the next row
        assert b["label"].replace("&", "&amp;") in row and f"Mostly {b['example']}" in unescape(row)
        assert main.fi_money(b["per_month_eur"]) in row and f"{b['share_pct']}%" in row
        assert ("Easiest place to save" in row) == (b["basket"] == "everyday")
    assert page.index('id="acct-current"') < page.index('id="baskets"') < page.index('id="recent"')
    assert "Concept prototype" not in page


def test_baskets_card_opens_the_chat_with_the_question(client):
    page = client.get("/s-mobiili/?lang=en&persona=" + main.DEFAULT_PERSONA).text
    href = unescape(re.search(r'id="baskets" href="([^"]+)"', page).group(1))
    u = urlsplit(href)
    assert u.path == "/chat" and parse_qs(u.query) == {"q": ["Where does my money go?"], "as": [main.DEFAULT_PERSONA]}
    assert not re.search(r'id="baskets" href=', client.get("/s-mobiili/?web=1").text)  # no chat in the chat's sheet
    assert urlsplit(re.search(r'id="baskets" href="([^"]+)"', client.get("/s-mobiili/?lang=en").text).group(1)).query \
        == "q=Where+does+my+money+go%3F"
    fi = re.search(r'id="baskets" href="([^"]+)"', client.get("/s-mobiili/?lang=fi").text).group(1)
    assert parse_qs(urlsplit(unescape(fi)).query) == {"q": ["Mihin rahani menevät?"]}  # asked in the page's language


def test_guide_points_at_the_baskets_card(client):
    page = client.get("/s-mobiili/?lang=en").text
    steps = json.loads(re.search(r'id="guide-steps">(.*?)</script>', page, re.S).group(1))
    step = next(s for s in steps if s["target"] == "baskets")
    assert "3 baskets" in step["say"]


def test_chat_has_no_suggestion_chips(client):
    page = client.get("/chat").text  # a plain conversation: no chips of ready-made questions
    assert 'id="top"' not in page and 'id="chips"' not in page and "Play the basket game" not in page


def test_chat_deep_link_asks_q_once_on_load(client):
    page = client.get("/chat", params={"q": "Where does my money go?"}).text
    assert 'const deepQ = "Where does my money go?";' in page
    assert "const deepQ = \"\";" in client.get("/chat").text


def test_whatsapp_welcome_lists_the_three_baskets_per_month(client, monkeypatch):
    sent = []
    monkeypatch.setattr(main.wa, "_post", sent.append)
    main.welcome("358400000001")
    first = next(b["text"]["body"] for b in sent if b.get("type") == "text")
    lines = [ln for ln in first.splitlines() if ln.startswith("• ")]
    assert len(lines) == 3
    for ln, b in zip(lines, baskets()):
        assert ln.startswith(f"• {b['label']} (e.g. {b['example']}): ") and ln.endswith(" a month")
        assert onboarding._money(b["per_month_eur"]) in ln


def test_smobiili_home_card_in_finnish_uses_the_finnish_names(client):
    page = client.get("/s-mobiili/?lang=fi").text
    card = unescape(page[page.index('id="baskets"'):page.index('id="acct-pocket"')])
    assert "Mihin rahasi menevät" in card and "Kuukausikeskiarvo" in card and "Tästä on helpoin säästää" in card
    assert all(name in card for name in NAMES_FI.values())
    assert all(f"Suurimmat: {b['example_fi']}" in card for b in baskets())
    assert "Pakolliset" not in card and "Lipsuu" not in card and "esim." not in card
