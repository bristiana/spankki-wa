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
from jinja2 import pass_context
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import bills, broker, enable_banking, guardrails, learn, mirror, mood, onboarding, quiz, receipts, rules, tts, voice
from . import whatsapp as wa
from .agent import Coach
from .engine import BASKETS, DEMO_BANK, HIDE_DEMO_BANK, FUNDS_BY_ID, RESERVE_NOTE_EUR, Engine
from .tools import confirm_url, pending_id_of, public_url, with_web

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
# chat, S-mobiili, the widgets and the WhatsApp demo phone. DEMO_PERSONA=<CUSTID> picks another one.
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


# Family names for the app's own profile (Minä); the dataset has none. Chat and WhatsApp keep first names.
SURNAMES = {HERO: "Virtanen"}  # the hero, Antti


def full_name(persona: str | None) -> str:
    p = valid_persona(persona)
    return " ".join(x for x in (PERSONAS[p]["name"], SURNAMES.get(p, "")) if x)


@app.middleware("http")
async def remember_persona(request: Request, call_next):
    """A link with ?as= also switches the session: the choice is kept in a cookie."""
    response = await call_next(request)
    if "as" in request.query_params:
        response.set_cookie(PERSONA_COOKIE, persona_of(request), max_age=30 * 24 * 3600, samesite="lax", httponly=True)
    pick = request.query_params.get("lang")
    if pick in LANGS and request.cookies.get(LANG_COOKIE) != pick:  # the FI | EN switch: remembered afterwards
        response.set_cookie(LANG_COOKIE, pick, max_age=365 * 24 * 3600, samesite="lax")
    return response


# ---------------- S-mobiili language: Finnish by default, ?lang=en|fi switches and a cookie remembers it ----------------
LANG_COOKIE = "lang"
LANGS = ("fi", "en")


def ui_lang(request: Request) -> str:
    pick = request.query_params.get("lang")
    if pick in LANGS:
        return pick
    kept = request.cookies.get(LANG_COOKIE)
    return kept if kept in LANGS else "fi"


def lang_url(request: Request, code: str) -> str:
    """This page in the other language: the same path and query, with lang= swapped."""
    q = [(k, v) for k, v in request.query_params.multi_items() if k != "lang"] + [("lang", code)]
    return request.url.path + "?" + urlencode(q)


@pass_context
def _t(ctx, fi: str, en: str) -> str:
    """Templates: {{ t("Tilit ja kortit", "Accounts and cards") }}, in the page's language."""
    return en if ui_lang(ctx["request"]) == "en" else fi


# The three baskets and the persona switcher's labels, in Finnish (the engine's own labels are English).
BASKET_FI = {"regular": {"label": "Pakolliset ja säännölliset", "example": "vuokra"},
             "irregular": {"label": "Pakolliset ja epäsäännölliset", "example": "rikkinäinen pesukone"},
             "everyday": {"label": "Lipsuu sormien läpi", "example": "kahvit"}}
PERSONA_FI = (("Demo default", "Oletusasiakas"), ("First job", "Ensimmäinen työ"), ("Student-age", "Opiskelija-ikäinen"),
              ("High earner", "Hyvätuloinen"), ("Irregular income", "Epäsäännölliset tulot"),
              ("Homeowner", "Asunnonomistaja"), ("Espoo area", "Espoon seutu"), ("Vantaa area", "Vantaan seutu"))


def persona_label(label: str, lang: str = "fi") -> str:
    if lang == "en":
        return label.replace("Demo default", "Default customer")
    for en, fi in PERSONA_FI:
        label = label.replace(en, fi)
    return label


def tr(lang: str, fi: str, en: str) -> str:
    return en if lang == "en" else fi


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


def fi_money(x: float) -> str:
    """S-mobiili writes money the Finnish way: 2 000,00 € (non-breaking spaces), -12,30 €."""
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " €"


templates.env.filters["nb"] = nb
templates.env.filters["money"] = money_nb
templates.env.filters["fimoney"] = fi_money
templates.env.globals.update(t=_t, ui_lang=ui_lang, lang_url=lang_url, persona_label=persona_label, basket_fi=BASKET_FI,
                             basket_fi_by_label={BASKETS[k]["label"]: v["label"] for k, v in BASKET_FI.items()})
DEMO_COOKIE = "oiva_demo"
templates.env.globals["wa_login"] = lambda request: (enable_banking.mask(wa_phone(request))
                                                     or ("Demo" if request.cookies.get(DEMO_COOKIE) else ""))


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
    buttons = [b for b in reply.buttons if "chart" not in b]  # a chart is drawn only in the web chat
    if reaction and message_id:
        wa.send_reaction(to, message_id, reaction)
    if not (reply.text or "").strip():
        if buttons:  # nothing to say but something to tap
            for b in buttons:
                wa.send_url_button(to, wa.OVERFLOW_BODY, b["label"], b["url"])
        elif not (reaction and message_id):
            wa.send_text(to, "Okay.")
        return
    parts = wa.bubbles(reply.text)
    if buttons:
        wa.send_bubbles(to, parts[:-1])
        first, *rest = buttons
        wa.send_url_button(to, parts[-1], first["label"], first["url"])
        for k, b in enumerate(rest, start=2):  # e.g. several links, each with its own PIN
            wa.send_url_button(to, f"{k}/{len(buttons)}", b["label"], b["url"])
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
    wa.send_text(to, "Done. Oiva is switched off for this number and our chat history is deleted. "
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
                "url": (OPEN_APP, confirm_url("pay", p["pending_id"], web))}
    if bid.startswith("resume:") and bid != "resume:later":  # "Finish it" on a confirmation left open yesterday
        p = e.pending(bid[7:])
        if not p or p["status"] != "pending":
            return {"text": "That one's already done."}
        owner[p["pending_id"]] = user
        path = {"payment": "pay", "investment": "invest", "saving": "save", "pause_investing": "stop"}[p["kind"]]
        return {"text": f"Here it is again: *{p['name']}*, {money(p['amount'])}. It's ready in S-mobiili: "
                        "confirm with Face ID.",
                "url": (OPEN_APP, confirm_url(path, p["pending_id"], web))}
    if bid.startswith("later:"):
        note_later(e, user, bid)
        if bid == "later:unusual":
            return {"text": "Okay, enjoy! I'll keep an eye on what's left until payday."}
        if bid == "later:goal":
            return {"text": "Okay. I'll look at your pace again next week."}
        if bid == "later:bills":
            return {"text": "Okay. I'll keep an eye on your bills."}
        return {"text": "Sure, I'll check back with you tomorrow."}
    if bid == "pause:investing":
        p = e.create_pause_investing()
        owner[p["pending_id"]] = user
        nxt = f" The next one would go out on {nice_date(p['next_charge'])}." if p["next_charge"] else ""
        return {"text": f"Pause your *monthly investing* of about *{money(p['amount'])}*?{nxt} What you've invested "
                        "so far stays invested. It's ready in S-mobiili: confirm with Face ID.",
                "url": (OPEN_APP, confirm_url("stop", p["pending_id"], web))}
    if bid.startswith("keep:"):
        return {"text": "Okay, your monthly investing stays on. Paying yourself first, on autopilot."}
    if bid.startswith("remind:"):
        c = next((x for x in e.commitments()["regular_payments"] if x["id"] == bid[7:]), None)
        if c and c["next_expected"]:
            return {"text": f"Okay, I'll message you the day before your {c['name'].lower()} is due "
                            f"({nice_date(c['next_expected'])})."}
        return {"text": "Your utility bills come on different days, so there's no date to remind you of. I'll tell "
                        "you when one is well above your usual range."}
    if bid == "learn:buffer" or bid.startswith("learn:save:"):  # a lesson's action: a goal, then the usual save
        goal_id, amount = learn.goal_for(e, bid)
        if amount < 1:
            g = e.state["goals"][goal_id]
            return {"text": f"Your *{g['name']}* goal of *{money(g['target'])}* is set. Nothing is free to move "
                            "before payday, so the first saving can wait for payday."}
        bid = f"save:{goal_id}:{amount:.0f}"
    if bid.startswith("save:"):
        _, goal_id, amount = bid.split(":")
        p = e.create_saving(goal_id, float(amount))
        owner[p["pending_id"]] = user
        goal = p["name"].split(": ", 1)[1]
        return {"text": f"Move *{money(p['amount'])}* to your savings pocket for *{goal}*? "
                        "It's ready in S-mobiili: confirm with Face ID.",
                "url": (OPEN_APP, confirm_url("save", p["pending_id"], web))}
    if bid == "quiz:money":
        return {"text": "Your balance and your three baskets, on the S-mobiili home screen.",
                "url": ("Open S-mobiili", f"{public_url()}/s-mobiili/{q}")}
    return None


