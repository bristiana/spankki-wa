"""FastAPI app: WhatsApp webhook, bank-style confirm pages, web chat, demo triggers.

Run: uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import broker, onboarding, rules, templates_wa
from . import whatsapp as wa
from .agent import Coach
from .engine import (BASKETS, DEMO_BANK, EXPLORE_GROUP_BY, EXPLORE_METRICS, EXPLORE_WHAT, FUNDS_BY_ID, MCC_LABELS, PERIODS,
                     SPEND_CATEGORIES, Engine, merchant_codes)
from .tools import explore_query, public_url

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
}
names: dict[str, str] = {}  # WhatsApp number -> first name from the profile
web_sessions: set[str] = set()  # web chat sessions that get proactive messages in their inbox
HELD_STALE_S = 3 * 24 * 3600  # a held proactive message older than this is dropped, not delivered


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
    return date.fromisoformat(iso).strftime("%a %d %b") if iso else "not known yet"


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
    payload = await request.json()
    for f in wa.failed_statuses(payload):
        log.warning("delivery failed to %s: %s %s", str(f["to"])[-4:], f["code"], f["title"])
    new = [m for m in wa.parse(payload) if m["id"] not in seen]
    for m in new:
        seen.add(m["id"])
        last_seen[m["from"]] = time.time()
    if new:
        bg.add_task(lambda: [handle_message(m) for m in new])  # one delivery, answered in order
    return {"ok": True}


def window_open(phone: str | None) -> bool:
    return bool(phone) and time.time() - last_seen.get(phone, 0) < WINDOW_S


def send_reply(to: str, reply):
    """Short bubbles like a person typing; a button rides on the last bubble."""
    parts = wa.bubbles(reply.text)
    if reply.buttons:
        wa.send_bubbles(to, parts[:-1])
        first, *rest = reply.buttons
        wa.send_url_button(to, parts[-1], first["label"], first["url"])
        for b in rest:
            wa.send_url_button(to, "One more:", b["label"], b["url"])
    else:
        wa.send_bubbles(to, parts)


STOP_WORDS = {"stop", "unsubscribe", "delete my data", "switch off", "lopeta"}


def switch_off(to: str):
    """Consent withdrawn: forget the connection, the chat history and the name."""
    engine.state.setdefault("connected", {}).pop(to, None)
    engine.state.get("held", {}).pop(to, None)
    engine._save()
    coach().reset(to)
    onboarding.forget(engine, to)
    names.pop(to, None)
    wa.send_text(to, "Done ✅ Kurre is switched off for this number and our chat history is deleted. "
                     "Your bank data never left S-Pankki. Write *Hi* any time to start again.")


def button_reply(user: str, bid: str, web: bool = False) -> dict | None:
    """Direct answers to quick-reply buttons (no AI needed). None = not a direct action."""
    q = "?web=1" if web else ""
    if bid.startswith("pay:"):
        p = engine.create_payment(bid[4:])
        owner[p["pending_id"]] = user
        return {"text": f"Here's your *{p['name'].lower()}* payment of *{money(p['amount'])}*. "
                        "Confirm it with your PIN in S-Pankki.",
                "url": (f"Pay {money(p['amount'])}", f"{public_url()}/pay/{p['pending_id']}{q}")}
    if bid.startswith("later:"):
        if bid == "later:unusual":
            return {"text": "Great, enjoy it 🙂 I'll keep an eye on your safe-to-spend."}
        if bid == "later:goal":
            return {"text": "Okay 👍 I'll look at your pace again next week."}
        return {"text": "Sure ⏰ I'll remind you again tomorrow at 9:00."}
    if bid.startswith("stop:"):
        p = engine.create_stop_subscription(bid[5:])
        owner[p["pending_id"]] = user
        per = "month" if p["cadence"] == "monthly" else "year"
        return {"text": f"Stop future card payments to *{p['name']}* ({money(p['amount'])}/{per})? Confirm with your PIN.",
                "url": (f"Stop {p['name']}"[:20], f"{public_url()}/stop/{p['pending_id']}{q}")}
    if bid.startswith("keep:"):
        return {"text": f"Okay, {bid[5:]} stays on 👍 I'll tell you if the price changes again."}
    if bid.startswith("save:"):
        _, goal_id, amount = bid.split(":")
        p = engine.create_saving(goal_id, float(amount))
        owner[p["pending_id"]] = user
        goal = p["name"].split(": ", 1)[1]
        return {"text": f"Move *{money(p['amount'])}* to your savings pocket for *{goal}*? Confirm with your PIN.",
                "url": (f"Save {money(p['amount'])}", f"{public_url()}/save/{p['pending_id']}{q}")}
    return None


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
        emoji = wa.quick_reaction(m["text"])
        if emoji:
            wa.send_reaction(to, m["id"], emoji)
        if (m["text"] or "").strip().lower().strip(".!") in STOP_WORDS:
            switch_off(to)
            return
        if not connected(to):
            wa.send_url_button(
                to,
                f"Hi {name} 👋 I'm *Kurre* 🐿️, your S-Pankki money coach. I answer questions about your spending, remind you "
                "before bills and help you save.\n\nTo start, connect your S-Pankki account. You choose what I can see, "
                "and you can switch me off any time.",
                "Connect S-Pankki", f"{public_url()}/connect?u={to}")
            return
        bid = m.get("button_id") or ""
        held = take_held(to)
        if held:  # a proactive message went out as a template: the full one comes first
            wa.send_reply_buttons(to, *signal_message(held, name))
        if bid == templates_wa.SHOW or m.get("kind") == "button":  # a tap on a template's button
            if not held:
                wa.send_text(to, "That update is no longer current 🙂 Ask me anything about your money.")
            return
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
            wa.send_text(to, "For now I can read text messages only. What would you like to know?")
            return
        text = ASK.get(bid) or wa.redact(m["text"])
        if text.strip().lower() in ("my data", "download my data", "export"):
            wa.send_url_button(to, "Your data, yours to keep (MyData): every transaction and what Kurre "
                                   "calculated from it, as a file.", "Download my data", f"{public_url()}/app/export.json")
            return
        if bid not in ASK:
            t = onboarding.turn(engine, to, text, bid)
            if t:
                send_turn(to, t)
                return
        reply = coach().ask(to, text, name=name)
        for b in reply.buttons:
            if "chart" not in b:
                owner[b["url"].rsplit("/", 1)[-1]] = to
        send_reply(to, reply)
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
                  "Kurre now counts the card bill in safe to spend. Synthetic demo bank, no real data.",
                  "Ask Kurre: \"Show my money across all my banks\"."],
        "wa": wa_back(request) if u else ""})


@app.post("/connect", response_class=HTMLResponse)
def connect_submit(request: Request, bg: BackgroundTasks, u: str = Form(""), scope: list[str] = Form([])):
    engine.state.setdefault("connected", {})[u] = {"scope": scope, "at": engine.today.isoformat()}
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


# ---------------- Confirm pages ----------------
def _pending_or_404(pid: str, kind: str) -> dict:
    p = engine.pending(pid)
    if not p or p["kind"] != kind:
        raise HTTPException(404, "This link has expired.")
    return p


def bad_pin(request: Request, path: str):
    web = "&web=1" if request.query_params.get("web") else ""
    return RedirectResponse(f"{path}?err=1{web}", status_code=303)


def not_done(request: Request, err: Exception):
    """The bank said no (bill already paid, not enough money): a page, not a 500."""
    msg = str(err)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Not done", "failed": True, "lines": [msg[:1].upper() + msg[1:] + "."], "wa": wa_back(request)})


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
        return bad_pin(request, f"/pay/{pid}")
    try:
        done = engine.confirm(pid)
    except ValueError as e:
        return not_done(request, e)
    s = engine.safe_to_spend()
    if done["first_time"]:
        notify(pid, f"✅ *{done['name']} paid*: {money(done['amount'])}\n"
                         f"Balance now {money(done['balance_eur'])}.\n"
                         f"Safe to spend until payday ({nice_date(s['next_payday']['date'])}): "
                         f"*{money(s['safe_to_spend_until_payday_eur'])}*, about {money(s['safe_per_day_eur'])} a day.")
    return templates.TemplateResponse(request, "done.html", {
        "title": "Payment sent", "lines": [f"{done['name']}: {money(done['amount'])}", f"Balance {money(done['balance_eur'])}"],
        "wa": wa_back(request)})


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
        return bad_pin(request, f"/invest/{pid}")
    try:
        done = engine.confirm(pid)
    except ValueError as e:
        return not_done(request, e)
    first_time = done["first_time"]
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
    if first_time:
        notify(pid, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Done", "lines": [msg.replace("✅ ", "").replace("*", "")], "wa": wa_back(request)})


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
        return bad_pin(request, f"/stop/{pid}")
    done = engine.confirm(pid)
    subs = engine.subscriptions()
    msg = (f"✅ *{done['name']} stopped.* No more charges of {money(done['amount'])}. "
           f"That's {money(done['per_year_eur'])} a year back in your pocket.\n"
           f"Active subscriptions now: {subs['active_count']}, {money(subs['active_per_month_eur'])} a month.")
    if done["first_time"]:
        notify(pid, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Payment stopped", "lines": [msg.replace("✅ ", "").replace("*", "")],
        "wa": wa_back(request)})


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
        return bad_pin(request, f"/save/{pid}")
    try:
        done = engine.confirm(pid)
    except ValueError as e:
        return not_done(request, e)
    g = engine.state["goals"][p["goal_id"]]
    pct = round(g["saved"] / g["target"] * 100)
    msg = (f"✅ *Saved {money(done['amount'])}* for {g['name']}.\n"
           f"{money(g['saved'])} of {money(g['target'])} ({pct}%). Balance now {money(done['balance_eur'])}.")
    if done["first_time"]:
        notify(pid, msg)
    return templates.TemplateResponse(request, "done.html", {
        "title": "Saved", "lines": [msg.replace("✅ ", "").replace("*", "")], "wa": wa_back(request)})


# ---------------- Demo triggers (used while filming) ----------------
def alert_message(kind: str, name: str) -> tuple[str, list] | None:
    if kind not in ("salary", "unusual", "renewal"):
        raise HTTPException(400, "kind must be salary, unusual or renewal")
    a = engine.alert(kind)
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

def reminder_text(name: str = DEMO_NAME) -> tuple[str, dict]:
    rent = next(b for b in engine.upcoming_bills(45) if b["category"] == "Housing")  # next month's once paid
    return bill_text(rent, engine.safe_to_spend(), name), rent


def bill_text(bill: dict, s: dict, name: str) -> str:
    what = bill["name"].lower()
    when = "tomorrow" if bill["days_away"] == 1 else f"in {bill['days_away']} days"
    return (f"Hi {name} 👋 Your *{what} of {money(bill['amount_eur'])}* is due *{when}* ({nice_date(bill['due_date'])}).\n\n"
            f"After {what} and your usual bills you have *{money(s['safe_to_spend_until_payday_eur'])}* safe to spend "
            f"until payday on {nice_date(s['next_payday']['date'])}. That's about *{money(s['safe_per_day_eur'])} a day*.")


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
        return bill_text(b, d["safe"], name), [(f"pay:{b['bill_id']}", f"Pay {b['name'].lower()} now"),
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
            f"about {money(d['safe_per_day_eur'])} a day.")
    if d["bills_next_7d"]:
        text += "\nComing up: " + ", ".join(f"{b['name']} {money(b['amount_eur'])} on {nice_date(b['due_date'])}"
                                            for b in d["bills_next_7d"]) + "."
    return text, [("ask:week", "Where did it go?"), ("ask:tight", "How do I stretch it?")]


def held_signal(to: str) -> dict | None:
    """The proactive signal waiting for this person since a template went out, if still fresh."""
    h = engine.state.get("held", {}).get(to)
    return h["signal"] if h and time.time() - h["at"] < HELD_STALE_S else None


def take_held(to: str) -> dict | None:
    sig = held_signal(to)
    if engine.state.get("held", {}).pop(to, None):
        engine._save()
    return sig


def mask(to: str) -> str:
    return to if to.startswith("web:") else "•••" + to[-4:]


def wake_one(to: str, dry: bool, force: bool = False) -> dict:
    """Pick this person's top signal and deliver it (or, when dry, only say how it would go)."""
    sig = rules.pick(engine, to, force)
    out = {"to": mask(to), "signal": sig and sig["type"], "via": None}
    if not sig:
        return out
    web = to.startswith("web:")
    text, buttons = signal_message(sig, DEMO_NAME if web else name_of(to))
    out.update(dedup_key=sig["dedup_key"], text=text)
    tpl = None
    if web:
        out["via"] = "inbox"
    elif window_open(to):
        out["via"] = "text"
    else:
        tpl = templates_wa.template_for(sig, engine.today)
        held = held_signal(to)
        if not tpl:
            return {**out, "reason": "no safe template for this signal"}
        if held and held["priority"] <= sig["priority"]:
            return {**out, "reason": f"{held['type']} is already waiting for a reply"}
        out.update(via="template", template=tpl[0], params=tpl[1])
    if dry:
        return out
    if web:
        inbox.setdefault(to, []).append(text)
    elif tpl:
        wa.send_template(to, tpl[0], tpl[1], templates_wa.LANG, payload=templates_wa.SHOW)
        engine.state.setdefault("held", {})[to] = {"signal": sig, "at": time.time()}  # one at a time
    else:
        wa.send_reply_buttons(to, text, buttons)
    engine.state.setdefault("sent", {}).setdefault(to, {})[sig["dedup_key"]] = engine.today.isoformat()
    engine._save()
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
    seen.clear()
    owner.clear()
    inbox.clear()
    web_sessions.clear()
    return {"reset": True}


