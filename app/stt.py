"""What you say to Oiva in the web chat's Helper -> text, with ElevenLabs Speech-to-Text (Scribe).

The page records a short clip (MediaRecorder: audio/webm on Chrome and Android, audio/mp4 on Safari and iPhone) and
posts it to /api/listen; the transcript goes back into the message box. The key stays on the server
(ELEVENLABS_API_KEY); the audio is not stored. With no key the page uses the browser's own speech recognition, or
hides the mic. Cost guards: a clip is at most MAX_BYTES (about a minute of speech; the page stops at 30 s), each
visitor may send PER_VISITOR_MINUTE clips a minute and PER_VISITOR_DAY a day, and all visitors together
PER_MINUTE a minute and DAILY_CALLS / DAILY_BYTES a day.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import date

import httpx

log = logging.getLogger("stt")

API = "https://api.elevenlabs.io/v1/speech-to-text"
# Scribe v3: ElevenLabs' newest batch model for recorded audio (docs: Models, Nov 2026). Override with
# ELEVENLABS_STT_MODEL_ID; when the account can't use it, scribe_v2 is tried once.
DEFAULT_MODEL = "scribe_v3"
FALLBACK_MODEL = "scribe_v2"
MAX_BYTES = 600_000        # one clip; the page records 30 s at 32 kbps (~120 KB; Safari's AAC up to ~500 KB)
MIN_BYTES = 1_000          # shorter is a tap, not speech
MAX_SECONDS = 30           # what the page records at most (said here so the page and tests agree)
PER_MINUTE = 20            # calls to ElevenLabs a minute, all visitors together
DAILY_CALLS = 400          # a day, all visitors; override ELEVENLABS_STT_DAILY_CALLS
DAILY_BYTES = 20_000_000   # audio sent a day, all visitors: ~1.5 h at the page's 32 kbps; less if a client
                           # sends very low-bitrate audio, which DAILY_CALLS (400 clips of at most MAX_BYTES) also bounds
PER_VISITOR_MINUTE = 6
PER_VISITOR_DAY = 60
VISITORS = 5_000           # visitors remembered for the per-visitor caps
TIMEOUT = httpx.Timeout(18.0, connect=5.0)  # two tries fit in the page's 45 s
# Content types MediaRecorder gives on Chrome, Firefox, Safari (desktop and iPhone) and Android.
TYPES = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/wav": "wav",
         "audio/x-m4a": "m4a", "audio/aac": "aac", "video/webm": "webm", "video/mp4": "mp4"}

_lock = threading.Lock()
_spent = {"day": "", "calls": 0, "bytes": 0}
_calls: list[float] = []
_visitors: dict[str, list[float]] = {}  # visitor -> times of their clips today
_missing: dict[str, float] = {}  # model -> when this account was refused it (retried after an hour)


class STTError(Exception):
    """Why the clip could not be turned into text. `status` is what /api/listen answers; `reason` a short code the
    page turns into words."""

    def __init__(self, reason: str, status: int = 502):
        super().__init__(reason)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def kind(content_type: str) -> str | None:
    """'webm' / 'mp4' / ... for an audio content type MediaRecorder makes ("audio/webm;codecs=opus"), else None."""
    return TYPES.get(str(content_type or "").split(";")[0].strip().lower())


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def admit(visitor: str, size: int) -> None:
    """Count one clip against the caps, or raise STTError (429 for this visitor or the day, 503 for a busy minute)."""
    now, today = time.time(), date.today().isoformat()
    with _lock:
        if _spent["day"] != today:
            _spent.update(day=today, calls=0, bytes=0)
            _visitors.clear()
        mine = [t for t in _visitors.get(visitor, []) if now - t < 86_400]
        if len(mine) >= PER_VISITOR_DAY:
            raise STTError("visitor day", 429)
        if sum(1 for t in mine if now - t < 60) >= PER_VISITOR_MINUTE:
            raise STTError("visitor minute", 429)
        if _spent["calls"] >= _int_env("ELEVENLABS_STT_DAILY_CALLS", DAILY_CALLS) or _spent["bytes"] + size > DAILY_BYTES:
            raise STTError("daily budget used", 429)
        _calls[:] = [t for t in _calls if now - t < 60]
        if len(_calls) >= PER_MINUTE:
            raise STTError("busy", 503)
        _calls.append(now)
        mine.append(now)
        _visitors[visitor] = mine
        if len(_visitors) > VISITORS:  # forget the quietest
            for k in sorted(_visitors, key=lambda k: _visitors[k][-1])[: len(_visitors) - VISITORS]:
                _visitors.pop(k, None)
        _spent["calls"] += 1
        _spent["bytes"] += size


def _refund(size: int) -> None:
    with _lock:
        _spent["calls"] = max(0, _spent["calls"] - 1)
        _spent["bytes"] = max(0, _spent["bytes"] - size)


async def transcribe(audio: bytes, content_type: str, lang: str = "fi", visitor: str = "",
                     client: httpx.AsyncClient | None = None) -> str:
    """The words in `audio`, in the page language. Raises STTError: no key 503, bad clip 400/413/415, a cap 429/503,
    ElevenLabs down 502, nothing heard 422."""
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise STTError("no key", 503)
    ext = kind(content_type)
    if not ext:
        raise STTError("type", 415)
    if len(audio) > MAX_BYTES:
        raise STTError("too long", 413)
    if len(audio) < MIN_BYTES:
        raise STTError("too short", 400)
    lang = "en" if lang == "en" else "fi"
    model = os.environ.get("ELEVENLABS_STT_MODEL_ID", "").strip() or DEFAULT_MODEL
    if not re.fullmatch(r"[a-z0-9_]{3,40}", model):
        raise STTError("bad model id", 503)
    if model != FALLBACK_MODEL and time.monotonic() - _missing.get(model, -3600) < 3600:
        model = FALLBACK_MODEL
    admit(visitor or "?", len(audio))
    mime = str(content_type).split(";")[0].strip().lower()
    own = client is None
    c = client or httpx.AsyncClient(timeout=TIMEOUT)
    started = time.monotonic()

    async def ask(m: str) -> httpx.Response:
        data = {"model_id": m, "language_code": lang, "tag_audio_events": "false"}
        return await c.post(API, headers={"xi-api-key": key}, data=data,
                            files={"file": (f"clip.{ext}", audio, mime)})

    try:
        r = await ask(model)
        if r.status_code in (400, 404, 422) and model != FALLBACK_MODEL and "model" in _detail(r).lower():
            log.warning("stt model %s refused (%s); using %s", model, _detail(r), FALLBACK_MODEL)
            _missing[model] = time.monotonic()
            model = FALLBACK_MODEL
            r = await ask(model)
    except httpx.HTTPError as e:
        if isinstance(e, httpx.ConnectError | httpx.ConnectTimeout):  # never reached them: not billed
            _refund(len(audio))
        log.warning("elevenlabs stt unreachable: %s", type(e).__name__)
        raise STTError("unreachable") from None
    finally:
        if own:
            await c.aclose()
    if r.status_code != 200:
        if r.status_code >= 500 or r.status_code == 429:
            _refund(len(audio))
        log.warning("elevenlabs stt answered %s (%s)", r.status_code, _detail(r))
        raise STTError(f"elevenlabs {r.status_code}", 400 if r.status_code in (400, 422) else 502)
    try:
        text = str(r.json().get("text") or "")
    except Exception:
        raise STTError("bad answer") from None
    text = " ".join(re.sub(r"\((?:[^()]{1,40})\)|\[(?:[^\[\]]{1,40})\]", " ", text).split())[:1000]  # (music), [noise]
    log.info("elevenlabs stt %s %s %d bytes -> %d chars, %.1fs", model, lang, len(audio), len(text),
             time.monotonic() - started)
    if not re.search(r"\w", text):
        raise STTError("nothing heard", 422)
    return text


def _detail(r: httpx.Response) -> str:
    """ElevenLabs' own error code or message, never the request or its headers."""
    try:
        d = r.json().get("detail")
        if isinstance(d, dict):
            return str(d.get("code") or d.get("status") or d.get("message") or "")[:120]
        if isinstance(d, list) and d and isinstance(d[0], dict):
            return str(d[0].get("msg") or d[0].get("loc") or "")[:120]
        return str(d)[:120]
    except Exception:
        return ""
