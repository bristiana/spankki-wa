"""The Helper's mic (app/stt.py, /api/listen). ElevenLabs is mocked: no test reaches the network."""
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, stt

KEY = "sk_test_DO_NOT_LEAK_1234567890"
CLIP = b"\x1aE\xdf\xa3" + b"\x00" * 4000  # looks like a webm, big enough to be speech


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    stt._spent.update(day="", calls=0, bytes=0)
    stt._calls.clear(); stt._visitors.clear(); stt._missing.clear()
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_STT_MODEL_ID", "ELEVENLABS_STT_DAILY_CALLS"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def eleven(monkeypatch):
    state = {"calls": [], "answer": lambda req: httpx.Response(200, json={"text": "How much is left until payday?",
                                                                          "language_code": "eng"})}

    def handler(req):
        state["calls"].append(req)
        return state["answer"](req)

    real = httpx.AsyncClient
    monkeypatch.setattr(stt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def post(client, body=CLIP, ctype="audio/webm;codecs=opus", lang="en", **headers):
    return client.post(f"/api/listen?lang={lang}", content=body, headers={"content-type": ctype, **headers})


def form(req) -> str:
    return req.content.decode("latin-1")


def test_transcript_from_elevenlabs(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    r = post(client)
    assert r.status_code == 200 and r.json() == {"text": "How much is left until payday?"}
    [req] = eleven["calls"]
    assert str(req.url) == "https://api.elevenlabs.io/v1/speech-to-text" and req.headers["xi-api-key"] == KEY
    body = form(req)
    assert 'name="model_id"\r\n\r\nscribe_v3' in body and 'name="language_code"\r\n\r\nen' in body
    assert 'filename="clip.webm"' in body and "Content-Type: audio/webm" in body


def test_finnish_and_safari_mp4(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    eleven["answer"] = lambda req: httpx.Response(200, json={"text": "Paljonko minulla on rahaa (naurua)"})
    r = post(client, ctype="audio/mp4", lang="fi")
    assert r.json() == {"text": "Paljonko minulla on rahaa"}  # sound tags dropped
    body = form(eleven["calls"][0])
    assert 'name="language_code"\r\n\r\nfi' in body and 'filename="clip.mp4"' in body


def test_no_key_is_204_and_no_call(client, eleven):
    assert post(client).status_code == 204 and not eleven["calls"]


def test_key_never_in_responses_or_logs(client, eleven, monkeypatch, caplog):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    caplog.set_level(logging.DEBUG)
    eleven["answer"] = lambda req: httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})
    r = post(client)
    assert r.status_code == 502 and KEY not in r.text and KEY not in caplog.text
    page = client.get("/chat?lang=en").text
    assert KEY not in page and 'id="mic"' in page and 'data-server="1"' in page and "listen.js" in page


def test_page_without_key_says_so(client):
    page = client.get("/chat").text
    assert 'data-server="0"' in page  # the page uses the browser's own recognition, or hides the mic


@pytest.mark.parametrize("ctype", ["text/plain", "application/json", "", "image/png"])
def test_only_audio(client, eleven, monkeypatch, ctype):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert post(client, ctype=ctype).status_code == 415 and not eleven["calls"]


def test_too_big_and_too_small(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    r = post(client, body=b"\x00" * (stt.MAX_BYTES + 1))
    assert r.status_code == 413 and r.json()["error"] == "too long"
    assert post(client, body=b"\x00" * 10).status_code == 400
    assert not eleven["calls"]


def test_other_sites_cannot_use_it(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert post(client, origin="https://evil.example").status_code == 403
    assert post(client, **{"sec-fetch-site": "cross-site"}).status_code == 403
    assert post(client, origin="http://testserver").status_code == 200
    assert len(eleven["calls"]) == 1


def test_per_visitor_minute_cap(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    codes = [post(client, **{"cf-connecting-ip": "1.2.3.4"}).status_code for _ in range(stt.PER_VISITOR_MINUTE + 2)]
    assert codes[: stt.PER_VISITOR_MINUTE] == [200] * stt.PER_VISITOR_MINUTE and codes[-1] == 429
    assert post(client, **{"cf-connecting-ip": "5.6.7.8"}).status_code == 200  # someone else still can
    assert len(eleven["calls"]) == stt.PER_VISITOR_MINUTE + 1


def test_daily_cap(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_STT_DAILY_CALLS", "2")
    codes = [post(client, **{"cf-connecting-ip": f"9.9.9.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_global_minute_cap(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    codes = [post(client, **{"cf-connecting-ip": f"8.8.{i}.1"}).status_code for i in range(stt.PER_MINUTE + 1)]
    assert codes[-1] == 503 and codes.count(200) == stt.PER_MINUTE


def test_nothing_heard(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    eleven["answer"] = lambda req: httpx.Response(200, json={"text": " (silence) "})
    assert post(client).status_code == 422


def test_elevenlabs_down_refunds(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    eleven["answer"] = lambda req: httpx.Response(500, json={})
    assert post(client).status_code == 502 and stt._spent["calls"] == 0


def test_model_refused_falls_back_to_v2(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    eleven["answer"] = lambda req: (httpx.Response(400, json={"detail": {"status": "invalid_model_id"}})
                                    if "scribe_v3" in form(req) else httpx.Response(200, json={"text": "Hei"}))
    assert post(client).json() == {"text": "Hei"}
    assert ["scribe_v3" in form(c) for c in eleven["calls"]] == [True, False]
    assert post(client).status_code == 200 and "scribe_v2" in form(eleven["calls"][-1])  # remembered


def test_unreachable(client, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    real = httpx.AsyncClient

    def boom(req):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(stt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(boom), **kw))
    assert post(client).status_code == 502
