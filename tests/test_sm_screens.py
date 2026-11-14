"""The S-mobiili mock's Oiva screens: Spend, Budget, People like me, Bills, and the Talk to Oiva button."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import Engine
from app.smobiili_screens import SCREENS

LABEL = "Concept prototype · not the real S-mobiili"


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("WA_TOKEN", "DEMO_PHONE", "WA_DISPLAY_NUMBER", "T212_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    return TestClient(main.app)


def test_screen_routes_render(client):
    assert SCREENS == ("spend", "budget", "compare", "bills")
    for name in SCREENS:
        r = client.get(f"/s-mobiili/{name}")
        assert r.status_code == 200, name
        html = r.text
        assert f'data-screen="{name}"' in html and LABEL in html
        assert '/static/oiva/data.js' in html and '/static/oiva/screens.js' in html


def test_unknown_screen_is_404(client):
    assert client.get("/s-mobiili/nope").status_code == 404


def test_home_links_to_screens_and_talk_button(client):
    html = client.get("/s-mobiili/").text
    for name in SCREENS:
        assert f'id="tile-{name}"' in html and f'href="/s-mobiili/{name}' in html
    assert 'id="talk-oiva"' in html


def test_persona_carries_into_screen_links(client):
    html = client.get("/s-mobiili/?persona=CUST04831").text
    assert 'href="/s-mobiili/spend?persona=CUST04831"' in html
    back = client.get("/s-mobiili/spend?persona=CUST04831").text
    assert 'href="/s-mobiili/?persona=CUST04831"' in back


def test_screens_have_no_face_id_overlay(client):
    assert 'id="fid"' not in client.get("/s-mobiili/budget").text


def test_call_view_and_voice_scripts_on_home_and_screens(client):
    for url in ("/s-mobiili/", "/s-mobiili/compare"):
        html = client.get(url).text
        assert 'id="oiva-call"' in html and 'id="call-end"' in html and 'id="call-mute"' in html
        for js in ("oiva.js", "voice-stub.js", "call.js"):
            assert f"/static/oiva/{js}" in html, (url, js)


def test_voice_status_says_whether_voice_js_exists(client):
    from pathlib import Path
    exists = (Path(__file__).resolve().parent.parent / "app/static/oiva/voice.js").exists()
    assert client.get("/s-mobiili/voice-status").json() == {"voice_js": exists}
