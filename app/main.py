"""FastAPI app: WhatsApp webhook, bank-style confirm pages, web chat, demo triggers.

Run: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

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

from . import broker, learn, onboarding, quiz, rules, voice
from . import whatsapp as wa
from .agent import Coach
from .engine import (BASKETS, DEMO_BANK, EXPLORE_GROUP_BY, EXPLORE_METRICS, EXPLORE_WHAT, FUNDS_BY_ID, MCC_LABELS, PERIODS,
                     RESERVE_NOTE_EUR, SPEND_CATEGORIES, Engine, merchant_codes)
from .tools import confirm_url, explore_query, public_url

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

engine = Engine(today=date.fromisoformat(os.environ.get("DEMO_TODAY", "2026-10-06")))
_coach: Coach | None = None
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="S-Pankki Chat")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
seen: set[str] = set()
owner: dict[str, str] = {}
inbox: dict[str, list[str]] = {}  # web chat session -> confirmations waiting to be shown
last_seen: dict[str, float] = {}  # WhatsApp number -> when they last wrote (free-form replies only within 24 h)
WINDOW_S = 24 * 3600
DEMO_NAME = "Aino"
# Quick-reply buttons that turn into a question for the coach.
ASK = {
    "ask:tight": "My budget is tight until payday. What can I do?",
    "ask:goal": "Help me set up a savings goal.",
    "ask:invest": "I'd like to invest part of my salary. What are my options?",
    "ask:unusual": "Tell me more about that big payment and how it fits my month.",
    "ask:pace": "My savings goal is behind plan. How can I catch up?",
    "ask:week": "What did I spend on in the last 7 days?",
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
    """The default persona's coach (WhatsApp, Aino) or, for a judge's pick, one coach per persona: its own
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
ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "data" / "personas"
STATE_DIR = ROOT / "data" / "state"  # one state file per extra persona: payments and goals never leak across
DEFAULT_PERSONA = "CUST04831"
PERSONA_COOKIE = "kurre_as"


def load_personas(path: Path = PERSONA_DIR / "index.json") -> dict[str, dict]:
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    out = {r["id"]: r for r in rows}
    out.setdefault(DEFAULT_PERSONA, {"id": DEFAULT_PERSONA, "name": DEMO_NAME, "file": None, "age": 24,
                                     "label": "Demo persona, 24", "description": ""})
    out[DEFAULT_PERSONA]["name"] = DEMO_NAME
    return out


PERSONAS = load_personas()
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
            p = PERSONAS[persona]
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            _engines[persona] = Engine(csv_path=PERSONA_DIR / p["file"], today=engine.today,
                                       state_path=STATE_DIR / f"{persona}.json", age=p.get("age"))
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


def send_reply(to: str, reply):
    """Short bubbles like a person typing; a button rides on the last bubble."""
    parts = wa.bubbles(reply.text)
    if reply.buttons:
        wa.send_bubbles(to, parts[:-1])
        first, *rest = reply.buttons
        wa.send_url_button(to, parts[-1], first["label"], first["url"])
        for k, b in enumerate(rest, start=2):  # e.g. one stop link per subscription, each with its own PIN
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
    if bid.startswith("later:"):
        if bid == "later:unusual":
            return {"text": "Great, enjoy it 🙂 I'll keep an eye on your safe-to-spend."}
        if bid == "later:goal":
            return {"text": "Okay 👍 I'll look at your pace again next week."}
        return {"text": "Sure ⏰ I'll remind you again tomorrow at 9:00."}
    if bid.startswith("stop:"):
        p = e.create_stop_subscription(bid[5:])
        owner[p["pending_id"]] = user
        per = "month" if p["cadence"] == "monthly" else "year"
        return {"text": f"Stop future card payments to *{p['name']}* ({money(p['amount'])}/{per})? "
                        "It's ready in S-mobiili: confirm with Face ID.",
                "url": (OPEN_APP, confirm_url("stop", p["pending_id"]) + q)}
    if bid.startswith("keep:"):
        return {"text": f"Okay, {bid[5:]} stays on 👍 I'll tell you if the price changes again."}
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


