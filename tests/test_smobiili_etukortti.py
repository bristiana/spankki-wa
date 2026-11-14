"""S-mobiili mock: the digital S-Etukortti screen and Kassakuitti, the digital receipt of a real grocery purchase."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, receipts
from app.engine import Engine

# Pictographs and dingbats: the mock's UI never uses emoji.
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "DEMO_PHONE", "WA_DISPLAY_NUMBER", "T212_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    return TestClient(main.app)


def cents(page: str) -> list[int]:
    return [int(c) for c in re.findall(r'data-cents="(\d+)"', page)]


def test_etukortti_tab_opens_the_card_and_the_receipts(client):
    page = client.get("/s-mobiili/?tab=etukortti&persona=CUST01756").text
    assert 'href="/s-mobiili/etukortti?persona=CUST01756"' in page
    assert 'href="/s-mobiili/kuitit?persona=CUST01756"' in page


def test_digital_card_screen(client):
    r = client.get("/s-mobiili/etukortti")
    assert r.status_code == 200
    page = r.text
    for text in ("S-Etukortti", "Käytössä", "Näin käytät", "Avaa S-mobiili kassalla - kirjautumista ei tarvita!",
                 "Vie puhelin rauhallisesti maksupäätteelle", "Siirry maksusovellukseen linkistä",
                 "Tarkemmat ohjeet S-Etukortti.fi", "Asetukset"):
        assert text in page
    assert re.search(r'<a [^>]*aria-current="page"[^>]*>.*?S-Etukortti</a>', page, re.S)  # bottom bar: S-Etukortti
    assert "https://www.s-etukortti" not in page.lower() and not EMOJI.search(page)


def test_receipt_list_is_the_personas_grocery_purchases(client):
    page = client.get("/s-mobiili/kuitit").text
    rows = receipts.grocery_rows(main.engine)
    assert len(rows) and set(rows["category"]) == {"Groceries"}
    first = receipts.receipt_list(main.engine)[0]
    assert f'href="/s-mobiili/kuitit/{first["id"]}"' in page and main.fi_money(first["total_eur"]) in page
    assert not EMOJI.search(page)


def test_receipt_screen_sums_to_the_real_purchase(client):
    first = receipts.receipt_list(main.engine)[0]
    r = client.get(f"/s-mobiili/kuitit/{first['id']}")
    assert r.status_code == 200
    page = r.text
    for text in ("Kassakuitti", "Yhteensä", "Avaa kuitin viivakoodi", "Jaa tai lataa PDF-kuitti", "Turun Osuuskauppa",
                 "Demo receipt", first["store"]):
        assert text in page
    assert main.fi_money(first["total_eur"]) in page
    d = first["date"]
    assert f"{d.day}.{d.month}.{d.year}" in page
    assert sum(cents(page)) == round(first["total_eur"] * 100)
    assert not EMOJI.search(page)


def test_every_receipt_adds_up_and_is_stable():
    for persona in main.PERSONAS:
        e = main.engine_for(persona) if persona != main.DEFAULT_PERSONA else main.engine
        for k in receipts.receipt_list(e, n=60):
            r = receipts.build(e, k["id"], persona)
            assert sum(x["cents"] for x in r["lines"]) == round(k["total_eur"] * 100)
            assert r["lines"] == receipts.build(e, k["id"], persona)["lines"]  # deterministic per purchase
            for x in r["lines"]:
                if x["sub"] and "EUR/KG" in x["sub"]:
                    kg, per = float(x["sub"].split()[0]), float(x["sub"].split()[2])
                    assert round(kg * per * 100) == x["cents"]


def test_receipt_of_a_non_grocery_row_is_404(client):
    rows = main.engine.base
    other = int(rows[rows["category"] != "Groceries"].index[0])
    assert client.get(f"/s-mobiili/kuitit/{other}").status_code == 404
    assert client.get("/s-mobiili/kuitit/999999").status_code == 404
