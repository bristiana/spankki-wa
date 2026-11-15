"""Oiva's voice in the web chat's Helper mode: text -> speech with ElevenLabs.

The key stays on the server (ELEVENLABS_API_KEY in the environment); the browser only ever gets the audio.
With no key, or when ElevenLabs fails, `speak` returns None and the page falls back to the browser's own
speechSynthesis. Only text the server itself said (a reply, the greeting, a mirrored message: `allow`) is
spoken, so the public URL is no free text-to-speech proxy. Same text, same voice -> one call: the audio is cached
in memory. A daily character budget and a per-minute call cap bound what it can spend.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import date

import httpx

log = logging.getLogger("tts")

API = "https://api.elevenlabs.io/v1/text-to-speech"
# "George", a premade ElevenLabs voice (warm, calm storyteller), the voice in ElevenLabs' own API examples.
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"
# Speaks Finnish and English (and ~27 more); it picks the language from the text.
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT = "mp3_44100_64"
MAX_CHARS = 600            # one reply read aloud; longer text is cut at a sentence end
DAILY_CHARS = 5_000        # sent to ElevenLabs per day (cache hits are free); override ELEVENLABS_DAILY_CHARS
PER_MINUTE = 12            # calls to ElevenLabs a minute at most
CACHE_ITEMS = 200
SAID_ITEMS = 500           # recent texts the server said, the only ones it will speak
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

_cache: OrderedDict[str, bytes] = OrderedDict()
_lock = threading.Lock()
_spent = {"day": "", "chars": 0}
_calls: list[float] = []
_said: OrderedDict[str, None] = OrderedDict()


def _norm(text: str) -> str:
    """Letters and digits only: the page joins a reply's parts with ". " and drops markdown; this ignores both."""
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def allow(parts) -> None:
    """Remember a reply (its parts, as the page will join them) as one the server said, so it may be spoken."""
    parts = [parts] if isinstance(parts, str) else list(parts or [])
    k = "".join(_norm(p) for p in parts)
    if not k:
        return
    with _lock:
        _said[k] = None
        _said.move_to_end(k)
        while len(_said) > SAID_ITEMS:
            _said.popitem(last=False)


def allowed(text: str) -> bool:
    k = _norm(text)
    with _lock:
        return bool(k) and k in _said


class TTSError(Exception):
    """ElevenLabs could not speak this; the caller falls back. `status` is what /api/voice answers."""

    def __init__(self, reason: str, status: int = 502):
        super().__init__(reason)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def clean(text: str, lang: str) -> str:
    """What is read aloud: whitespace folded, the euro sign said as a word, cut to MAX_CHARS at a sentence end."""
    t = " ".join(str(text or "").split()).replace("€", " euroa" if lang == "fi" else " euros")
    t = " ".join(t.split())
    if len(t) > MAX_CHARS:
        cut = t[:MAX_CHARS]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: end + 1] if end > MAX_CHARS // 3 else cut.rsplit(" ", 1)[0]
    return t


def _budget() -> int:
    try:
        return int(os.environ.get("ELEVENLABS_DAILY_CHARS") or DAILY_CHARS)
    except ValueError:
        return DAILY_CHARS


async def speak(text: str, lang: str = "fi", client: httpx.AsyncClient | None = None) -> bytes:
    """MP3 bytes for `text`. Raises TTSError (no key or busy: 503; empty: 400; day's budget: 429; ElevenLabs down: 502)."""
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise TTSError("no key", 503)
    lang = "en" if lang == "en" else "fi"
    t = clean(text, lang)
    if not t:
        raise TTSError("empty", 400)
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE
    model = os.environ.get("ELEVENLABS_MODEL_ID", "").strip() or DEFAULT_MODEL
    if not re.fullmatch(r"[A-Za-z0-9]{10,40}", voice):
        raise TTSError("bad voice id", 503)
    ck = hashlib.sha256(f"{voice}|{model}|{lang}|{t}".encode()).hexdigest()
    with _lock:
        if ck in _cache:
            _cache.move_to_end(ck)
            return _cache[ck]
        today = date.today().isoformat()
        if _spent["day"] != today:
            _spent.update(day=today, chars=0)
        if _spent["chars"] + len(t) > _budget():
            raise TTSError("daily budget used", 429)
        now = time.monotonic()
        _calls[:] = [x for x in _calls if now - x < 60]
        if len(_calls) >= PER_MINUTE:
            raise TTSError("too many calls a minute", 503)  # this reply only: not 429, which ends it for the visit
        _calls.append(now)
        _spent["chars"] += len(t)  # counted before the call so parallel requests can't overshoot
    body: dict = {"text": t, "model_id": model}
    if not model.startswith("eleven_multilingual"):  # flash/turbo v2.5 take a language; multilingual_v2 refuses it
        body["language_code"] = lang
    own = client is None
    c = client or httpx.AsyncClient(timeout=TIMEOUT)
    started = time.monotonic()
    try:
        r = await c.post(f"{API}/{voice}", params={"output_format": OUTPUT},
                         headers={"xi-api-key": key, "accept": "audio/mpeg"}, json=body)
    except httpx.HTTPError as e:
        _refund(len(t))
        log.warning("elevenlabs unreachable: %s", type(e).__name__)
        raise TTSError("unreachable") from None
    finally:
        if own:
            await c.aclose()
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("audio/"):
        _refund(len(t))
        log.warning("elevenlabs answered %s (%s)", r.status_code, _detail(r))
        raise TTSError(f"elevenlabs {r.status_code}")
    audio = r.content
    log.info("elevenlabs %d chars, %d bytes, %.1fs", len(t), len(audio), time.monotonic() - started)
    with _lock:
        _cache[ck] = audio
        while len(_cache) > CACHE_ITEMS:
            _cache.popitem(last=False)
    return audio


def _refund(n: int) -> None:
    with _lock:
        _spent["chars"] = max(0, _spent["chars"] - n)


def _detail(r: httpx.Response) -> str:
    """ElevenLabs' own error code (e.g. quota_exceeded, invalid_api_key), never the request or its headers."""
    try:
        d = r.json().get("detail")
        return str(d.get("status") or d.get("code") or "")[:60] if isinstance(d, dict) else str(d)[:60]
    except Exception:
        return ""
