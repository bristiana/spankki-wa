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
    tts._missing.clear()
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_VOICE_ID_EN", "ELEVENLABS_VOICE_ID_FI",
              "ELEVENLABS_MODEL_ID", "ELEVENLABS_DAILY_CHARS", "ELEVENLABS_STABILITY", "ELEVENLABS_SIMILARITY",
              "ELEVENLABS_STYLE", "ELEVENLABS_SPEED", "ELEVENLABS_SPEAKER_BOOST"):
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
    assert req.url.host == "api.elevenlabs.io" and req.url.path == f"/v1/text-to-speech/{tts.DEFAULT_VOICES['fi']}"
    assert req.headers["xi-api-key"] == KEY
    body = __import__("json").loads(req.content)
    assert body["model_id"] == "eleven_multilingual_v2" and "language_code" not in body
    assert "183 euroa 31 senttiä" in body["text"] and "€" not in body["text"]


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
    for url in ("/chat", "/chat?lang=en", "/static/oiva/helper.js"):
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
    assert 'fetch("/api/voice"' in js and "got === 204" in js and "synth(chunks" in js
    assert "speechSynthesis.speak" in js and 'store.get("oiva-voice") !== "off"' in js  # talks unless muted


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
    html = client.get(f"/chat?lang={lang}").text
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


@pytest.mark.parametrize("lang", ["en", "fi"])
def test_voice_follows_the_page_language(client, eleven, monkeypatch, lang):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert say(client, "Hello there. Hei vaan.", lang).status_code == 200
    [req] = eleven["calls"]
    assert req.url.path.endswith("/" + tts.DEFAULT_VOICES[lang])


def test_per_language_voice_env(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "AAAAAAAAAAAAAAAAAAAA")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_FI", "FFFFFFFFFFFFFFFFFFFF")
    say(client, "Hei.", "fi"); say(client, "Hi.", "en")
    assert [r.url.path.rsplit("/", 1)[1] for r in eleven["calls"]] == ["FFFFFFFFFFFFFFFFFFFF", "AAAAAAAAAAAAAAAAAAAA"]