def handle_message(m: dict):
    to, name = m["from"], first_name(m["name"])
    names[to] = name
    try:
        wa.mark_read_typing(m["id"])
        if m.get("media_id") and not m["text"]:  # a voice note: from here on it is handled like typed text
            try:
                m["text"] = voice.to_text(m["media_id"])
            except voice.VoiceError as e:
                wa.send_text(to, str(e))
                return
            wa.send_text(to, f"🎙️ _{wa.short(m['text'].replace('_', ' '), 300)}_")  # what Birky heard, so a mishearing is visible
        emoji = wa.quick_reaction(m["text"])
        if emoji:
            wa.send_reaction(to, m["id"], emoji)
        word = (m["text"] or "").strip().lower().strip(".!")
        # mid-game or mid-course, a bare "stop" pauses it
        stop = word in ("stop", "lopeta") and (quiz.active(engine, to) or learn.active(engine, to))
        if word in STOP_WORDS and not stop:
            switch_off(to)
            return
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
        bid = m.get("button_id") or ""
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
            wa.send_text(to, "I can read text and listen to voice notes, but not photos or files yet. What would you like to know?")
            return
        text = ASK.get(bid) or wa.redact(m["text"])
        if text.strip().lower() in ("my data", "download my data", "export"):
            wa.send_url_button(to, "Your data, yours to keep (MyData): every transaction and what Birky "
                                   "calculated from it, as a file.", "Download my data", f"{public_url()}/app/export.json")
            return
        if bid not in ASK:
            t = flow_turn(engine, to, text, bid)
            if t:
                if stop:
                    t.text += "\n\nTo switch me off completely, write *STOP* again."
                send_turn(to, t)
                remember(None, to, m["text"] or text, t)
                return
        reply = coach().ask(to, text, name=name, context=flow_context(engine, to))
        for b in reply.buttons:
            if "chart" not in b:
                owner[b["url"].rsplit("/", 1)[-1]] = to
        send_reply(to, reply)
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


