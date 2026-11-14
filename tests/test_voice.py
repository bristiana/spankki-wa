import httpx
import pytest

from app import voice


class Resp:
    def __init__(self, body, content=b""):
        self.body, self.content = body, content

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("STATE_REMOTE", "PUBLIC_URL", "WA_VERIFY_TOKEN", "CF_ACCOUNT_ID", "CF_API_TOKEN", "WA_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_behind_the_worker_uses_the_stt_route(monkeypatch):
    monkeypatch.setenv("STATE_REMOTE", "1")
    monkeypatch.setenv("PUBLIC_URL", "https://oiva.example/")
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append((url, kw)) or Resp({"text": " Hi Oiva "}))
    assert voice.transcribe(b"ogg") == "Hi Oiva"
    url, kw = calls[0]
    assert url == "https://oiva.example/__stt" and kw["params"] == {"key": "vt"}
    assert kw["json"]["audio"] == "b2dn" and "Oiva" in kw["json"]["initial_prompt"]


def test_locally_uses_the_workers_ai_rest_api(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    calls = []
    monkeypatch.setattr(httpx, "post",
                        lambda url, **kw: calls.append((url, kw)) or Resp({"success": True, "result": {"text": "Rent?"}}))
    assert voice.transcribe(b"ogg") == "Rent?"
    assert calls[0][0].endswith("/accounts/acct/ai/run/@cf/openai/whisper-large-v3-turbo")
    assert calls[0][1]["headers"] == {"Authorization": "Bearer tok"}


def test_not_configured_or_silent_note_says_so(monkeypatch):
    with pytest.raises(voice.VoiceError, match="can't listen"):
        voice.transcribe(b"ogg")
    monkeypatch.setenv("CF_ACCOUNT_ID", "a")
    monkeypatch.setenv("CF_API_TOKEN", "t")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: Resp({"result": {"text": "  "}}))
    with pytest.raises(voice.VoiceError, match="couldn't hear any words"):
        voice.transcribe(b"ogg")


def test_long_voice_note_is_refused_before_download(monkeypatch):
    monkeypatch.setenv("WA_TOKEN", "wt")
    got = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: got.append(url) or Resp({"url": "x", "file_size": 9_000_000}))
    with pytest.raises(voice.VoiceError, match="a bit long"):
        voice.to_text("m1")
    assert len(got) == 1  # only the metadata, never the audio


def test_download_then_transcribe(monkeypatch):
    monkeypatch.setenv("WA_TOKEN", "wt")
    monkeypatch.setenv("CF_ACCOUNT_ID", "a")
    monkeypatch.setenv("CF_API_TOKEN", "t")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: Resp({"url": "https://cdn/x", "file_size": 10}, b"ogg")
                        if url.endswith("/m1") else Resp({}, b"ogg-bytes"))
    seen = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.append(kw["json"]["audio"]) or Resp({"result": {"text": "ok"}}))
    assert voice.to_text("m1") == "ok"
    assert seen == ["b2dnLWJ5dGVz"]