def test_finnish_voice_missing_from_account_falls_back_to_english(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    fi_voice, en_voice = "FFFFFFFFFFFFFFFFFFFF", tts.DEFAULT_VOICES["en"]
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_FI", fi_voice)
    eleven["answer"] = lambda req: (httpx.Response(400, json={"detail": {"code": "voice_not_found"}})
                                    if req.url.path.endswith(fi_voice) else
                                    httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"}))
    assert say(client, "Hei Antti.").content == MP3
    assert say(client, "Toinen lause.").content == MP3  # remembered: straight to the English voice
    assert [r.url.path.rsplit("/", 1)[1] for r in eleven["calls"]] == [fi_voice, en_voice, en_voice]


def test_squirrel_missing_from_account_falls_back_to_chris_with_his_own_settings(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    squirrel = tts.DEFAULT_VOICES["en"]
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_FI", squirrel)  # one voice for both, as when ELEVENLABS_VOICE_ID is set
    eleven["answer"] = lambda req: (httpx.Response(400, json={"detail": {"code": "voice_not_found"}})
                                    if req.url.path.endswith(squirrel) else
                                    httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"}))
    assert say(client, "Hei Antti.").content == MP3
    assert say(client, "Hi there.", "en").content == MP3  # remembered for English too: straight to Chris
    paths = [r.url.path.rsplit("/", 1)[1] for r in eleven["calls"]]
    assert paths == [squirrel, tts.FALLBACK_VOICE, tts.FALLBACK_VOICE]
    bodies = [__import__("json").loads(r.content) for r in eleven["calls"]]
    assert "voice_settings" in bodies[0] and all("voice_settings" not in b for b in bodies[1:])


def test_both_voices_missing_falls_through_to_chris_in_one_request(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    fi, en = tts.DEFAULT_VOICES["fi"], tts.DEFAULT_VOICES["en"]
    eleven["answer"] = lambda req: (httpx.Response(400, json={"detail": {"code": "voice_not_found"}})
                                    if not req.url.path.endswith(tts.FALLBACK_VOICE) else
                                    httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"}))
    assert say(client, "Hei Antti.").content == MP3
    assert say(client, "Toinen lause.").content == MP3  # remembered: straight to Chris
    assert [r.url.path.rsplit("/", 1)[1] for r in eleven["calls"]] == [fi, en, tts.FALLBACK_VOICE, tts.FALLBACK_VOICE]


def test_chris_chosen_on_purpose_keeps_the_settings(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", tts.FALLBACK_VOICE)
    say(client, "Hi.", "en")
    assert "voice_settings" in __import__("json").loads(eleven["calls"][0].content)


def test_v3_model_gets_a_stability_it_takes(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_v3")
    say(client, "Hei.")
    assert __import__("json").loads(eleven["calls"][0].content)["voice_settings"]["stability"] == 0.5


def test_default_voices_are_the_squirrel_and_sini():
    assert tts.DEFAULT_VOICES == {"en": "fjgAVa6FpNYGo4UpjqML", "fi": "8X97zFR9k9pK2xyFI7Nr"}
    assert tts.FALLBACK_VOICE not in tts.DEFAULT_VOICES.values()


def test_squirrel_settings_are_sent(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    say(client, "Hei.")
    body = __import__("json").loads(eleven["calls"][0].content)
    assert body["voice_settings"] == {"stability": 0.45, "similarity_boost": 0.75, "style": 0.3,
                                      "use_speaker_boost": True, "speed": 1.05}


def test_voice_settings_from_env_kept_in_range(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_STABILITY", "0.6")
    monkeypatch.setenv("ELEVENLABS_SIMILARITY", "abc")   # not a number: default
    monkeypatch.setenv("ELEVENLABS_STYLE", "2")          # above 1: 1
    monkeypatch.setenv("ELEVENLABS_SPEED", "0.1")        # below 0.7: 0.7
    monkeypatch.setenv("ELEVENLABS_SPEAKER_BOOST", "0")
    say(client, "Hei.")
    body = __import__("json").loads(eleven["calls"][0].content)
    assert body["voice_settings"] == {"stability": 0.6, "similarity_boost": 0.75, "style": 1.0,
                                      "use_speaker_boost": False, "speed": 0.7}


def test_changed_settings_are_not_served_from_the_cache(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    say(client, "Hei.")
    monkeypatch.setenv("ELEVENLABS_SPEED", "1.0")
    say(client, "Hei.")
    assert len(eleven["calls"]) == 2


def test_a_piece_of_a_reply_may_be_spoken_but_not_other_text(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    tts.allow(["You have **183.31 €** left.", "Groceries took most of it. Want tips?"])
    ok = lambda t: client.post("/api/voice", json={"text": t, "lang": "en"}).status_code
    assert ok("You have 183.31 € left.") == 200
    assert ok("Groceries took most of it.") == 200
    assert ok("Want tips?") == 200
    assert ok("Groceries took most of the cake.") == 403
    assert ok("Say anything I like") == 403
    assert ok("a") == 403 and ok("Y o u h a v e") == 403 and ok("You have") == 403  # whole sentences only


def test_pieces_of_a_list_with_links_are_spoken_in_the_servers_words(client, eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    tts.allow(["Kolme vinkkiä:\n1. Maksa vuokra ensin.\n2. Säästä 50 € kuussa.\nKatso [ohje](https://x.example/o) tästä."])
    ok = lambda t: client.post("/api/voice", json={"text": t, "lang": "fi"}).status_code
    assert ok("Kolme vinkkiä: Maksa vuokra ensin. Säästä 50 € kuussa.") == 200
    assert ok("Katso ohje tästä.") == 200
    assert ok("Säästä 500 € kuussa.") == 403
    sent = [__import__("json").loads(r.content)["text"] for r in eleven["calls"]]
    assert sent == ["Kolme vinkkiä: Maksa vuokra ensin. Säästä 50 euroa kuussa.", "Katso ohje tästä."]


@pytest.mark.parametrize("lang,text,want", [
    ("en", "Rent **1 313.89 €** is due.", "Rent 1313 euros and 89 cents is due."),
    ("fi", "Vuokra 1 313,89 € erääntyy.", "Vuokra 1313 euroa 89 senttiä erääntyy."),
    ("en", "Only 0.50 € and 12 € left, 3 % more.", "Only 50 cents and 12 euros left, 3 percent more."),
    ("fi", "Säästä 1 € päivässä 🐿️", "Säästä 1 euro päivässä."),
    ("en", "Your baskets:\n- Groceries 40 €\n- Fuel 12 €\nSee [details](https://x.example/a).",
     "Your baskets: Groceries 40 euros. Fuel 12 euros. See details."),
])
def test_spoken_text(lang, text, want):
    assert tts.clean(text, lang) == want


def test_caps_fit_a_whole_reply():
    assert tts.MAX_CHARS >= 1500 and tts.DAILY_CHARS == 20_000 and tts.PER_MINUTE == 30