def flow_context(e: Engine, user: str, text: str = "") -> str | None:
    """What the coach should know when a question comes in the middle of the interview or the basket game: the
    waiting question is sent again right after the reply, so the reply must not end with a question of its own."""
    waiting = onboarding.waiting(e, user, text)
    if waiting:
        return ("The person asked for the short goal interview (no AI) and their message is about its open question: "
                f"\"{waiting}\" Explain what they asked in a few lines and stop: don't ask that question again or add "
                "one of your own; they answer it by typing or with its buttons.")
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
    """A "maybe later": the open thread Oiva comes back to once, the next day (rules.open_threads)."""
    e.state.setdefault("later", {})[user] = {"key": key, "offer": offer, "at": time.time()}
    e._save()


def note_chat_later(e: Engine, user: str, text: str, last_said: str):
    """"maybe later" typed right after Oiva offered something: remember the offer (its last bubble, a question)."""
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
    parts = [flow_context(e, user, text), guardrails.escalation_context(kind) if kind else None, extra]
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
                  "asked them to delete it. Don't mention it again; answer only the rest, in one bubble.")


def flow_turn(e: Engine, user: str, text: str, bid: str = "") -> onboarding.Turn | None:
    """The no-AI flows, in order: the Learn course, the basket game, the interview. None = the coach answers."""
    return flow_turn_named(e, user, text, bid)[1]


def flow_turn_named(e: Engine, user: str, text: str, bid: str = "") -> tuple[str, onboarding.Turn | None]:
    """flow_turn, and which flow answered ("learn", "quiz", "interview"; "" = none): the helper squirrel's pose."""
    for name, flow in (("learn", learn), ("quiz", quiz), ("interview", onboarding)):
        t = flow.turn(e, user, text, bid)
        if t:
            return name, t
    return "", None


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
        photo: bool = False, viewer: str | None = None):
    """coach.ask, telling the bill tool whose turn it is and whether it may prepare the bill payment. viewer: the
    WhatsApp number whose real linked banks may be used (default: the WhatsApp sender; none for a web visitor)."""
    hint = None if photo else bills.context_line(engine_for(persona), user)
    token = bills.turn.set((user, may_pay))
    who = viewer or (None if user.startswith("web:") else user)
    vtoken = enable_banking.viewer.set(who)
    htoken = HIDE_DEMO_BANK.set(bool(enable_banking.load(who)) if who else False)
    try:
        return coach(persona).ask(user, text, name=name, context=" ".join(x for x in (context, hint) if x) or None)
    finally:
        bills.turn.reset(token)
        enable_banking.viewer.reset(vtoken)
        HIDE_DEMO_BANK.reset(htoken)


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
        bills.note_unreadable(engine, to)  # a follow-up "explain this bill" then refers to this photo
        wa.send_text(to, "That photo didn't open for me. Can you send it again?")
        return
    problem = bills.reply_problem(bill)
    if problem:
        wa.send_text(to, problem)
        return
    reply = ask_about_bill(None, to, bill, m["text"] and wa.redact(m["text"]), name)
    for b in reply.buttons:
        if "chart" not in b:
            owner[pending_id_of(b["url"])] = to
    send_reply(to, reply, m["id"])


def handle_message(m: dict):
    to, name = m["from"], first_name(m["name"])
    names[to] = name
    mirror.incoming(to, m)  # the owner's messages show in the app's chat too
    try:
        wa.mark_read_typing(m["id"])
        if m.get("kind") == "audio" and m.get("media_id") and not m["text"]:  # a voice note: handled like typed text
            try:
                m["text"] = voice.to_text(m["media_id"])
            except voice.VoiceError as e:
                wa.send_text(to, str(e))
                return
            wa.send_text(to, f"Heard: _{wa.short(m['text'].replace('_', ' '), 300)}_")  # what Oiva heard, so a mishearing is visible
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
        said = (m["text"] or "").strip()
        if not secret and LOGIN.match(said):  # works before Connect S-Pankki too: it only signs a link for this number
            wa.send_url_button(to, "Tap to log in to the S-Pankki site with this WhatsApp. The link is just for "
                                   "you; don't forward it.", "Log in", f"{public_url()}/login?t={enable_banking.sign(to)}")
            return
        if not secret and REFRESH_BANK.match(said) and enable_banking.configured() and enable_banking.load(to):
            n = enable_banking.refresh(to)
            total = sum(b for x in enable_banking.picture(to) for a in x["accounts"] if (b := a.get("balance_eur")))
            wa.send_text(to, f"Refreshed {n} account(s) from your bank: {money(total)} in total, all currencies in euros.")
            return
        if not secret and ADD_BANK.search(said) and enable_banking.configured():
            wa.send_url_button(to, "Bring your other bank here too (Revolut, OP, Nordea...). Read only: I see balances "
                                   "and the last 90 days, I can't move money there. The link is just for you.",
                               "Add a bank", f"{public_url()}/bank?t={enable_banking.sign(to)}")
            return
        if not connected(to) and to not in engine.state.get("switched_off", {}):
            # Open demo: a new number goes straight onto the synthetic S-Pankki data, so anyone (the jury) can
            # just ask. Someone who wrote STOP still has to reconnect on purpose (the button below).
            engine.state.setdefault("connected", {})[to] = {"scope": ["demo"], "at": engine.today.isoformat()}
            engine._save()
            if onboarding.GREETING.match(said) or not said:
                welcome(to)
                return
            wa.send_text(to, f"Hi {name}, I'm *Oiva* from S-Pankki. Ask me what's left until payday, or where last "
                             "month's money went. Write *add bank* to bring in your Revolut too.")
        if not connected(to):
            wa.send_url_button(
                to,
                f"Hi {name}, I'm *Oiva*, S-Pankki's money coach. I can tell you where your money goes and nudge you "
                "before a bill is due.\n\nFirst, connect your S-Pankki account. You pick what I see, "
                "and you can switch me off any time.",
                "Connect S-Pankki", f"{public_url()}/connect?u={to}")
            return
        if onboarding.RESTART.match(m["text"] or ""):  # a clean slate and a hello; no flow starts by itself
            fresh_start(engine, to)
            wa.send_text(to, onboarding.HELLO)
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
            wa.send_url_button(to, "Your data, yours to keep (MyData): every transaction and what Oiva "
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
                owner[pending_id_of(b["url"])] = to
        send_reply(to, reply, None if secret else m["id"])
        for note in (quiz.paused_note(engine, to), learn.paused_note(engine, to)):  # how to get back to it
            if note:
                wa.send_text(to, note)
        onboarding.side_question(engine, to, text)  # mid-interview: never re-sent; another topic pauses it
    except Exception:
        log.exception("handling message failed")
        wa.send_text(to, "Something broke on my end. Try again in a minute?")


# ---------------- Connect (consent) ----------------
@app.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, u: str = ""):
    return templates.TemplateResponse(request, "connect.html", {"u": u, "done": connected(u) if u else False,
                                                                 "linked": engine.linked_bank()})


@app.get("/connect/bank", response_class=HTMLResponse)
def bank_page(request: Request, u: str = ""):
    """PSD2 account information consent for a second bank. Mock: the bank and its data are synthetic. With Enable
    Banking set up, "Add another bank" goes to the real one (/bank); the mock stays at ?demo=1."""
    if enable_banking.configured() and not request.query_params.get("demo"):
        return RedirectResponse("/bank", status_code=303)
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
                  "Oiva now counts the card bill in safe to spend.",
                  "Ask Oiva: \"Show my money across all my banks\"."],
        "wa": wa_back(request) if u else ""})


# ---------------- another bank, for real (Enable Banking), login with WhatsApp ----------------
ADD_BANK = re.compile(r"\b(add|link|connect|bring)\b(?!.*s-?pankki).*\b(banks?|accounts?|revolut|enable\s*banking)\b",
                      re.I)


REFRESH_BANK = re.compile(r"^\s*(refresh|sync|update|reload)\b.*\b(banks?|revolut|accounts?)\b", re.I)
LOGIN = re.compile(r"^\s*(log\s*in|login|sign\s*in)\b", re.I)


def wa_phone(request: Request) -> str | None:
    """The WhatsApp number behind a signed link (?t=) or the cookie it set."""
    return enable_banking.verify(request.query_params.get("t") or request.cookies.get(enable_banking.COOKIE))


