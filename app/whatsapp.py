"""WhatsApp Cloud API (Meta): webhook parsing, signature check, and sending messages."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time

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


# ---------------- Meta's limits, checked before anything is sent ----------------
# developers.facebook.com/docs/whatsapp/cloud-api/messages (text, reply buttons, cta_url, list, reaction)
TEXT_MAX = 4096
BODY_MAX = 1024  # reply buttons and cta_url; a list message body may be 4096
HEADER_MAX = FOOTER_MAX = 60
BUTTONS_MAX = 3
TITLE_MAX = 20  # reply button title, cta_url display_text, list button
BUTTON_ID_MAX = 256
ROWS_MAX = SECTIONS_MAX = 10
ROW_TITLE_MAX = SECTION_TITLE_MAX = 24
ROW_DESC_MAX = 72
ROW_ID_MAX = 200
OVERFLOW_BODY = "👇"  # the interactive part of a body whose last paragraph alone was too long to carry the buttons
_DEV_HOSTS = ("http://localhost", "http://127.0.0.1")
_EMOJI_MODIFIERS = re.compile("[️⃣\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F]")


def short(text: str, limit: int = TITLE_MAX) -> str:
    """A label that fits: cut with "…" so the cut shows."""
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def single_emoji(e: str) -> bool:
    """One emoji, possibly with a skin tone, variation selector, ZWJ sequence or flag. "" removes a reaction."""
    if e == "":
        return True
    for part in _EMOJI_MODIFIERS.sub("", e).split("‍"):
        flag = len(part) == 2 and all("\U0001F1E6" <= c <= "\U0001F1FF" for c in part)
        if not (flag or (len(part) == 1 and ord(part) > 0x2000 and not part.isalnum())):
            return False
    return True


def _len(errs: list, what: str, value, limit: int, required: bool = True):
    value = value if isinstance(value, str) else ""
    if required and not value.strip():
        errs.append(f"{what} is empty")
    elif len(value) > limit:
        errs.append(f"{what} is {len(value)} chars, max {limit}")


def _validate_interactive(i: dict, errs: list):
    itype, action = i.get("type"), i.get("action") or {}
    _len(errs, "body", (i.get("body") or {}).get("text"), TEXT_MAX if itype == "list" else BODY_MAX)
    if (i.get("header") or {}).get("type") == "text":
        _len(errs, "header", i["header"].get("text"), HEADER_MAX)
    if "footer" in i:
        _len(errs, "footer", (i["footer"] or {}).get("text"), FOOTER_MAX)
    if itype == "button":
        buttons = [b.get("reply") or {} for b in action.get("buttons") or []]
        if not 1 <= len(buttons) <= BUTTONS_MAX:
            errs.append(f"{len(buttons)} reply buttons, 1 to {BUTTONS_MAX} allowed")
        for b in buttons:
            _len(errs, f"button title {b.get('title')!r}", b.get("title"), TITLE_MAX)
            _len(errs, f"button id {str(b.get('id'))[:40]!r}", b.get("id"), BUTTON_ID_MAX)
        for what in ("id", "title"):
            if len({b.get(what) for b in buttons}) < len(buttons):
                errs.append(f"button {what}s are not unique")
    elif itype == "cta_url":
        prm = action.get("parameters") or {}
        _len(errs, f"cta_url display_text {prm.get('display_text')!r}", prm.get("display_text"), TITLE_MAX)
        url = prm.get("url") or ""
        if not url.startswith(("https://",) + _DEV_HOSTS):
            errs.append(f"cta_url url {url[:60]!r} is not https")
    elif itype == "list":
        _len(errs, "list button", action.get("button"), TITLE_MAX)
        sections = action.get("sections") or []
        rows = [r for s in sections for r in s.get("rows") or []]
        if not 1 <= len(sections) <= SECTIONS_MAX:
            errs.append(f"{len(sections)} list sections, 1 to {SECTIONS_MAX} allowed")
        if not 1 <= len(rows) <= ROWS_MAX:
            errs.append(f"{len(rows)} list rows, 1 to {ROWS_MAX} allowed")
        for s in sections:
            _len(errs, "section title", s.get("title"), SECTION_TITLE_MAX, required=len(sections) > 1)
        for r in rows:
            _len(errs, f"row title {r.get('title')!r}", r.get("title"), ROW_TITLE_MAX)
            _len(errs, "row id", r.get("id"), ROW_ID_MAX)
            _len(errs, "row description", r.get("description"), ROW_DESC_MAX, required=False)
        if len({r.get("id") for r in rows}) < len(rows):
            errs.append("row ids are not unique")
    else:
        errs.append(f"unknown interactive type {itype!r}")


def validate_payload(body: dict) -> list[str]:
    """Everything in a Cloud API message body that Meta would reject; [] = fine. Pure: no I/O."""
    if "status" in body:  # read receipt + typing indicator
        return [] if body.get("message_id") else ["status without message_id"]
    errs: list[str] = []
    if not body.get("to"):
        errs.append("no recipient")
    kind = body.get("type")
    if kind == "text":
        _len(errs, "text body", (body.get("text") or {}).get("body"), TEXT_MAX)
    elif kind == "interactive":
        _validate_interactive(body.get("interactive") or {}, errs)
    elif kind == "reaction":
        r = body.get("reaction") or {}
        if not r.get("message_id"):
            errs.append("reaction without message_id")
        if not single_emoji(r.get("emoji") or ""):
            errs.append(f"reaction {r.get('emoji')!r} is not a single emoji")
    elif kind == "template":  # on its way out: Birky only writes inside the 24-hour window
        if not (body.get("template") or {}).get("name"):
            errs.append("template without name")
    else:
        errs.append(f"unknown message type {kind!r}")
    return errs


def split_text(text: str, limit: int = TEXT_MAX) -> list[str]:
    """Pieces of at most `limit` chars, cut at a blank line, a line end or a space when there is one."""
    out, rest = [], text.strip()
    while len(rest) > limit:
        cut = max(rest.rfind(sep, 0, limit + 1) for sep in ("\n\n", "\n", " "))
        cut = cut if cut > limit // 2 else limit
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].strip()
    return out + [rest] if rest else out


def fit_body(text: str, limit: int = BODY_MAX) -> tuple[list[str], str]:
    """(text messages to send first, interactive body). The body keeps the last paragraphs that fit; when even the
    last one is too long, all of it goes first as text and the buttons ride on a short pointer."""
    text = text.strip()
    if len(text) <= limit:
        return [], text
    paras = re.split(r"\n\s*\n", text)
    if len(paras[-1].strip()) > limit:
        return split_text(text), OVERFLOW_BODY
    i = len(paras) - 1
    while i > 0 and len("\n\n".join(paras[i - 1:])) <= limit:
        i -= 1
    return split_text("\n\n".join(paras[:i])), "\n\n".join(paras[i:]).strip()


def _repair_action(itype: str, action: dict) -> tuple[str, dict]:
    if itype == "button" and len(action.get("buttons") or []) > BUTTONS_MAX:  # more than 3 quick replies: a list
        rows = [{"id": b["reply"]["id"], "title": short(b["reply"]["title"], ROW_TITLE_MAX)} for b in action["buttons"]]
        if len(rows) > ROWS_MAX:
            log.error("dropping %d list rows over %d: %s", len(rows) - ROWS_MAX, ROWS_MAX, rows[ROWS_MAX:])
        return "list", {"button": "Choose", "sections": [{"rows": rows[:ROWS_MAX]}]}
    if itype == "button":
        return itype, {**action, "buttons": [{**b, "reply": {**b["reply"], "title": short(b["reply"].get("title") or "?")}}
                                             for b in action.get("buttons") or []]}
    if itype == "cta_url":
        prm = action.get("parameters") or {}
        return itype, {**action, "parameters": {**prm, "display_text": short(prm.get("display_text") or "Open")}}
    if itype == "list":
        left, sections = ROWS_MAX, []
        for s in (action.get("sections") or [])[:SECTIONS_MAX]:
            rows = [{**r, "title": short(r.get("title") or "?", ROW_TITLE_MAX)}
                    | ({"description": short(r["description"], ROW_DESC_MAX)} if r.get("description") else {})
                    for r in (s.get("rows") or [])[:left]]
            left -= len(rows)
            if rows:
                sections.append({**s, "rows": rows} | ({"title": short(s["title"], SECTION_TITLE_MAX)}
                                                       if s.get("title") else {}))
        return itype, {**action, "button": short(action.get("button") or "Choose"), "sections": sections}
    return itype, action


def repair(body: dict) -> list[dict]:
    """Bodies Meta accepts, made from one it would reject, without losing the message: long texts are split, a long
    interactive body goes first as text, labels are cut with "…", more than 3 quick replies become a list."""
    kind = body.get("type")
    if kind == "text":
        return [{**body, "text": {**body["text"], "body": p}} for p in split_text(body["text"].get("body") or "")] or [body]
    if kind != "interactive":
        return [body]
    i = dict(body["interactive"])
    itype, action = _repair_action(i.get("type"), i.get("action") or {})
    for part, limit in (("header", HEADER_MAX), ("footer", FOOTER_MAX)):
        if (i.get(part) or {}).get("text"):
            i[part] = {**i[part], "text": short(i[part]["text"], limit)}
    lead, text = fit_body((i.get("body") or {}).get("text") or "", TEXT_MAX if itype == "list" else BODY_MAX)
    i.update(type=itype, action=action, body={"text": text or OVERFLOW_BODY})
    return ([{"to": body.get("to"), "type": "text", "text": {"body": p, "preview_url": False}} for p in lead]
            + [{**body, "interactive": i}])


def _post(body: dict) -> dict | None:
    """Send one message. An invalid body is logged and repaired (see repair) instead of being rejected by Meta."""
    problems = validate_payload(body)
    if not problems:
        return _send(body)
    log.warning("WA payload invalid (%s), repairing: %s", "; ".join(problems), str(body)[:500])
    parts = repair(body)
    still = [p for b in parts for p in validate_payload(b)]
    if still:
        log.error("WA payload still invalid after repair, sending anyway: %s", "; ".join(still))
    out = None
    for b in parts:
        out = _send(b) or out
    return out


def _send(body: dict) -> dict | None:
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
    """A text message; one longer than Meta's 4096 chars goes as several, never cut off."""
    out = None
    for part in split_text(text) or [text]:
        out = _post({"to": to, "type": "text", "text": {"body": part, "preview_url": False}})
    return out


