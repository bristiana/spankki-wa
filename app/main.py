"""FastAPI app: WhatsApp webhook, bank-style confirm pages, web chat, demo triggers.

Run: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import bills, broker, guardrails, learn, onboarding, quiz, rules, voice
from . import whatsapp as wa
from .agent import Coach
from .engine import (BASKETS, DEMO_BANK, EXPLORE_GROUP_BY, EXPLORE_METRICS, EXPLORE_WHAT, FUNDS_BY_ID, MCC_LABELS, PERIODS,
                     RESERVE_NOTE_EUR, SPEND_CATEGORIES, Engine, merchant_codes)
from .tools import confirm_url, explore_query, public_url

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

# ---------------- Personas: the demo default and the other real customers judges can switch to ----------------
ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "data" / "personas"
STATE_DIR = ROOT / "data" / "state"  # one state file per extra persona: payments and goals never leak across
AINO = "CUST04831"  # the engine's own default (data/persona.csv); still in the switcher
PERSONA_COOKIE = "kurre_as"


def load_personas(path: Path = PERSONA_DIR / "index.json") -> dict[str, dict]:
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    out = {r["id"]: r for r in rows}
    out.setdefault(AINO, {"id": AINO, "name": "Aino", "file": None, "age": 24, "label": "First job, 24",
                          "description": ""})
    return out


PERSONAS = load_personas()
# The hero (index.json "hero": true, picked by rule in scripts/extract_personas.py) is who people meet first: the web
# chat, /app, the widgets, the landing page and the WhatsApp demo phone. DEMO_PERSONA=<CUSTID> picks another one.
HERO = next((p["id"] for p in PERSONAS.values() if p.get("hero")), AINO)


def pick_default(env: str | None) -> str:
    return env if env in PERSONAS else HERO


def default_state_path(persona: str) -> Path:
    """Aino keeps data/state.json; any other default gets its own file, so a state saved for one customer (payments,
    goals) is never replayed on another one's account."""
    return ROOT / "data" / "state.json" if persona == AINO else STATE_DIR / f"{persona}.json"


DEFAULT_PERSONA = pick_default(os.environ.get("DEMO_PERSONA"))
DEMO_NAME = PERSONAS[DEFAULT_PERSONA]["name"]


def persona_engine(persona: str, today: date, state_path: Path | None = None) -> Engine:
    """A persona's engine from index.json (Aino's rows are data/persona.csv, the engine's default)."""
    p = PERSONAS[persona]
    return Engine(csv_path=PERSONA_DIR / p["file"] if p.get("file") else None, today=today,
                  state_path=state_path or STATE_DIR / f"{persona}.json", age=p.get("age"))


engine = persona_engine(DEFAULT_PERSONA, date.fromisoformat(os.environ.get("DEMO_TODAY", "2026-10-06")),
                        default_state_path(DEFAULT_PERSONA))
_coach: Coach | None = None
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="S-Pankki Chat")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
seen: set[str] = set()
owner: dict[str, str] = {}
inbox: dict[str, list[str]] = {}  # web chat session -> confirmations waiting to be shown
last_seen: dict[str, float] = {}  # WhatsApp number -> when they last wrote (free-form replies only within 24 h)
WINDOW_S = 24 * 3600
# Quick-reply buttons that turn into a question for the coach.
ASK = {
    "ask:tight": "My budget is tight until payday. What can I do?",
    "ask:goal": "Help me set up a savings goal.",
    "ask:invest": "I'd like to invest part of my salary. What are my options?",
    "ask:unusual": "Tell me more about that big payment and how it fits my month.",
    "ask:pace": "My savings goal is behind plan. How can I catch up?",
    "ask:week": "What did I spend on in the last 7 days?",
    "ask:bills": "What are my regular payments?",
    "ask:everyday": "Where does my 'slips through your fingers' money go? Explain that basket with my last month.",
}
names: dict[str, str] = {}  # WhatsApp number -> first name from the profile
web_sessions: set[str] = set()  # web chat sessions that get proactive messages in their inbox


def first_name(profile: str) -> str:
    """'Cristiana💕 B.' -> 'Cristiana'. Falls back to the demo name."""
    word = (profile or "").split()[0] if (profile or "").split() else ""
    clean = "".join(ch for ch in word if ch.isalpha() or ch in "-'")
    return clean or DEMO_NAME


def name_of(phone: str | None) -> str:
    return names.get(phone or "", DEMO_NAME)


def coach(persona: str | None = None) -> Coach:
    """The default persona's coach (WhatsApp and the web app's default) or, for a judge's pick, one coach per persona: its own
    engine and its own conversation histories. All share one API client."""
    global _coach
    if _coach is None:
        _coach = Coach(engine)
    if persona in (None, DEFAULT_PERSONA) or persona not in PERSONAS:
        return _coach
    with _persona_lock:
        if persona not in _coaches:
            _coaches[persona] = Coach(engine_for(persona), client=_coach.client)
        return _coaches[persona]


# ---------------- Personas: judges switch the web app to other real customers ----------------
_engines: dict[str, Engine] = {}
_coaches: dict[str, Coach] = {}
_persona_lock = threading.RLock()  # coach() builds the engine under the same lock
session_persona: dict[str, str] = {}  # web chat session -> the persona it last chatted as (for proactive messages)


def valid_persona(pid: str | None) -> str:
    return pid if pid in PERSONAS else DEFAULT_PERSONA


def persona_of(request: Request) -> str:
    """?as=<CUSTID> (links) wins over the cookie; anything not in index.json is the default persona."""
    return valid_persona(request.query_params.get("as") or request.cookies.get(PERSONA_COOKIE))


def engine_for(persona: str | None) -> Engine:
    """The persona's engine, created on first use. The default persona is the global `engine`."""
    if persona in (None, DEFAULT_PERSONA) or persona not in PERSONAS:
        return engine
    with _persona_lock:
        if persona not in _engines:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            _engines[persona] = persona_engine(persona, engine.today)
        return _engines[persona]


def name_for(persona: str | None) -> str:
    return PERSONAS[valid_persona(persona)]["name"]


def persona_ctx(persona: str) -> dict:
    return {"persona": PERSONAS[persona], "personas": list(PERSONAS.values())}


@app.middleware("http")
async def remember_persona(request: Request, call_next):
    """A link with ?as= also switches the session: the choice is kept in a cookie."""
    response = await call_next(request)
    if "as" in request.query_params:
        response.set_cookie(PERSONA_COOKIE, persona_of(request), max_age=30 * 24 * 3600, samesite="lax", httponly=True)
    return response


def nice_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %d %b") if iso else "not known yet"


def money(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ")


_NB = re.compile(r"(?<=\d) (?=\d{3}(?!\d))|(?<=\d) (?=€)")


def nb(text) -> str:
    """Web pages: an amount never breaks across lines ("1 313.89 €" keeps its spaces non-breaking)."""
    return _NB.sub(" ", str(text))


def money_nb(x: float) -> str:
    return nb(money(x))


templates.env.filters["nb"] = nb
templates.env.filters["money"] = money_nb


def connected(phone: str) -> bool:
    """The demo phone counts as connected unless it switched off (STOP): a redeploy or demo reset starts a fresh
    container with empty state, and the demo owner should not have to reconnect after every one."""
    if phone in engine.state.setdefault("connected", {}):
        return True
    demo = os.environ.get("DEMO_PHONE", "")
    return bool(phone) and phone == demo and phone not in engine.state.get("switched_off", {})


# ---------------- WhatsApp ----------------
@app.get("/webhook")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == os.environ.get("WA_VERIFY_TOKEN"):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403)


@app.post("/webhook")
async def incoming(request: Request, bg: BackgroundTasks):
    raw = await request.body()
    if not wa.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403, "bad signature")
    payload = await request.json()
    for f in wa.failed_statuses(payload):
        log.warning("delivery failed to %s: %s %s", str(f["to"])[-4:], f["code"], f["title"])
    done = engine.state.setdefault("wa_seen", [])  # survives a redeploy, so Meta's redeliveries are not answered twice
    new = [m for m in wa.parse(payload) if m["id"] not in seen and m["id"] not in done]
    for m in new:
        seen.add(m["id"])
        done.append(m["id"])
        last_seen[m["from"]] = time.time()
        engine.state.setdefault("wa_last", {})[m["from"]] = last_seen[m["from"]]
    if new:
        del done[:-300]
        engine._save()
    if new:
        bg.add_task(lambda: [handle_message(m) for m in new])  # one delivery, answered in order
    return {"ok": True}


def window_open(phone: str | None) -> bool:
    last = max(last_seen.get(phone, 0), engine.state.get("wa_last", {}).get(phone, 0)) if phone else 0
    return bool(phone) and time.time() - last < WINDOW_S


def send_reply(to: str, reply, message_id: str | None = None):
    """A reaction on their message first (when the coach picked one), then short bubbles like a person typing
    ("---" or blank lines split them); a button rides on the last bubble. A reply that is only a reaction sends
    just the reaction."""
    reaction = getattr(reply, "reaction", None)
    if reaction and message_id:
        wa.send_reaction(to, message_id, reaction)
    if not (reply.text or "").strip():
        if reply.buttons:  # nothing to say but something to tap
            for b in reply.buttons:
                wa.send_url_button(to, wa.OVERFLOW_BODY, b["label"], b["url"])
        elif not (reaction and message_id):
            wa.send_text(to, "👍")
        return
    parts = wa.bubbles(reply.text)
    if reply.buttons:
        wa.send_bubbles(to, parts[:-1])
        first, *rest = reply.buttons
        wa.send_url_button(to, parts[-1], first["label"], first["url"])
        for k, b in enumerate(rest, start=2):  # e.g. several links, each with its own PIN
            wa.send_url_button(to, f"{k}/{len(reply.buttons)}", b["label"], b["url"])
    else:
        wa.send_bubbles(to, parts)


STOP_WORDS = {"stop", "unsubscribe", "delete my data", "switch off", "lopeta"}


def switch_off(to: str):
    """Consent withdrawn: forget the connection, the chat history and the name."""
    engine.state.setdefault("connected", {}).pop(to, None)
    engine.state.setdefault("switched_off", {})[to] = engine.today.isoformat()
    engine._save()
    coach().reset(to)
    onboarding.forget(engine, to)
    quiz.forget(engine, to)
    learn.forget(engine, to)
    names.pop(to, None)
    wa.send_text(to, "Done ✅ Birky is switched off for this number and our chat history is deleted. "
                     "Your bank data never left S-Pankki. Write *Hi* any time to start again.")


OPEN_APP = "Open in S-mobiili"  # the button on a prepared money move (a WhatsApp cta_url label: max 20 chars)