def mirror_viewer(request: Request) -> bool:
    """The app's chat mirrors the owner's WhatsApp only for the owner, logged in with their WhatsApp link: their
    conversation can hold real bank numbers, so the demo account and everyone else get their own chat."""
    me = mirror.owner()
    return bool(me) and re.sub(r"\D", "", wa_phone(request) or "") == me


def _set_login(response, token: str):
    response.set_cookie(enable_banking.COOKIE, token, max_age=enable_banking.LINK_DAYS * 86400, httponly=True,
                        secure=True, samesite="lax")


@app.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str = "/s-mobiili/"):
    """Login with WhatsApp: ?t= comes from the link Oiva sends when you write "login"; without it, the way there."""
    nxt = next if next.startswith("/") and not next.startswith("//") else "/s-mobiili/"
    t = request.query_params.get("t")
    if request.query_params.get("demo"):  # the jury: the demo account, no WhatsApp needed
        r = RedirectResponse(nxt, status_code=303)
        r.set_cookie(DEMO_COOKIE, "1", max_age=enable_banking.LINK_DAYS * 86400, httponly=True, secure=True,
                     samesite="lax")
        return r
    if t and enable_banking.verify(t):
        r = RedirectResponse(nxt, status_code=303)
        _set_login(r, t)
        return r
    return templates.TemplateResponse(request, "login.html", {"wa": os.environ.get("WA_DISPLAY_NUMBER", ""),
                                                              "me": enable_banking.mask(wa_phone(request)),
                                                              "expired": bool(t)})


PRIVACY = [
    ("What Oiva sees", "Your S-Pankki demo data, and, only if you link it, your other bank through Enable Banking "
     "(PSD2, read only): account balances and the last 90 days of transactions. Oiva can't move money there."),
    ("Who sees it", "Only you: linked banks are tied to your WhatsApp number and shown only after you log in with it. "
     "To answer you, your messages and the numbers they need go to Anthropic (the AI) and, on WhatsApp, through Meta."),
    ("How long", "Bank access lasts 90 days, then you confirm again. Remove a bank any time on the Add a bank page; "
     "write STOP on WhatsApp to switch Oiva off and delete the chat history."),
]
TERMS = [
    ("What this is", "A hackathon prototype. Information and education about your own money, not financial advice, "
     "not a loan offer."),
    ("Other banks", "Read only, through Enable Banking. You approve access in your own bank and can withdraw it "
     "there or here."),
    ("No guarantees", "Numbers are estimates from your transactions. Check anything important with your bank."),
]


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "legal.html", {"title": "Privacy", "sections": PRIVACY})


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse(request, "legal.html", {"title": "Terms", "sections": TERMS})


@app.get("/logout")
def logout():
    r = RedirectResponse("/s-mobiili/", status_code=303)
    r.delete_cookie(enable_banking.COOKIE)
    r.delete_cookie(DEMO_COOKIE)
    return r


