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
# One voice per page language (the FI | EN toggle), each overridable: ELEVENLABS_VOICE_ID_EN / _FI, or
# ELEVENLABS_VOICE_ID for both. EN: "Ziggy - Cute little Australian character", a library character voice: young,
# friendly and a little quirky, but a normal speaking pitch (the squeaky "Miffy Mouse", M5t0724ORuAGCh3p3DUR, sounded
# too much like a cartoon mouse). FI: "Sini - Energetic and spirited", a native Finnish library voice, bright and
# playful. The library has no cute native Finnish character voice, and Miffy reading Finnish got the amounts wrong in
# tests (183 € came back as 194), while Sini said every number right. ELEVENLABS_VOICE_ID_FI=W9YsrfUjc4DOYR4fhg8M
# brings back "Jarmo", the calm narrator. When the account can't use a voice, the English one speaks, then the
# premade "Chris" (in every account).
DEFAULT_VOICES = {"en": "fjgAVa6FpNYGo4UpjqML", "fi": "8X97zFR9k9pK2xyFI7Nr"}
FALLBACK_VOICE = "iP95p4xoKVk53GoZ742B"  # "Chris", premade; spoken with its own settings
# How the squirrel is spoken (ElevenLabs voice_settings), each overridable: ELEVENLABS_STABILITY, _SIMILARITY, _STYLE
# (0-1), _SPEAKER_BOOST (1/0), _SPEED (0.7-1.2). Slightly livelier than neutral and a touch faster: friendly, not a
# cartoon; stability near the middle keeps amounts and percents clear.
DEFAULT_SETTINGS = {"stability": 0.45, "similarity_boost": 0.75, "style": 0.3, "use_speaker_boost": True, "speed": 1.05}
_SETTING_ENV = {"stability": ("ELEVENLABS_STABILITY", 0.0, 1.0), "similarity_boost": ("ELEVENLABS_SIMILARITY", 0.0, 1.0),
                "style": ("ELEVENLABS_STYLE", 0.0, 1.0), "speed": ("ELEVENLABS_SPEED", 0.7, 1.2)}
DEFAULT_VOICE = DEFAULT_VOICES["en"]
# Speaks Finnish and English (and ~27 more); it picks the language from the text.
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT = "mp3_44100_64"
MAX_CHARS = 1_500          # one request (the page sends a reply in pieces of a few sentences); cut at a sentence end
DAILY_CHARS = 20_000       # sent to ElevenLabs per day (cache hits are free); override ELEVENLABS_DAILY_CHARS
PER_MINUTE = 30            # calls to ElevenLabs a minute at most (a reply is 1-4 pieces)
CACHE_ITEMS = 200
SAID_ITEMS = 500           # recent texts the server said, the only ones it will speak
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

_cache: OrderedDict[str, bytes] = OrderedDict()
_lock = threading.Lock()
_spent = {"day": "", "chars": 0}
_calls: list[float] = []
_said: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()  # reply key -> its sentences (normalized, as said)
_missing: dict[str, float] = {}  # voice id -> when ElevenLabs said this account doesn't have it (retried after an hour)
MISSING_FOR = 3600


def _norm(text: str) -> str:
    """Letters and digits only: markdown, punctuation and spacing don't change what is said."""
    return re.sub(r"[\W_]+", "", str(text or "").lower())


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'(A-ZÄÖÅ0-9])")


def sentences(parts) -> list[str]:
    """A reply as the page speaks it (helper.js spoken() and pieces(), kept the same): each line without links,
    URLs, list markers, markdown or emoji, ended with a period, then split into sentences."""
    parts = [parts] if isinstance(parts, str) else list(parts or [])
    lines = []
    for p in parts:
        for ln in str(p or "").split("\n"):
            ln = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", ln)
            ln = re.sub(r"https?://\S+", "", ln)
            ln = re.sub(r"^\s*(?:[-•*·]|\d+[.)])\s+", "", ln)
            ln = re.sub(r"[*_`#>~]", "", ln)
            ln = " ".join(_EMOJI.sub("", ln).split())
            if ln:
                lines.append(ln if re.search(r"[.!?:;…]$", ln) else ln + ".")
    return [x for x in _SENTENCE_END.split(" ".join(lines)) if _norm(x)]