# ---------------- Web chat (same coach, embeddable in S-mobiili) ----------------
def _ctx(**extra) -> dict:
    return {"money": money, "nice": nice_date, "wa": os.environ.get("WA_DISPLAY_NUMBER", ""), **extra}


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
def export_json():
    return JSONResponse(engine.export(), headers={"content-disposition": 'attachment; filename="my-s-pankki-data.json"'})


@app.get("/app/export.csv")
def export_csv():
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "category", "basket", "amount_eur", "type", "merchant"])
    for t in engine.export()["transactions"]:
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
    try:
        view, error = engine.explore(**explore_args(request)), None
    except ValueError as e:  # a hand-edited link: show the default view and say why
        view, error = engine.explore(), str(e)
    q = view["query"]
    current = next((f"{k}:{q[k]}" for k in ("category", "basket", "merchant_type") if q[k]), "")
    return templates.TemplateResponse(request, "explore.html", _ctx(
        v=view, error=error, current=current, filters=explore_filters(q["merchant_type"]), labels=EXPLORE_LABELS,
        options={"what": EXPLORE_WHAT, "metric": EXPLORE_METRICS, "group_by": EXPLORE_GROUP_BY, "period": PERIODS},
        csv_url="/app/explore.csv?" + explore_query(q)))


@app.get("/app/explore.csv")
def explore_csv(request: Request):
    """Exactly the rows and the total shown on the Explore page for the same query."""
    import csv
    import io
    try:
        v = engine.explore(**explore_args(request))
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
    o = engine.overview()
    return templates.TemplateResponse(request, "dashboard.html", _ctx(
        o=o, name=DEMO_NAME, today=date.fromisoformat(o["today"]).strftime("%A %d %B"), **_chart(o)))


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, q: str = "", embed: str = ""):
    """?q= asks a question once on load (deep link); ?embed=1 drops the site chrome for a WebView."""
    return templates.TemplateResponse(request, "chat.html", {"name": DEMO_NAME, "q": q[:500], "embed": embed == "1"})