@app.get("/bank", response_class=HTMLResponse)
def real_bank_page(request: Request):
    phone = wa_phone(request)
    if not phone and request.cookies.get(DEMO_COOKIE):  # the demo account links the synthetic Demo Bank
        return RedirectResponse("/connect/bank?demo=1", status_code=303)
    if not phone:
        return RedirectResponse("/login?next=/bank", status_code=303)
    if not enable_banking.configured():
        return templates.TemplateResponse(request, "done.html", {
            "title": "Not available yet", "failed": True, "lines": ["Linking other banks isn't switched on here."],
            "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})
    try:
        names = enable_banking.banks()
    except Exception:
        log.exception("bank list failed")
        names = enable_banking.FIRST
    linked = enable_banking.load(phone)
    r = templates.TemplateResponse(request, "bank_real.html", {"banks": names, "linked": linked,
                                                               "err": request.query_params.get("err")})
    if request.query_params.get("t"):
        _set_login(r, request.query_params["t"])
    return r


@app.post("/bank/start")
def real_bank_start(request: Request, bank: str = Form(...)):
    phone = wa_phone(request)
    if not phone:
        return RedirectResponse("/bank", status_code=303)
    try:
        url = enable_banking.start(phone, bank, f"{public_url()}/eb/callback")
    except Exception:
        log.exception("bank start failed")
        return RedirectResponse("/bank?err=start", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.post("/bank/unlink")
def real_bank_unlink(request: Request):
    phone = wa_phone(request)
    if phone:
        enable_banking.unlink(phone)
    return RedirectResponse("/bank", status_code=303)


@app.get("/eb/callback", response_class=HTMLResponse)
def real_bank_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    wa_no = os.environ.get("WA_DISPLAY_NUMBER", "")
    p = enable_banking.pending(state)
    if not p:
        return templates.TemplateResponse(request, "done.html", {"title": "Link expired", "failed": True, "wa": wa_no,
                                                                 "lines": ["Write \"add bank\" to Oiva again."]})
    phone, bank = p
    if not code:
        return templates.TemplateResponse(request, "done.html", {"title": f"{bank} not linked", "failed": True,
                                                                 "wa": wa_no, "lines": ["The bank didn't finish the "
                                                                 "connection, so nothing was saved."]})
    try:
        link = enable_banking.finish(phone, bank, code)
    except Exception:
        log.exception("bank session failed")
        return templates.TemplateResponse(request, "done.html", {"title": f"{bank} not linked", "failed": True,
                                                                 "wa": wa_no, "lines": ["The bank didn't open the "
                                                                 "connection. Try again in a minute."]})
    if not link["accounts"]:
        return templates.TemplateResponse(request, "done.html", {"title": f"{bank}: no account shared", "failed": True,
                                                                 "wa": wa_no, "lines": ["Try again and tick the "
                                                                 "account when your bank asks which ones to share."]})
    total = sum(a.get("balance_eur") or 0 for a in link["accounts"])
    n = sum(len(a.get("transactions") or []) for a in link["accounts"])
    line = f"{bank} linked: {len(link['accounts'])} account(s), {money(total)} in total, {n} transactions."
    try:
        wa.send_text(phone, line + " Ask me: \"Show my money across all my banks\".")
    except Exception:
        log.exception("bank linked message failed")
    return templates.TemplateResponse(request, "done.html", {"title": f"{bank} linked", "wa": wa_no, "lines": [
        line, "Read only, last 90 days. Oiva now adds it to your full picture."]})


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
    """Right after Connect: the three baskets, the S-mobiili button and a hello. No questions pushed: Oiva follows
    the person's lead (a goal interview, the risk question or check-ins only when they ask)."""
    lead = onboarding.intro(engine)
    wa.send_bubbles(to, lead[:-1])
    wa.send_url_button(to, "Your whole money picture, any time.", "Open S-mobiili", f"{public_url()}/s-mobiili/")
    wa.send_text(to, lead[-1])
    remember(None, to, "[connected S-Pankki]", onboarding.Turn(lead[-1], lead=lead[:-1], done=True))


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
    "expired": ("This link has expired", "Payment and saving links from Oiva work once, and this one is no longer valid. "
                                         "Nothing was paid or moved. Ask Oiva again for a fresh link."),
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
    return msg.replace("*", "")


# One confirm for every way in (the PIN page and S-mobiili's Face ID): the same engine call, the same double-confirm
# and overdraft checks, the same message back to WhatsApp or the web chat. Each returns (what the engine did, the
# receipt title, the receipt lines) and raises ValueError when the bank says no.
def settle_payment(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    s = e.safe_to_spend()
    if done["first_time"]:
        notify(pid, f"*{done['name']} paid*: {money(done['amount'])}\n"
                    f"Balance now {money(done['balance_eur'])}.\n"
                    f"Safe to spend until payday ({nice_date(s['next_payday']['date'])}): "
                    f"*{money(s['safe_to_spend_until_payday_eur'])}*, about {money(s['safe_per_day_eur'])} a day.")
    return done, "Payment sent", [f"{done['name']}: {money(done['amount'])}", f"Balance {money(done['balance_eur'])}"]


def settle_investment(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    if done.get("monthly"):
        msg = (f"*Monthly saving started*: {money(done['amount'])} into {done['name']} every payday, "
               f"first on {nice_date(done['starts'])}. You can change or stop it any time.")
    else:
        msg = f"*Invested* {money(done['amount'])} in {done['name']}. Balance now {money(done['balance_eur'])}."
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
    msg = (f"*Monthly investing paused.* The deposit of about {money(done['amount'])} won't go out{when}. "
           f"That's {money(done['per_year_eur'])} a year staying on your account.\n"
           "What you've invested so far stays invested. Start again any time.")
    if done["first_time"]:
        notify(pid, msg)
    return done, "Investing paused", [plain(msg)]


def settle_saving(e: Engine, pid: str, p: dict) -> tuple[dict, str, list[str]]:
    done = e.confirm(pid)
    g = e.state["goals"][p["goal_id"]]
    pct = round(g["saved"] / g["target"] * 100)
    msg = (f"*Saved {money(done['amount'])}* for {g['name']}.\n"
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


DEMO_IBAN = "FI00 0000 0000 0000 00"
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
        return ("Order sent to *Trading 212 (Practice account)*:\n" + "\n".join(lines)
                + "\nIt fills at the next market price; if the exchange is closed, when it opens.")
    return "The broker order didn't go through yet. Your money is set aside and I'll retry."


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
                 ("From", "S-Pankki current account"), ("To", "Savings account")],
        "button": f"Save {money(p['amount'])}"})


@app.post("/save/{pid}", response_class=HTMLResponse)
def save_confirm(request: Request, pid: str, pin: str = Form("")):
    return pin_confirm(request, f"/save/{pid}", "saving", pid, pin)


# ---------------- S-mobiili mock: Oiva's buttons open the bank app with the move filled in ----------------
# A concept prototype, clearly labelled: our own screens, not S-Bank's. Review -> Face ID (simulated) -> receipt.
SM_ROUTES = {"pay": "payment", "save": "saving", "invest": "investment", "stop": "pause_investing"}
RISK_WORDS = {"low": "Low risk: small ups and downs, and slow growth.",
              "medium": "Medium risk: it can drop in a bad year, and grows more over time.",
              "high": "High risk: it can fall 20-30% in a bad year. Best for 10+ years."}
# The same review screens in Finnish (S-mobiili's default language). Oiva's guide lines are English, except the save flow's.
RISK_WORDS_FI = {"low": "Matala riski: pienet heilahtelut ja hidas kasvu.",
                 "medium": "Keskitason riski: arvo voi laskea huonona vuonna, ja se kasvaa enemmän ajan myötä.",
                 "high": "Korkea riski: arvo voi laskea 20-30 % huonona vuonna. Sopii 10+ vuodeksi."}
FUND_WHAT_FI = {"prudent": "Euroalueen rahamarkkinat (seuraa XEON-ETF:ää). Hyvin pienet heilahtelut, pienin odotettu kasvu.",
                "balanced": "Puolet maailman osakkeita, puolet euroalueen rahamarkkinoita (VWCE + XEON). Voi laskea huonona "
                            "vuonna, kasvaa enemmän ajan myötä.",
                "growth": "Maailman osakkeet, noin 3 700 yritystä (seuraa VWCE-ETF:ää). Voi laskea 20-30 % huonona vuonna; "
                          "suurin odotettu kasvu 10+ vuodessa."}
INVEST_NOTE_FI = "Rahastojen arvo voi nousta tai laskea. Aiemmat tai arvioidut tuotot eivät ole lupaus."
PAUSE_NOTE_FI = "Vain uudet kuukausittaiset talletukset loppuvat. Voit aloittaa sijoittamisen uudelleen milloin vain."


def fi_day(iso: str) -> str:
    """2026-10-31 -> 31.10.2026 (a full date the Finnish way)."""
    d = date.fromisoformat(iso)
    return f"{d.day}.{d.month}.{d.year}"


def demo_reference(seed: str) -> str:
    """A made-up Finnish-style payment reference (7-3-1 check digit), the same for the same bill: demo only."""
    base = str(10**8 + int(hashlib.sha1(seed.encode()).hexdigest(), 16) % (9 * 10**8))
    ref = base + str(-sum(int(d) * w for d, w in zip(reversed(base), (7, 3, 1) * 3)) % 10)
    return f"{ref[:5]} {ref[5:]}"


def short_money(x: float) -> str:
    """97 € rather than 97.00 € in Oiva's bubbles."""
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


def sm_flow(route: str, e: Engine, p: dict, lang: str = "en") -> dict:
    """A flow's review screen and Oiva's lines on it. Guide targets are element ids on the page. lang: the screen's
    language (title, row values, note); row labels come in English and the template has their Finnish names. Oiva's
    guide lines are English, except the save flow's, which follow the screen."""
    amt = short_money(p["amount"])
    fi = lang != "en"
    T = lambda fi_text, en_text: fi_text if fi else en_text  # noqa: E731
    m = fi_money if fi else money
    day = lambda iso: fi_day(iso) if fi else nice_date(iso)  # noqa: E731
    acct = T("Käyttötili", "Current account")
    if route == "pay":
        photo = p.get("source") == "photo"  # a bill photo (bills.py): the bill's own IBAN and reference
        to = p["name"] if photo else recipient(p)
        due = f", due {nice_date(p['due_date'])}" if p.get("due_date") else ""
        return {"title": T("Maksa lasku", "Pay a bill"), "fi": "Maksu", "heading": p["name"], "amount": money(p["amount"]),
                "rows": [("recipient", "Recipient", to), ("iban", "IBAN", bill_iban(p) if photo else DEMO_IBAN),
                         ("reference", "Reference", (p.get("reference") or T("ei laskussa", "none on the bill"))
                          if photo else demo_reference(p["bill_id"])),
                         ("due", "Due date", day(p["due_date"]) if p.get("due_date") else "-"),
                         ("from", "From", acct)],
                "button": f"Pay {money(p['amount'])}", "cheer": f"Paid! {p['name']} is sorted.",
                "steps": [("amount", f"This is the amount we planned: {amt}", "talk"),
                          ("row-recipient", f"It goes to {to}{due}", "talk"),
                          ("row-reference", "IBAN and reference are copied from your bill. Nothing to type" if photo
                           else "IBAN and reference are filled in. Nothing to type", "talk")]}
    if route == "save":  # S-Pankki's real savings steps: goal, where it goes, amount now, review
        g = e.state["goals"][p["goal_id"]]
        after = g["saved"] + p["amount"]
        pct = round(after / g["target"] * 100)
        short = (lambda x: nb(fi_money(x).replace(",00", ""))) if fi else short_money
        amt, target, dl = short(p["amount"]), short(g["target"]), g.get("deadline")
        by, when = "", T("Ei asetettu", "Not set")
        if dl:
            d = date.fromisoformat(dl)
            year = f" {d.year}" if d.year != e.today.year else ""
            by = T(f" {FI_MONTH_FROM[d.month - 1][:-3]}hun{year} mennessä", f" by {d:%B}{year}")
            when = T(fi_day(dl), f"{d.day} {d:%B %Y}")
        to = T("Säästötili", "Savings account")
        return {"title": T("Siirto säästöön", "Move to savings"), "heading": g["name"], "amount": money(p["amount"]),
                "goal": {"name": g["name"], "target": m(g["target"]), "date": when}, "now": m(p["amount"]),
                "rows": [("goal", "Goal", g["name"]),
                         ("so-far", "Saved so far", f"{m(g['saved'])} {T('/', 'of')} {m(g['target'])}"),
                         ("after", "After this", f"{m(after)} ({pct}%)"),
                         ("debit", "From", acct), ("to", "To", to)],
                "cheer": T(f"Säästetty! {amt} on matkalla tavoitteeseen {g['name']}.",
                           f"Saved! {amt} is on its way to {g['name']}."),
                "steps": [("sv-goal", T(f"Tämä on tavoitteesi: {target}{by}.", f"This is your goal: {target}{by}."), "talk"),
                          ("sv-where", T("Rahat menevät säästötilille, erilleen käyttörahoista.",
                                         "The money goes to a savings account, separate from your spending."), "talk"),
                          ("sv-amount", T(f"Vain {amt} nyt, ei mitään automaattista.",
                                          f"Just {amt} now, nothing automatic."), "talk"),
                          ("sv-review", T("Tarkista ja vahvista Face ID:llä.", "Check it and confirm with Face ID."),
                           "happy")]}
    if route == "invest":
        f = FUNDS_BY_ID[p["fund_id"]]
        per = " / month" if p["monthly"] else ""
        when = invest_when(e, p) if not fi else (f"Joka palkkapäivä, alkaen {fi_day(e.next_payday()['date'])}"
                                                 if p["monthly"] else "Tänään, kertasijoitus")
        risk = (f"{RISK_WORDS_FI[f['risk']]} {f['risk_level']}/7." if fi
                else f"{RISK_WORDS[f['risk']]} {f['risk_level']} of 7.")
        return {"title": T("Kuukausisijoitus", "Invest monthly") if p["monthly"] else T("Sijoita", "Invest"),
                "fi": "Sijoitukset", "heading": f["name"], "amount": money(p["amount"]) + per,
                "note": T(INVEST_NOTE_FI, INVEST_NOTE),
                "rows": [("fund", "Fund", f"{f['name']} {T('(oma valintasi)', '(your choice)')}"),
                         ("risk", "Risk", risk),
                         ("holds", "Holds", FUND_WHAT_FI.get(f["id"], f["what"]) if fi else f["what"]),
                         ("when", "When", when), ("from", "From", acct)],
                "button": "Start monthly investing" if p["monthly"] else f"Invest {money(p['amount'])}",
                "cheer": "Done! It starts on payday." if p["monthly"] else "Invested.",
                "steps": [("amount", f"This is the amount we planned: {amt}{per}", "talk"),
                          ("row-fund", "The fund you picked. I explain, you choose", "talk"),
                          ("row-risk", "Risk in plain words: " + RISK_WORDS[f["risk"]].split(": ", 1)[1], "think")]}
    return {"title": T("Keskeytä kuukausisijoitus", "Pause monthly investing"), "fi": "Sijoitukset",
            "heading": T("Kuukausisijoitus", "Monthly investing"),
            "amount": f"{money(p['amount'])} / month", "note": T(PAUSE_NOTE_FI, PAUSE_NOTE), "button": "Pause investing",
            "rows": [("what", "What", T("Kuukausittainen talletus sijoituksiin", "Your monthly deposit to investments")),
                     ("usual", "Usually on", T(f"Kuun {p['usual_day']}. päivä", f"Day {p['usual_day']} of the month")),
                     ("next", "Next one", day(p["next_charge"]) if p["next_charge"] else "-"),
                     ("invested", "Already invested", T("Pysyy sijoitettuna", "Stays invested")), ("from", "From", acct)],
            "cheer": f"Paused. {short_money(p['per_year_eur'])} a year stays with you.",
            "steps": [("amount", f"Your monthly investing: about {amt} a month", "talk"),
                      ("row-next", "Only new deposits stop. " + (f"The next one was due {nice_date(p['next_charge'])}"
                                                                if p["next_charge"] else "Nothing else changes"), "talk"),
                      ("row-invested", "What you've already invested stays invested", "happy")]}


def guide_steps(steps) -> list[dict]:
    return [{"target": t, "say": nb(s), "mood": m} for t, s, m in steps]


def sm_ctx(request: Request, persona: str, **extra) -> dict:
    q = request.query_params
    keep = {k: q[k] for k in ("persona", "web", "autoplay") if q.get(k)}
    tab_url = lambda t: "/s-mobiili/" + (f"?{urlencode({**keep, **({'tab': t} if t else {})})}"  # noqa: E731
                                         if keep or t else "")
    en = ui_lang(request) == "en"
    # "Ask Oiva" on any screen: ask(fi, en) is the Helper chat asking that question in the page's language (ask() opens it)
    return {"wa": wa_back(request), "web": bool(q.get("web")), "autoplay": q.get("autoplay") == "1",
            "ask": lambda fi="", en_q="": chat_url(request, en_q if en else fi, persona),
            "home_url": tab_url(None), "tab_urls": {t: tab_url(t) for t in SM_TABS if t != "koti"},
            "name": name_for(persona), "full_name": full_name(persona), "tab": "koti", **extra}


# The real app's bottom bar: Koti · S-Etukortti · (+) Maksu · Pankki · Minä. "kortti" is the card page under Pankki.
SM_TABS = ("koti", "etukortti", "pankki", "mina", "kortti", "raha")
# "raha" (Pankki -> Your money): the seven Oiva charts, drawn from app/static/mockups/charts/data (built by
# scripts/build_chart_mockup_fixtures.py). Those figures are one customer's own (the one their "source" names), so
# the tile shows on that account only.
MONEY_DATA = Path(__file__).parent / "static/mockups/charts/data"
MONEY_SPEND = json.loads((MONEY_DATA / "spend.json").read_text())
MONEY_PERSONA = next(iter(re.findall(r"CUST\d{5}", MONEY_SPEND["source"])), None)
FI_MONTH_FROM = ("tammikuulta", "helmikuulta", "maaliskuulta", "huhtikuulta", "toukokuulta", "kesäkuulta",
                 "heinäkuulta", "elokuulta", "syyskuulta", "lokakuulta", "marraskuulta", "joulukuulta")


def money_asks() -> dict:
    """What tapping Oiva or a chart card on Your money asks her, (FI, EN), each built from the card's own title in
    money.js (the month is the charts' month, from the data). "oiva" is the floating Oiva's question about the screen."""
    m = int(MONEY_SPEND["month"][5:7])
    fi, en = FI_MONTH_FROM[m - 1].removesuffix("lta"), date(2000, m, 1).strftime("%B")  # syyskuu, September
    return {"oiva": ("Kerro kulutuksestani tässä kuussa", "Tell me about my spending this month"),
            "trend": (f"Miltä kulutukseni {fi}ssa näyttää?", f"How is my spending in {en} going?"),
            "cats": (f"Mihin rahani menivät {fi}ssa?", f"Where did my money go in {en}?"),
            "budget": ("Paljonko budjeteissani on jäljellä?", "How much is left in my budgets?"),
            "sit": ("Miten vertaudun kaltaisiini?", "Where do I sit compared with people like me?"),
            "fig": ("Miten tuloni vertautuvat kaltaisiini?", "How does my income compare with people like me?"),
            "income": ("Paljonko jää laskujen ja sijoittamisen jälkeen?", "What is left after bills and investing?"),
            "cal": (f"Mitkä ovat {fi}n säännölliset maksuni?", f"What are my regular payments in {en}?")}


def chat_url(request: Request, q: str, persona: str) -> str:
    """The S-mobiili chat asking q once (no q: just open it), as the shown persona when ?persona= picks it. Under
    ?web=1 (this page is inside the web chat's confirm sheet) it is the bare ?embed=1 chat."""
    p = request.query_params
    query = urlencode({**({"q": q} if q else {}), **({"embed": "1"} if p.get("web") else {}),
                       **({"as": persona} if p.get("persona") else {})})
    return "/chat" + (f"?{query}" if query else "")


def demo_bonus(persona: str, today: date) -> dict:
    """The S-Etukortti bonus on S-mobiili's home. Bonus is not in the challenge data, so this is a made-up, stable
    figure per persona (labelled demo on the page), in the range a household's S-Group shopping earns."""
    h = int(hashlib.sha1(persona.encode()).hexdigest(), 16)
    pay_month = today.month if today.day < 10 else today.month % 12 + 1
    return {"coming": 18 + h % 4000 / 100, "last": 20 + (h >> 16) % 4000 / 100, "to_next": 5.0,
            "from_month": FI_MONTH_FROM[(today.month - 2) % 12],
            "month_en": date(2000, (today.month - 2) % 12 + 1, 1).strftime("%B"), "pay_day": f"10.{pay_month}."}




def ready_param(request: Request) -> tuple[str, str] | tuple[None, None]:
    route, _, pid = (request.query_params.get("ready") or "").partition(":")
    return (route, pid) if route in SM_ROUTES and pid else (None, None)


def home_persona(request: Request) -> str:
    """Whose home to draw. A ready move is shown on its creator's home (like the flow, which acts on the creator, not
    the cookie), unless ?persona= picks one explicitly."""
    persona = sm_persona(request)
    route, pid = ready_param(request)
    if not route or request.query_params.get("persona"):
        return persona
    for other in [persona] + [x for x in PERSONAS if x != persona]:
        p = engine_for(other).pending(pid)
        if p and p["kind"] == SM_ROUTES[route]:
            return other
    return persona


def sm_ready(request: Request, e: Engine) -> dict | None:
    """The "Ready for you" card on S-mobiili home: the move behind ?ready=<route>:<pending id> (what a money-move
    button opens). None when it is missing, malformed or not this persona's; {"done": True} once it is confirmed."""
    route, pid = ready_param(request)
    p = e.pending(pid) if route else None
    if not p or p["kind"] != SM_ROUTES[route]:
        return None
    if p["status"] != "pending":
        return {"done": True}
    amt = short_money(p["amount"])
    if ui_lang(request) == "fi":
        amt_fi = nb(fi_money(p["amount"]).replace(",00 €", " €"))
        if route == "pay":
            to = p["name"] if p.get("source") == "photo" else recipient(p)
            due = f" · eräpäivä {fi_date(p['due_date'])}" if p.get("due_date") else ""
            what, sub = f"Maksa: {p['name']} · {fi_money(p['amount'])}", f"Saaja {to}{due}"
        elif route == "save":
            g = e.state["goals"].get(p.get("goal_id"), {})
            what, sub = f"Säästä {amt_fi} säästötilille", f"Tavoite: {g.get('name') or 'oma tavoitteesi'}"
        elif route == "invest":
            per = " kuussa" if p["monthly"] else ""
            what, sub = f"Sijoita {amt_fi}{per}", f"Rahasto: {FUNDS_BY_ID[p['fund_id']]['name']}"
        else:
            what = f"Keskeytä kuukausisijoitus · {amt_fi}"
            sub = (f"Seuraava olisi ollut {fi_date(p['next_charge'])}. " if p.get("next_charge") else "") + \
                "Jo sijoitettu pysyy sijoitettuna"
    elif route == "pay":
        to = p["name"] if p.get("source") == "photo" else recipient(p)
        due = f" · due {nice_date(p['due_date'])}" if p.get("due_date") else ""
        what, sub = f"Pay {p['name'].lower()} · {money(p['amount'])}", f"To {to}{due}"
    elif route == "save":
        g = e.state["goals"].get(p.get("goal_id"), {})
        what, sub = f"Save {amt} to your savings account", f"For {g.get('name') or 'your goal'}"
    elif route == "invest":
        per = " a month" if p["monthly"] else ""
        what, sub = f"Invest {amt}{per}", f"Into {FUNDS_BY_ID[p['fund_id']]['name']}"
    else:
        what = f"Pause monthly investing · {amt}"
        sub = (f"Next one was due {nice_date(p['next_charge'])}. " if p.get("next_charge") else "") + \
            "What's invested stays invested"
    q = request.query_params
    keep = {k: q[k] for k in ("persona", "web", "autoplay", "pin") if q.get(k)}
    return {"done": False, "what": what, "sub": sub,
            "url": f"/s-mobiili/{route}/{pid}" + (f"?{urlencode(keep)}" if keep else "")}


@app.get("/s-mobiili/", response_class=HTMLResponse)
def smobiili_home(request: Request):
    persona = home_persona(request)
    e = engine_for(persona)
    a = accounts(e)
    tab = request.query_params.get("tab") or "koti"
    if tab not in SM_TABS:
        tab = "koti"
    if tab == "raha" and persona != MONEY_PERSONA:  # the charts are one customer's own figures: nobody else sees them
        tab = "pankki"
    if tab != "koti":  # the other tabs: the bank's own screens, no guide walk
        bills_due = e.upcoming_bills(30)
        en = ui_lang(request) == "en"
        asks = {k: chat_url(request, q[en], persona) for k, q in money_asks().items()} if tab == "raha" else {}
        return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
            request, persona, screen="tab", tab=tab, a=a, steps=[], ready=None, bills_due=bills_due,
            personas=[{"id": k, "name": v["name"], "label": v["label"]} for k, v in PERSONAS.items()],
            persona_id=persona, money=persona == MONEY_PERSONA, asks=asks))
    b = e.baskets("last_3_months")
    ready = sm_ready(request, e)
    first = [("ready", "I've got it ready, tap to check and confirm", "happy")] if ready and not ready["done"] else []
    steps = [("acct-current", f"Your current account: {short_money(a['current'])}", "talk"),
             ("baskets", "Everything you spend falls in 3 baskets. Tap it and I'll show you where it goes", "talk"),
             ("acct-pocket", "Your savings account" if a["pocket_open"] else "Your savings account, once you open one",
              "talk"),
             ("acct-funds", "Your funds. Only you choose where money goes", "talk"),
             ("recent", "Your latest payments, straight from your account", "talk"),
             ("ask", "Questions? I'm in WhatsApp and right here", "wave")]
    steps = first + steps
    bills_due = e.upcoming_bills(30)
    bill = next(iter(bills_due), None)
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, persona, screen="home", a=a, recent=e.recent(8), steps=guide_steps(steps), baskets=b, ready=ready,
        bonus=demo_bonus(persona, e.today), bills_due=bills_due,
        bill={**bill, "due": fi_date(bill["due_date"])} if bill else None))


