"""FastAPI app: WhatsApp webhook, bank-style confirm pages, web chat, demo triggers.

Run: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import whatsapp as wa
from .agent import Coach
from .engine import FUNDS_BY_ID, Engine
from .tools import public_url

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

engine = Engine(today=date.fromisoformat(os.environ.get("DEMO_TODAY", "2026-10-06")))
_coach: Coach | None = None
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="S-Pankki Chat")
seen: set[str] = set()
owner: dict[str, str] = {}  # pending_id -> WhatsApp number that asked for it
DEMO_NAME = "Aino"


def coach() -> Coach:
    global _coach
    if _coach is None:
        _coach = Coach(engine)
    return _coach


def nice_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %d %b")


def money(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ")


def connected(phone: str) -> bool:
    return phone in engine.state.setdefault("connected", {})


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
    for m in wa.parse(await request.json()):
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        bg.add_task(handle_message, m)
    return {"ok": True}


def send_reply(to: str, reply):
    if reply.buttons:
        first, *rest = reply.buttons
        wa.send_url_button(to, reply.text, first["label"], first["url"])
        for b in rest:
            wa.send_url_button(to, "One more:", b["label"], b["url"])
    else:
        wa.send_text(to, reply.text)


def handle_message(m: dict):
    to, name = m["from"], (m["name"] or DEMO_NAME).split()[0]
    try:
        wa.mark_read_typing(m["id"])
        if not connected(to):
            wa.send_url_button(
                to,
                f"Hi {name} 👋 I'm your S-Pankki money coach. I can answer questions about your spending, remind you "
                "before bills and help you save.\n\nTo start, connect your S-Pankki account. You choose what I can see, "
                "and you can switch me off any time.",
                "Connect S-Pankki", f"{public_url()}/connect?u={to}")
            return
        bid = m.get("button_id") or ""
        if bid.startswith("pay:"):
            p = engine.create_payment(bid[4:])
            owner[p["pending_id"]] = to
            wa.send_url_button(to, f"Here's your *{p['name'].lower()}* payment of *{money(p['amount'])}*. "
                                   "Confirm it with your PIN in S-Pankki.",
                               f"Pay {money(p['amount'])}", f"{public_url()}/pay/{p['pending_id']}")
            return
        if bid.startswith("later:"):
            wa.send_text(to, "Sure ⏰ I'll remind you again tomorrow at 9:00.")
            return
        if m["text"] is None:
            wa.send_text(to, "For now I can read text messages only. What would you like to know?")
            return
        text = "My budget is tight until payday. What can I do?" if bid == "ask:tight" else m["text"]
        reply = coach().ask(to, text, name=name)
        for b in reply.buttons:
            owner[b["url"].rsplit("/", 1)[-1]] = to
        send_reply(to, reply)
    except Exception:
        log.exception("handling message failed")
        wa.send_text(to, "Sorry, something went wrong on my side. Try again in a minute.")


# ---------------- Connect (consent) ----------------
@app.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, u: str = ""):
    return templates.TemplateResponse(request, "connect.html", {"u": u, "done": connected(u) if u else False})


@app.post("/connect", response_class=HTMLResponse)
def connect_submit(request: Request, bg: BackgroundTasks, u: str = Form(""), scope: list[str] = Form([])):
    engine.state.setdefault("connected", {})[u] = {"scope": scope, "at": engine.today.isoformat()}
    engine._save()
    if u:
        bg.add_task(welcome, u)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Connected", "lines": ["Your S-Pankki account is connected.", "Go back to WhatsApp, your coach is ready."],
        "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})


def welcome(to: str):
    reply = coach().ask(to, "I just connected my account. Give me a very short first look at where my money went in the "
                            "last 3 months using the three baskets, then tell me what you can help with.", name=DEMO_NAME)
    send_reply(to, reply)


# ---------------- Confirm pages ----------------
def _pending_or_404(pid: str, kind: str) -> dict:
    p = engine.pending(pid)
    if not p or p["kind"] != kind:
        raise HTTPException(404, "This link has expired.")
    return p


@app.get("/pay/{pid}", response_class=HTMLResponse)
def pay_page(request: Request, pid: str):
    p = _pending_or_404(pid, "payment")
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "action": f"/pay/{pid}", "title": "Confirm payment", "amount": money(p["amount"]),
        "rows": [("To", "Landlord · Asunto Oy Demo" if p["category"] == "Housing" else p["name"]),
                 ("Account", "FI00 0000 0000 0000 00 (demo)"), ("For", p["name"]),
                 ("Due", nice_date(p["due_date"])), ("From", "S-Pankki current account")],
        "button": f"Pay {money(p['amount'])}"})


@app.post("/pay/{pid}", response_class=HTMLResponse)
def pay_confirm(request: Request, pid: str, pin: str = Form("")):
    p = _pending_or_404(pid, "payment")
    if len(pin) != 4 or not pin.isdigit():
        return RedirectResponse(f"/pay/{pid}?err=1", status_code=303)
    first_time = p["status"] != "done"
    done = engine.confirm(pid)
    s = engine.safe_to_spend()
    if first_time and (to := owner.get(pid) or os.environ.get("DEMO_PHONE")):
        wa.send_text(to, f"✅ *{done['name']} paid*: {money(done['amount'])}\n"
                         f"Balance now {money(done['balance_eur'])}.\n"
                         f"Safe to spend until payday ({nice_date(s['next_payday']['date'])}): "
                         f"*{money(s['safe_to_spend_until_payday_eur'])}*, about {money(s['safe_per_day_eur'])} a day.")
    return templates.TemplateResponse(request, "done.html", {
        "title": "Payment sent", "lines": [f"{done['name']}: {money(done['amount'])}", f"Balance {money(done['balance_eur'])}"],
        "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})


@app.get("/invest/{pid}", response_class=HTMLResponse)
def invest_page(request: Request, pid: str):
    p = _pending_or_404(pid, "investment")
    f = FUNDS_BY_ID[p["fund_id"]]
    when = f"Every payday, from {nice_date(engine.next_payday()['date'])}" if p["monthly"] else "Today, one-off"
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "action": f"/invest/{pid}", "title": "Confirm investment",
        "amount": money(p["amount"]) + (" / month" if p["monthly"] else ""),
        "rows": [("Fund", f["name"]), ("Risk", f"{f['risk']} ({f['risk_level']}/7)"), ("Holds", f["what"]),
                 ("When", when), ("From", "S-Pankki current account")],
        "note": "Funds can go down as well as up. Past or scenario returns are not a promise.",
        "button": "Invest" if not p["monthly"] else "Start monthly saving"})


@app.post("/invest/{pid}", response_class=HTMLResponse)
def invest_confirm(request: Request, pid: str, pin: str = Form("")):
    p = _pending_or_404(pid, "investment")
    if len(pin) != 4 or not pin.isdigit():
        return RedirectResponse(f"/invest/{pid}?err=1", status_code=303)
    first_time = p["status"] != "done"
    done = engine.confirm(pid)
    if done.get("monthly"):
        msg = (f"✅ *Monthly saving started*: {money(done['amount'])} into {done['name']} every payday, "
               f"first on {nice_date(done['starts'])}. You can change or stop it any time.")
    else:
        msg = f"✅ *Invested* {money(done['amount'])} in {done['name']}. Balance now {money(done['balance_eur'])}."
    if first_time and (to := owner.get(pid) or os.environ.get("DEMO_PHONE")):
        wa.send_text(to, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Done", "lines": [msg.replace("✅ ", "").replace("*", "")], "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})


# ---------------- Demo triggers (used while filming) ----------------
def reminder_text(name: str = DEMO_NAME) -> tuple[str, dict]:
    rent = next(b for b in engine.upcoming_bills() if b["category"] == "Housing")
    s = engine.safe_to_spend()
    when = "tomorrow" if rent["days_away"] == 1 else f"in {rent['days_away']} days"
    text = (f"Hi {name} 👋 Your *rent of {money(rent['amount_eur'])}* is due *{when}* ({nice_date(rent['due_date'])}).\n\n"
            f"After rent and your usual bills you have *{money(s['safe_to_spend_until_payday_eur'])}* safe to spend "
            f"until payday on {nice_date(s['next_payday']['date'])}. That's about *{money(s['safe_per_day_eur'])} a day*.")
    return text, rent


@app.post("/demo/remind")
def demo_remind(to: str | None = None):
    to = to or os.environ.get("DEMO_PHONE")
    if not to:
        raise HTTPException(400, "pass ?to=<number> or set DEMO_PHONE")
    text, rent = reminder_text()
    wa.send_reply_buttons(to, text, [(f"pay:{rent['bill_id']}", "Pay rent now"),
                                     (f"later:{rent['bill_id']}", "Remind me later"),
                                     ("ask:tight", "How do I stretch it?")])
    return {"sent": True, "text": text}


@app.post("/demo/reset")
def demo_reset():
    engine.reset()
    coach().reset()
    seen.clear()
    owner.clear()
    return {"reset": True}


# ---------------- Web chat (same coach, embeddable in S-mobiili) ----------------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/chat")


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(request, "chat.html", {"name": DEMO_NAME})


@app.post("/api/chat")
async def chat_api(request: Request):
    body = await request.json()
    sid = "web:" + str(body.get("session", "default"))[:40]
    text = str(body.get("text", "")).strip()[:1000]
    if not text:
        raise HTTPException(400)
    if text == "__remind__":
        t, rent = reminder_text()
        return {"text": t, "buttons": [], "quick": [{"label": "Pay rent now", "send": f"__pay__:{rent['bill_id']}"},
                                                   {"label": "How do I stretch it?", "send": "My budget is tight until payday. What can I do?"}]}
    if text.startswith("__pay__:"):
        p = engine.create_payment(text.split(":", 1)[1])
        return {"text": f"Here's your *{p['name'].lower()}* payment of *{money(p['amount'])}*. Confirm it with your PIN.",
                "buttons": [{"label": f"Pay {money(p['amount'])}", "url": f"/pay/{p['pending_id']}"}]}
    import anyio
    reply = await anyio.to_thread.run_sync(lambda: coach().ask(sid, text, name=DEMO_NAME))
    return JSONResponse({"text": reply.text, "tools": reply.tools_used,
                         "buttons": [{"label": b["label"], "url": b["url"].replace(public_url(), "")} for b in reply.buttons]})


@app.get("/health")
def health():
    return {"ok": True, "today": engine.today.isoformat(), "balance": engine.balance(), "whatsapp": wa.configured()}
