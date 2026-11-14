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

from . import broker
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
# Quick-reply buttons that turn into a question for the coach.
ASK = {
    "ask:tight": "My budget is tight until payday. What can I do?",
    "ask:goal": "Help me set up a savings goal.",
    "ask:invest": "I'd like to invest part of my salary. What are my options?",
    "ask:unusual": "Tell me more about that big payment and how it fits my month.",
}
names: dict[str, str] = {}  # WhatsApp number -> first name from the profile


def first_name(profile: str) -> str:
    """'Cristiana💕 B.' -> 'Cristiana'. Falls back to the demo name."""
    word = (profile or "").split()[0] if (profile or "").split() else ""
    clean = "".join(ch for ch in word if ch.isalpha() or ch in "-'")
    return clean or DEMO_NAME


def name_of(phone: str | None) -> str:
    return names.get(phone or "", DEMO_NAME)


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
    to, name = m["from"], first_name(m["name"])
    names[to] = name
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
            wa.send_text(to, "Sure ⏰ I'll remind you again tomorrow at 9:00." if bid != "later:unusual"
                         else "Great, enjoy it 🙂 I'll keep an eye on your safe-to-spend.")
            return
        if bid.startswith("stop:"):
            p = engine.create_stop_subscription(bid[5:])
            owner[p["pending_id"]] = to
            per = "month" if p["cadence"] == "monthly" else "year"
            wa.send_url_button(to, f"Stop future card payments to *{p['name']}* ({money(p['amount'])}/{per})? "
                                   "Confirm with your PIN.", f"Stop {p['name']}"[:20], f"{public_url()}/stop/{p['pending_id']}")
            return
        if bid.startswith("keep:"):
            wa.send_text(to, f"Okay, {bid[5:]} stays on 👍 I'll tell you if the price changes again.")
            return
        if bid.startswith("save:"):
            _, goal_id, amount = bid.split(":")
            p = engine.create_saving(goal_id, float(amount))
            owner[p["pending_id"]] = to
            goal = p["name"].split(": ", 1)[1]
            wa.send_url_button(to, f"Move *{money(p['amount'])}* to your savings pocket for *{goal}*? "
                                   "Confirm with your PIN.", f"Save {money(p['amount'])}", f"{public_url()}/save/{p['pending_id']}")
            return
        if m["text"] is None:
            wa.send_text(to, "For now I can read text messages only. What would you like to know?")
            return
        text = ASK.get(bid, m["text"])
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
                            "last 3 months using the three baskets, then tell me what you can help with.", name=name_of(to))
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
        if first_time and broker.configured():
            order = broker.place(done["fund_id"], done["amount"])
            engine.state.setdefault("orders", []).append({"pending_id": pid, "fund": done["name"],
                                                          "amount": done["amount"], **order})
            engine._save()
            msg += "\n\n" + order_text(order)
    if first_time and (to := owner.get(pid) or os.environ.get("DEMO_PHONE")):
        wa.send_text(to, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Done", "lines": [msg.replace("✅ ", "").replace("*", "")], "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})


def order_text(order: dict) -> str:
    if order["status"] == "placed":
        lines = [f"• {leg['name']}: {leg['quantity']} shares" for leg in order["legs"]
                 if leg["status"] not in ("failed", "skipped")]
        return ("📈 Order sent to *Trading 212 (Practice account)*:\n" + "\n".join(lines)
                + "\nIt fills at the next market price; if the exchange is closed, when it opens.")
    return "⚠️ The broker order didn't go through yet. Your money is set aside and I'll retry."


@app.get("/stop/{pid}", response_class=HTMLResponse)
def stop_page(request: Request, pid: str):
    p = _pending_or_404(pid, "stop_subscription")
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "action": f"/stop/{pid}", "title": "Stop recurring card payment",
        "amount": money(p["amount"]) + (" / month" if p["cadence"] == "monthly" else " / year"),
        "rows": [("Merchant", p["name"]), ("Next charge", nice_date(p["next_charge"]) if p["next_charge"] else "-"),
                 ("You save", f"{money(p['per_year_eur'])} a year"), ("Card", "S-Pankki Visa •• 4821 (demo)")],
        "note": "Future charges from this merchant will be declined. Also cancel your account with the service.",
        "button": f"Stop {p['name']}"})