def button_reply(user: str, bid: str, web: bool = False, e: Engine | None = None) -> dict | None:
    """Direct answers to quick-reply buttons (no AI needed). None = not a direct action."""
    q = "?web=1" if web else ""
    e = e or engine
    if bid.startswith("pay:"):
        p = e.create_payment(bid[4:])
        owner[p["pending_id"]] = user
        return {"text": f"Here's your *{p['name'].lower()}* payment of *{money(p['amount'])}*, filled in and ready "
                        "in S-mobiili. Check it and confirm with Face ID.",
                "url": (OPEN_APP, confirm_url("pay", p["pending_id"]) + q)}
    if bid.startswith("resume:") and bid != "resume:later":  # "Finish it" on a confirmation left open yesterday
        p = e.pending(bid[7:])
        if not p or p["status"] != "pending":
            return {"text": "That one's already done ✅"}
        owner[p["pending_id"]] = user
        path = {"payment": "pay", "investment": "invest", "saving": "save", "pause_investing": "stop"}[p["kind"]]
        return {"text": f"Here it is again: *{p['name']}*, {money(p['amount'])}. It's ready in S-mobiili: "
                        "confirm with Face ID.",
                "url": (OPEN_APP, confirm_url(path, p["pending_id"]) + q)}
    if bid.startswith("later:"):
        note_later(e, user, bid)
        if bid == "later:unusual":
            return {"text": "Great, enjoy it 🙂 I'll keep an eye on your safe-to-spend."}
        if bid == "later:goal":
            return {"text": "Okay 👍 I'll look at your pace again next week."}
        if bid == "later:bills":
            return {"text": "Okay 👍 I'll keep an eye on your bills."}
        return {"text": "Sure ⏰ I'll check back with you tomorrow."}
    if bid == "pause:investing":
        p = e.create_pause_investing()
        owner[p["pending_id"]] = user
        nxt = f" The next one would go out on {nice_date(p['next_charge'])}." if p["next_charge"] else ""
        return {"text": f"Pause your *monthly investing* of about *{money(p['amount'])}*?{nxt} What you've invested "
                        "so far stays invested. It's ready in S-mobiili: confirm with Face ID.",
                "url": (OPEN_APP, confirm_url("stop", p["pending_id"]) + q)}
    if bid.startswith("keep:"):
        return {"text": "Okay, your monthly investing stays on 👍 Paying yourself first, on autopilot."}
    if bid.startswith("remind:"):
        c = next((x for x in e.commitments()["regular_payments"] if x["id"] == bid[7:]), None)
        if c and c["next_expected"]:
            return {"text": f"Okay ⏰ I'll message you the day before your {c['name'].lower()} is due "
                            f"({nice_date(c['next_expected'])})."}
        return {"text": "Your utility bills come on different days, so there's no date to remind you of. I'll tell "
                        "you when one is well above your usual range."}
    if bid == "learn:buffer" or bid.startswith("learn:save:"):  # a lesson's action: a goal, then the usual save
        goal_id, amount = learn.goal_for(e, bid)
        if amount < 1:
            g = e.state["goals"][goal_id]
            return {"text": f"Your *{g['name']}* goal of *{money(g['target'])}* is set 🌰 Nothing is free to move "
                            "before payday, so the first saving can wait for payday."}
        bid = f"save:{goal_id}:{amount:.0f}"
    if bid.startswith("save:"):
        _, goal_id, amount = bid.split(":")
        p = e.create_saving(goal_id, float(amount))
        owner[p["pending_id"]] = user
        goal = p["name"].split(": ", 1)[1]
        return {"text": f"Move *{money(p['amount'])}* to your savings pocket for *{goal}*? "
                        "It's ready in S-mobiili: confirm with Face ID.",
                "url": (OPEN_APP, confirm_url("save", p["pending_id"]) + q)}
    if bid == "quiz:money":
        return {"text": "Your three baskets, balance and safe to spend, all in one place 📊",
                "url": ("Open My money", f"{public_url()}/app{q}")}
    return None


def flow_context(e: Engine, user: str) -> str | None:
    """What the coach should know when a question comes in the middle of the interview or the basket game: the
    waiting question is sent again right after the reply, so the reply must not end with a question of its own."""
    waiting = onboarding.reask(e, user)
    if waiting:
        return ("The person is in the short first-chat interview (no AI). Right after your reply, the app asks them "
                f"again: \"{waiting.text}\" Answer only their side question, in a few lines, and end without a "
                "question or offer of your own.")
    lesson = learn.waiting_lesson(e, user)
    if lesson:
        return ("The person paused the short money course (no AI) at this lesson: \"" + lesson + "\" Their "
                "question may be about it. Right after your reply, the app tells them how to pick the course up. "
                "Answer only their question, in a few lines, and end without a question or offer of your own.")
    paused = quiz.waiting_question(e, user)
    if paused:
        return ("The person paused the basket game (no AI) at this question: \"" + paused + "\" Right after your "
                "reply, the app tells them how to pick it up. Answer only their question, in a few lines, and end "
                "without a question or offer of your own.")
    return None


LATER = re.compile(r"^\W*(maybe later|later|not now|not today|another time|some other time|ask me later|"
                   r"tomorrow|maybe tomorrow|myöhemmin|ehkä myöhemmin|senare)\W*$", re.I)


def note_later(e: Engine, user: str, key: str, offer: str | None = None):
    """A "maybe later": the open thread Birky comes back to once, the next day (rules.open_threads)."""
    e.state.setdefault("later", {})[user] = {"key": key, "offer": offer, "at": time.time()}
    e._save()


def note_chat_later(e: Engine, user: str, text: str, last_said: str):
    """"maybe later" typed right after Birky offered something: remember the offer (its last bubble, a question)."""
    if not LATER.match(text or ""):
        return
    offer = (wa.bubbles(last_said)[-1] if last_said.strip() else "").strip()
    if offer.endswith("?") and len(offer) <= 300:
        import hashlib
        note_later(e, user, "chat:" + hashlib.sha1(offer.encode()).hexdigest()[:8], offer)


def ask_text(e: Engine, user: str, bid: str) -> str | None:
    """The question a quick-reply button stands for (ASK), or "Yes, let's do it" on yesterday's maybe-later."""
    if bid == "resume:later":
        offer = (e.state.get("later", {}).get(user) or {}).get("offer")
        return f'Yes, let\'s do it now. You offered: "{offer}"' if offer else "Yes, let's pick up where we left off."
    return ASK.get(bid)


def touch(e: Engine, user: str):
    """When they last wrote: open threads come back only after a quiet day, inside the 24-hour window."""
    e.state.setdefault("last_msg", {})[user] = time.time()


def coach_context(e: Engine, user: str, text: str, extra: str | None = None) -> str | None:
    """What the coach should know right now: a waiting flow question, a hand-off to a person at S-Pankki."""
    kind = guardrails.escalation(text)
    parts = [flow_context(e, user), guardrails.escalation_context(kind) if kind else None, extra]
    return " ".join(p for p in parts if p) or None


def fresh_start(e: Engine, user: str, persona: str | None = None) -> str:
    """"/new" or "fresh start": the conversation history goes, running flows wait quietly, no follow-ups pending."""
    coach(persona).reset(user)
    for kind in ("learn", "quiz", "onboarding"):
        r = e.state.get(kind, {}).get(user)
        if r and not r.get("done") and r.get("step") != "done":
            r["paused"], r["note"] = True, False
    e.state.get("later", {}).pop(user, None)
    sent = e.state.setdefault("sent", {}).setdefault(user, {})
    for t in rules.open_threads(e, user, force=True):  # a clean slate: nothing from before comes back tomorrow
        sent[t["dedup_key"]] = e.today.isoformat()
    e._save()
    return guardrails.FRESH_REPLY


SECRET_CONTEXT = ("Their message had a {what} in it; it was removed before it reached you and the app has already "
                  "asked them to delete it. Don't mention it again; answer only the rest.")


def flow_turn(e: Engine, user: str, text: str, bid: str = "") -> onboarding.Turn | None:
    """The no-AI flows, in order: the Learn course, the basket game, the interview. None = the coach answers."""
    return learn.turn(e, user, text, bid) or quiz.turn(e, user, text, bid) or onboarding.turn(e, user, text, bid)


def remember(persona: str | None, user: str, said: str, t: onboarding.Turn):
    """Interview and game turns go into the coach's history, so a follow-up knows what the person just saw."""
    try:
        c = coach(persona)
        if hasattr(c, "remember"):
            c.remember(user, said, "\n\n".join(t.lead + [t.text]))
    except Exception:  # the history is a nicety: the interview and the game work without it
        log.exception("could not remember a flow turn")


def send_turn(to: str, t: onboarding.Turn):
    """An interview message on WhatsApp: lead lines, then the question with its reply buttons."""
    wa.send_bubbles(to, t.lead)
    if t.buttons:
        wa.send_reply_buttons(to, t.text, t.buttons)
    else:
        wa.send_text(to, t.text)


def notify(pid: str, text: str):
    """Tell whoever asked for this money move: a WhatsApp number or a web chat session."""
    to = owner.get(pid) or os.environ.get("DEMO_PHONE")
    if not to:
        return
    if to.startswith("web:"):
        inbox.setdefault(to, []).append(text)
    else:
        wa.send_text(to, text)


def wa_back(request: Request) -> str:
    return "" if request.query_params.get("web") else os.environ.get("WA_DISPLAY_NUMBER", "")


def ask(persona: str | None, user: str, text: str, name: str, context: str | None = None, may_pay: bool = True,
        photo: bool = False):
    """coach.ask, telling the bill tool whose turn it is and whether it may prepare the bill payment."""
    hint = None if photo else bills.context_line(engine_for(persona), user)
    token = bills.turn.set((user, may_pay))
    try:
        return coach(persona).ask(user, text, name=name, context=" ".join(x for x in (context, hint) if x) or None)
    finally:
        bills.turn.reset(token)


def ask_about_bill(persona: str | None, user: str, bill: dict, caption: str | None, name: str):
    """A bill photo just read: explain it, or, only when the caption asks to pay, prepare the payment. Any other
    money document (a statement, a receipt, a payslip) is only explained: nothing to pay, so no payment tool."""
    if bills.is_document(bill):
        e = engine_for(persona)
        context = " ".join(x for x in (bills.EXPLAIN_DOC, flow_context(e, user)) if x)
        return ask(persona, user, bills.doc_text(bill, caption), name, context, may_pay=False, photo=True)
    e, pay = engine_for(persona), bills.asks_to_pay(caption)
    context = " ".join(x for x in (bills.PAY if pay else bills.EXPLAIN, flow_context(e, user)) if x)
    return ask(persona, user, bills.coach_text(e, bill, caption), name, context, may_pay=pay, photo=True)


