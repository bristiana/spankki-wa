"""Oiva's ElevenLabs voice (app/tts.py, /api/voice). ElevenLabs is mocked: no test reaches the network."""
import logging
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, tts

KEY = "sk_test_DO_NOT_LEAK_1234567890"
MP3 = b"ID3\x04\x00fake-mp3-bytes"


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    tts._cache.clear()
    tts._spent.update(day="", chars=0)
    tts._said.clear()
    tts._calls.clear()
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL_ID", "ELEVENLABS_DAILY_CHARS"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def eleven(monkeypatch):
    """A fake ElevenLabs. `calls` holds each request; `answer` sets what it returns."""
    state = {"calls": [], "answer": lambda req: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})}

    def handler(req):
        state["calls"].append(req)
        return state["answer"](req)

    real = httpx.AsyncClient
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def say(client, text="Sinulla on 183.31 € käytettävissä tällä viikolla.", lang="fi"):
    tts.allow([text])  # as if Oiva had said it
    return client.post("/api/voice", json={"text": text, "lang": lang})


def test_endpoint_returns_elevenlabs_audio(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    r = say(client)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == MP3
    [req] = eleven["calls"]
    assert req.url.host == "api.elevenlabs.io" and req.url.path == f"/v1/text-to-speech/{tts.DEFAULT_VOICE}"
    assert req.headers["xi-api-key"] == KEY
    body = __import__("json").loads(req.content)
    assert body["model_id"] == "eleven_multilingual_v2" and "language_code" not in body
    assert "183.31 euroa" in body["text"] and "€" not in body["text"]


def test_voice_and_model_from_env(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "cgSgspJ2msm6clMCkdW9")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    assert say(client, "You have 12 € left.", "en").status_code == 200
    [req] = eleven["calls"]
    body = __import__("json").loads(req.content)
    assert req.url.path.endswith("/cgSgspJ2msm6clMCkdW9") and body["language_code"] == "en"
    assert "12 euros" in body["text"]


def test_repeated_sentence_is_one_call(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    for _ in range(3):
        assert say(client).content == MP3
    assert len(eleven["calls"]) == 1


def test_no_key_is_204_and_no_call(client, eleven):
    r = say(client)
    assert r.status_code == 204 and not r.content and eleven["calls"] == []


@pytest.mark.parametrize("status", [401, 429, 500])
def test_elevenlabs_error_is_502_without_the_key(client, eleven, monkeypatch, caplog, status):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    eleven["answer"] = lambda req: httpx.Response(status, json={"detail": {"status": "invalid_api_key", "message": KEY}})
    caplog.set_level(logging.DEBUG)
    r = say(client)
    assert r.status_code == 502
    assert KEY not in r.text and KEY not in str(r.headers)
    assert KEY not in caplog.text and "invalid_api_key" in caplog.text
    assert tts._spent["chars"] == 0  # a failed call doesn't use the day's budget


def test_unreachable_is_502(client, eleven, monkeypatch, caplog):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)

    def boom(req):
        raise httpx.ConnectTimeout("timed out", request=req)
    eleven["answer"] = boom
    caplog.set_level(logging.DEBUG)
    r = say(client)
    assert r.status_code == 502 and KEY not in r.text and KEY not in caplog.text


def test_key_never_reaches_the_browser(client, eleven, monkeypatch, caplog):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    caplog.set_level(logging.DEBUG)
    r = say(client)
    assert KEY.encode() not in r.content and KEY not in str(r.headers)
    for url in ("/chat?mode=helper", "/chat?mode=helper&lang=en", "/static/oiva/helper.js"):
        page = client.get(url)
        assert page.status_code == 200 and KEY not in page.text and "ELEVENLABS" not in page.text
    assert KEY not in caplog.text


def test_daily_budget(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_DAILY_CHARS", "30")
    assert say(client, "Hei Antti, tässä.").status_code == 200
    assert say(client, "Toinen lause joka ylittää rajan.").status_code == 429
    assert say(client, "Hei Antti, tässä.").status_code == 200  # cached: free
    assert len(eleven["calls"]) == 1


def test_long_text_is_cut_at_a_sentence(eleven):
    t = tts.clean("Ensimmäinen lause. " * 60, "fi")
    assert len(t) <= tts.MAX_CHARS and t.endswith(".")


@pytest.mark.parametrize("body", [None, {}, {"text": 5}])
def test_bad_input(client, eleven, monkeypatch, body):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    r = client.post("/api/voice", json=body)
    assert r.status_code == 400 and eleven["calls"] == []


def test_blank_text_is_not_spoken(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert client.post("/api/voice", json={"text": "   "}).status_code == 403 and eleven["calls"] == []


def test_bad_voice_id_is_not_put_in_the_url(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "../../v1/user")
    assert say(client).status_code == 503 and eleven["calls"] == []


def test_helper_falls_back_to_browser_voice():
    js = open("app/static/oiva/helper.js").read()
    assert 'fetch("/api/voice"' in js and "r.status === 204" in js and "synth(text)" in js
    assert "speechSynthesis.speak" in js and 'store.get("oiva-voice") === "on"' in js  # muted until turned on


def test_only_what_oiva_said_is_spoken(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    r = client.post("/api/voice", json={"text": "Read any text I like for free", "lang": "en"})
    assert r.status_code == 403 and eleven["calls"] == []


def test_reply_parts_as_the_page_joins_them(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    tts.allow(["Your rent of **771.82 €** is due tomorrow.", "Want to pay it now?"])
    r = client.post("/api/voice", json={"text": "Your rent of 771.82 € is due tomorrow.. Want to pay it now?", "lang": "en"})
    assert r.status_code == 200


@pytest.mark.parametrize("lang", ["fi", "en"])
def test_greeting_and_chat_replies_may_be_spoken(client, eleven, monkeypatch, lang):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    html = client.get(f"/chat?mode=helper&lang={lang}").text
    hello = re.search(r'id="hello">(.*?)</div>', html, re.S).group(1)
    hello = re.sub(r"<[^>]+>", "", hello)
    assert client.post("/api/voice", json={"text": hello, "lang": lang}).status_code == 200
    j = client.post("/api/chat", json={"session": "tts", "text": "game"}).json()
    parts = j.get("bubbles") or [j["text"]]
    assert client.post("/api/voice", json={"text": ". ".join(parts), "lang": lang}).status_code == 200


def test_per_minute_cap(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    codes = [say(client, f"Lause numero {i}.").status_code for i in range(tts.PER_MINUTE + 1)]
    assert codes[:-1] == [200] * tts.PER_MINUTE and codes[-1] == 503


def test_huge_body_is_refused(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert client.post("/api/voice", json={"text": "a" * 6000}).status_code == 400