def fi_date(iso: str) -> str:
    """2026-10-31 -> 31.10. (how S-mobiili writes a due date)."""
    d = date.fromisoformat(iso)
    return f"{d.day}.{d.month}."


# S-Etukortti sub-screens: the digital card ("Käytössä") and Kassakuitti, the digital receipts. Registered before
# /s-mobiili/{route}/{pid} so /s-mobiili/kuitit/<n> is not taken for a flow.
def sm_keep(request: Request) -> str:
    keep = urlencode({k: request.query_params[k] for k in ("persona", "web") if request.query_params.get(k)})
    return "?" + keep if keep else ""


def sm_sub(request: Request, persona: str, extra: str, title: str, back: str | None = None, **more):
    """back: where the chevron goes (default: the S-Etukortti tab)."""
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, persona, screen="extra", extra=extra, page_title=title, tab="etukortti", steps=[], ready=None,
        keep=sm_keep(request), back_url=back, **more))


@app.get("/s-mobiili/etukortti", response_class=HTMLResponse)
def smobiili_etukortti_card(request: Request):
    return sm_sub(request, sm_persona(request), "kortti", "S-Etukortti")


@app.get("/s-mobiili/kuitit", response_class=HTMLResponse)
def smobiili_receipts(request: Request):
    persona = sm_persona(request)
    return sm_sub(request, persona, "kuitit", tr(ui_lang(request), "Kuitit", "Receipts"),
                  receipts=receipts.receipt_list(engine_for(persona)))