def bill_photo(to: str, name: str, m: dict):
    try:
        bill = bills.read(engine, to, getattr(coach(), "client", None), bills.download(m["media_id"]), None)
    except bills.BillError as e:
        wa.send_text(to, str(e))
        return
    except Exception as e:  # no WA token, Meta or the AI down: say so plainly, keep the details in the log
        log.warning("bill photo %s failed: %s", m["media_id"], e)
        wa.send_text(to, "Sorry, I couldn't open that photo. Could you send it again in a moment?")
        return
    problem = bills.reply_problem(bill)
    if problem:
        wa.send_text(to, problem)
        return
    reply = ask_about_bill(None, to, bill, m["text"] and wa.redact(m["text"]), name)
    for b in reply.buttons:
        owner[b["url"].rsplit("/", 1)[-1]] = to
    send_reply(to, reply, m["id"])


def handle_message(m: dict):
    to, name = m["from"], first_name(m["name"])
    names[to] = name
    try:
        wa.mark_read_typing(m["id"])
        if m.get("kind") == "audio" and m.get("media_id") and not m["text"]:  # a voice note: handled like typed text
            try:
                m["text"] = voice.to_text(m["media_id"])
            except voice.VoiceError as e:
                wa.send_text(to, str(e))
                return
            wa.send_text(to, f"🎙️ _{wa.short(m['text'].replace('_', ' '), 300)}_")  # what Birky heard, so a mishearing is visible
        word = (m["text"] or "").strip().lower().strip(".!")
        # mid-game or mid-course, a bare "stop" pauses it
        stop = word in ("stop", "lopeta") and (quiz.active(engine, to) or learn.active(engine, to))
        if word in STOP_WORDS and not stop:
            switch_off(to)
            return
        bid = m.get("button_id") or ""
        secret = [] if bid else guardrails.secrets(m["text"])
        if secret:  # a card number, PIN or code: never to the AI, and a kind "please delete that"
            wa.send_reaction(to, m["id"], "🔒")
            wa.send_bubbles(to, wa.bubbles(guardrails.secret_reply(secret)))
        if not connected(to):
            wa.send_url_button(
                to,
                f"Hi {name} 👋 I'm *Birky* 🐿️, your S-Pankki money coach. I answer questions about your spending, remind you "
                "before bills and help you save.\n\nTo start, connect your S-Pankki account. You choose what I can see, "
                "and you can switch me off any time.",
                "Connect S-Pankki", f"{public_url()}/connect?u={to}")
            return
        if onboarding.RESTART.match(m["text"] or ""):  # the way back to the welcome, e.g. for the demo phone
            welcome(to)
            return
        if guardrails.FRESH.match(m["text"] or ""):
            wa.send_text(to, fresh_start(engine, to))
            return
        if m.get("kind") in bills.KINDS and m.get("media_id"):  # a bill photo: explain it, pay only when asked
            bill_photo(to, name, m)
            return
        learn.step_away(engine, to, bid)
        try:
            direct = button_reply(to, bid)
        except ValueError as e:  # e.g. tapping "Pay rent now" again after paying
            direct = {"text": f"I can't do that: {e}"}
        if direct:
            if direct.get("url"):
                wa.send_url_button(to, direct["text"], direct["url"][0], direct["url"][1])
            else:
                wa.send_text(to, direct["text"])
            return
        if m["text"] is None:
            wa.send_text(to, "I can read text, voice notes, photos, PDFs, Word, Excel and CSV files, but not this one yet. "
                             "What would you like to know?")
            return
        asked = ask_text(engine, to, bid)
        text = asked or wa.redact(m["text"])
        extra = None
        if secret:
            text = guardrails.leftover_question(text)
            if not text:
                return
            extra = SECRET_CONTEXT.format(what=secret[0])
        if text.strip().lower() in ("my data", "download my data", "export"):
            wa.send_url_button(to, "Your data, yours to keep (MyData): every transaction and what Birky "
                                   "calculated from it, as a file.", "Download my data", f"{public_url()}/app/export.json")
            return
        if not asked:
            t = flow_turn(engine, to, text, bid)
            if t:
                if stop:
                    t.text += "\n\nTo switch me off completely, write *STOP* again."
                send_turn(to, t)
                remember(None, to, m["text"] or text, t)
                return
        c = coach()
        if hasattr(c, "last_said"):
            note_chat_later(engine, to, text, c.last_said(to))
        reply = ask(None, to, text, name, coach_context(engine, to, text, extra))
        for b in reply.buttons:
            if "chart" not in b:
                owner[b["url"].rsplit("/", 1)[-1]] = to
        send_reply(to, reply, None if secret else m["id"])
        for note in (quiz.paused_note(engine, to), learn.paused_note(engine, to)):  # how to get back to it
            if note:
                wa.send_text(to, note)
        again = onboarding.reask(engine, to)  # a side question mid-interview: the waiting question comes back
        if again:
            send_turn(to, again)
    except Exception:
        log.exception("handling message failed")
        wa.send_text(to, "Sorry, something went wrong on my side. Try again in a minute.")


# ---------------- Connect (consent) ----------------
@app.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, u: str = ""):
    return templates.TemplateResponse(request, "connect.html", {"u": u, "done": connected(u) if u else False,
                                                                 "linked": engine.linked_bank()})


@app.get("/connect/bank", response_class=HTMLResponse)
def bank_page(request: Request, u: str = ""):
    """PSD2 account information consent for a second bank. Mock: the bank and its data are synthetic."""
    return templates.TemplateResponse(request, "bank.html", {"u": u, "bank": DEMO_BANK, "linked": engine.linked_bank()})


@app.post("/connect/bank", response_class=HTMLResponse)
def bank_submit(request: Request, u: str = Form(""), scope: list[str] = Form([])):
    if "accounts" not in scope:
        return RedirectResponse(f"/connect/bank?u={u}&err=1", status_code=303)
    f = engine.link_bank(scope)
    bill = f["next_card_bill"]
    return templates.TemplateResponse(request, "done.html", {
        "title": f"{DEMO_BANK['name']} linked",
        "lines": [f"Savings {money(f['banks'][1]['accounts'][0]['balance_eur'])} · card to repay "
                  f"{money(bill['amount_eur'])} by {nice_date(bill['due_date'])}.",
                  "Birky now counts the card bill in safe to spend. Synthetic demo bank, no real data.",
                  "Ask Birky: \"Show my money across all my banks\"."],
        "wa": wa_back(request) if u else ""})


@app.post("/connect", response_class=HTMLResponse)
def connect_submit(request: Request, bg: BackgroundTasks, u: str = Form(""), scope: list[str] = Form([])):
    engine.state.setdefault("connected", {})[u] = {"scope": scope, "at": engine.today.isoformat()}
    engine.state.get("switched_off", {}).pop(u, None)
    engine._save()
    if u:
        bg.add_task(welcome, u)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Connected", "lines": ["Your S-Pankki account is connected.", "Go back to WhatsApp, your coach is ready."],
        "wa": wa_back(request)})


def welcome(to: str):
    """Right after Connect: the three baskets, the My money button, then the first interview question."""
    t = onboarding.start(engine, to)
    wa.send_bubbles(to, t.lead)
    wa.send_url_button(to, "Your whole money picture, any time 📊", "Open My money", f"{public_url()}/app")
    send_turn(to, onboarding.Turn(t.text, t.buttons))
    remember(None, to, "[connected S-Pankki]", t)


# ---------------- Confirm pages ----------------
def _pending_or_404(pid: str, kind: str) -> tuple[Engine, dict]:
    """The pending action and the engine of the persona that created it (not the viewer's current pick)."""
    for persona in PERSONAS:
        e = engine_for(persona)
        p = e.pending(pid)
        if p and p["kind"] == kind:
            return e, p
    raise HTTPException(404, "This link has expired.")


# ---------------- Friendly error pages: a browser gets a page, an API caller keeps JSON ----------------
CONFIRM_PATHS = ("/pay/", "/invest/", "/save/", "/stop/", "/s-mobiili/")
ERRORS = {
    "expired": ("This link has expired", "Payment and saving links from Birky work once, and this one is no longer valid. "
                                         "Nothing was paid or moved. Ask Birky again for a fresh link."),
    404: ("Page not found", "This page doesn't exist. It may have moved, or the link has a typo."),
    400: ("That didn't work", "Something in the link or the form wasn't right. Go back and try again."),
    500: ("Something went wrong on our side", "Nothing was paid or moved. Try again in a minute."),
}


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") and not request.url.path.startswith(("/api/", "/webhook", "/cron"))