# ---------------- S-mobiili integration: native home-screen cards, no AI ----------------
WIDGETS_MAX_AGE = 300


def ask_link(question: str) -> str:
    return "/chat?" + urlencode({"q": question})


def widgets() -> dict:
    """Small cards a banking app home screen can draw natively. Every number comes from the engine;
    each card deep-links into the chat with a question (or a direct action that needs no AI)."""
    s = engine.safe_to_spend()
    pay = s["next_payday"]
    cards = [{"id": "safe_to_spend", "title": "Safe to spend", "value": money(s["safe_to_spend_until_payday_eur"]),
              "subtitle": f"until payday {nice_date(pay['date'])} · {money(s['safe_per_day_eur'])} a day",
              "amount_eur": s["safe_to_spend_until_payday_eur"], "per_day_eur": s["safe_per_day_eur"],
              "payday": pay["date"], "days_to_payday": pay["days_away"],
              "deep_link": ask_link("What is safe for me to spend until payday?")}]
    bill = next((b for b in engine.upcoming_bills(45) if b["bill_id"] and b["category"] != "Invest Deposit"), None)
    if bill:
        when = "tomorrow" if bill["days_away"] == 1 else f"in {bill['days_away']} days"
        cards.append({"id": "next_bill", "title": "Next bill", "value": money(bill["amount_eur"]),
                      "subtitle": f"{bill['name']} · {nice_date(bill['due_date'])} ({when})",
                      "name": bill["name"], "amount_eur": bill["amount_eur"], "due_date": bill["due_date"],
                      "days_away": bill["days_away"],
                      "deep_link": ask_link(f"How does my {bill['name'].lower()} fit before payday?"),
                      "actions": [{"label": "Pay", "deep_link": ask_link(f"__btn__:pay:{bill['bill_id']}")}]})
    b = engine.baskets("this_month")
    if not b["total_spent_eur"]:  # early in the month: show the last full month instead of three zeros
        b = engine.baskets("last_month")
    month = "this month" if b["from"][:7] == engine.today.isoformat()[:7] else "last month"
    cards.append({"id": "baskets", "title": "Your three baskets", "value": money(b["total_spent_eur"]),
                  "subtitle": f"spent {month}", "period": month.replace(" ", "_"), "from": b["from"], "to": b["to"],
                  "total_eur": b["total_spent_eur"],
                  "baskets": [{"key": x["basket"], "label": x["label"], "share_pct": x["share_pct"],
                               "total_eur": x["total_eur"]} for x in b["baskets"]],
                  "deep_link": ask_link(f"Where did my money go {month}, basket by basket?")})
    subs = engine.subscriptions()["subscriptions"]
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
    goals = engine.goals()
    if goals:
        g = goals[0]
        cards.append({"id": "goal", "title": g["goal"]["name"], "value": f"{g['progress_pct']}%",
                      "subtitle": f"{money(g['goal']['saved'])} of {money(g['goal']['target'])}",
                      "goal_id": g["goal"]["id"], "progress_pct": g["progress_pct"],
                      "saved_eur": g["goal"]["saved"], "target_eur": g["goal"]["target"],
                      "deadline": g["goal"].get("deadline"),
                      "deep_link": ask_link(f"How is my {g['goal']['name']} goal going?")})
    return {"version": 1, "as_of": engine.today.isoformat(), "max_age_s": WIDGETS_MAX_AGE, "widgets": cards}