@app.get("/s-mobiili/kuitit/{row_id}", response_class=HTMLResponse)
def smobiili_receipt(request: Request, row_id: int):
    persona = sm_persona(request)
    r = receipts.build(engine_for(persona), row_id, persona)
    if not r:
        raise HTTPException(404)
    return sm_sub(request, persona, "kuitti", tr(ui_lang(request), "Kassakuitti", "Receipt"),
                  "/s-mobiili/kuitit" + sm_keep(request), r=r)


@app.get("/s-mobiili/{route}/{pid}", response_class=HTMLResponse)
def smobiili_flow(request: Request, route: str, pid: str):
    if route not in SM_ROUTES:
        raise HTTPException(404)
    if request.query_params.get("pin"):  # the PIN page stays as a fallback
        return RedirectResponse(f"/{route}/{pid}" + ("?web=1" if request.query_params.get("web") else ""),
                                status_code=303)
    e, p = _pending_or_404(pid, SM_ROUTES[route])
    f = sm_flow(route, e, p, ui_lang(request))
    steps = f.pop("steps")
    if route != "save":  # the save flow's last line, on its review, already says Face ID
        steps.append(("faceid", "Tap here and use Face ID", "talk"))
    return templates.TemplateResponse(request, "smobiili.html", sm_ctx(
        request, sm_persona(request), screen="flow", route=route, pid=pid, p=p, f=f, steps=guide_steps(steps),
        done=p["status"] == "done"))


@app.post("/s-mobiili/{route}/{pid}/confirm")
async def smobiili_confirm(request: Request, route: str, pid: str):
    """Face ID passed in the mock: the same settle step as the PIN page. JSON only, so a cross-site form can't post
    it. A second confirm is refused with 409 and moves nothing."""
    if route not in SM_ROUTES:
        raise HTTPException(404)
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "Send JSON.")
    e, p = _pending_or_404(pid, SM_ROUTES[route])
    if p["status"] == "done":
        raise HTTPException(409, "Already confirmed. Nothing more was moved.")
    try:
        done, title, lines = SETTLE[p["kind"]](e, pid, p)
    except ValueError as err:
        return JSONResponse({"detail": sentence(err)}, status_code=422)
    if not done["first_time"]:  # two taps at once: the engine's lock let only one through
        raise HTTPException(409, "Already confirmed. Nothing more was moved.")
    if route == "save" and not e.state.get("savings_pocket_opened"):  # step 2 picked the savings account
        e.state["savings_pocket_opened"] = e.today.isoformat()
        e._save()
    if ui_lang(request) == "fi":
        title, lines = receipt_fi(e, p, done)
    return {"ok": True, "title": title, "lines": [nb(x) for x in lines], "balance_eur": done["balance_eur"]}