def error_page(request: Request, status: int, key=None):
    title, text = ERRORS.get(key or status, ERRORS[500 if status >= 500 else 400])
    return templates.TemplateResponse(request, "error.html", {"title": title, "text": text, "wa": wa_back(request)},
                                      status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if not wants_html(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
    expired = exc.status_code == 404 and request.url.path.startswith(CONFIRM_PATHS)
    return error_page(request, exc.status_code, "expired" if expired else None)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path, exc_info=exc)
    if wants_html(request):
        return error_page(request, 500)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


def bad_pin(request: Request, path: str):
    web = "&web=1" if request.query_params.get("web") else ""
    return RedirectResponse(f"{path}?err=1{web}", status_code=303)


def not_done(request: Request, err: Exception):
    """The bank said no (bill already paid, not enough money): a page, not a 500."""
    return templates.TemplateResponse(request, "done.html", {
        "title": "Not done", "failed": True, "lines": [sentence(err)], "wa": wa_back(request)})


def sentence(err: Exception) -> str:
    msg = str(err)
    return msg[:1].upper() + msg[1:] + "."


def plain(msg: str) -> str:
    return msg.replace("✅ ", "").replace("*", "")


# One confirm for every way in (the PIN page and S-mobiili's Face ID): the same engine call, the same double-confirm
# and overdraft checks, the same message back to WhatsApp or the web chat. Each returns (what the engine did, the
# receipt title, the receipt lines) and raises ValueError when the bank says no.
def settle_payment(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    s = e.safe_to_spend()
    if done["first_time"]:
        notify(pid, f"✅ *{done['name']} paid*: {money(done['amount'])}\n"
                    f"Balance now {money(done['balance_eur'])}.\n"
                    f"Safe to spend until payday ({nice_date(s['next_payday']['date'])}): "
                    f"*{money(s['safe_to_spend_until_payday_eur'])}*, about {money(s['safe_per_day_eur'])} a day.")
    return done, "Payment sent", [f"{done['name']}: {money(done['amount'])}", f"Balance {money(done['balance_eur'])}"]


def settle_investment(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    if done.get("monthly"):
        msg = (f"✅ *Monthly saving started*: {money(done['amount'])} into {done['name']} every payday, "
               f"first on {nice_date(done['starts'])}. You can change or stop it any time.")
    else:
        msg = f"✅ *Invested* {money(done['amount'])} in {done['name']}. Balance now {money(done['balance_eur'])}."
        if done["first_time"] and broker.configured():
            order = broker.place(done["fund_id"], done["amount"])
            e.state.setdefault("orders", []).append({"pending_id": pid, "fund": done["name"],
                                                     "amount": done["amount"], **order})
            e._save()
            msg += "\n\n" + order_text(order)
    if done["first_time"]:
        notify(pid, msg)
    return done, "Done", [plain(msg)]


def settle_pause_investing(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    when = f" on {nice_date(done['next_charge'])}" if done.get("next_charge") else ""
    msg = (f"✅ *Monthly investing paused.* The deposit of about {money(done['amount'])} won't go out{when}. "
           f"That's {money(done['per_year_eur'])} a year staying on your account.\n"
           "What you've invested so far stays invested. Start again any time.")
    if done["first_time"]:
        notify(pid, msg)
    return done, "Investing paused", [plain(msg)]


def settle_saving(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    g = e.state["goals"][p["goal_id"]]
    pct = round(g["saved"] / g["target"] * 100)
    msg = (f"✅ *Saved {money(done['amount'])}* for {g['name']}.\n"
           f"{money(g['saved'])} of {money(g['target'])} ({pct}%). Balance now {money(done['balance_eur'])}.")
    if done["first_time"]:
        notify(pid, msg)
    return done, "Saved", [plain(msg)]


SETTLE = {"payment": settle_payment, "investment": settle_investment, "pause_investing": settle_pause_investing,
          "saving": settle_saving}


def pin_confirm(request: Request, path: str, kind: str, pid: str, pin: str):
    """A PIN page's form: 4 digits, then the shared settle step."""
    e, p = _pending_or_404(pid, kind)
    if len(pin) != 4 or not pin.isdigit():
        return bad_pin(request, path)
    try:
        _, title, lines = SETTLE[kind](e, pid, p)
    except ValueError as err:
        return not_done(request, err)
    return templates.TemplateResponse(request, "done.html", {"title": title, "lines": lines, "wa": wa_back(request)})


DEMO_IBAN = "FI00 0000 0000 0000 00 (demo)"
INVEST_NOTE = "Funds can go down as well as up. Past or scenario returns are not a promise."
PAUSE_NOTE = "Only new monthly deposits stop. You can start investing again any time."


def recipient(p: dict) -> str:
    return "Landlord · Asunto Oy Demo" if p["category"] == "Housing" else p["name"]


def bill_iban(p: dict) -> str:
    """The account number read from a bill photo, in groups of four."""
    return " ".join(re.findall(".{1,4}", p["iban"]))


def pause_rows(p: dict) -> list[tuple[str, str]]:
    return [("What", "Your monthly deposit to investments"), ("Usually on", f"day {p['usual_day']} of the month"),
            ("Next one", nice_date(p["next_charge"]) if p["next_charge"] else "-"),
            ("Already invested", "Stays invested")]


def invest_when(e: Engine, p: dict) -> str:
    return f"Every payday, from {nice_date(e.next_payday()['date'])}" if p["monthly"] else "Today, one-off"


@app.get("/pay/{pid}", response_class=HTMLResponse)
def pay_page(request: Request, pid: str):
    e, p = _pending_or_404(pid, "payment")
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "wa": wa_back(request), "action": f"/pay/{pid}", "title": "Confirm payment", "amount": money(p["amount"]),
        "rows": [("To", p["name"]), ("Account", bill_iban(p)),
                 ("Reference", p.get("reference") or "none on the bill"), ("Due", nice_date(p["due_date"])),
                 ("From", "S-Pankki current account")] if p.get("source") == "photo" else  # a bill photo (bills.py)
                [("To", recipient(p)), ("Account", DEMO_IBAN), ("For", p["name"]),
                 ("Due", nice_date(p["due_date"])), ("From", "S-Pankki current account")],
        "button": f"Pay {money(p['amount'])}"})


@app.post("/pay/{pid}", response_class=HTMLResponse)
def pay_confirm(request: Request, pid: str, pin: str = Form("")):
    return pin_confirm(request, f"/pay/{pid}", "payment", pid, pin)


@app.get("/invest/{pid}", response_class=HTMLResponse)
def invest_page(request: Request, pid: str):
    e, p = _pending_or_404(pid, "investment")
    f = FUNDS_BY_ID[p["fund_id"]]
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "wa": wa_back(request), "action": f"/invest/{pid}", "title": "Confirm investment",
        "amount": money(p["amount"]) + (" / month" if p["monthly"] else ""),
        "rows": [("Fund", f["name"]), ("Risk", f"{f['risk']} ({f['risk_level']}/7)"), ("Holds", f["what"]),
                 ("When", invest_when(e, p)), ("From", "S-Pankki current account")],
        "note": INVEST_NOTE, "button": "Invest" if not p["monthly"] else "Start monthly saving"})


@app.post("/invest/{pid}", response_class=HTMLResponse)
def invest_confirm(request: Request, pid: str, pin: str = Form("")):
    return pin_confirm(request, f"/invest/{pid}", "investment", pid, pin)


def order_text(order: dict) -> str:
    if order["status"] == "placed":
        lines = [f"• {leg['name']}: {leg['quantity']} shares" for leg in order["legs"]
                 if leg["status"] not in ("failed", "skipped")]
        return ("📈 Order sent to *Trading 212 (Practice account)*:\n" + "\n".join(lines)
                + "\nIt fills at the next market price; if the exchange is closed, when it opens.")
    return "⚠️ The broker order didn't go through yet. Your money is set aside and I'll retry."


@app.get("/stop/{pid}", response_class=HTMLResponse)
def stop_page(request: Request, pid: str):
    e, p = _pending_or_404(pid, "pause_investing")
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "wa": wa_back(request), "action": f"/stop/{pid}", "title": "Pause monthly investing",
        "amount": money(p["amount"]) + " / month", "rows": pause_rows(p), "note": PAUSE_NOTE,
        "button": "Pause investing"})


@app.post("/stop/{pid}", response_class=HTMLResponse)
def stop_confirm(request: Request, pid: str, pin: str = Form("")):
    return pin_confirm(request, f"/stop/{pid}", "pause_investing", pid, pin)


@app.get("/save/{pid}", response_class=HTMLResponse)
def save_page(request: Request, pid: str):
    e, p = _pending_or_404(pid, "saving")
    g = e.state["goals"][p["goal_id"]]
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "wa": wa_back(request), "action": f"/save/{pid}", "title": "Move to savings", "amount": money(p["amount"]),
        "rows": [("Goal", g["name"]), ("Saved so far", f"{money(g['saved'])} of {money(g['target'])}"),
                 ("From", "S-Pankki current account"), ("To", "Savings pocket")],
        "button": f"Save {money(p['amount'])}"})


@app.post("/save/{pid}", response_class=HTMLResponse)
def save_confirm(request: Request, pid: str, pin: str = Form("")):
    return pin_confirm(request, f"/save/{pid}", "saving", pid, pin)


# ---------------- S-mobiili mock: Birky's buttons open the bank app with the move filled in ----------------
# A concept prototype, clearly labelled: our own screens, not S-Bank's. Review -> Face ID (simulated) -> receipt.
SM_ROUTES = {"pay": "payment", "save": "saving", "invest": "investment", "stop": "pause_investing"}
RISK_WORDS = {"low": "Low risk: small ups and downs, and slow growth.",
              "medium": "Medium risk: it can drop in a bad year, and grows more over time.",
              "high": "High risk: it can fall 20-30% in a bad year. Best for 10+ years."}


def demo_reference(seed: str) -> str:
    """A made-up Finnish-style payment reference (7-3-1 check digit), the same for the same bill: demo only."""
    base = str(10**8 + int(hashlib.sha1(seed.encode()).hexdigest(), 16) % (9 * 10**8))
    ref = base + str(-sum(int(d) * w for d, w in zip(reversed(base), (7, 3, 1) * 3)) % 10)
    return f"{ref[:5]} {ref[5:]}"


def short_money(x: float) -> str:
    """97 € rather than 97.00 € in Birky's bubbles."""
    return nb(money(x).replace(".00 €", " €"))


def sm_persona(request: Request) -> str:
    """?persona= works like ?as= in the mock (handy when recording), without touching the cookie."""
    pick = request.query_params.get("persona")
    return valid_persona(pick) if pick else persona_of(request)


def pocket_open(e: Engine) -> bool:
    return bool(e.state.get("savings_pocket_opened")) or any(g.get("saved") for g in e.state.get("goals", {}).values())


def accounts(e: Engine) -> dict:
    goals = list(e.state.get("goals", {}).values())
    return {"current": e.balance(), "pocket": round(sum(g.get("saved", 0) for g in goals), 2),
            "pocket_open": pocket_open(e), "goals": goals, "funds": e.portfolio()["net_put_in_eur"],
            "plans": e.state.get("plans", [])}


