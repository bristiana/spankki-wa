"""S-mobiili's Pankki tab, one level down: every tile opens its own screen with the customer's real data, money moves
open the same review and Face ID as Oiva's buttons, and back always returns to where the customer came from."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import Engine

ANTTI = "CUST01656"
SCREENS = ["/s-mobiili/tilit", "/s-mobiili/tilit/s-tili", "/s-mobiili/maksut", "/s-mobiili/maksut/uusi",
           "/s-mobiili/maksut/e-laskut", "/s-mobiili/saastot", "/s-mobiili/sijoitukset", "/s-mobiili/kortti/tapahtumat",
           "/s-mobiili/kortti/rajat", "/s-mobiili/kortti/alue"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "DEMO_PHONE", "WA_DISPLAY_NUMBER", "T212_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert main.DEFAULT_PERSONA == ANTTI
    e = main.persona_engine(ANTTI, date(2026, 10, 6), tmp_path / f"{ANTTI}.json")
    monkeypatch.setattr(main, "engine", e)  # the default persona's engine: what engine_for(ANTTI) returns
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    c = TestClient(main.app)
    c.cookies.set("lang", "en")
    c.engine = e
    return c


def get(client, url, lang="en"):
    client.cookies.set("lang", lang)
    r = client.get(url)
    assert r.status_code == 200, url
    return r.text


def href(page, el_id):
    m = re.search(rf'id="{el_id}"[^>]*href="([^"]+)"', page) or re.search(rf'href="([^"]+)"[^>]*id="{el_id}"', page)
    assert m, el_id
    return m.group(1).replace("&amp;", "&")


def fi(x):
    return main.fi_money(x)


def test_every_pankki_tile_opens_its_own_screen(client):
    page = get(client, "/s-mobiili/?tab=pankki")
    assert href(page, "tile-accounts") == "/s-mobiili/tilit"
    assert href(page, "tile-payments") == "/s-mobiili/maksut"
    assert href(page, "tile-cards") == "/s-mobiili/?tab=kortti"
    assert href(page, "tile-savings") == "/s-mobiili/saastot"
    assert href(page, "tile-investments") == "/s-mobiili/sijoitukset"
    tiles = re.findall(r'<a class="card tile"[^>]*href="([^"]+)"', page)
    assert "/s-mobiili/" not in tiles  # no tile falls back to Home


@pytest.mark.parametrize("lang", ["en", "fi"])
@pytest.mark.parametrize("url", SCREENS)
def test_every_sub_screen_loads_with_a_back_link_and_no_dead_taps(client, url, lang):
    page = get(client, url, lang)
    back = re.search(r'<header class="top"><a href="([^"]+)" aria-label="(Back|Takaisin)"', page)
    assert back and back.group(1).startswith("/s-mobiili/")
    assert 'href="#"' not in page
    # anything drawn as tappable (a chevron row) is a link
    assert not re.search(r'<div class="li tap"', page)
    assert ("Back" if lang == "en" else "Takaisin") in page


def test_accounts_and_s_tili_show_the_engines_numbers(client):
    e = client.engine
    page = get(client, "/s-mobiili/tilit")
    assert fi(e.balance()) in page and fi(e.portfolio()["net_put_in_eur"]) in page
    assert href(page, "acct-s-tili") == "/s-mobiili/tilit/s-tili"
    page = get(client, "/s-mobiili/tilit/s-tili")
    assert fi(e.balance()) in page
    assert "FI00 3939 0000 0016 56" in page  # check digits 00: never a real account
    for x in e.recent(5):
        assert x["what"] in page and fi(x["amount_eur"]) in page


def test_payments_list_rent_and_regular_payments(client):
    e = client.engine
    rent = next(b for b in e.upcoming_bills(45) if b["category"] == "Housing")
    page = get(client, "/s-mobiili/maksut")
    assert fi(rent["amount_eur"]) in page and f'value="{rent["bill_id"]}"' in page
    assert "Monthly investing" in page and 'value="investment_deposit-' not in page  # a plan, not a bill
    page = get(client, "/s-mobiili/maksut", "fi")
    assert "Vuokra" in page and "Erääntyvät" in page and "Toistuvat maksut" in page


def test_paying_a_bill_runs_face_id_and_comes_back_to_payments(client):
    e = client.engine
    rent = next(b for b in e.upcoming_bills(45) if b["category"] == "Housing")
    r = client.post("/s-mobiili/maksut/maksa", data={"bill_id": rent["bill_id"], "from": "maksut"},
                    follow_redirects=False)
    assert r.status_code == 303
    pid = re.fullmatch(r"/s-mobiili/pay/(\w+)\?from=maksut", r.headers["location"]).group(1)
    page = get(client, r.headers["location"])
    assert '<a href="/s-mobiili/maksut" aria-label="Back"' in page
    assert 'id="cancel"' in page and 'id="done-back" href="/s-mobiili/maksut">Back to Payments' in page
    before = e.balance()
    j = client.post(f"/s-mobiili/pay/{pid}/confirm", json={}).json()
    assert j["ok"] and round(before - e.balance(), 2) == rent["amount_eur"]
    # paid early: not offered again (no second payment for the same due date)
    assert rent["bill_id"] not in [b["bill_id"] for b in e.upcoming_bills(45)]
    assert f'value="{rent["bill_id"]}"' not in get(client, "/s-mobiili/maksut")


def test_new_payment_checks_the_form_then_pays_once(client):
    e = client.engine
    r = client.post("/s-mobiili/maksut/uusi", data={"name": "Taloyhtiö", "iban": "FI00 1234", "amount": "12,50"})
    assert r.status_code == 200 and "Check the recipient" in r.text and 'value="Taloyhtiö"' in r.text
    client.cookies.set("lang", "fi")
    r = client.post("/s-mobiili/maksut/uusi", data={"name": "X", "iban": "FI2112345600000785", "amount": "999999"})
    assert "Kirjoita summa" in r.text
    client.cookies.set("lang", "en")
    r = client.post("/s-mobiili/maksut/uusi", data={"name": "Taloyhtiö Kuusikko", "iban": "fi21 1234 5600 0007 85",
                                                    "amount": "45,90", "reference": "13"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?from=uusi")
    pid = re.search(r"/pay/(\w+)", r.headers["location"]).group(1)
    p = e.pending(pid)
    assert (p["source"], p["iban"], p["amount"], p["reference"]) == ("manual", "FI2112345600000785", 45.9, "13")
    page = get(client, r.headers["location"])
    assert "Taloyhtiö Kuusikko" in page and "FI21 1234 5600 0007 85" in page
    before = e.balance()
    assert client.post(f"/s-mobiili/pay/{pid}/confirm", json={}).json()["ok"]
    assert round(before - e.balance(), 2) == 45.9
    assert client.post(f"/s-mobiili/pay/{pid}/confirm", json={}).status_code == 409


def test_savings_start_a_buffer_then_add_money(client):
    e = client.engine
    page = get(client, "/s-mobiili/saastot")
    assert 'id="start-buffer"' in page and "Not opened yet" in page
    r = client.post("/s-mobiili/saastot/aloita", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/s-mobiili/saastot"
    page = get(client, "/s-mobiili/saastot")
    assert 'id="goal-emergencybuffer"' in page and 'name="amount" value="50"' in page
    assert "Hätävara" in get(client, "/s-mobiili/saastot", "fi")
    r = client.post("/s-mobiili/saastot/lisaa", data={"goal_id": "emergencybuffer", "amount": "50"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?from=saastot")
    pid = re.search(r"/save/(\w+)", r.headers["location"]).group(1)
    assert 'Back to Savings' in get(client, r.headers["location"])
    assert client.post(f"/s-mobiili/save/{pid}/confirm", json={}).json()["ok"]
    assert e.state["goals"]["emergencybuffer"]["saved"] == 50
    assert "50,00" in get(client, "/s-mobiili/saastot")


def test_investments_show_deposits_and_pause_through_face_id(client):
    e = client.engine
    pf = e.portfolio()
    page = get(client, "/s-mobiili/sijoitukset")
    assert fi(pf["net_put_in_eur"]) in page
    for v in pf["by_year_eur"].values():
        assert fi(v) in page
    r = client.post("/s-mobiili/sijoitukset/tauko", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?from=sijoitukset")
    pid = re.search(r"/stop/(\w+)", r.headers["location"]).group(1)
    assert client.post(f"/s-mobiili/stop/{pid}/confirm", json={}).json()["ok"]
    page = get(client, "/s-mobiili/sijoitukset")
    assert 'id="pause-investing"' not in page and "Paused" in page


def test_card_page_rows_all_go_somewhere(client):
    page = get(client, "/s-mobiili/?tab=kortti")
    assert href(page, "card-transactions") == "/s-mobiili/kortti/tapahtumat"
    assert href(page, "card-limits") == "/s-mobiili/kortti/rajat"
    assert href(page, "card-region") == "/s-mobiili/kortti/alue"
    assert 'id="card-lock"' in page and 'id="gpay"' in page
    assert not re.search(r'<(div|span) class="li tap"', page)


def test_card_transactions_link_groceries_to_their_receipt_and_back(client):
    page = get(client, "/s-mobiili/kortti/tapahtumat")
    m = re.search(r'href="(/s-mobiili/kuitit/\d+\?from=kortti)"', page)
    assert m
    receipt = get(client, m.group(1))
    assert '<a href="/s-mobiili/kortti/tapahtumat" aria-label="Back"' in receipt


def test_persona_and_web_are_kept_on_every_link(client):
    page = get(client, "/s-mobiili/maksut?persona=CUST01656")
    assert "/s-mobiili/maksut/uusi?persona=CUST01656" in page
    assert 'action="/s-mobiili/maksut/maksa?persona=CUST01656"' in page
    page = get(client, "/s-mobiili/?tab=pankki&persona=CUST01656")
    assert href(page, "tile-savings") == "/s-mobiili/saastot?persona=CUST01656"


def test_the_bottom_bar_plus_opens_a_new_payment(client):
    page = get(client, "/s-mobiili/?tab=pankki")
    assert re.search(r'<a href="/s-mobiili/maksut/uusi" class="pay">', page)


def test_a_double_tap_on_new_payment_opens_the_same_review(client):
    form = {"name": "Taloyhtiö Kuusikko", "iban": "FI2112345600000785", "amount": "45,90", "reference": "13"}
    a = client.post("/s-mobiili/maksut/uusi", data=form, follow_redirects=False).headers["location"]
    b = client.post("/s-mobiili/maksut/uusi", data=form, follow_redirects=False).headers["location"]
    assert a == b
    pid = re.search(r"/pay/(\w+)", a).group(1)
    assert "New payment" in get(client, a)
    assert client.post(f"/s-mobiili/pay/{pid}/confirm", json={}).json()["ok"]
    r = client.post("/s-mobiili/maksut/uusi", data=form, follow_redirects=False)
    assert r.status_code == 200 and "already paid this amount" in r.text and 'name="again" value="1"' in r.text
    r = client.post("/s-mobiili/maksut/uusi", data={**form, "again": "1"}, follow_redirects=False)
    assert r.status_code == 303 and re.search(r"/pay/(\w+)", r.headers["location"]).group(1) != pid
    page = get(client, "/s-mobiili/tilit/s-tili")
    assert "Taloyhtiö Kuusikko" in page  # the S-Tili shows who was paid, not "Other"


def test_money_move_posts_only_from_this_site(client):
    for url in ("/s-mobiili/maksut/uusi", "/s-mobiili/maksut/maksa", "/s-mobiili/saastot/aloita",
                "/s-mobiili/saastot/lisaa", "/s-mobiili/sijoitukset/tauko"):
        r = client.post(url, data={}, headers={"origin": "https://evil.example"}, follow_redirects=False)
        assert r.status_code == 403, url
    r = client.post("/s-mobiili/saastot/aloita", headers={"origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303


def test_paused_investing_reads_paused_on_payments_and_paid_rent_reads_paid(client):
    e = client.engine
    pid = e.create_pause_investing()["pending_id"]
    e.confirm(pid)
    rent = next(b for b in e.upcoming_bills(45) if b["category"] == "Housing")
    e.confirm(e.create_payment(rent["bill_id"])["pending_id"])
    page = get(client, "/s-mobiili/maksut")
    assert re.search(r'id="reg-investing".*?Paused', page, re.S) and "Stopped" not in page
    assert re.search(r'id="reg-rent".*?Paid \(due 3 Nov\)', page, re.S)


def test_every_pankki_screen_has_the_chat_icon_and_the_new_basket_name(client):
    for url in SCREENS:  # like the Card screen: the chat bubble top right opens the Helper as the same customer
        assert href(get(client, url + "?persona=" + ANTTI), "ask").startswith(f"/chat?as={ANTTI}"), url
    en, fi = get(client, "/s-mobiili/saastot"), get(client, "/s-mobiili/saastot", "fi")
    assert "occasional costs" in en and "satunnaiset menot" in fi
    assert "irregular" not in en and "epäsäännöll" not in fi


def test_e_invoices_never_break_after_the_hyphen(client):
    for lang in ("en", "fi"):
        page = get(client, "/s-mobiili/?tab=pankki", lang)
        tile = re.search(r'id="tile-payments".*?</a>', page, re.S).group(0)
        assert re.search(r"e-(laskut|laskuja|invoices)", tile) is None and "e-⁠" in tile