def _interactive(to: str, text: str, interactive: dict):
    """An interactive message; a body over 1024 chars sends its start first as text (fit_body)."""
    lead, body = fit_body(text)
    for part in lead:
        send_text(to, part)
    return _post({"to": to, "type": "interactive", "interactive": {**interactive, "body": {"text": body}}})


def send_url_button(to: str, text: str, label: str, url: str):
    """One call-to-action button that opens a page (pay / invest / connect). Label up to 20 chars."""
    return _interactive(to, text, {"type": "cta_url",
                                   "action": {"name": "cta_url", "parameters": {"display_text": label, "url": url}}})


def send_reply_buttons(to: str, text: str, buttons: list[tuple[str, str]]):
    """Up to 3 quick-reply buttons: [(id, title)], titles up to 20 chars. More is repaired in _post, not cut here."""
    return _interactive(to, text, {"type": "button", "action": {
        "buttons": [{"type": "reply", "reply": {"id": i, "title": t}} for i, t in buttons]}})


# ---------------- conversation feel and privacy (ideas from Moni) ----------------

BUBBLE_PAUSE_S = 0.8


def quick_reaction(text: str | None) -> str | None:
    """An instant receipt on the person's message: 👀 question, 🙏 thanks, 💰 money news, 👋 hello."""
    t = (text or "").lower()
    if re.search(r"\b(thanks|thank you|thx|kiitos|mersi)\b", t):
        return "🙏"
    if re.search(r"\b(salary|paid|bonus|raise|palkka)\b", t):
        return "💰"
    if re.match(r"^\s*(hi|hey|hello|moi|hei|terve|salut)\b", t) and len(t) < 25:
        return "👋"
    if "?" in t or re.match(r"^\s*(how|what|why|where|when|can|should|is|am|do)\b", t):
        return "👀"
    return None


