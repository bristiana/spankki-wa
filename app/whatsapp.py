"""WhatsApp Cloud API (Meta): webhook parsing, signature check, and sending messages."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

import httpx

log = logging.getLogger("whatsapp")
GRAPH = "https://graph.facebook.com/v25.0"


def configured() -> bool:
    return bool(os.environ.get("WA_TOKEN") and os.environ.get("WA_PHONE_NUMBER_ID"))


def verify_signature(raw: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, raw body)."""
    secret = os.environ.get("WA_APP_SECRET", "")
    if not secret:
        log.warning("WA_APP_SECRET not set: skipping signature check (dev only)")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def parse(payload: dict) -> list[dict]:
    """Incoming user messages, oldest first: {id, from, name, kind, text, button_id}."""
    out = []
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            if ch.get("field") != "messages":
                continue
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                kind = m.get("type")
                text, button_id = None, None
                if kind == "text":
                    text = m["text"]["body"]
                elif kind == "interactive":
                    r = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                    text, button_id = r.get("title"), r.get("id")
                elif kind == "button":
                    text, button_id = m["button"].get("text"), m["button"].get("payload")
                out.append({"id": m["id"], "from": m["from"], "name": names.get(m["from"], ""), "kind": kind,
                            "text": text, "button_id": button_id, "ts": int(m.get("timestamp", 0))})
    return sorted(out, key=lambda x: x["ts"])


def _post(body: dict) -> dict | None:
    if not configured():
        log.info("WA not configured, would send: %s", body)
        return None
    url = f"{GRAPH}/{os.environ['WA_PHONE_NUMBER_ID']}/messages"
    r = httpx.post(url, json={"messaging_product": "whatsapp", **body},
                   headers={"Authorization": f"Bearer {os.environ['WA_TOKEN']}"}, timeout=20)
    if r.status_code >= 400:
        log.error("WA send failed %s: %s", r.status_code, r.text[:500])
        return None
    return r.json()


def mark_read_typing(message_id: str):
    _post({"status": "read", "message_id": message_id, "typing_indicator": {"type": "text"}})


def send_text(to: str, text: str):
    return _post({"to": to, "type": "text", "text": {"body": text[:4096], "preview_url": False}})


def send_url_button(to: str, text: str, label: str, url: str):
    """One call-to-action button that opens a page (pay / invest / connect)."""
    return _post({"to": to, "type": "interactive", "interactive": {
        "type": "cta_url", "body": {"text": text[:1024]},
        "action": {"name": "cta_url", "parameters": {"display_text": label[:20], "url": url}}}})


def send_reply_buttons(to: str, text: str, buttons: list[tuple[str, str]]):
    """Up to 3 quick-reply buttons: [(id, title)]."""
    return _post({"to": to, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": text[:1024]},
        "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons[:3]]}}})
