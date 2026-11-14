"""Mobile UX pass: friendly error pages, amounts that never break, confirm pages that fit the chat's sheet."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import Engine

HTML = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
NBSP = " "


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "T212_API_KEY", "WA_DISPLAY_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def rent_link(client) -> str:
    return main.engine.create_payment(main.engine.upcoming_bills()[0]["bill_id"])["pending_id"]


@pytest.mark.parametrize("kind", ["pay", "invest", "save", "stop"])
def test_unknown_confirm_link_is_a_friendly_page(client, kind):
    r = client.get(f"/{kind}/nope", headers=HTML)
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert "This link has expired" in r.text and "Nothing was paid or moved" in r.text
    assert 'href="/chat"' in r.text and '"detail"' not in r.text


def test_posting_a_pin_to_an_unknown_link_is_a_friendly_page(client):
    r = client.post("/pay/nope", data={"pin": "1234"}, headers=HTML)
    assert r.status_code == 404 and "This link has expired" in r.text


def test_api_callers_still_get_json(client):
    r = client.get("/pay/nope")  # no text/html in Accept: a script, not a browser
    assert r.status_code == 404 and r.json() == {"detail": "This link has expired."}
    assert client.post("/api/chat", json={"text": ""}, headers=HTML).headers["content-type"].startswith("application/json")


def test_unknown_page_is_friendly(client):
    r = client.get("/no-such-page", headers=HTML)
    assert r.status_code == 404 and "Page not found" in r.text and "{" not in r.text.split("<main>")[1].split("</main>")[0]


def test_server_error_is_a_page_not_a_trace(tmp_path, monkeypatch):
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")

    def boom():
        raise RuntimeError("secret internals")
    monkeypatch.setattr(main.engine, "overview", boom)
    r = TestClient(main.app, raise_server_exceptions=False).get("/app", headers=HTML)
    assert r.status_code == 500 and "Something went wrong on our side" in r.text
    assert "secret internals" not in r.text and "Traceback" not in r.text


def test_bad_pin_message_is_friendly(client):
    pid = rent_link(client)
    page = client.get(f"/pay/{pid}?err=1&web=1").text
    assert "The PIN is 4 digits" in page and 'aria-invalid="true"' in page


def test_confirm_page_in_chat_sheet_has_no_site_chrome_and_can_cancel(client):
    pid = rent_link(client)
    sheet = client.get(f"/pay/{pid}?web=1").text
    assert 'class="bar"' not in sheet and "<footer>" not in sheet and "<summary>Viewing as" not in sheet
    assert "Cancel" in sheet and "data-back" in sheet
    page = client.get(f"/pay/{pid}").text  # the same link opened on its own keeps the site's header
    assert 'class="bar"' in page


def test_done_page_always_has_a_way_back(client):
    pid = rent_link(client)
    r = client.post(f"/pay/{pid}?web=1", data={"pin": "1234"})
    assert "Payment sent" in r.text and "Back to the chat" in r.text
    again = client.get(f"/pay/{pid}?web=1").text
    assert "Already confirmed" in again and "Back to the chat" in again


def test_amounts_do_not_break_across_lines(client):
    assert main.money_nb(1313.89) == f"1{NBSP}313.89{NBSP}€"
    assert main.nb("Balance 82 799.61 € on Thu 15 Oct") == f"Balance 82{NBSP}799.61{NBSP}€ on Thu 15 Oct"
    pid = rent_link(client)
    page = client.get(f"/pay/{pid}").text
    assert f"771.82{NBSP}€" in page and "771.82 €" not in page
    assert f"{NBSP}€" in client.get("/app").text and "771.82 €" not in client.get("/app").text


def test_stop_page_survives_a_bank_refusal(client):
    p = main.engine.create_pause_investing()
    q = main.engine.create_pause_investing()  # two pause links, e.g. tapped twice
    client.post(f"/stop/{p['pending_id']}", data={"pin": "1234"})
    r = client.post(f"/stop/{q['pending_id']}", data={"pin": "1234"})
    assert r.status_code == 200 and "Not done" in r.text and "already paused" in r.text


def test_pages_have_a_favicon_and_mark_the_current_tab(client):
    page = client.get("/app").text
    assert 'rel="icon"' in page and '<a href="/app" aria-current="page">My money</a>' in page


@pytest.mark.parametrize("raw, shown", [
    ("loans of about €379,899.51, for homes", "loans of about 379 899.51 €, for homes"),
    ("a median of €15,000 (72 applications)", "a median of 15 000 € (72 applications)"),
    ("1,234.50€ in total", "1 234.50 € in total"),
    ("You have 68.60 € safe to spend.", "You have 68.60 € safe to spend."),
    ("items 1, 2, 3", "items 1, 2, 3"),
])
def test_ai_replies_use_the_app_currency_style(raw, shown):
    from app.agent import euro_style
    assert euro_style(raw) == shown


def test_chat_page_has_typing_state_and_keeps_amounts_together(client):
    page = client.get("/chat").text
    assert "Birky is thinking" in page and 'role", "status"' in page
    assert "function nb(" in page and "visualViewport" in page and "function reveal(" in page