def settle_stop_subscription(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    subs = e.subscriptions()
    msg = (f"✅ *{done['name']} stopped.* No more charges of {money(done['amount'])}. "
           f"That's {money(done['per_year_eur'])} a year back in your pocket.\n"
           f"Active subscriptions now: {subs['active_count']}, {money(subs['active_per_month_eur'])} a month.")
    if done["first_time"]:
        notify(pid, msg)
    return done, "Payment stopped", [plain(msg)]


def settle_saving(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    g = e.state["goals"][p["goal_id"]]
    pct = round(g["saved"] / g["target"] * 100)
    msg = (f"✅ *Saved {money(done['amount'])}* for {g['name']}.\n"
           f"{money(g['saved'])} of {money(g['target'])} ({pct}%). Balance now {money(done['balance_eur'])}.")
    if done["first_time"]:
        notify(pid, msg)
    return done, "Saved", [plain(msg)]


SETTLE = {"payment": settle_payment, "investment": settle_investment, "stop_subscription": settle_stop_subscription,
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
DEMO_CARD = "S-Pankki Visa •• 4821 (demo)"
INVEST_NOTE = "Funds can go down as well as up. Past or scenario returns are not a promise."
STOP_NOTE = "Future charges from this merchant will be declined. Also cancel your account with the service."


def recipient(p: dict) -> str:
    return "Landlord · Asunto Oy Demo" if p["category"] == "Housing" else p["name"]


def invest_when(e: Engine, p: dict) -> str:
    return f"Every payday, from {nice_date(e.next_payday()['date'])}" if p["monthly"] else "Today, one-off"


@app.get("/pay/{pid}", response_class=HTMLResponse)
def pay_page(request: Request, pid: str):
    e, p = _pending_or_404(pid, "payment")
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "wa": wa_back(request), "action": f"/pay/{pid}", "title": "Confirm payment", "amount": money(p["amount"]),
        "rows": [("To", recipient(p)), ("Account", DEMO_IBAN), ("For", p["name"]),
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
    e, p = _pending_or_404(pid, "stop_subscription")
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "wa": wa_back(request), "action": f"/stop/{pid}", "title": "Stop recurring card payment",
        "amount": money(p["amount"]) + (" / month" if p["cadence"] == "monthly" else " / year"),
        "rows": [("Merchant", p["name"]), ("Next charge", nice_date(p["next_charge"]) if p["next_charge"] else "-"),
                 ("You save", f"{money(p['per_year_eur'])} a year"), ("Card", DEMO_CARD)],
        "note": STOP_NOTE, "button": f"Stop {p['name']}"})


@app.post("/stop/{pid}", response_class=HTMLResponse)
def stop_confirm(request: Request, pid: str, pin: str = Form("")):
    return pin_confirm(request, f"/stop/{pid}", "stop_subscription", pid, pin)


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
SM_ROUTES = {"pay": "payment", "save": "saving", "invest": "investment", "stop": "stop_subscription"}
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
        return {"title": "Pay a bill", "fi": "Maksu", "heading": p["name"], "amount": money(p["amount"]),
                "rows": [("recipient", "Recipient", recipient(p)), ("iban", "IBAN", DEMO_IBAN),
                         ("reference", "Reference", demo_reference(p["bill_id"])),
                         ("due", "Due date", nice_date(p["due_date"])), ("from", "From", "Current account")],
                "button": f"Pay {money(p['amount'])}", "cheer": f"Paid! {p['name']} is sorted 🌰",
                "steps": [("amount", f"This is the amount we planned: {amt}", "talk"),
                          ("row-recipient", f"It goes to {recipient(p)}, due {nice_date(p['due_date'])}", "talk"),
                          ("row-reference", "IBAN and reference are filled in. Nothing to type", "talk")]}
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
    per = "month" if p["cadence"] == "monthly" else "year"
    return {"title": "Stop a card payment", "fi": "Kortit", "heading": p["name"], "amount": f"{money(p['amount'])} / {per}",
            "note": STOP_NOTE, "button": f"Stop {p['name']}",
            "rows": [("merchant", "Merchant", p["name"]),
                     ("next", "Next charge", nice_date(p["next_charge"]) if p["next_charge"] else "-"),
                     ("saves", "You save", f"{money(p['per_year_eur'])} a year"), ("card", "Card", DEMO_CARD)],
            "cheer": f"Stopped. {short_money(p['per_year_eur'])} a year back 🌰",
            "steps": [("amount", f"{p['name']} costs you {amt} a {per}", "talk"),
                      ("row-saves", f"Stopping it saves {short_money(p['per_year_eur'])} a year", "happy")]}


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


@app.get("/s-mobiili/", response_class=HTMLResponse)
def smobiili_home(request: Request):
    persona = sm_persona(request)
    e = engine_for(persona)
    a = accounts(e)
    steps = [("acct-current", f"Your current account: {short_money(a['current'])}", "talk"),
             ("acct-pocket", "Your Savings pocket" if a["pocket_open"] else "Your Savings pocket, once you open one",
              "talk"),
             ("acct-funds", "Your funds. Only you choose where money goes", "talk"),
             ("recent", "Your latest payments, straight from your account", "talk"),
             ("ask", "Questions? I'm in WhatsApp and right here", "wave")]
    bill = next(iter(e.upcoming_bills(30)), None)
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, persona, screen="home", a=a, recent=e.recent(8), steps=guide_steps(steps),
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
    if kind not in ("salary", "unusual", "renewal"):
        raise HTTPException(400, "kind must be salary, unusual or renewal")
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
    inc = a.get("price_increase")
    when = "tomorrow" if a["days_to_next"] == 1 else f"in {a['days_to_next']} days"
    text = f"🔁 *{a['name']}* renews {when}: {money(a['price_eur'])}."
    if inc:
        text += f"\nThe price went up from {money(inc['from_eur'])} to {money(inc['to_eur'])} on {nice_date(inc['since'])}."
    text += f"\nThat's {money(a['per_year_eur'])} a year. Keep it?"
    return text, [(f"keep:{a['name']}", "Keep it"), (f"stop:{a['name']}", "Stop payments")]


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
        if d["source"] == "subscription":
            return alert_text("renewal", d, name)
        b = d["bill"]
        return bill_text(b, d["safe"], name), [(f"pay:{b['bill_id']}", pay_label(b["name"])),
                                               (f"later:{b['bill_id']}", "Remind me later"),
                                               ("ask:tight", "How do I stretch it?")]
    if kind == "SUBSCRIPTION_PRICE_INCREASE":
        inc = d["price_increase"]
        per = "month" if d["cadence"] == "monthly" else "year"
        return (f"📈 *{d['name']}* went up from {money(inc['from_eur'])} to *{money(inc['to_eur'])}* a {per} "
                f"on {nice_date(inc['since'])}. That's {money(d['extra_per_year_eur'])} more a year, "
                f"{money(d['per_year_eur'])} a year in total.\n\nStill worth it?",
                [(f"keep:{d['name']}", "Keep it"), (f"stop:{d['name']}", "Stop payments")])
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
    subs = engine.subscriptions()["subscriptions"]
    up = next((x for x in subs if x["price_increase"]), subs[0])
    cut = engine.simulate_cut("Restaurants", 20)
    growth = next((r for r in (cut.get("real_past_check") or {}).get("results", []) if r and "Growth" in r["fund"]),
                  {"value_today_eur": 0})
    L = {"rest": engine.monthly_avg("Restaurants", 3), "times": rest["times"], "rent": rent["amount_eur"],
         "safe": engine.safe_to_spend()["safe_to_spend_until_payday_eur"], "sub_name": up["name"],
         "sub_from": (up["price_increase"] or {}).get("from_eur", up["price_eur"]), "sub_to": up["price_eur"],
         "sub_year": up["per_year_eur"], "cut": cut["saving_per_month_eur"], "cut_growth": growth["value_today_eur"]}
    return templates.TemplateResponse(request, "landing.html", _ctx(L=L))


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
        **persona_ctx(persona)))


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
    subs = e.subscriptions()["subscriptions"]
    up = next((x for x in subs if x["status"] == "active" and x["price_increase"]), None)
    off = next((x for x in subs if x["status"] == "inactive"), None)
    if up:
        inc = up["price_increase"]
        cards.append({"id": "subscription", "kind": "price_increase", "title": f"{up['name']} went up",
                      "value": money(inc["to_eur"]), "subtitle": f"was {money(inc['from_eur'])} · {money(up['per_year_eur'])} a year",
                      "name": up["name"], "from_eur": inc["from_eur"], "to_eur": inc["to_eur"],
                      "per_year_eur": up["per_year_eur"],
                      "deep_link": ask_link(f"{up['name']} went up in price. Is it still worth it?")})
    elif off:
        cards.append({"id": "subscription", "kind": "inactive", "title": f"{off['name']} looks unused",
                      "value": money(off["price_eur"]), "subtitle": f"last charged {nice_date(off['last_charge'])}",
                      "name": off["name"], "price_eur": off["price_eur"], "last_charge": off["last_charge"],
                      "deep_link": ask_link("What subscriptions am I paying for?")})
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


