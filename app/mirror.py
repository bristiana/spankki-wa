"""One conversation, two windows: the owner's WhatsApp chat with Oiva, shown live in the app's chat.

Everything the owner's number sends Oiva on WhatsApp and everything Oiva sends back is kept here, and the app's chat
(/chat) shows it, polling /api/mirror. What the owner types in the app is answered in the app, with the same history
and the same Oiva, and kept here too, but it never goes to WhatsApp (app -> WhatsApp would message the phone).

Only one number is mirrored: MIRROR_PHONE, or else DEMO_PHONE. Other numbers still talk to Oiva on WhatsApp; they just
do not show in the app. Kept in memory only, so a restart starts the conversation from scratch.
"""
from __future__ import annotations

import os
import re
import threading
import time

APP_KEY = "web:mirror"  # the app chat's history key; the coach reads and writes the owner's history under it
MAX = 400

_lock = threading.Lock()
_log: list[dict] = []
_next = 1


def owner() -> str:
    return re.sub(r"\D", "", os.environ.get("MIRROR_PHONE") or os.environ.get("DEMO_PHONE") or "")


def is_owner(phone: str | None) -> bool:
    me = owner()
    return bool(me) and re.sub(r"\D", "", phone or "") == me


def add(role: str, text: str, buttons: list[dict] | None = None, source: str = "wa") -> dict | None:
    """role: "me" (the person) or "oiva". buttons: [{"label"}] quick replies (tapping one in the app sends the label)
    or [{"label", "url"}] links (pay, save, connect), opened in the app."""
    global _next
    text = (text or "").strip()
    if not text and not buttons:
        return None
    with _lock:
        m = {"id": _next, "role": role, "text": text, "buttons": list(buttons or []), "source": source,
             "at": round(time.time())}
        _next += 1
        _log.append(m)
        del _log[:-MAX]
        return m


def since(after: int = 0) -> list[dict]:
    with _lock:
        return [m for m in _log if m["id"] > after]


def reset():
    with _lock:
        _log.clear()


def incoming(phone: str, m: dict):
    """A WhatsApp message from the owner, as the app shows it: its words, or what it was (a voice note, a photo)."""
    if not is_owner(phone):
        return
    text = (m.get("text") or "").strip()
    kind = m.get("kind") or ""
    if kind == "audio" and not m.get("button_id"):
        text = "Voice note" + (f": {text}" if text else "")
    elif kind == "image":
        text = "Photo" + (f": {text}" if text else "")
    elif kind == "document":
        text = "Document" + (f": {text}" if text else "")
    add("me", text or "(message)")


def outgoing(body: dict):
    """A WhatsApp message Oiva is sending (the Cloud API body): kept when it goes to the owner."""
    if not is_owner(body.get("to")):
        return
    kind = body.get("type")
    if kind == "text":
        add("oiva", body.get("text", {}).get("body", ""))
    elif kind == "interactive":
        it = body.get("interactive", {})
        text = it.get("body", {}).get("text", "")
        action = it.get("action", {})
        buttons = [{"label": b.get("reply", {}).get("title", "")} for b in action.get("buttons", [])]
        if it.get("type") == "cta_url":
            p = action.get("parameters", {})
            buttons = [{"label": p.get("display_text", ""), "url": p.get("url", "")}]
        add("oiva", text, [b for b in buttons if b["label"]])