def sm_flow(route: str, e: Engine, p: dict) -> dict:
    """A flow's review screen and Birky's lines on it. Guide targets are element ids on the page."""
    amt = short_money(p["amount"])
    if route == "pay":
        photo = p.get("source") == "photo"  # a bill photo (bills.py): the bill's own IBAN and reference
        to = p["name"] if photo else recipient(p)
        due = f", due {nice_date(p['due_date'])}" if p.get("due_date") else ""
        return {"title": "Pay a bill", "fi": "Maksu", "heading": p["name"], "amount": money(p["amount"]),
                "rows": [("recipient", "Recipient", to), ("iban", "IBAN", bill_iban(p) if photo else DEMO_IBAN),
                         ("reference", "Reference", (p.get("reference") or "none on the bill") if photo
                          else demo_reference(p["bill_id"])),
                         ("due", "Due date", nice_date(p["due_date"]) if p.get("due_date") else "-"),
                         ("from", "From", "Current account")],
                "button": f"Pay {money(p['amount'])}", "cheer": f"Paid! {p['name']} is sorted 🌰",
                "steps": [("amount", f"This is the amount we planned: {amt}", "talk"),
                          ("row-recipient", f"It goes to {to}{due}", "talk"),
                          ("row-reference", "IBAN and reference are copied from your bill. Nothing to type" if photo
                           else "IBAN and reference are filled in. Nothing to type", "talk")]}
    if route == "save":
        g = e.state["goals"][p["goal_id"]]
        after = g["saved"] + p["amount"]
        pct = round(after / g["target"] * 100)
        return {"title": "Move to savings", "fi": "Säästäjä", "heading": g["name"], "amount": money(p["amount"]),
                "rows": [("goal", "Goal", g["name"]),
                         ("so-far", "Saved so far", f"{money(g['saved'])} of {money(g['target'])}"),
                         ("after", "After this", f"{money(after)} ({pct}%)"),
                         ("from", "From", "Current account"), ("to", "To", "Savings pocket")],
                "button": f"Save {money(p['amount'])}", "cheer": f"Saved! {amt} is in your pocket 🌰",
                "first_pocket": not pocket_open(e),
                "steps": [("amount", f"This is the amount we planned: {amt}", "talk"),
                          ("row-to", "Your Savings pocket. It keeps this apart from everyday money", "talk"),
                          ("row-after", f"That takes {g['name']} to {pct}%", "happy")]}
    if route == "invest":
        f = FUNDS_BY_ID[p["fund_id"]]
        per = " / month" if p["monthly"] else ""
        return {"title": "Invest monthly" if p["monthly"] else "Invest", "fi": "Sijoitukset", "heading": f["name"],
                "amount": money(p["amount"]) + per, "note": INVEST_NOTE,
                "rows": [("fund", "Fund", f"{f['name']} (your choice)"),
                         ("risk", "Risk", f"{RISK_WORDS[f['risk']]} {f['risk_level']} of 7."),
                         ("holds", "Holds", f["what"]), ("when", "When", invest_when(e, p)),
                         ("from", "From", "Current account")],
                "button": "Start monthly investing" if p["monthly"] else f"Invest {money(p['amount'])}",
                "cheer": "Done! It starts on payday 🌱" if p["monthly"] else "Invested 🌱",
                "steps": [("amount", f"This is the amount we planned: {amt}{per}", "talk"),
                          ("row-fund", "The fund you picked. I explain, you choose", "talk"),
                          ("row-risk", "Risk in plain words: " + RISK_WORDS[f["risk"]].split(": ", 1)[1], "think")]}
    return {"title": "Pause monthly investing", "fi": "Sijoitukset", "heading": "Monthly investing",
            "amount": f"{money(p['amount'])} / month", "note": PAUSE_NOTE, "button": "Pause investing",
            "rows": [("what", "What", "Your monthly deposit to investments"),
                     ("usual", "Usually on", f"Day {p['usual_day']} of the month"),
                     ("next", "Next one", nice_date(p["next_charge"]) if p["next_charge"] else "-"),
                     ("invested", "Already invested", "Stays invested"), ("from", "From", "Current account")],
            "cheer": f"Paused. {short_money(p['per_year_eur'])} a year stays with you 🌰",
            "steps": [("amount", f"Your monthly investing: about {amt} a month", "talk"),
                      ("row-next", "Only new deposits stop. " + (f"The next one was due {nice_date(p['next_charge'])}"
                                                                if p["next_charge"] else "Nothing else changes"), "talk"),
                      ("row-invested", "What you've already invested stays invested", "happy")]}


def guide_steps(steps) -> list[dict]:
    return [{"target": t, "say": nb(s), "mood": m} for t, s, m in steps]


def sm_ctx(request: Request, persona: str, **extra) -> dict:
    q = request.query_params
    keep = {k: q[k] for k in ("persona", "web", "autoplay") if q.get(k)}
    return {"wa": wa_back(request), "web": bool(q.get("web")), "autoplay": q.get("autoplay") == "1",
            "home_url": "/s-mobiili/" + (f"?{urlencode(keep)}" if keep else ""), "name": name_for(persona), **extra}


@app.get("/s-mobiili", include_in_schema=False)
def smobiili_root(request: Request):
    return RedirectResponse("/s-mobiili/" + (f"?{request.url.query}" if request.url.query else ""), status_code=307)


BASKETS_Q = "Where does my money go?"  # the S-mobiili baskets card and the 4th chat chip ask the same question


@app.get("/s-mobiili/", response_class=HTMLResponse)
def smobiili_home(request: Request):
    persona = sm_persona(request)
    e = engine_for(persona)
    a = accounts(e)
    b = e.baskets("last_3_months")
    steps = [("acct-current", f"Your current account: {short_money(a['current'])}", "talk"),
             ("baskets", "Everything you spend falls in 3 baskets. Tap it and I'll show you where it goes", "talk"),
             ("acct-pocket", "Your Savings pocket" if a["pocket_open"] else "Your Savings pocket, once you open one",
              "talk"),
             ("acct-funds", "Your funds. Only you choose where money goes", "talk"),
             ("recent", "Your latest payments, straight from your account", "talk"),
             ("ask", "Questions? I'm in WhatsApp and right here", "wave")]
    bill = next(iter(e.upcoming_bills(30)), None)
    q = {"q": BASKETS_Q, **({"embed": "1"} if request.query_params.get("web") else {}),
         **({"as": persona} if request.query_params.get("persona") else {})}
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, persona, screen="home", a=a, recent=e.recent(8), steps=guide_steps(steps), baskets=b,
        baskets_url="/chat?" + urlencode(q),
        bill={**bill, "due": nice_date(bill["due_date"])} if bill else None))


@app.get("/s-mobiili/{route}/{pid}", response_class=HTMLResponse)
def smobiili_flow(request: Request, route: str, pid: str):
    if route not in SM_ROUTES:
        raise HTTPException(404)
    if request.query_params.get("pin"):  # the PIN page stays as a fallback
        return RedirectResponse(f"/{route}/{pid}" + ("?web=1" if request.query_params.get("web") else ""),
                                status_code=303)
    e, p = _pending_or_404(pid, SM_ROUTES[route])
    f = sm_flow(route, e, p)
    steps = guide_steps(f.pop("steps") + [("faceid", "Tap here and use Face ID", "talk")])
    pocket = guide_steps([("open-pocket", "First time? Open your Savings pocket. It's free, one tap", "talk")]
                         if f.get("first_pocket") else [])
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, sm_persona(request), screen="flow", route=route, pid=pid, p=p, f=f, steps=steps,
        pocket_steps=pocket, done=p["status"] == "done"))


@app.post("/s-mobiili/{route}/{pid}/confirm")
async def smobiili_confirm(request: Request, route: str, pid: str):
    """Face ID passed in the mock: the same settle step as the PIN page. JSON only, so a cross-site form can't post
    it. A second confirm is refused with 409 and moves nothing."""
    if route not in SM_ROUTES:
        raise HTTPException(404)
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "Send JSON.")
    try:
        body = await request.json()
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    e, p = _pending_or_404(pid, SM_ROUTES[route])
    if p["status"] == "done":
        raise HTTPException(409, "Already confirmed. Nothing more was moved.")
    try:
        done, title, lines = SETTLE[p["kind"]](e, pid, p)
    except ValueError as err:
        return JSONResponse({"detail": sentence(err)}, status_code=422)
    if not done["first_time"]:  # two taps at once: the engine's lock let only one through
        raise HTTPException(409, "Already confirmed. Nothing more was moved.")
    if route == "save" and body.get("open_pocket") and not e.state.get("savings_pocket_opened"):
        e.state["savings_pocket_opened"] = e.today.isoformat()
        e._save()
    return {"ok": True, "title": title, "lines": [nb(x) for x in lines], "balance_eur": done["balance_eur"]}


# ---------------- Demo triggers (used while filming) ----------------
def alert_message(kind: str, name: str, e: Engine | None = None) -> tuple[str, list] | None:
    if kind not in ("salary", "unusual", "bills"):
        raise HTTPException(400, "kind must be salary, unusual or bills")
    a = (e or engine).alert(kind)
    if kind != "salary" and not a["found"]:
        return None
    return alert_text(kind, a, name)


def alert_text(kind: str, a: dict, name: str) -> tuple[str, list]:
    """Text and quick replies for an engine alert (or a proactive signal carrying the same data)."""
    if kind == "salary":
        goals = a["goals"]
        text = (f"💰 {name}, your salary of *{money(a['salary_eur'])}* just landed. Balance now {money(a['balance_eur'])}.\n\n"
                f"Pay yourself first: put *{money(a['suggest_save_eur'])}* aside now, before it slips through your fingers?")
        first = ([(f"save:{goals[0]['id']}:{a['suggest_save_eur']:.0f}", f"Save {a['suggest_save_eur']:.0f} €")] if goals
                 else [("ask:goal", "Set a savings goal")])
        return text, first + [("ask:invest", "Invest instead"), ("later:salary", "Not now")]
    if kind == "unusual":
        text =(f"👀 Heads-up: *{money(a['amount_eur'])}* at {a['where']} on {nice_date(a['date'])}. "
                f"That's *{a['times_usual']}x* your usual {a['category'].lower()} payment ({money(a['usual_eur'])}).\n\n"
                "It sits in your *slips through your fingers* basket. Was that planned?")
        return text, [("later:unusual", "Yes, all good"), ("ask:unusual", "Tell me more")]
    return bills_text(a)


def bills_text(a: dict) -> tuple[str, list]:
    """A real regular payment (engine.commitments()) that went up, a utility bill above the usual range, or the next
    one coming up. a["type"] is increase, unusual_bill or upcoming."""
    what = a["name"].lower()
    if a["type"] == "increase":
        tr = a["trend"]
        text = (f"📈 Your *{what}* went up: *{money(tr['last_per_month_eur'])} a month* in {tr['last_months']}, "
                f"from {money(tr['previous_per_month_eur'])} a month in {tr['previous_months']}.")
        if a.get("extra_per_year_eur"):
            text += f" That's {money(a['extra_per_year_eur'])} more a year."
        if a["id"] == "investing":
            return text + "\n\nKeep it, or pause it for now?", [("keep:investing", "Keep it"),
                                                               ("pause:investing", "Pause investing")]
        return text + "\n\nIt sits in your *mandatory & regular* basket.", [
            ("ask:bills", "My regular payments"), ("ask:tight", "How do I stretch it?")]
    if a["type"] == "unusual_bill":
        odd = a["unusual_bill"]
        return (f"🧾 Heads-up: a *{money(odd['amount_eur'])}* utility bill on {nice_date(odd['date'])}. One bill is "
                f"usually {money(odd['usual_low_eur'])} to {money(odd['usual_high_eur'])}.\n\n"
                f"Your utilities come to {money(a['per_month_eur'])} a month, about {round(a['bills_per_month'])} "
                "bills.", [("ask:bills", "My regular payments"), ("later:bills", "OK, thanks")])
    when = "tomorrow" if a["days_to_next"] == 1 else f"in {a['days_to_next']} days"
    day = nice_date(a["next_expected"])
    if a["id"] == "investing":
        return (f"📈 Your *monthly investing* of about {money(a['per_month_eur'])} goes out {when} ({day}).\n\n"
                "Keep it, or pause it for now?", [("keep:investing", "Keep it"), ("pause:investing", "Pause investing")])
    return (f"🧾 Your *{what}* of about {money(a['per_month_eur'])} is due {when} ({day}).",
            [("remind:" + a["id"], "Remind me before"), ("ask:bills", "My regular payments")])