def allow(parts) -> None:
    """Remember a reply (its parts) as one the server said, so it, or a run of its sentences, may be spoken."""
    said = [(_norm(x), x) for x in sentences(parts)]
    k = "".join(n for n, _ in said)
    if not k:
        return
    with _lock:
        _said[k] = said
        _said.move_to_end(k)
        while len(_said) > SAID_ITEMS:
            _said.popitem(last=False)


def allowed(text: str) -> str | None:
    """The server's own words for `text` when it is a reply Oiva said here, or a run of whole sentences of one (the
    page speaks a long reply a few sentences at a time); None for anything else. Only these words are sent on."""
    want = [_norm(x) for x in sentences(text)]
    whole = "".join(want)
    if not whole:
        return None
    with _lock:
        if whole in _said:
            return " ".join(x for _, x in _said[whole])
        for said in reversed(_said.values()):
            norms = [n for n, _ in said]
            for i in range(len(norms) - len(want) + 1):
                if norms[i:i + len(want)] == want:
                    return " ".join(x for _, x in said[i:i + len(want)])
    return None


class TTSError(Exception):
    """ElevenLabs could not speak this; the caller falls back. `status` is what /api/voice answers."""

    def __init__(self, reason: str, status: int = 502):
        super().__init__(reason)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?![\d])\s?€"
                     r"|€\s?(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?![\d])")
def _amount(m: re.Match, lang: str) -> str:
    """"3 345.06 €" -> "3345 euros and 6 cents" / "3345 euroa 6 senttiä": no thousands gap to trip on."""
    whole, cents = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    e = int(re.sub(r"\D", "", whole))
    c = int((cents or "0").ljust(2, "0"))
    if e == 0 and c:
        return (f"{c} senttiä" if c > 1 else "1 sentti") if lang == "fi" else (f"{c} cents" if c > 1 else "1 cent")
    if lang == "fi":
        s = f"{e} euro" if e == 1 else f"{e} euroa"
        return s + (f" {c} senttiä" if c > 1 else " 1 sentti" if c == 1 else "")
    s = f"{e} euro" if e == 1 else f"{e} euros"
    return s + (f" and {c} cents" if c > 1 else " and 1 cent" if c == 1 else "")


def clean(text: str, lang: str) -> str:
    """What is read aloud: markdown, links and emoji dropped, list lines as sentences, amounts and % in words,
    cut to MAX_CHARS at a sentence end."""
    t = str(text or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)              # [label](url) -> label
    t = re.sub(r"https?://\S+", "", t)
    t = _EMOJI.sub("", t)
    lines = []
    for ln in t.splitlines():
        ln = re.sub(r"^\s*(?:[-•*·]|\d+[.)])\s+", "", ln).strip()  # list markers
        if ln:
            lines.append(ln if re.search(r"[.!?:;…]$", ln) else ln + ".")
    t = " ".join(lines)
    t = re.sub(r"[*_`#>~]+", "", t)
    t = _AMOUNT.sub(lambda m: _amount(m, lang), t)
    t = re.sub(r"(\d)\s?%", r"\1 prosenttia" if lang == "fi" else r"\1 percent", t)
    t = t.replace("€", " euroa" if lang == "fi" else " euros")
    t = re.sub(r"\besim\.", "esimerkiksi", t) if lang == "fi" else re.sub(r"\be\.g\.", "for example", t)
    t = re.sub(r"\s*/\s*kk\b", " kuukaudessa", t) if lang == "fi" else re.sub(r"\s*/\s*(?:mo|month)\b", " a month", t)
    t = re.sub(r"\s+([.,!?])", r"\1", " ".join(t.split()))
    t = re.sub(r"\.{2,}", ".", t)
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
    voice = voice_for(lang)
    model = os.environ.get("ELEVENLABS_MODEL_ID", "").strip() or DEFAULT_MODEL
    if not re.fullmatch(r"[A-Za-z0-9]{10,40}", voice):
        raise TTSError("bad voice id", 503)
    chosen = voice
    for _ in range(2):  # not in this account (remembered for an hour): the next voice down
        if time.monotonic() - _missing.get(voice, -MISSING_FOR) < MISSING_FOR and voice != FALLBACK_VOICE:
            voice = _fallback(voice)
    settings = _settings_for(voice, chosen, model)
    ck = hashlib.sha256(f"{voice}|{model}|{lang}|{settings}|{t}".encode()).hexdigest()
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
    if settings:
        body["voice_settings"] = settings
    if not model.startswith("eleven_multilingual"):  # flash/turbo v2.5 take a language; multilingual_v2 refuses it
        body["language_code"] = lang
    own = client is None
    c = client or httpx.AsyncClient(timeout=TIMEOUT)
    started = time.monotonic()
    try:
        r = await c.post(f"{API}/{voice}", params={"output_format": OUTPUT},
                         headers={"xi-api-key": key, "accept": "audio/mpeg"}, json=body)
        for _ in range(2):  # FI voice missing -> the English voice -> Chris
            fallback = _fallback(voice)
            if not (r.status_code in (400, 404) and "voice_not_found" in _codes(r) and voice != fallback):
                break
            log.warning("voice %s is not in this ElevenLabs account; using %s", voice, fallback)
            _missing[voice] = time.monotonic()
            voice = fallback
            settings = _settings_for(voice, chosen, model)
            body.pop("voice_settings", None)
            if settings:
                body["voice_settings"] = settings
            ck = hashlib.sha256(f"{voice}|{model}|{lang}|{settings}|{t}".encode()).hexdigest()
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