@app.post("/stop/{pid}", response_class=HTMLResponse)
def stop_confirm(request: Request, pid: str, pin: str = Form("")):
    p = _pending_or_404(pid, "stop_subscription")
    if len(pin) != 4 or not pin.isdigit():
        return RedirectResponse(f"/stop/{pid}?err=1", status_code=303)
    first_time = p["status"] != "done"
    done = engine.confirm(pid)
    subs = engine.subscriptions()
    msg = (f"✅ *{done['name']} stopped.* No more charges of {money(done['amount'])}. "
           f"That's {money(done['per_year_eur'])} a year back in your pocket.\n"
           f"Active subscriptions now: {subs['active_count']}, {money(subs['active_per_month_eur'])} a month.")
    if first_time and (to := owner.get(pid) or os.environ.get("DEMO_PHONE")):
        wa.send_text(to, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Payment stopped", "lines": [msg.replace("✅ ", "").replace("*", "")],
        "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})


@app.get("/save/{pid}", response_class=HTMLResponse)
def save_page(request: Request, pid: str):
    p = _pending_or_404(pid, "saving")
    g = engine.state["goals"][p["goal_id"]]
    return templates.TemplateResponse(request, "confirm.html", {
        "p": p, "action": f"/save/{pid}", "title": "Move to savings", "amount": money(p["amount"]),
        "rows": [("Goal", g["name"]), ("Saved so far", f"{money(g['saved'])} of {money(g['target'])}"),
                 ("From", "S-Pankki current account"), ("To", "Savings pocket")],
        "button": f"Save {money(p['amount'])}"})


@app.post("/save/{pid}", response_class=HTMLResponse)
def save_confirm(request: Request, pid: str, pin: str = Form("")):
    p = _pending_or_404(pid, "saving")
    if len(pin) != 4 or not pin.isdigit():
        return RedirectResponse(f"/save/{pid}?err=1", status_code=303)
    first_time = p["status"] != "done"
    done = engine.confirm(pid)
    g = engine.state["goals"][p["goal_id"]]
    pct = round(g["saved"] / g["target"] * 100)
    msg = (f"✅ *Saved {money(done['amount'])}* for {g['name']}.\n"
           f"{money(g['saved'])} of {money(g['target'])} ({pct}%). Balance now {money(done['balance_eur'])}.")
    if first_time and (to := owner.get(pid) or os.environ.get("DEMO_PHONE")):
        wa.send_text(to, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Saved", "lines": [msg.replace("✅ ", "").replace("*", "")], "wa": os.environ.get("WA_DISPLAY_NUMBER", "")})


# ---------------- Demo triggers (used while filming) ----------------
def alert_message(kind: str, name: str) -> tuple[str, list] | None:
    a = engine.alert(kind)
    if kind == "salary":
        goals = a["goals"]
        text = (f"💰 {name}, your salary of *{money(a['salary_eur'])}* just landed.\n\n"
                f"Pay yourself first: put *{money(a['suggest_save_eur'])}* aside now, before it slips through your fingers?")
        first = ([(f"save:{goals[0]['id']}:{a['suggest_save_eur']:.0f}", f"Save {a['suggest_save_eur']:.0f} €")] if goals
                 else [("ask:goal", "Set a savings goal")])
        return text, first + [("ask:invest", "Invest instead"), ("later:salary", "Not now")]
    if kind == "unusual":
        if not a["found"]:
            return None
        text = (f"👀 Heads-up: *{money(a['amount_eur'])}* at {a['where']} on {nice_date(a['date'])}. "
                f"That's *{a['times_usual']}x* your usual {a['category'].lower()} payment ({money(a['usual_eur'])}).\n\n"
                "It sits in your *slips through your fingers* basket. Was that planned?")
        return text, [("later:unusual", "Yes, all good"), ("ask:unusual", "Tell me more")]
    if kind == "renewal":
        inc = a.get("price_increase")
        when = "tomorrow" if a["days_to_next"] == 1 else f"in {a['days_to_next']} days"
        text = f"🔁 *{a['name']}* renews {when}: {money(a['price_eur'])}."
        if inc:
            text += f"\nThe price went up from {money(inc['from_eur'])} to {money(inc['to_eur'])} on {nice_date(inc['since'])}."
        text += f"\nThat's {money(a['per_year_eur'])} a year. Keep it?"
        return text, [(f"keep:{a['name']}", "Keep it"), (f"stop:{a['name']}", "Stop payments")]
    raise HTTPException(400, "kind must be salary, unusual or renewal")


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
    text, rent = reminder_text(name_of(to))
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
    try:
        reply = await anyio.to_thread.run_sync(lambda: coach().ask(sid, text, name=DEMO_NAME))
    except Exception:
        log.exception("coach failed")
        return JSONResponse({"text": "Sorry, I can't think right now (the AI service is not reachable). "
                                     "Bill reminders and payments still work.", "buttons": []})
    return JSONResponse({"text": reply.text, "tools": reply.tools_used,
                         "buttons": [{"label": b["label"], "url": b["url"].replace(public_url(), "")} for b in reply.buttons]})


@app.get("/health")
def health():
    return {"ok": True, "today": engine.today.isoformat(), "balance": engine.balance(), "whatsapp": wa.configured()}