@app.post("/demo/alert")
def demo_alert(kind: str, to: str | None = None):
    to = to or os.environ.get("DEMO_PHONE")
    if not to:
        raise HTTPException(400, "pass ?to=<number> or set DEMO_PHONE")
    m = alert_message(kind, name_of(to))
    if not m:
        return {"sent": False, "reason": "nothing to say"}
    wa.send_reply_buttons(to, m[0], m[1])
    return {"sent": True, "text": m[0]}

def reminder_text(name: str = DEMO_NAME, e: Engine | None = None) -> tuple[str, dict] | tuple[None, None]:
    e = e or engine
    rent = next((b for b in e.upcoming_bills(45) if b["category"] == "Housing"), None)  # next month's once paid
    return (bill_text(rent, e.safe_to_spend(), name), rent) if rent else (None, None)


def pay_label(name: str) -> str:
    """"Pay rent now", or "Pay now" when the bill's name would not fit a 20-character button."""
    label = f"Pay {name.lower()} now"
    return label if len(label) <= wa.TITLE_MAX else "Pay now"


def bill_text(bill: dict, s: dict, name: str) -> str:
    what = bill["name"].lower()
    when = "tomorrow" if bill["days_away"] == 1 else f"in {bill['days_away']} days"
    return (f"Hi {name} 👋 Your *{what} of {money(bill['amount_eur'])}* is due *{when}* ({nice_date(bill['due_date'])}).\n\n"
            f"After {what} and your usual bills you have *{money(s['safe_to_spend_until_payday_eur'])}* safe to spend "
            f"until payday on {nice_date(s['next_payday']['date'])}. That's about *{money(s['safe_per_day_eur'])} a day*."
            + reserve_line(s.get("reserve_eur", 0)))


def reserve_line(reserve: float) -> str:
    """Money built up on the account beyond this pay cycle is not spending money: say what it is."""
    if reserve < RESERVE_NOTE_EUR:
        return ""
    return f"\nThe other {money(reserve)} on your account is beyond this pay cycle, more like savings."


@app.post("/demo/remind")
def demo_remind(to: str | None = None):
    to = to or os.environ.get("DEMO_PHONE")
    if not to:
        raise HTTPException(400, "pass ?to=<number> or set DEMO_PHONE")
    text, rent = reminder_text(name_of(to))
    wa.send_reply_buttons(to, text, [(f"pay:{rent['bill_id']}", "Pay rent now"),
                                     (f"later:{rent['bill_id']}", "Remind me later"),
                                     ("ask:tight", "How do I stretch it?")])
    return {"sent": True, "text": text}


# ---------------- Proactive messages (cron) ----------------
def signal_message(sig: dict, name: str) -> tuple[str, list]:
    """The full message for a rules.py signal. Every number comes from sig["data"]."""
    d, kind = sig["data"], sig["type"]
    if kind == "SALARY_RECEIVED":
        return alert_text("salary", d, name)
    if kind == "UNUSUAL_TRANSACTION":
        return alert_text("unusual", d, name)
    if kind == "RECURRING_CHARGE_UPCOMING":
        if d["source"] != "bill":  # the monthly investing or a fixed-day utility bill (engine.commitments())
            return bills_text({**d, "type": "upcoming"})
        b = d["bill"]
        return bill_text(b, d["safe"], name), [(f"pay:{b['bill_id']}", pay_label(b["name"])),
                                               (f"later:{b['bill_id']}", "Remind me later"),
                                               ("ask:tight", "How do I stretch it?")]
    if kind == "BILL_INCREASE":
        return bills_text({**d, "type": "increase" if d["increase"] == "trend" else "unusual_bill"})
    if kind == "OPEN_THREAD":
        return thread_message(d, name)
    if kind == "GOAL_DRIFT":
        return (f"🎯 {name}, your *{d['name']}* goal is a bit behind plan: {money(d['saved_eur'])} saved, "
                f"while {money(d['expected_by_now_eur'])} would be on track by today. To reach {money(d['target_eur'])} "
                f"by {nice_date(d['deadline'])}, that's about *{money(d['needed_per_month_eur'])} a month*.\n\n"
                f"Catch up with {money(d['catch_up_eur'])} now?",
                [(f"save:{d['goal_id']}:{d['catch_up_eur']:.0f}", f"Save {d['catch_up_eur']:.0f} €"),
                 ("ask:pace", "How to catch up"), ("later:goal", "Not now")])
    text = (f"📅 Your week, {name}: *{money(d['spent_7d_eur'])}* spent in the last 7 days "
            f"({money(d['spent_prev_7d_eur'])} the week before).\n"
            f"Safe to spend until payday ({nice_date(d['next_payday'])}): *{money(d['safe_eur'])}*, "
            f"about {money(d['safe_per_day_eur'])} a day." + reserve_line(d.get("reserve_eur", 0)))
    if d["bills_next_7d"]:
        text += "\nComing up: " + ", ".join(f"{b['name']} {money(b['amount_eur'])} on {nice_date(b['due_date'])}"
                                            for b in d["bills_next_7d"]) + "."
    return text, [("ask:week", "Where did it go?"), ("ask:tight", "How do I stretch it?")]


def thread_message(d: dict, name: str) -> tuple[str, list]:
    """The "comes back a day later" message for an open thread: one short friendly line, one button to carry on."""
    t = d["thread"]
    if t == "learn":
        done = f" ({d['done']} of {d['of']} done 🌰)" if d["done"] else ""
        return (f"Hey {name} 📚 Lesson {d['lesson']}/{d['of']}, *{d['title'].lower()}*, is waiting{done}. 30 seconds?",
                [("learn:start", "Continue lesson")])
    if t == "onboarding":
        return (f"Hey {name} 👋 We stopped halfway through setting up your goal. Finish it? It takes a minute.",
                [("ob:resume", "Let's finish")])
    if t == "quiz":
        return (f"Hey {name} 🧺 Your basket game is paused at {d['round']}/{d['of']}. One more round?",
                [("quiz:resume", "Keep playing")])
    if t == "pin":
        return (f"Hey {name} 🔐 *{d['name']}* ({money(d['amount_eur'])}) is still waiting for you to confirm it "
                "with Face ID in S-mobiili. Want to finish it?", [(f"resume:{d['pending_id']}", "Finish it")])
    if t == "goal":
        return (f"Hey {name} 🌰 Your *{d['name']}* goal is set, but nothing's in it yet. Start with "
                f"{money(d['amount_eur'])}?",
                [(f"save:{d['goal_id']}:{d['amount_eur']:.0f}", f"Save {d['amount_eur']:.0f} €")])
    if d["kind"] == "bill":
        b = d["bill"]
        return (f"Hey {name} ⏰ You said later yesterday: *{b['name'].lower()}* of {money(b['amount_eur'])} is due "
                f"{nice_date(b['due_date'])}. Pay it now?", [(f"pay:{b['bill_id']}", pay_label(b["name"]))])
    return (f"Hey {name} 🙂 Yesterday you said maybe later to this: \"{d['offer']}\" Still up for it?",
            [("resume:later", "Yes, let's do it")])


def mask(to: str) -> str:
    return to if to.startswith("web:") else "•••" + to[-4:]


def wake_one(to: str, dry: bool, force: bool = False) -> dict:
    """Pick this person's top signal and deliver it (or, when dry, only say how it would go)."""
    web = to.startswith("web:")
    persona = session_persona.get(to) if web else None
    e = engine_for(persona)
    sig = rules.pick(e, to, force)
    out = {"to": mask(to), "signal": sig and sig["type"], "via": None}
    if not sig:
        return out
    text, buttons = signal_message(sig, name_for(persona) if web else name_of(to))
    out.update(dedup_key=sig["dedup_key"], text=text)
    if web:
        out["via"] = "inbox"
    elif window_open(to):
        out["via"] = "text"
    else:  # WhatsApp allows free messages only within 24 h of the person's last one; Birky waits to be asked
        return {**out, "reason": "outside the 24-hour window: waits until the person writes"}
    if dry:
        return out
    if web:
        inbox.setdefault(to, []).append(text)
    else:
        wa.send_reply_buttons(to, text, buttons)
    e.state.setdefault("sent", {}).setdefault(to, {})[sig["dedup_key"]] = e.today.isoformat()
    e._save()
    return out


@app.post("/cron/wake")
def cron_wake(key: str = "", to: str | None = None, dry_run: bool = False, force: bool = False):
    """Run the rules for everyone connected (or one `to`) and deliver each top signal.
    Sends nothing unless DEMO_PROACTIVE=1: otherwise it is always a dry run."""
    if not key or key != os.environ.get("WA_VERIFY_TOKEN"):
        raise HTTPException(403)
    live = os.environ.get("DEMO_PROACTIVE") == "1"
    targets = [to] if to else [p for p in engine.state.get("connected", {}) if p] + sorted(web_sessions)
    results = [wake_one(t, dry=dry_run or not live, force=force) for t in targets]
    log.info("wake: live=%s dry=%s %s", live, dry_run, [(r["to"], r["signal"], r["via"]) for r in results])
    return {"live": live, "dry_run": dry_run or not live, "results": results}


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_panel(request: Request, key: str = ""):
    """Filming helper: buttons that send each proactive message to the demo phone."""
    if not key or key != os.environ.get("WA_VERIFY_TOKEN"):
        raise HTTPException(404)
    phone = os.environ.get("DEMO_PHONE", "")
    return templates.TemplateResponse(request, "demo.html", _ctx(
        phone=phone, open=window_open(phone), key=key, proactive=os.environ.get("DEMO_PROACTIVE") == "1"))


@app.post("/demo/reset")
def demo_reset():
    engine.reset()
    coach().reset()
    with _persona_lock:  # every persona: loaded ones in place, the rest by dropping their state file
        for e in _engines.values():
            e.reset()
        for c in _coaches.values():
            c.reset()
        for pid in set(PERSONAS) - set(_engines) - {DEFAULT_PERSONA}:
            (STATE_DIR / f"{pid}.json").unlink(missing_ok=True)
    session_persona.clear()
    seen.clear()
    owner.clear()
    inbox.clear()
    web_sessions.clear()
    return {"reset": True}