@app.post("/api/chat")
async def chat_api(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "send a JSON object")
    sid = "web:" + str(body.get("session", "default"))[:40]
    text = str(body.get("text", "")).strip()[:1000]
    if not text:
        raise HTTPException(400)
    web_sessions.add(sid)
    # The page sends the persona it was drawn for; the cookie covers other callers.
    persona = valid_persona(body.get("as")) if body.get("as") else persona_of(request)
    session_persona[sid] = persona
    e, name = engine_for(persona), name_for(persona)

    def quick(buttons):
        return [{"label": t, "send": f"__btn__:{i}"} for i, t in buttons]

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
        return {"text": "\n\n".join(t.lead + [t.text]), "buttons": list(buttons), "quick": quick(t.buttons)}

    if text == "__start__":  # the web chat's stand-in for Connect S-Pankki
        t = onboarding.start(e, sid)
        remember(persona, sid, "[connected S-Pankki]", t)
        return interview(t, [{"label": "Open My money", "url": "/app"}])
    bid = ""
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
        text = ASK.get(bid, bid)
    if bid not in ASK:
        typed = "" if bid else text
        t = flow_turn(e, sid, typed, bid)
        if t:
            remember(persona, sid, typed or f"[tapped {bid}]", t)
            return interview(t)
    import anyio
    context = flow_context(e, sid)
    try:
        reply = await anyio.to_thread.run_sync(lambda: coach(persona).ask(sid, text, name=name, context=context))
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
    extra = "".join(f"\n\n{x}" for x in (paused, waiting, again and again.text) if x)
    return JSONResponse({"text": reply.text + extra, "tools": reply.tools_used, "trace": reply.trace,
                         "buttons": out, "charts": charts, "quick": quick(again.buttons) if again else []})


@app.get("/api/inbox")
def chat_inbox(session: str = ""):
    return {"messages": inbox.pop("web:" + session[:40], [])}


@app.get("/health")
def health():
    return {"ok": True, "today": engine.today.isoformat(), "balance": engine.balance(), "whatsapp": wa.configured()}