def receipt_fi(e: Engine, p: dict, done: dict) -> tuple[str, list[str]]:
    """S-mobiili's receipt after Face ID, in Finnish (the settle step's own lines are English: WhatsApp, PIN page)."""
    bal = f"Saldo nyt {fi_money(done['balance_eur'])}."
    if p["kind"] == "payment":
        return "Maksu lähetetty", [f"{done['name']}: {fi_money(done['amount'])}", bal]
    if p["kind"] == "saving":
        g = e.state["goals"][p["goal_id"]]
        pct = round(g["saved"] / g["target"] * 100)
        return "Säästetty", [f"{fi_money(done['amount'])} tavoitteeseen {g['name']}. {fi_money(g['saved'])} / "
                             f"{fi_money(g['target'])} ({pct} %). {bal}"]
    if p["kind"] == "investment":
        if done.get("monthly"):
            return "Valmis", [f"Kuukausisäästö alkoi: {fi_money(done['amount'])} rahastoon {done['name']} joka "
                              f"palkkapäivä, ensimmäisen kerran {fi_day(done['starts'])}. Voit muuttaa tai lopettaa "
                              "sen milloin vain."]
        return "Valmis", [f"Sijoitettu {fi_money(done['amount'])} rahastoon {done['name']}. {bal}"]
    when = f" {fi_day(done['next_charge'])}" if done.get("next_charge") else ""
    return "Sijoittaminen keskeytetty", [
        f"Kuukausisijoitus keskeytetty. Noin {fi_money(done['amount'])} talletus jää tekemättä{when}. Tililläsi pysyy "
        f"{fi_money(done['per_year_eur'])} vuodessa. Jo sijoitettu pysyy sijoitettuna. Voit aloittaa uudelleen milloin vain."]


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
        text = (f"{name}, your salary of *{money(a['salary_eur'])}* just landed. Balance now {money(a['balance_eur'])}.\n\n"
                f"Pay yourself first: put *{money(a['suggest_save_eur'])}* aside now, before it slips through your fingers?")
        first = ([(f"save:{goals[0]['id']}:{a['suggest_save_eur']:.0f}", f"Save {a['suggest_save_eur']:.0f} €")] if goals
                 else [("ask:goal", "Set a savings goal")])
        return text, first + [("ask:invest", "Invest instead"), ("later:salary", "Not now")]
    if kind == "unusual":
        text =(f"Heads-up: *{money(a['amount_eur'])}* at {a['where']} on {nice_date(a['date'])}. "
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
        text = (f"Your *{what}* went up: *{money(tr['last_per_month_eur'])} a month* in {tr['last_months']}, "
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
        return (f"Heads-up: a *{money(odd['amount_eur'])}* utility bill on {nice_date(odd['date'])}. One bill is "
                f"usually {money(odd['usual_low_eur'])} to {money(odd['usual_high_eur'])}.\n\n"
                f"Your utilities come to {money(a['per_month_eur'])} a month, about {round(a['bills_per_month'])} "
                "bills.", [("ask:bills", "My regular payments"), ("later:bills", "OK, thanks")])
    when = "tomorrow" if a["days_to_next"] == 1 else f"in {a['days_to_next']} days"
    day = nice_date(a["next_expected"])
    if a["id"] == "investing":
        return (f"Your *monthly investing* of about {money(a['per_month_eur'])} goes out {when} ({day}).\n\n"
                "Keep it, or pause it for now?", [("keep:investing", "Keep it"), ("pause:investing", "Pause investing")])
    return (f"Your *{what}* of about {money(a['per_month_eur'])} is due {when} ({day}).",
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
    return (f"Hi {name}, your *{what} of {money(bill['amount_eur'])}* is due *{when}* ({nice_date(bill['due_date'])}).\n\n"
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
        return (f"{name}, your *{d['name']}* goal is a bit behind plan: {money(d['saved_eur'])} saved, "
                f"while {money(d['expected_by_now_eur'])} would be on track by today. To reach {money(d['target_eur'])} "
                f"by {nice_date(d['deadline'])}, that's about *{money(d['needed_per_month_eur'])} a month*.\n\n"
                f"Catch up with {money(d['catch_up_eur'])} now?",
                [(f"save:{d['goal_id']}:{d['catch_up_eur']:.0f}", f"Save {d['catch_up_eur']:.0f} €"),
                 ("ask:pace", "How to catch up"), ("later:goal", "Not now")])
    text = (f"Your week, {name}: *{money(d['spent_7d_eur'])}* spent in the last 7 days "
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
        done = f" ({d['done']} of {d['of']} done)" if d["done"] else ""
        return (f"Hey {name}! Lesson {d['lesson']}/{d['of']}, *{d['title'].lower()}*, is waiting{done}. 30 seconds?",
                [("learn:start", "Continue lesson")])
    if t == "onboarding":
        return (f"Hey {name}, we stopped halfway through setting up your goal. Finish it? It takes a minute.",
                [("ob:resume", "Let's finish")])
    if t == "quiz":
        return (f"Hey {name}, your basket game is paused at {d['round']}/{d['of']}. One more round?",
                [("quiz:resume", "Keep playing")])
    if t == "pin":
        return (f"Hey {name}, *{d['name']}* ({money(d['amount_eur'])}) is still waiting for you to confirm it "
                "with Face ID in S-mobiili. Want to finish it?", [(f"resume:{d['pending_id']}", "Finish it")])
    if t == "goal":
        return (f"Hey {name}, your *{d['name']}* goal is set, but nothing's in it yet. Start with "
                f"{money(d['amount_eur'])}?",
                [(f"save:{d['goal_id']}:{d['amount_eur']:.0f}", f"Save {d['amount_eur']:.0f} €")])
    if d["kind"] == "bill":
        b = d["bill"]
        return (f"Hey {name}, you said later yesterday: *{b['name'].lower()}* of {money(b['amount_eur'])} is due "
                f"{nice_date(b['due_date'])}. Pay it now?", [(f"pay:{b['bill_id']}", pay_label(b["name"]))])
    return (f"Hey {name}, yesterday you said maybe later to this: \"{d['offer']}\" Still up for it?",
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
    else:  # WhatsApp allows free messages only within 24 h of the person's last one; Oiva waits to be asked
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


@app.get("/", include_in_schema=False)
@app.get("/app", include_in_schema=False)
@app.get("/app/explore", include_in_schema=False)
@app.get("/mobile-demo", include_in_schema=False)
@app.get("/s-mobiili", include_in_schema=False)
def to_smobiili(request: Request):
    """The web side is S-mobiili; the old pages (landing, My money, Explore, the concept mock) lead there."""
    return RedirectResponse("/s-mobiili/" + (f"?{request.url.query}" if request.url.query else ""), status_code=307)


@app.get("/app/export.json")
def export_json(request: Request):
    return JSONResponse(engine_for(persona_of(request)).export(), headers={"content-disposition": 'attachment; filename="my-s-pankki-data.json"'})


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, q: str = "", embed: str = ""):
    """?q= asks a question once on load (deep link); ?embed=1 drops the site chrome for a WebView."""
    persona = persona_of(request)
    mirrored = mirror_viewer(request)
    if mirrored:  # the app's chat is the owner's WhatsApp conversation, on the WhatsApp demo customer's data
        persona = DEFAULT_PERSONA
    name = name_for(persona)
    tts.allow([f"Hei {name}! Olen Oiva, S-Pankin rahavalmentajasi. Kysy rahoistasi, vaikka paljonko on jäljellä palkkapäivään."])  # #hello
    tts.allow([f"Hi {name}! I'm Oiva, your S-Pankki money coach. Ask me about your money, like what's left until payday."])
    shown = request.query_params.get("as")  # Back and the tab bar keep the customer the chat was opened for
    keep = {"persona": shown} if shown in PERSONAS else {}
    tab = lambda t: "/s-mobiili/" + (f"?{urlencode({**keep, **({'tab': t} if t else {})})}" if keep or t else "")  # noqa: E731
    return templates.TemplateResponse(request, "chat.html", {"name": name, "q": q[:500], "back_url": tab(None),
                                                             "tab_urls": {t: tab(t) for t in ("etukortti", "pankki", "mina")},
                                                             "embed": True, "mirror": mirrored,
                                                             "framed": embed != "1", "persona": PERSONAS[persona]})


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


WEB_UPLOAD_MAX = bills.MAX_BYTES * 4 // 3 + 64_000  # a 5 MB photo as base64, plus the rest of the JSON


def web_words(out):
    """The web chat's reply as the page gets it: no emoji in the words (wa.strip_emoji) and no reaction. A reaction
    on the person's message is a WhatsApp thing; on the web it is dropped."""
    if isinstance(out, JSONResponse):
        return JSONResponse(web_words(json.loads(out.body)), status_code=out.status_code)
    if not isinstance(out, dict):
        return out
    out = {k: v for k, v in out.items() if k != "reaction"}
    if isinstance(out.get("text"), str):
        out["text"] = wa.strip_emoji(out["text"])
    if isinstance(out.get("bubbles"), list):
        out["bubbles"] = [b for b in (wa.strip_emoji(x) for x in out["bubbles"]) if b]
    for key in ("buttons", "quick"):
        if isinstance(out.get(key), list):
            out[key] = [{**b, "label": wa.strip_emoji(b["label"])} if isinstance(b, dict) and "label" in b else b
                        for b in out[key]]
    return out


def with_mood(out):
    """Every web chat reply names the helper squirrel's pose (app/mood.py); a reply without one is a plain answer."""
    if isinstance(out, JSONResponse):
        j = json.loads(out.body)
        if isinstance(j, dict) and "mood" not in j:
            return JSONResponse({**j, "mood": "error" if j.get("error") else "success"}, status_code=out.status_code)
        return out
    if isinstance(out, dict) and "mood" not in out:
        return {**out, "mood": "success"}
    return out


@app.post("/api/chat")
async def chat_api(request: Request):
    """One turn of the web chat; its words carry no emoji and it has no reaction (web_words). With "mirror": true
    (the app's chat) it is the owner's conversation: the same history as their WhatsApp, kept in the mirror log."""
    out = with_mood(web_words(await chat_turn(request)))
    body = await request.json()
    said = json.loads(out.body) if isinstance(out, JSONResponse) else out
    if isinstance(said, dict):  # the parts as the page reads them aloud (/api/voice speaks only these)
        tts.allow(said.get("bubbles") or ([said["text"]] if said.get("text") else []))
    if isinstance(body, dict) and body.get("mirror") and mirror_viewer(request):
        said = str(body.get("shown") or body.get("text") or "").strip()
        if said and not said.startswith("__"):
            mirror.add("me", said[:1000], source="app")
        j = json.loads(out.body) if isinstance(out, JSONResponse) else out
        if isinstance(j, dict):
            parts = j.get("bubbles") or ([j["text"]] if j.get("text") else [])
            links = [{"label": b["label"], "url": b["url"]} for b in j.get("buttons") or []]
            for i, part in enumerate(parts):
                mirror.add("oiva", part, links if i == len(parts) - 1 else None, source="app")
    return out


@app.post("/api/voice")
async def voice_api(request: Request):
    """Oiva's reply read aloud (Helper mode with the sound on): {"text", "lang": "fi"|"en"} -> audio/mpeg from
    ElevenLabs (app/tts.py). 204 when no ELEVENLABS_API_KEY is set, an error status when it fails: either way the
    page reads it with the browser's own voice instead. The key never leaves the server."""
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("text"), str) or len(body["text"]) > 5000:
        return JSONResponse({"error": "text"}, status_code=400)
    if not tts.configured():
        return Response(status_code=204)
    if not tts.allowed(body["text"]):  # only what Oiva said here: no free text-to-speech for the internet
        return JSONResponse({"error": "not a reply"}, status_code=403)
    try:
        audio = await tts.speak(body["text"], "en" if body.get("lang") == "en" else "fi")
    except tts.TTSError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)
    return Response(audio, media_type="audio/mpeg")


@app.get("/api/mirror")
def mirror_api(request: Request, after: int = 0):
    """The owner's conversation, WhatsApp and app, from message `after` on; the app's chat polls it. Owner only."""
    if not mirror_viewer(request):
        return {"on": False, "messages": []}
    msgs = mirror.since(after)
    for m in msgs:
        if m.get("role") != "me":
            tts.allow([m.get("text") or ""])
    return {"on": True, "messages": msgs}


async def chat_turn(request: Request):
    if int(request.headers.get("content-length") or 0) > WEB_UPLOAD_MAX:  # before reading it
        raise HTTPException(413, "image too large (max 5 MB)")
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "send a JSON object")
    sid = "web:" + str(body.get("session", "default"))[:40]
    if body.get("mirror") and mirror_viewer(request):  # the app's chat: the owner's WhatsApp conversation, same history
        sid = mirror.APP_KEY
        body["as"] = DEFAULT_PERSONA
        coach(None).alias[sid] = mirror.owner()
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
        return {"text": t, "buttons": [], "mood": "checking", "quick": quick([(f"pay:{rent['bill_id']}", "Pay rent now"),
                                                          (f"later:{rent['bill_id']}", "Remind me later"),
                                                          ("ask:tight", "How do I stretch it?")])}
    if text.startswith("__alert__:"):
        m = alert_message(text.split(":", 1)[1], name, e)
        return {"text": m[0], "buttons": [], "mood": "caution", "quick": quick(m[1])} if m else {"text": "Nothing to flag right now.", "buttons": []}
    def interview(t: onboarding.Turn, buttons=(), flow: str = "interview"):
        return {"text": "\n\n".join(t.lead + [t.text]), "bubbles": [*t.lead, t.text], "buttons": list(buttons),
                "quick": quick(t.buttons), "mood": mood.for_flow(flow, t)}

    def said(reply_text: str, reaction: str | None = None, **more):
        """A plain answer from the app, in bubbles ("---" splits them), with the reaction for their message."""
        parts = wa.bubbles(reply_text)
        return {"text": "\n\n".join(parts), "bubbles": parts, "reaction": reaction, "buttons": [], **more}

    if text == "__start__":  # the web chat's stand-in for Connect S-Pankki
        lead = onboarding.intro(e)
        t = onboarding.Turn(lead[-1], lead=lead[:-1], done=True)
        remember(persona, sid, "[connected S-Pankki]", t)
        return interview(t)
    touch(e, sid)
    bid, secret, extra = "", [], None
    if text.startswith("__btn__:"):
        bid = text.split(":", 1)[1]
        learn.step_away(e, sid, bid)
        try:
            direct = button_reply(sid, bid, web=True, e=e)
        except ValueError as err:
            return {"text": f"I can't do that: {err}", "buttons": [], "mood": "error"}
        if direct:
            url = direct.get("url")
            return {"text": direct["text"], "buttons": [{"label": url[0], "url": url[1].replace(public_url(), "")}] if url else [],
                    "mood": "needs_approval" if url else "success"}
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
        flow, t = flow_turn_named(e, sid, typed, bid)
        if t:
            remember(persona, sid, typed or f"[tapped {bid}]", t)
            return interview(t, flow=flow)
    c = coach(persona)
    if not bid and hasattr(c, "last_said"):
        note_chat_later(e, sid, text, c.last_said(sid))
    context = coach_context(e, sid, text, extra)
    me = wa_phone(request)
    return await web_answer(e, sid, lambda: ask(persona, sid, text, name, context, viewer=me), quick, secret, text)


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
        return {"text": str(err), "buttons": [], "mood": "error"}
    except Exception:
        log.exception("bill photo failed")
        return JSONResponse({"text": "That photo didn't open for me. Can you send it again?",
                             "buttons": []})
    problem = bills.reply_problem(bill)
    if problem:
        return {"text": problem, "buttons": []}
    return await web_answer(e, sid, lambda: ask_about_bill(persona, sid, bill, caption, name), quick)


async def web_answer(e: Engine, sid: str, run, quick, secret=(), asked: str = ""):
    """The coach's reply for the web chat: bubbles, buttons, charts, and any paused flow's note."""
    import anyio
    try:
        reply = await anyio.to_thread.run_sync(run)
    except Exception:
        log.exception("coach failed")
        return JSONResponse({"text": "I can't answer right now, something's down on my side. "
                                     "Bill reminders and payments still work.", "buttons": [], "mood": "error"})
    out, charts = [], []
    for b in reply.buttons:
        if "chart" in b:  # drawn inside the bubble
            charts.append(b["chart"])
            continue
        owner[pending_id_of(b["url"])] = sid
        out.append({"label": b["label"], "url": with_web(b["url"].replace(public_url(), ""))})
    paused = quiz.paused_note(e, sid)
    waiting = learn.paused_note(e, sid)
    onboarding.side_question(e, sid, asked)  # mid-interview: never re-sent; another topic pauses it
    parts = wa.bubbles(reply.text) if reply.text.strip() else []
    if secret:
        parts = wa.bubbles(guardrails.secret_reply(secret)) + parts
    notes = [x for x in (paused, waiting) if x]
    return JSONResponse({"text": "\n\n".join(parts + notes), "bubbles": parts + notes,
                         "reaction": "🔒" if secret else getattr(reply, "reaction", None),
                         "tools": reply.tools_used, "trace": reply.trace,
                         "buttons": out, "charts": charts, "quick": [],
                         "mood": mood.for_reply(reply.tools_used, out, e, reply.trace)})


@app.get("/api/inbox")
def chat_inbox(session: str = ""):
    return {"messages": [m for m in (wa.strip_emoji(x) for x in inbox.pop("web:" + session[:40], [])) if m]}


@app.get("/health")
def health():
    return {"ok": True, "today": engine.today.isoformat(), "balance": engine.balance(), "whatsapp": wa.configured(), "banks": enable_banking.configured()}