# ---------------- Web chat (same coach, embeddable in S-mobiili) ----------------
def _ctx(**extra) -> dict:
    return {"money": money_nb, "nice": nice_date, "wa": os.environ.get("WA_DISPLAY_NUMBER", ""), **extra}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(request: Request):
    off = engine.official_benchmark() or {"comparison": []}
    rest = next((r for r in off["comparison"] if r["category"] == "Restaurants"), {"times": "-"})
    rent = next((b for b in engine.upcoming_bills(45) if b["category"] == "Housing"), {"amount_eur": 0})
    reg = engine.commitments()
    util = next((x for x in reg["regular_payments"] if x["id"] == "utilities"), None) or {}
    cut = engine.simulate_cut("Restaurants", 20)
    growth = next((r for r in (cut.get("real_past_check") or {}).get("results", []) if r and "Growth" in r["fund"]),
                  {"value_today_eur": 0})
    L = {"rest": engine.monthly_avg("Restaurants", 3), "times": rest["times"], "rent": rent["amount_eur"],
         "safe": engine.safe_to_spend()["safe_to_spend_until_payday_eur"], "bills_year": reg["bills_per_year_eur"],
         "util_month": util.get("per_month_eur", 0), "util_count": round(util.get("bills_per_month", 0)),
         "odd": util.get("unusual_bill"), "cut": cut["saving_per_month_eur"], "cut_growth": growth["value_today_eur"],
         "fi_same_age": not off.get("group", "").endswith(" all")}  # Statistics Finland has age groups only up to 34
    return templates.TemplateResponse(request, "landing.html", _ctx(L=L, who=PERSONAS[DEFAULT_PERSONA],
                                                                     baskets=engine.baskets("last_3_months")))


def _chart(o: dict) -> dict:
    vals = [v for _, v in o["series"]]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = [(round(i * 300 / (len(vals) - 1), 1), round(64 - (v - lo) / span * 58, 1)) for i, v in enumerate(vals)]
    line = " ".join(f"{x},{y}" for x, y in pts)
    top = max(max(w["in"], w["out"]) for w in o["flow"]) or 1
    bars = []
    for i, w in enumerate(o["flow"]):
        hin, hout = round(w["in"] / top * 95, 1), round(w["out"] / top * 95, 1)
        bars.append({"x": round(i * 25 + 3, 1), "hin": hin, "yin": round(100 - hin, 1), "hout": hout, "yout": round(100 - hout, 1)})
    return {"series_pts": line, "series_area": f"0,70 {line} 300,70", "flow_bars": bars}


@app.get("/app/export.json")
def export_json(request: Request):
    return JSONResponse(engine_for(persona_of(request)).export(), headers={"content-disposition": 'attachment; filename="my-s-pankki-data.json"'})


@app.get("/app/export.csv")
def export_csv(request: Request):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "category", "basket", "amount_eur", "type", "merchant"])
    for t in engine_for(persona_of(request)).export()["transactions"]:
        w.writerow([t["date"], t["category"], t["basket"] or "", t["amount_eur"], t["type"], t["merchant"] or ""])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"content-disposition": 'attachment; filename="my-s-pankki-transactions.csv"'})


# ---------------- MyData explorer: the customer builds their own view, no AI ----------------
EXPLORE_KEYS = ("what", "category", "basket", "merchant_type", "group_by", "metric", "period", "start", "end")
EXPLORE_LABELS = {
    "what": {"spent": "Spending", "income": "Income"},
    "metric": {"sum": "Euros", "count": "Number of payments", "average": "Average payment"},
    "group_by": {"month": "Month", "week": "Week", "weekday": "Day of the week", "category": "Category",
                 "basket": "Basket", "merchant": "Place", "merchant_type": "Kind of place"},
    "period": {"last_12_months": "Last 12 months", "last_3_months": "Last 3 months", "last_month": "Last month",
               "this_month": "This month", "last_30_days": "Last 30 days", "last_7_days": "Last 7 days",
               "all": "Everything"},
}


def explore_args(request: Request) -> dict:
    """Query string -> explore() arguments. `filter=category:Restaurants` (the page's single filter dropdown) or the
    separate category / basket / merchant_type parameters (links from the chat)."""
    q = request.query_params
    args = {k: q.get(k) for k in EXPLORE_KEYS}
    kind, _, value = (q.get("filter") or "").partition(":")
    if kind in ("category", "basket", "merchant_type") and value:
        args[kind] = value
    return {k: v[:40] for k, v in args.items() if v}


def explore_filters(merchant_type: str | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """Dropdown groups. Kinds of place are the MCC labels get_spending uses; a word from a chat link ('coffee')
    that is not one code gets its own option so the dropdown shows what is selected."""
    places = sorted(((f"merchant_type:{c}", label) for c, label in MCC_LABELS.items()), key=lambda o: o[1])
    if merchant_type and merchant_type not in MCC_LABELS:
        places.insert(0, (f"merchant_type:{merchant_type}", merchant_codes(merchant_type)[0]))
    return [("Category", [(f"category:{c}", c) for c in SPEND_CATEGORIES]),
            ("Basket", [(f"basket:{k}", b["label"]) for k, b in BASKETS.items()]),
            ("Kind of place", places)]


@app.get("/app/explore", response_class=HTMLResponse)
def explore_page(request: Request):
    persona = persona_of(request)
    eng = engine_for(persona)
    try:
        view, error = eng.explore(**explore_args(request)), None
    except ValueError as e:  # a hand-edited link: show the default view and say why
        view, error = eng.explore(), str(e)
    q = view["query"]
    current = next((f"{k}:{q[k]}" for k in ("category", "basket", "merchant_type") if q[k]), "")
    return templates.TemplateResponse(request, "explore.html", _ctx(
        **persona_ctx(persona),
        v=view, error=error, current=current, filters=explore_filters(q["merchant_type"]), labels=EXPLORE_LABELS,
        options={"what": EXPLORE_WHAT, "metric": EXPLORE_METRICS, "group_by": EXPLORE_GROUP_BY, "period": PERIODS},
        csv_url="/app/explore.csv?" + explore_query(q)))


@app.get("/app/explore.csv")
def explore_csv(request: Request):
    """Exactly the rows and the total shown on the Explore page for the same query."""
    import csv
    import io
    try:
        v = engine_for(persona_of(request)).explore(**explore_args(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    q = v["query"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([q["group_by"], {"sum": "amount_eur", "count": "payments", "average": "average_payment_eur"}[q["metric"]],
                "payments", "total_eur"])
    for r in v["rows"]:
        w.writerow([r["label"], r["value"], r["transactions"], r["total_eur"]])
    w.writerow(["Total", v["totals"]["value"], v["totals"]["transactions"], v["totals"]["total_eur"]])
    parts = [q["what"], q["category"] or q["basket"] or q["merchant_type"], "by", q["group_by"], q["period"]]
    name = "".join(ch if ch.isalnum() else "-" for ch in "-".join(p for p in parts if p).lower())
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"content-disposition": f'attachment; filename="my-s-pankki-{name}.csv"'})


@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request):
    persona = persona_of(request)
    o = engine_for(persona).overview()
    return templates.TemplateResponse(request, "dashboard.html", _ctx(
        o=o, name=name_for(persona), today=date.fromisoformat(o["today"]).strftime("%A %d %B"), **_chart(o),
        peers=_peers_card(engine_for(persona)), **persona_ctx(persona)))


def _peers_card(eng: Engine) -> dict | None:
    """'People like you' on /app: None (no card) when the benchmark file is missing."""
    from . import peers
    try:
        return peers.card(eng.new_life_budget())
    except ValueError:
        return None


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, q: str = "", embed: str = ""):
    """?q= asks a question once on load (deep link); ?embed=1 drops the site chrome for a WebView."""
    persona = persona_of(request)
    return templates.TemplateResponse(request, "chat.html", {"name": name_for(persona), "q": q[:500],
                                                             "embed": embed == "1", **persona_ctx(persona)})


# ---------------- S-mobiili integration: native home-screen cards, no AI ----------------
WIDGETS_MAX_AGE = 300


def ask_link(question: str) -> str:
    return "/chat?" + urlencode({"q": question})