def send_reaction(to: str, message_id: str, emoji: str):
    return _post({"to": to, "type": "reaction", "reaction": {"message_id": message_id, "emoji": emoji}})


def bubbles(text: str, max_bubbles: int = 3) -> list[str]:
    """Split a reply on blank lines into at most 3 chat bubbles (the last one takes the rest)."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(parts) <= max_bubbles:
        return parts or [text]
    return parts[: max_bubbles - 1] + ["\n\n".join(parts[max_bubbles - 1:])]


def send_bubbles(to: str, parts: list[str]):
    for i, p in enumerate(parts):
        if i:
            time.sleep(BUBBLE_PAUSE_S)
        send_text(to, p)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def redact(text: str) -> str:
    """Remove what the coach never needs before it reaches the AI: card numbers, IBANs, Finnish personal IDs."""
    t = re.sub(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b", "[IBAN removed]", text)
    t = re.sub(r"\b\d{6}[-+AaBCDEFYXWVU]\d{3}[0-9A-Ya-y]\b", "[personal ID removed]", t)
    return re.sub(r"\b\d(?:[ -]?\d){12,18}\b",
                  lambda m: "[card number removed]" if _luhn(re.sub(r"\D", "", m.group())) else m.group(), t)


def failed_statuses(payload: dict) -> list[dict]:
    """Meta accepts a send with 200 and reports a failed delivery later, as a status: {to, code, title}."""
    out = []
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            for st in ch.get("value", {}).get("statuses", []):
                if st.get("status") == "failed":
                    err = (st.get("errors") or [{}])[0]
                    out.append({"to": st.get("recipient_id", ""), "code": err.get("code"),
                                "title": str(err.get("title") or err.get("message") or "")[:120]})
    return out