def voice_for(lang: str) -> str:
    """The voice for the page language: ELEVENLABS_VOICE_ID_EN / _FI, else ELEVENLABS_VOICE_ID, else the default."""
    lang = "en" if lang == "en" else "fi"
    return (os.environ.get(f"ELEVENLABS_VOICE_ID_{lang.upper()}", "").strip()
            or os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICES[lang])


def _fallback(voice: str) -> str:
    """Who speaks when the account can't use `voice`: the English voice, unless that is the same (or also missing),
    then the premade Chris."""
    en = voice_for("en")
    gone = time.monotonic() - _missing.get(en, -MISSING_FOR) < MISSING_FOR
    return en if en != voice and not gone and re.fullmatch(r"[A-Za-z0-9]{10,40}", en) else FALLBACK_VOICE


def _settings_for(voice: str, chosen: str, model: str) -> dict | None:
    """The squirrel's settings, unless Chris is speaking as the fallback (then his own). eleven_v3 takes only
    stability 0, 0.5 or 1: the nearest."""
    if voice == FALLBACK_VOICE and voice != chosen:
        return None
    out = voice_settings()
    if model.startswith("eleven_v3"):
        out["stability"] = min((0.0, 0.5, 1.0), key=lambda x: abs(x - out["stability"]))
    return out


def voice_settings() -> dict:
    """DEFAULT_SETTINGS with any ELEVENLABS_STABILITY / _SIMILARITY / _STYLE / _SPEED / _SPEAKER_BOOST, kept in range;
    a value that isn't a number keeps the default."""
    out = dict(DEFAULT_SETTINGS)
    for k, (env, lo, hi) in _SETTING_ENV.items():
        try:
            out[k] = min(hi, max(lo, float(os.environ[env])))
        except (KeyError, ValueError):
            pass
    boost = os.environ.get("ELEVENLABS_SPEAKER_BOOST", "").strip().lower()
    if boost:
        out["use_speaker_boost"] = boost not in ("0", "false", "no", "off")
    return out


def _refund(n: int) -> None:
    with _lock:
        _spent["chars"] = max(0, _spent["chars"] - n)


def _codes(r: httpx.Response) -> set[str]:
    try:
        d = r.json().get("detail")
        return {str(d.get("code") or ""), str(d.get("status") or "")} if isinstance(d, dict) else set()
    except Exception:
        return set()


def _detail(r: httpx.Response) -> str:
    """ElevenLabs' own error code (e.g. quota_exceeded, invalid_api_key), never the request or its headers."""
    try:
        d = r.json().get("detail")
        return str(d.get("code") or d.get("status") or "")[:60] if isinstance(d, dict) else str(d)[:60]
    except Exception:
        return ""