def widgets(e: Engine | None = None) -> dict:
    """Small cards a banking app home screen can draw natively. Every number comes from the engine;
    each card deep-links into the chat with a question (or a direct action that needs no AI)."""
    e = e or engine
    s = e.safe_to_spend()
    pay = s["next_payday"]
    cards = [{"id": "safe_to_spend", "title": "Safe to spend", "value": money(s["safe_to_spend_until_payday_eur"]),
              "subtitle": (f"until payday {nice_date(pay['date'])}" if pay["date"] else f"next {s['horizon_days']} days")
                          + f" · {money(s['safe_per_day_eur'])} a day",
              "amount_eur": s["safe_to_spend_until_payday_eur"], "per_day_eur": s["safe_per_day_eur"],
              "payday": pay["date"], "days_to_payday": pay["days_away"],
              "cycle_budget_eur": s["cycle_budget_eur"], "reserve_eur": s["reserve_eur"],
              "deep_link": ask_link("What is safe for me to spend until payday?")}]
    bill = next((b for b in e.upcoming_bills(45) if b["bill_id"] and b["category"] != "Invest Deposit"), None)
    if bill:
        when = "tomorrow" if bill["days_away"] == 1 else f"in {bill['days_away']} days"
        cards.append({"id": "next_bill", "title": "Next bill", "value": money(bill["amount_eur"]),
                      "subtitle": f"{bill['name']} · {nice_date(bill['due_date'])} ({when})",
                      "name": bill["name"], "amount_eur": bill["amount_eur"], "due_date": bill["due_date"],
                      "days_away": bill["days_away"],
                      "deep_link": ask_link(f"How does my {bill['name'].lower()} fit before payday?"),
                      "actions": [{"label": "Pay", "deep_link": ask_link(f"__btn__:pay:{bill['bill_id']}")}]})
    b = e.baskets("this_month")
    if not b["total_spent_eur"]:  # early in the month: show the last full month instead of three zeros
        b = e.baskets("last_month")
    month = "this month" if b["from"][:7] == e.today.isoformat()[:7] else "last month"
    cards.append({"id": "baskets", "title": "Your three baskets", "value": money(b["total_spent_eur"]),
                  "subtitle": f"spent {month}", "period": month.replace(" ", "_"), "from": b["from"], "to": b["to"],
                  "total_eur": b["total_spent_eur"],
                  "baskets": [{"key": x["basket"], "label": x["label"], "share_pct": x["share_pct"],
                               "total_eur": x["total_eur"]} for x in b["baskets"]],
                  "deep_link": ask_link(f"Where did my money go {month}, basket by basket?")})
    reg = e.commitments()
    outs = [x for x in reg["regular_payments"] if x["direction"] == "out" and x["status"] == "active"]
    up = next((x for x in outs if (x.get("trend") or {}).get("direction") == "up"), None)
    odd = next((x for x in outs if x.get("unusual_bill")), None)
    if up:
        tr = up["trend"]
        cards.append({"id": "regular_payments", "kind": "increase", "title": f"{up['name']} went up",
                      "value": f"{money(tr['last_per_month_eur'])}/mo",
                      "subtitle": f"was {money(tr['previous_per_month_eur'])} a month ({tr['previous_months']})",
                      "name": up["name"], "from_eur": tr["previous_per_month_eur"], "to_eur": tr["last_per_month_eur"],
                      "deep_link": ask_link(f"Why did my {up['name'].lower()} go up?")})
    elif odd:
        u = odd["unusual_bill"]
        cards.append({"id": "regular_payments", "kind": "unusual_bill", "title": "A bigger utility bill",
                      "value": money(u["amount_eur"]),
                      "subtitle": f"{nice_date(u['date'])} · usually {money(u['usual_low_eur'])} to {money(u['usual_high_eur'])}",
                      "name": odd["name"], "amount_eur": u["amount_eur"], "date": u["date"],
                      "usual_low_eur": u["usual_low_eur"], "usual_high_eur": u["usual_high_eur"],
                      "deep_link": ask_link("What are my regular payments?")})
    elif outs:
        cards.append({"id": "regular_payments", "kind": "summary", "title": "Regular payments",
                      "value": f"{money(reg['outgoing_per_month_eur'])}/mo",
                      "subtitle": f"{len(outs)} found in your account · {money(reg['outgoing_per_year_eur'])} a year",
                      "per_month_eur": reg["outgoing_per_month_eur"], "per_year_eur": reg["outgoing_per_year_eur"],
                      "deep_link": ask_link("What are my regular payments?")})
    goals = e.goals()
    if goals:
        g = goals[0]
        cards.append({"id": "goal", "title": g["goal"]["name"], "value": f"{g['progress_pct']}%",
                      "subtitle": f"{money(g['goal']['saved'])} of {money(g['goal']['target'])}",
                      "goal_id": g["goal"]["id"], "progress_pct": g["progress_pct"],
                      "saved_eur": g["goal"]["saved"], "target_eur": g["goal"]["target"],
                      "deadline": g["goal"].get("deadline"),
                      "deep_link": ask_link(f"How is my {g['goal']['name']} goal going?")})
    return {"version": 1, "as_of": e.today.isoformat(), "max_age_s": WIDGETS_MAX_AGE, "widgets": cards}


@app.get("/api/widgets")
def widgets_api(request: Request):
    return JSONResponse(widgets(engine_for(persona_of(request))),
                        headers={"cache-control": f"private, max-age={WIDGETS_MAX_AGE}", "vary": "cookie"})


@app.get("/mobile-demo", response_class=HTMLResponse)
def mobile_demo(request: Request):
    """Concept mock: Birky's cards and chat inside a generic banking app home screen."""
    persona = persona_of(request)
    e = engine_for(persona)
    return templates.TemplateResponse(request, "mobile_demo.html", _ctx(
        name=name_for(persona), balance=e.balance(), recent=e.recent(6)))


WEB_UPLOAD_MAX = bills.MAX_BYTES * 4 // 3 + 64_000  # a 5 MB photo as base64, plus the rest of the JSON


@app.post("/api/chat")
async def chat_api(request: Request):
    if int(request.headers.get("content-length") or 0) > WEB_UPLOAD_MAX:  # before reading it
        raise HTTPException(413, "image too large (max 5 MB)")
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "send a JSON object")
    sid = "web:" + str(body.get("session", "default"))[:40]
    text = str(body.get("text", "")).strip()[:1000]
    if not text and not body.get("image"):
        raise HTTPException(400)
    web_sessions.add(sid)
    # The page sends the persona it was drawn for; the cookie covers other callers.
    persona = valid_persona(body.get("as")) if body.get("as") else persona_of(request)
    session_persona[sid] = persona
    e, name = engine_for(persona), name_for(persona)

    def quick(buttons):
        return [{"label": t, "send": f"__btn__:{i}"} for i, t in buttons]

    if body.get("image"):
        return await web_bill(persona, sid, e, name, body["image"], wa.redact(text), quick)
    if text == "__remind__":
        t, rent = reminder_text(name, e)
        if not rent:
            return {"text": "No rent or housing payment is due in the next 45 days.", "buttons": []}
        return {"text": t, "buttons": [], "quick": quick([(f"pay:{rent['bill_id']}", "Pay rent now"),
                                                          (f"later:{rent['bill_id']}", "Remind me later"),
                                                          ("ask:tight", "How do I stretch it?")])}
    if text.startswith("__alert__:"):
        m = alert_message(text.split(":", 1)[1], name, e)
        return {"text": m[0], "buttons": [], "quick": quick(m[1])} if m else {"text": "Nothing to flag right now.", "buttons": []}
    def interview(t: onboarding.Turn, buttons=()):
        return {"text": "\n\n".join(t.lead + [t.text]), "bubbles": [*t.lead, t.text], "buttons": list(buttons),
                "quick": quick(t.buttons)}

    def said(reply_text: str, reaction: str | None = None, **more):
        """A plain answer from the app, in bubbles ("---" splits them), with the reaction for their message."""
        parts = wa.bubbles(reply_text)
        return {"text": "\n\n".join(parts), "bubbles": parts, "reaction": reaction, "buttons": [], **more}

    if text == "__start__":  # the web chat's stand-in for Connect S-Pankki
        t = onboarding.start(e, sid)
        remember(persona, sid, "[connected S-Pankki]", t)
        return interview(t, [{"label": "Open My money", "url": "/app"}])
    touch(e, sid)
    bid, secret, extra = "", [], None
    if text.startswith("__btn__:"):
        bid = text.split(":", 1)[1]
        learn.step_away(e, sid, bid)
        try:
            direct = button_reply(sid, bid, web=True, e=e)
        except ValueError as err:
            return {"text": f"I can't do that: {err}", "buttons": []}
        if direct:
            url = direct.get("url")
            return {"text": direct["text"], "buttons": [{"label": url[0], "url": url[1].replace(public_url(), "")}] if url else []}
        text = ask_text(e, sid, bid) or bid
    else:
        if guardrails.FRESH.match(text):
            return said(fresh_start(e, sid, persona), "👌")
        secret = guardrails.secrets(text)
        text = wa.redact(text)
        if secret:  # never to the AI; a kind "please delete that", then the rest of the question if there is one
            rest = guardrails.leftover_question(text)
            warn = guardrails.secret_reply(secret)
            if not rest:
                return said(warn, "🔒")
            text, extra = rest, SECRET_CONTEXT.format(what=secret[0])
    asked = bool(bid) and ask_text(e, sid, bid) is not None
    if not asked:
        typed = "" if bid else text
        t = flow_turn(e, sid, typed, bid)
        if t:
            remember(persona, sid, typed or f"[tapped {bid}]", t)
            return interview(t)
    c = coach(persona)
    if not bid and hasattr(c, "last_said"):
        note_chat_later(e, sid, text, c.last_said(sid))
    context = coach_context(e, sid, text, extra)
    return await web_answer(e, sid, lambda: ask(persona, sid, text, name, context), quick, secret)


async def web_bill(persona: str, sid: str, e: Engine, name: str, image, caption: str, quick):
    """A bill photo from the web chat (base64, or a data: URL): read it, then explain it or, if asked, pay it."""
    import anyio
    try:
        data = base64.b64decode(str(image).split(",", 1)[-1], validate=True)
    except ValueError:
        raise HTTPException(400, "image is not base64")
    if len(data) > bills.MAX_BYTES:
        raise HTTPException(413, "image too large (max 5 MB)")
    try:
        bill = await anyio.to_thread.run_sync(lambda: bills.read(e, sid, getattr(coach(persona), "client", None),
                                                                 data, None))
    except bills.BillError as err:
        return {"text": str(err), "buttons": []}
    except Exception:
        log.exception("bill photo failed")
        return JSONResponse({"text": "Sorry, I couldn't read that photo just now. Could you try again in a moment?",
                             "buttons": []})
    problem = bills.reply_problem(bill)
    if problem:
        return {"text": problem, "buttons": []}
    return await web_answer(e, sid, lambda: ask_about_bill(persona, sid, bill, caption, name), quick)


async def web_answer(e: Engine, sid: str, run, quick, secret=()):
    """The coach's reply for the web chat: bubbles, buttons, charts, and any paused flow's note."""
    import anyio
    try:
        reply = await anyio.to_thread.run_sync(run)
    except Exception:
        log.exception("coach failed")
        return JSONResponse({"text": "Sorry, I can't think right now (the AI service is not reachable). "
                                     "Bill reminders and payments still work.", "buttons": []})
    out, charts = [], []
    for b in reply.buttons:
        if "chart" in b:  # drawn inside the bubble; its link opens the same view on the Explore page
            charts.append({**b["chart"], "url": b["url"].replace(public_url(), "")})
            continue
        owner[b["url"].rsplit("/", 1)[-1]] = sid
        out.append({"label": b["label"], "url": b["url"].replace(public_url(), "") + "?web=1"})
    paused = quiz.paused_note(e, sid)
    waiting = learn.paused_note(e, sid)
    again = onboarding.reask(e, sid)  # a side question mid-interview: the waiting question comes back
    parts = wa.bubbles(reply.text) if reply.text.strip() else []
    if secret:
        parts = wa.bubbles(guardrails.secret_reply(secret)) + parts
    notes = [x for x in (paused, waiting, again and again.text) if x]
    return JSONResponse({"text": "\n\n".join(parts + notes), "bubbles": parts + notes,
                         "reaction": "🔒" if secret else getattr(reply, "reaction", None),
                         "tools": reply.tools_used, "trace": reply.trace,
                         "buttons": out, "charts": charts, "quick": quick(again.buttons) if again else []})


@app.get("/api/inbox")
def chat_inbox(session: str = ""):
    return {"messages": inbox.pop("web:" + session[:40], [])}


@app.get("/health")
def health():
    return {"ok": True, "today": engine.today.isoformat(), "balance": engine.balance(), "whatsapp": wa.configured()}
