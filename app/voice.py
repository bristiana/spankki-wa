"""WhatsApp voice notes -> text, so Birky can be asked out loud.

The audio is fetched from Meta (Graph media API) and transcribed by Whisper on Cloudflare Workers AI:
in production through the Worker's /__stt route (AI binding, guarded by WA_VERIFY_TOKEN), locally
through the Workers AI REST API when CF_ACCOUNT_ID and CF_API_TOKEN are set. The audio is not stored.
"""
from __future__ import annotations

import base64
import logging
import os

import httpx

from .whatsapp import GRAPH

log = logging.getLogger("voice")

MODEL = "@cf/openai/whisper-large-v3-turbo"
MAX_BYTES = 8_000_000  # a few minutes of WhatsApp Opus; longer notes are refused, not cut
# Whisper spells names it has never seen phonetically ("Berkey", "spanky"); a hint fixes that.
HINT = "Birky, S-Pankki, S-mobiili, S-Bonus, Kela, euro, payday."


class VoiceError(Exception):
    """Why a voice note could not be turned into text, in words Birky can say."""


def _remote() -> tuple[str, dict] | None:
    url, key = os.environ.get("PUBLIC_URL", ""), os.environ.get("WA_VERIFY_TOKEN", "")
    if os.environ.get("STATE_REMOTE") == "1" and url and key:  # running behind the Worker
        return url.rstrip("/") + "/__stt", {"params": {"key": key}}
    acct, tok = os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_API_TOKEN", "")
    if acct and tok:
        return (f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{MODEL}",
                {"headers": {"Authorization": f"Bearer {tok}"}})
    return None


def available() -> bool:
    return bool(os.environ.get("WA_TOKEN")) and _remote() is not None


def download(media_id: str) -> bytes:
    auth = {"Authorization": f"Bearer {os.environ['WA_TOKEN']}"}
    meta = httpx.get(f"{GRAPH}/{media_id}", headers=auth, timeout=15)
    meta.raise_for_status()
    info = meta.json()
    if int(info.get("file_size") or 0) > MAX_BYTES:
        raise VoiceError("That voice note is a bit long for me. Could you keep it under a couple of minutes?")
    r = httpx.get(info["url"], headers=auth, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content


def transcribe(audio: bytes) -> str:
    target = _remote()
    if target is None:
        raise VoiceError("I can't listen to voice notes right now. Could you type it instead?")
    url, extra = target
    body = {"audio": base64.b64encode(audio).decode(), "initial_prompt": HINT, "vad_filter": True}
    r = httpx.post(url, json=body, timeout=60, **extra)
    r.raise_for_status()
    j = r.json()
    text = ((j.get("result") or j).get("text") or "").strip()  # REST wraps in {result}, the Worker does not
    if not text:
        raise VoiceError("I couldn't hear any words in that one. Could you try again, or type it?")
    return text


def to_text(media_id: str) -> str:
    """The transcript of a voice note, or VoiceError with a message for the user."""
    try:
        return transcribe(download(media_id))
    except VoiceError:
        raise
    except Exception as e:  # network, Meta or Workers AI: say so plainly, keep the details in the log
        log.warning("voice note %s failed: %s", media_id, e)
        raise VoiceError("Sorry, I couldn't hear that voice note. Could you send it again, or type it?") from e