@app.get("/api/widgets")
def widgets_api():
    return JSONResponse(widgets(), headers={"cache-control": f"private, max-age={WIDGETS_MAX_AGE}"})


@app.get("/mobile-demo", response_class=HTMLResponse)
def mobile_demo(request: Request):
    """Concept mock: Kurre's cards and chat inside a generic banking app home screen."""
    return templates.TemplateResponse(request, "mobile_demo.html", _ctx(
        name=DEMO_NAME, balance=engine.balance(), recent=engine.recent(6)))


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

    def quick(buttons):
        return [{"label": t, "send": f"__btn__:{i}"} for i, t in buttons]

    if text == "__remind__":
        t, rent = reminder_text(DEMO_NAME)
        return {"text": t, "buttons": [], "quick": quick([(f"pay:{rent['bill_id']}", "Pay rent now"),
                                                          (f"later:{rent['bill_id']}", "Remind me later"),
                                                          ("ask:tight", "How do I stretch it?")])}
    if text.startswith("__alert__:"):
        m = alert_message(text.split(":", 1)[1], DEMO_NAME)
        return {"text": m[0], "buttons": [], "quick": quick(m[1])} if m else {"text": "Nothing to flag right now.", "buttons": []}
    def interview(t: onboarding.Turn, buttons=()):
        return {"text": "\n\n".join(t.lead + [t.text]), "buttons": list(buttons), "quick": quick(t.buttons)}

    if text == "__start__":  # the web chat's stand-in for Connect S-Pankki
        return interview(onboarding.start(engine, sid), [{"label": "Open My money", "url": "/app"}])
    bid = ""
    if text.startswith("__btn__:"):
        bid = text.split(":", 1)[1]
        try:
            direct = button_reply(sid, bid, web=True)
        except ValueError as e:
            return {"text": f"I can't do that: {e}", "buttons": []}
        if direct:
            url = direct.get("url")
            return {"text": direct["text"], "buttons": [{"label": url[0], "url": url[1].replace(public_url(), "")}] if url else []}
        text = ASK.get(bid, bid)
    if bid not in ASK:
        t = onboarding.turn(engine, sid, "" if bid else text, bid)
        if t:
            return interview(t)
    import anyio
    try:
        reply = await anyio.to_thread.run_sync(lambda: coach().ask(sid, text, name=DEMO_NAME))
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
    again = onboarding.reask(engine, sid)  # a side question mid-interview: the waiting question comes back
    return JSONResponse({"text": reply.text + (f"\n\n{again.text}" if again else ""), "tools": reply.tools_used,
                         "buttons": out, "charts": charts, "quick": quick(again.buttons) if again else []})


@app.get("/api/inbox")
def chat_inbox(session: str = ""):
    return {"messages": inbox.pop("web:" + session[:40], [])}


@app.get("/health")
def health():
    return {"ok": True, "today": engine.today.isoformat(), "balance": engine.balance(), "whatsapp": wa.configured()}
