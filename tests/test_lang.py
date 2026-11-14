"""S-mobiili and the web chat in Finnish (default) or English: ?lang= switches and a cookie remembers it. And no page
keeps telling people the data is made up."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, receipts, tools
from app.engine import Engine

DISCLAIMERS = ("made up", "made-up", "synthetic", "concept", "demo figures", "demo receipt", "no real money",
               "not the real", "sample customer")


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "DEMO_PHONE", "WA_DISPLAY_NUMBER", "T212_API_KEY", "MIRROR_PHONE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PUBLIC_URL", "http://127.0.0.1:8806")
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    for d in (main.owner, main.inbox):
        d.clear()
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def ready_url(client) -> str:
    """Pay rent from the web chat: the "Ready for you" home it opens."""
    rent = next(b for b in main.engine.upcoming_bills(45) if b["category"] == "Housing")
    return client.post("/api/chat", json={"session": "t", "text": f"__btn__:pay:{rent['bill_id']}"}).json()["buttons"][0]["url"]


def pages(client) -> list[str]:
    first = receipts.receipt_list(main.engine)[0]
    url = ready_url(client)
    route, pid = tools.ready_of(url)
    return ["/s-mobiili/", url, "/s-mobiili/?tab=etukortti", "/s-mobiili/?tab=pankki", "/s-mobiili/?tab=mina",
            "/s-mobiili/?tab=kortti", "/s-mobiili/etukortti", "/s-mobiili/kuitit", f"/s-mobiili/kuitit/{first['id']}",
            f"/s-mobiili/{route}/{pid}", "/chat", "/login", "/connect"]


@pytest.mark.parametrize("lang", ["fi", "en"])
def test_no_page_says_the_data_is_made_up(client, lang):
    client.cookies.set("lang", lang)
    for path in pages(client):
        page = client.get(path).text.lower()
        for word in DISCLAIMERS:
            assert word not in page, (path, word)


def test_finnish_by_default_and_lang_sets_a_cookie_that_sticks(client):
    fi = client.get("/s-mobiili/")
    assert fi.status_code == 200 and '<html lang="fi">' in fi.text and "Tilit ja kortit" in fi.text
    assert "lang" not in client.cookies
    en = client.get("/s-mobiili/?lang=en")
    assert client.cookies.get("lang") == "en" and '<html lang="en">' in en.text
    later = client.get("/s-mobiili/").text  # no ?lang= any more: the cookie keeps English
    assert "Accounts and cards" in later and "Tilit ja kortit" not in later
    assert "Tilit ja kortit" in client.get("/s-mobiili/?lang=fi").text and client.cookies.get("lang") == "fi"
    assert "Tilit ja kortit" in client.get("/s-mobiili/?lang=xx").text  # unknown: the cookie's choice


def test_finnish_has_no_english_glosses(client):
    url = ready_url(client)
    home = client.get(url).text
    for en in ("Accounts and cards", "Current account", "Savings and", "Ready for you, from Oiva", "Check and confirm",
               "Where your money goes", "Your money coach", ">Recent<"):
        assert en not in home, en
    for fi in ("Tilit ja kortit", "Käyttötili", "Tarkista ja vahvista", "Mihin rahasi menevät", "Tapahtumat"):
        assert fi in home, fi
    flow = client.get("/s-mobiili/{}/{}".format(*tools.ready_of(url))).text
    assert "Peruuta" in flow and "Cancel" not in flow and "· back" not in flow and "Vahvista Face ID:llä" in flow
    assert "Maksa lasku" in flow and "Recipient" not in flow and "Saaja" in flow


def test_english_has_english_labels_and_not_the_finnish_ones(client):
    client.cookies.set("lang", "en")
    url = ready_url(client)
    home = client.get(url).text
    for en in ("Accounts and cards", "Current account", "Ready for you, from Oiva", "Check and confirm",
               "Savings and investments", "Transactions"):
        assert en in home, en
    for fi in ("Tilit ja kortit", "Käyttötili", "Valmiina sinulle", "Tarkista ja vahvista", "Koti</a>", "Maksu</a>",
               "Pankki</a>", "Minä</a>"):
        assert fi not in home, fi
    assert all(f"{x}</a>" in home for x in ("Home", "Pay", "Bank", "Me")) and "S-Etukortti</a>" in home
    flow = client.get("/s-mobiili/{}/{}".format(*tools.ready_of(url))).text
    assert "Pay a bill" in flow and "Confirm with Face ID" in flow and "Cancel" in flow and "Peruuta" not in flow
    receipt = client.get(f"/s-mobiili/kuitit/{receipts.receipt_list(main.engine)[0]['id']}").text
    assert "Total" in receipt and "Open the receipt barcode" in receipt and "Avaa kuitin viivakoodi" not in receipt
    assert "KIITOS KÄYNNISTÄ" in receipt  # the printed receipt stays as printed
    card = client.get("/s-mobiili/etukortti").text
    assert "How to use it" in card and "Näin käytät" not in card


def test_the_switch_is_on_every_smobiili_screen_and_keeps_the_query(client):
    for path in ["/s-mobiili/", "/s-mobiili/?tab=etukortti", "/s-mobiili/?tab=pankki", "/s-mobiili/kuitit", "/chat"]:
        page = client.get(path).text
        assert 'class="lang' in page and ">FI</a>" in page and ">EN</a>" in page, path
        assert re.search(r'href="[^"]*lang=en"[^>]*>EN</a>', page), path
    page = client.get("/s-mobiili/?tab=mina&persona=CUST04831").text
    assert 'href="/s-mobiili/?tab=mina&amp;persona=CUST04831&amp;lang=en"' in page
    fi = client.get("/s-mobiili/").text
    assert re.search(r'lang="fi" aria-current="true">FI</a>', fi)


def test_chat_chrome_follows_the_language(client):
    fi = client.get("/chat").text
    assert "Kysy rahoistasi" in fi and ">Lähetä<" in fi and "Viestit · rahavalmentajasi" in fi
    en = client.get("/chat?lang=en").text
    assert "Ask about your money" in en and ">Send<" in en and "Messages · money coach" in en
    assert "Kysy rahoistasi" not in en and client.cookies.get("lang") == "en"


def test_face_id_receipt_in_finnish(client):
    url = ready_url(client)
    route, pid = tools.ready_of(url)
    j = client.post(f"/s-mobiili/{route}/{pid}/confirm", json={}).json()
    assert j["ok"] and j["title"] == "Maksu lähetetty" and j["lines"][1].startswith("Saldo nyt")
