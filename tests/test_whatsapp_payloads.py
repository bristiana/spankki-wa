"""Every WhatsApp flow, driven through POST /webhook with signed payloads: each body handed to wa._post must be one
Meta accepts (wa.validate_payload). The AI is a fake coach that runs the real tools, so buttons come from real code."""
import hashlib
import hmac
import json
import time
from datetime import date
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import bills, main, rules, voice
from app import whatsapp as wa
from app.agent import Reply
from app.engine import Engine
from app.tools import ready_of, run_tool

SECRET = "test-app-secret"
BASE = "https://oiva.example"
PHONES = [("358401234567", "Aino Test"),
          ("358409876543", "Kristiina-Maria🌸🐿️ Väisänen-Hämäläinen-Korhonen ✨ (S-Pankki asiakas)")]


class FakeCoach:
    """Stands in for Claude: a reply scripted per test, built with the real tools (buttons, charts)."""

    def __init__(self):
        self.asked, self.contexts, self.resets = [], [], []
        self.reaction = None
        self.script = lambda e, text, buttons: "You have 68.60 € safe to spend until payday."

    def ask(self, user, text, name="there", context=None):
        self.asked.append(text)
        self.contexts.append(context)
        buttons = []
        return Reply(text=self.script(main.engine, text, buttons), buttons=buttons, reaction=self.reaction)

    def reset(self, user=None):
        self.resets.append(user)


class Chat:
    def __init__(self, client, phone, profile, sent):
        self.client, self.phone, self.profile, self.sent = client, phone, profile, sent
        self.ids = count(1)

    def _post(self, message: dict):
        n = next(self.ids)
        message = {"from": self.phone, "id": f"wamid.{self.phone}.{n}", "timestamp": str(1_790_000_000 + n), **message}
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "contacts": [{"wa_id": self.phone, "profile": {"name": self.profile}}],
            "messages": [message]}}]}]}
        raw = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        r = self.client.post("/webhook", content=raw, headers={"x-hub-signature-256": sig,
                                                                "content-type": "application/json"})
        assert r.status_code == 200
        return self

    def say(self, text):
        return self._post({"type": "text", "text": {"body": text}})

    def tap(self, button_id, title="Button"):
        return self._post({"type": "interactive", "interactive": {
            "type": "button_reply", "button_reply": {"id": button_id, "title": title}}})

    def pick(self, row_id, title="Row"):
        return self._post({"type": "interactive", "interactive": {
            "type": "list_reply", "list_reply": {"id": row_id, "title": title}}})

    def media(self, kind, mime="image/jpeg", caption=None):
        return self._post({"type": kind, kind: {"id": "media-1", "mime_type": mime}
                                               | ({"caption": caption} if caption else {})})

    # what came back
    def interactive(self, itype=None):
        return [b["interactive"] for b in self.sent if b.get("type") == "interactive"
                and (itype is None or b["interactive"]["type"] == itype)]

    def buttons(self):
        """[(id, title)] of the last reply-button message."""
        return [(b["reply"]["id"], b["reply"]["title"]) for b in self.interactive("button")[-1]["action"]["buttons"]]

    def url(self):
        return self.interactive("cta_url")[-1]["action"]["parameters"]["url"]

    def texts(self):
        return [b["text"]["body"] for b in self.sent if b.get("type") == "text"]

    def confirm(self, url=None):
        """A cta_url button lands on S-mobiili home; its "Ready for you" card opens the review, confirmed with
        (simulated) Face ID."""
        home = (url or self.url()).replace(BASE, "")
        route, pid = ready_of(home)
        path = f"/s-mobiili/{route}/{pid}"
        assert home.startswith("/s-mobiili/?ready=") and f'href="{path}"' in self.client.get(home).text
        assert self.client.get(path).status_code == 200
        page = self.client.post(path + "/confirm", json={})
        assert page.status_code == 200 and page.json()["ok"]
        return page

    def connect(self):
        r = self.client.post("/connect", data={"u": self.phone, "scope": ["tx"]})
        assert r.status_code == 200
        return self


@pytest.fixture(params=PHONES, ids=["short-name", "long-finnish-emoji-name"])
def chat(request, tmp_path, monkeypatch):
    monkeypatch.setenv("WA_APP_SECRET", SECRET)
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    monkeypatch.setenv("PUBLIC_URL", BASE)
    for k in ("WA_TOKEN", "DEMO_PHONE", "DEMO_PROACTIVE", "T212_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = FakeCoach()
    for d in (main.seen, main.owner, main.last_seen, main.names, main.inbox):
        d.clear()
    sent = []
    monkeypatch.setattr(wa, "_post", sent.append)
    monkeypatch.setattr(wa, "BUBBLE_PAUSE_S", 0)
    c = Chat(TestClient(main.app), *request.param, sent)
    yield c
    bad = [(errs, b) for b in sent if (errs := wa.validate_payload(b))]
    assert not bad, "invalid WhatsApp payloads:\n" + "\n".join(f"{e}: {json.dumps(b)[:300]}" for e, b in bad)


def connected(chat):
    main.engine.state.setdefault("connected", {})[chat.phone] = {"scope": ["tx"], "at": "2026-10-06"}
    return chat


# ---------------- connect, welcome, onboarding ----------------
def test_first_contact_gets_the_connect_button(chat):
    main.engine.state.setdefault("switched_off", {})[chat.phone] = "2026-10-06"  # a new number goes into the demo
    chat.say("Hi")
    assert chat.url() == f"{BASE}/connect?u={chat.phone}&lang=en"
    assert chat.interactive("cta_url")[-1]["action"]["parameters"]["display_text"] == "Connect S-Pankki"


def test_connect_sends_welcome_and_no_question(chat):
    chat.connect()
    assert [t for t in chat.texts() if t.startswith("Connected. ")]
    assert chat.url() == f"{BASE}/s-mobiili/"
    assert chat.texts()[-1] == "Hi! Ask me about your money, like what's left until payday." and not chat.interactive("button")


def test_onboarding_all_buttons_then_save(chat):
    chat.connect().say("set a goal").tap("ob:goal:trip").say("1,500 € by June")
    (bid, title), = chat.buttons()
    assert bid.startswith("save:trip:") and len(title) <= 20
    chat.tap(bid)
    assert "?ready=save:" in chat.url()
    chat.confirm()
    assert any(t.startswith("*Saved") for t in chat.texts())


def test_onboarding_typed_answers(chat):
    chat.connect().say("set a goal").say("I want 3000 for a trip to Lapland by December 2027")
    assert chat.buttons()[0][0].startswith("save:")


def test_onboarding_typed_long_goal_name(chat):
    chat.connect().say("set a goal")
    chat.say("I'm saving for a brand new electric cargo bicycle for the whole family, 4 200 € in 14 months")
    assert chat.buttons()[0][0].startswith("save:")


def test_onboarding_skip_everything(chat):
    chat.connect().say("set a goal").say("skip").tap("ob:skip")
    assert "When you have an amount in mind" in chat.texts()[-1]
    chat.tap("ob:goal:trip")  # an old button after the end
    assert "already set up" in chat.texts()[-1]


def test_onboarding_pause_resume_side_question_and_stale_button(chat):
    chat.connect().say("set a goal").tap("ob:goal:buffer").say("lots of money")  # no amount: asked again
    chat.say("later").say("continue").say("How much did I spend on taxis?")
    assert main._coach.asked == ["How much did I spend on taxis?"]
    chat.tap("ob:goal:home").say("2k in 6 months")
    assert chat.buttons()[0][0].startswith("save:")


# ---------------- the basket game ----------------
def test_basket_game_full_run_replay_pause_and_resume(chat):
    connected(chat).say("game")
    for rnd in range(5):
        options = chat.buttons()
        assert [i.split(":")[2] for i, _ in options] == [str(rnd)] * 3
        chat.tap(*options[rnd % 3])
    ids = [i for i, _ in chat.buttons()]
    assert ids == ["ask:everyday", "quiz:again", "quiz:money"]
    chat.tap("quiz:money")
    assert chat.url().endswith("/s-mobiili/")
    chat.tap("ask:everyday")
    chat.tap("quiz:again").say("1").say("stop")
    assert "Paused" in chat.texts()[-1]
    chat.say("game").say("3").say("What is safe to spend this week?")
    assert any("paused" in t for t in chat.texts())
    chat.tap("quiz:0:0:regular")  # a button from the first game
    chat.say("Play the basket game")


# ---------------- rent reminder, pay ----------------
def test_rent_reminder_pay_and_confirmation(chat):
    connected(chat)
    r = chat.client.post("/demo/remind", params={"to": chat.phone})
    assert r.json()["sent"]
    ids = [i for i, _ in chat.buttons()]
    assert ids[0].startswith("pay:") and ids[1].startswith("later:") and ids[2] == "ask:tight"
    chat.tap(ids[1]).tap(ids[2]).tap(ids[0])
    assert "?ready=pay:" in chat.url()
    chat.confirm()
    assert "Rent paid" in chat.texts()[-1]
    chat.tap(ids[0])  # paying again: refused, in words
    assert chat.texts()[-1].startswith("I can't do that")


# ---------------- AI replies with action buttons ----------------
def test_invest_save_pause_from_the_coach(chat):
    connected(chat)

    def script(e, text, buttons):
        if "invest" in text:
            run_tool(e, "create_investment_link", {"amount_eur": 10000, "fund_id": "growth", "monthly": True}, buttons)
            run_tool(e, "create_investment_link", {"amount_eur": 250, "fund_id": "prudent", "monthly": False}, buttons)
            return "Here are two ways in. The value can go down as well as up."
        if "save" in text:
            g = e.create_goal("Lapland trip with friends", 1500, "2027-06-30")["goal"]
            run_tool(e, "create_saving_link", {"goal_id": g["id"], "amount_eur": 125}, buttons)
            return "Put 125 € aside now for your Lapland trip?"
        run_tool(e, "create_pause_investing_link", {}, buttons)
        return "Pause your monthly investing? Confirm with your PIN."

    main._coach.script = script
    for question in ("I want to invest", "help me save", "pause the monthly deposits"):
        chat.say(question)
    urls = [i["action"]["parameters"]["url"] for i in chat.interactive("cta_url")]
    assert any("?ready=invest:" in u for u in urls) and any("?ready=save:" in u for u in urls) and any("?ready=stop:" in u for u in urls)
    for u in urls:
        chat.confirm(u)
    done = chat.texts()
    assert any("Monthly saving started" in t for t in done) and any("*Invested*" in t for t in done)
    assert any("Monthly investing paused" in t for t in done) and any("*Saved" in t for t in done)


def test_explore_chart_reply_is_words_only(chat):
    connected(chat)

    def script(e, text, buttons):
        run_tool(e, "explore_my_data", {"category": "Restaurants", "group_by": "weekday"}, buttons)
        run_tool(e, "explore_my_data", {"what": "income", "group_by": "month", "period": "last_3_months"}, buttons)
        return "You spend most on restaurants on Saturdays."

    main._coach.script = script
    chat.say("When do I spend most on restaurants?")
    assert not chat.interactive("cta_url")  # the chart is drawn only in the web chat
    assert chat.texts()[-1] == "You spend most on restaurants on Saturdays."


def test_money_guide_and_home_readiness_replies(chat):
    connected(chat)

    def script(e, text, buttons):
        if "home" in text:
            out = json.loads(run_tool(e, "get_home_readiness", {}, buttons))
            return f"From your salary, here is a first look.\n\nASP: {out['asp']['sources'][0]}"
        out = json.loads(run_tool(e, "get_money_guide", {"topic": "deposit-guarantee"}, buttons))
        return out["text"] + "\n\nSource: " + out["sources"][0]

    main._coach.script = script
    chat.say("Is my money safe in the bank?").say("Could I afford my first home?")
    assert len(chat.texts()) >= 2


def test_long_ai_replies_are_split_never_cut(chat):
    connected(chat)
    para = ("Your rent is the biggest regular payment, and it comes back every month on the 7th. " * 11).strip()
    replies = {
        "many": "\n\n".join([para] * 6),  # bubbles merge the rest into the last one: far over 1024
        "one": (para + " ") * 6,  # one paragraph over 4096 chars
    }

    def script(e, text, buttons):
        bill = next(b for b in e.upcoming_bills() if b["name"] == "Rent")
        if text != "plain":
            run_tool(e, "create_payment_link", {"bill_id": bill["bill_id"]}, buttons)
        return replies.get(text, replies["one"])

    main._coach.script = script
    for text in ("many", "one", "plain"):
        before = len(chat.sent)
        chat.say(text)
        out = chat.sent[before:]
        said = " ".join(b["text"]["body"] if b.get("type") == "text" else b["interactive"]["body"]["text"]
                        for b in out if b.get("type") in ("text", "interactive"))
        assert said.count("on the 7th") == replies.get(text, replies["one"]).count("on the 7th")  # nothing lost
    assert len(wa.bubbles(replies["many"])) == 3


def test_reply_with_two_buttons_and_a_failing_coach(chat):
    connected(chat)

    def script(e, text, buttons):
        if text == "boom":
            raise RuntimeError("AI down")
        bill = next(b for b in e.upcoming_bills() if b["name"] == "Rent")
        run_tool(e, "create_payment_link", {"bill_id": bill["bill_id"]}, buttons)
        g = e.create_goal("Buffer", 500)["goal"]
        run_tool(e, "create_saving_link", {"goal_id": g["id"], "amount_eur": 50}, buttons)
        return "Pay the rent first, then put 50 € aside."

    main._coach.script = script
    chat.say("rent and saving").say("boom")
    assert len(chat.interactive("cta_url")) == 2
    assert chat.texts()[-1].startswith("Something broke on my end")


# ---------------- proactive ----------------
@pytest.mark.parametrize("kind", ["salary", "unusual", "bills"])
def test_each_demo_alert_and_its_buttons(chat, kind):
    connected(chat)
    r = chat.client.post("/demo/alert", params={"kind": kind, "to": chat.phone}).json()
    if not r["sent"]:
        return
    for bid, title in chat.buttons():
        chat.tap(bid, title)
    for u in [i["action"]["parameters"]["url"] for i in chat.interactive("cta_url")]:
        chat.confirm(u)


def test_salary_alert_with_a_goal_and_a_long_name(chat):
    connected(chat)
    main.engine.create_goal("Interrail summer around Europe with my sister", 2400, "2027-06-30")
    chat.say("hello")  # the profile name is learnt from the first message
    chat.client.post("/demo/alert", params={"kind": "salary", "to": chat.phone})
    assert chat.buttons()[0][0].startswith("save:")


@pytest.mark.parametrize("today", ["2026-08-30", "2026-10-06", "2026-10-15", "2026-10-17"])
def test_cron_wake_every_signal(chat, monkeypatch, today):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    main.engine = Engine(today=date.fromisoformat(today), state_path=main.engine.state_path)
    connected(chat)
    gid = main.engine.create_goal("Very far away dream holiday", 5000, "2027-01-31")["goal"]["id"]
    main.engine.state["goals"][gid]["created"] = "2026-06-01"  # behind plan: a GOAL_DRIFT signal
    main.last_seen[chat.phone] = time.time()  # inside the 24-hour window: the full message, no template
    kinds = []
    for _ in range(12):  # force: every signal in turn, each once (wake_one marks it sent)
        r = chat.client.post("/cron/wake", params={"key": "vt", "force": 1}).json()["results"][0]
        if r["signal"] is None:
            break
        assert r["via"] == "text"
        kinds.append(r["signal"])
    assert "GOAL_DRIFT" in kinds and "WEEKLY_CHECK_IN" in kinds
    for sig in rules.signals(main.engine):  # and every candidate's full message, whatever won today
        wa.send_reply_buttons(chat.phone, *main.signal_message(sig, main.name_of(chat.phone)))


# ---------------- STOP, media, odd inputs ----------------
# ---------------- the Instinct voice: reactions, "---" bubbles, guardrails ----------------
def reactions(chat):
    return [b["reaction"] for b in chat.sent if b.get("type") == "reaction"]


def test_reaction_on_their_message_then_dash_bubbles(chat):
    connected(chat)
    main._coach.reaction = "🏠"
    main._coach.script = lambda e, text, buttons: "Yes, your rent is covered.\n---\nWant the payment link?"
    chat.say("is my rent covered?")
    kinds = [b["type"] for b in chat.sent if "status" not in b]
    assert kinds == ["reaction", "text", "text"]  # the reaction first, then one message per bubble
    assert reactions(chat) == [{"message_id": f"wamid.{chat.phone}.1", "emoji": "🏠"}]
    assert chat.texts() == ["Yes, your rent is covered.", "Want the payment link?"]
    assert not any("---" in t or "[react]" in t for t in chat.texts())


def test_reaction_only_reply_sends_just_the_reaction(chat):
    connected(chat)
    main._coach.reaction = "🙏"
    main._coach.script = lambda e, text, buttons: ""
    chat.say("thanks!")
    assert [b["type"] for b in chat.sent if "status" not in b] == ["reaction"] and chat.texts() == []


def test_no_reaction_when_the_pace_skips_it(chat):
    connected(chat)
    main._coach.script = lambda e, text, buttons: "68.60 €.\n---\nThat's until payday."
    chat.say("safe to spend?")
    assert reactions(chat) == [] and chat.texts() == ["68.60 €.", "That's until payday."]


def test_dash_bubbles_with_a_button_on_the_last(chat):
    connected(chat)
    main._coach.reaction = "✅"

    def script(e, text, buttons):
        bill = next(b for b in e.upcoming_bills() if b["name"] == "Rent")
        run_tool(e, "create_payment_link", {"bill_id": bill["bill_id"]}, buttons)
        return "I can't move money myself.\n---\nHere's the rent payment, ready for your PIN."

    main._coach.script = script
    chat.say("ok")
    assert chat.texts() == ["I can't move money myself."]
    assert chat.interactive("cta_url")[-1]["body"]["text"] == "Here's the rent payment, ready for your PIN."


def test_secret_gets_a_kind_stop_and_never_reaches_the_coach(chat):
    connected(chat).say("my PIN is 4821")
    assert reactions(chat)[-1]["emoji"] == "🔒" and main._coach.asked == []
    assert chat.texts()[0].startswith("Please delete that message, I never need your PIN")
    assert "number on your card" in chat.texts()[1]
    assert not any("4821" in json.dumps(b) for b in chat.sent)
    chat.say("card 4242 4242 4242 4242 why was I charged twice?")
    assert main._coach.asked == ["card [card number removed] why was I charged twice?"]
    assert "removed before it reached you" in main._coach.contexts[-1]
    assert "customer service in S-mobiili" in main._coach.contexts[-1]  # a dispute: a person at S-Pankki


def test_fraud_hands_off_to_s_pankki(chat):
    connected(chat).say("someone used my card, I didn't make this payment")
    assert "freeze it in S-mobiili" in main._coach.contexts[-1] and "no phone number" in main._coach.contexts[-1]


def test_fresh_start_clears_the_chat(chat):
    connected(chat).say("learn").say("/new")
    assert chat.texts()[-1] == "Hi! Ask me about your money, like what's left until payday." and main._coach.resets == [chat.phone]
    assert main.engine.state["learn"][chat.phone]["paused"]


def test_hello_at_the_goal_question_is_not_a_goal(chat):
    chat.connect().say("set a goal").say("hello")
    body = chat.interactive("button")[-1]["body"]["text"]
    assert "Saving for" not in body and body.startswith("Hey, good to see you.") and body.endswith("What would you like to put money aside for?")
    assert [i for i, _ in chat.buttons()] == ["ob:goal:trip", "ob:goal:buffer", "ob:goal:home"]
    chat.say("a trip to Rome")
    assert chat.interactive("button")[-1]["body"]["text"].startswith("Saving for a trip to Rome.")
    assert main._coach.asked == []


def test_open_thread_comes_back_the_next_day_with_one_button(chat, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    connected(chat).say("learn").tap("learn:next:0", "Next lesson").say("stop")
    # rent was already flagged this week, so nothing more urgent is waiting today
    main.engine.state["sent"] = {chat.phone: {"due:housing_payment-2026-10-07": "2026-10-01"}}
    ago = time.time() - 20 * 3600  # they went quiet yesterday, mid-course; WhatsApp's 24 hours are still open
    main.engine.state["wa_last"][chat.phone] = main.last_seen[chat.phone] = ago
    r = chat.client.post("/cron/wake", params={"key": "vt"}).json()["results"][0]
    assert r["signal"] == "OPEN_THREAD" and r["via"] == "text" and r["dedup_key"] == "thread:learn:1:1"
    assert "Lesson 2/5" in r["text"] and "1 of 5 done" in r["text"]
    assert chat.buttons() == [("learn:start", "Continue lesson")]
    chat.tap("learn:start", "Continue lesson")
    assert "*2/5" in chat.interactive("button")[-1]["body"]["text"]
    main.engine.state["wa_last"][chat.phone] = main.last_seen[chat.phone] = ago  # quiet again, same lesson
    main.engine.state["sent"][chat.phone] = {k: "2026-09-01" for k in main.engine.state["sent"][chat.phone]}
    again = chat.client.post("/cron/wake", params={"key": "vt"}).json()["results"][0]
    assert again["signal"] != "OPEN_THREAD"  # one useful return, never twice


def test_pin_link_left_open_comes_back_as_its_link(chat):
    connected(chat)
    bill = next(b for b in main.engine.upcoming_bills() if b["name"] == "Rent")
    p = main.engine.create_payment(bill["bill_id"])
    sig = next(s for s in rules.signals(main.engine, chat.phone, force=True) if s["type"] == "OPEN_THREAD")
    text, buttons = main.signal_message(sig, "Aino")
    wa.send_reply_buttons(chat.phone, text, buttons)
    chat.tap(*buttons[0])
    assert chat.url().endswith(f"/s-mobiili/?ready=pay:{p['pending_id']}")
    chat.confirm()


def test_stop_switches_off_and_hi_reconnects(chat):
    connected(chat).say("game").say("stop")
    assert "Paused" in chat.texts()[-1]
    chat.say("STOP")
    assert chat.texts()[-1].startswith("Done. Oiva is switched off")
    chat.say("Hi")
    assert "/connect?u=" in chat.url()


@pytest.mark.parametrize("kind,mime", [("video", "video/mp4"), ("sticker", "image/webp"), ("document", "application/zip"),
                                       ("location", "")])
def test_media_messages(chat, kind, mime):
    connected(chat).media(kind, mime)
    assert chat.texts()[-1].startswith("I can read text, voice notes, photos, PDFs")


def test_bill_photo_explain_then_pay(chat, monkeypatch):
    from tests.test_bills import PNG, VOLTTI
    monkeypatch.setattr(bills, "download", lambda media_id: PNG)
    monkeypatch.setattr(bills, "extract", lambda client, data, media_type: bills.clean(VOLTTI))
    errors = []

    def eager(e, text, buttons):  # tries to pay on every turn: only "pay it" may
        try:
            run_tool(e, "create_bill_payment_link", {}, buttons)
            return "Ready: *57.08 €* to Voltti Energia Oy (demo), due 20 Oct. Confirm it in the bank."
        except ValueError as err:
            errors.append(str(err))
            return "It's your September electricity bill: *57.08 €*, due Tue 20 Oct. Want me to prepare the payment?"
    main._coach.script = eager
    connected(chat).media("image", caption="explain it to me")
    assert "<bill_photo>" in main._coach.asked[-1] and main.engine.state["pending"] == {}
    assert not chat.interactive("cta_url") and "only asked to understand" in errors[-1]
    chat.say("ok let's pay it")
    [(pid, p)] = main.engine.state["pending"].items()
    assert chat.url() == f"{BASE}/s-mobiili/?ready=pay:{pid}" and p["iban"] == "FI5979900020451187"
    chat.confirm()
    assert any("Voltti Energia Oy (demo) paid" in t for t in chat.texts())


def test_bill_pdf_that_is_not_a_bill_and_one_that_cannot_be_opened(chat, monkeypatch):
    from tests.test_bills import NOT_A_BILL
    download = bills.download
    monkeypatch.setattr(bills, "download", lambda media_id: b"%PDF-1.7 ...")
    monkeypatch.setattr(bills, "extract", lambda client, data, media_type: bills.clean(NOT_A_BILL))
    connected(chat).media("document", "application/pdf")
    assert "can't see anything about money" in chat.texts()[-1] and main._coach.asked == []
    monkeypatch.setattr(bills, "download", download)
    chat.media("image")  # no WA_TOKEN in tests: the download fails and Oiva says so
    assert chat.texts()[-1].startswith("That photo didn't open for me")


def test_voice_note_is_answered_like_text(chat, monkeypatch):
    monkeypatch.setattr(voice, "to_text", lambda media_id: "How much can I spend until payday?")
    connected(chat).media("audio")
    assert chat.texts()[0] == "Heard: _How much can I spend until payday?_"
    assert main._coach.asked == ["How much can I spend until payday?"]
    assert "68.60" in chat.texts()[-1]


def test_voice_note_stop_works_by_voice(chat, monkeypatch):
    monkeypatch.setattr(voice, "to_text", lambda media_id: "Stop.")
    connected(chat).media("audio")
    assert chat.texts()[-1].startswith("Done. Oiva is switched off")


def test_voice_note_that_cannot_be_heard(chat, monkeypatch):
    def fail(media_id):
        raise voice.VoiceError("Sorry, I couldn't hear that voice note. Could you send it again, or type it?")
    monkeypatch.setattr(voice, "to_text", fail)
    connected(chat).media("audio")
    assert chat.texts() == ["Sorry, I couldn't hear that voice note. Could you send it again, or type it?"]
    assert main._coach.asked == []


def test_voice_note_without_speech_to_text(chat):
    connected(chat).media("audio")  # no WA_TOKEN in tests, so the download fails and Oiva says so
    assert chat.texts()[-1].startswith("Sorry, I couldn't hear that voice note")


def test_list_reply_and_my_data(chat):
    connected(chat).pick("ask:week", "Where did it go?").say("my data")
    assert chat.url().endswith("/app/export.json")


def test_whatsapp_stays_on_the_default_persona(chat):
    other = next(p for p in main.PERSONAS if p != main.DEFAULT_PERSONA)
    chat.client.get(f"/s-mobiili/?as={other}")  # a judge switched the web app
    connected(chat)
    r = chat.client.post("/demo/remind", params={"to": chat.phone}).json()
    assert "771.82 €" in r["text"]
    chat.say("What is safe to spend?")
    assert main._coach.asked == ["What is safe to spend?"] and other not in main._coaches


# ---------------- the demo phone after a restart ----------------
def test_demo_phone_hi_after_restart_goes_to_the_coach(chat, monkeypatch):
    monkeypatch.setenv("DEMO_PHONE", chat.phone)  # connected without a record, as after a redeploy
    chat.say("hi").say("start")
    assert main._coach.asked == ["hi", "start"]
    assert not chat.interactive("button") and not chat.interactive("cta_url")


@pytest.mark.parametrize("words", ["start over", "Get started!", "restart"])
def test_demo_phone_start_over_is_a_clean_hello(chat, monkeypatch, words):
    """'start over' clears the chat and says hello: no interview, no buttons, no questions pushed."""
    monkeypatch.setenv("DEMO_PHONE", chat.phone)
    chat.say("how much did I spend?").say(words)
    assert main._coach.asked == ["how much did I spend?"] and main._coach.resets == [chat.phone]
    assert chat.texts()[-1] == "Hi! Ask me about your money, like what's left until payday."
    assert not chat.interactive("button") and "u" not in main.engine.state.get("onboarding", {})
    assert chat.phone not in main.engine.state.get("onboarding", {})


def test_start_over_when_not_connected_asks_to_connect(chat):
    main.engine.state.setdefault("switched_off", {})[chat.phone] = "2026-10-06"  # new numbers go into the demo
    chat.say("start over")
    assert "/connect?u=" in chat.url() and not chat.interactive("button")


# ---------------- the validator and the repair in _post ----------------
def buttons_body(titles, text="Pick one", ids=None):
    ids = ids or [f"id{n}" for n in range(len(titles))]
    return {"to": "1", "type": "interactive", "interactive": {"type": "button", "body": {"text": text}, "action": {
        "buttons": [{"type": "reply", "reply": {"id": i, "title": t}} for i, t in zip(ids, titles)]}}}


def cta(label, url):
    return {"to": "1", "type": "interactive", "interactive": {"type": "cta_url", "body": {"text": "Hi"},
            "action": {"name": "cta_url", "parameters": {"display_text": label, "url": url}}}}


def test_validate_payload_limits():
    v = wa.validate_payload
    assert v({"status": "read", "message_id": "m"}) == []
    assert v({"to": "1", "type": "text", "text": {"body": "x" * 4096}}) == []
    assert v({"to": "1", "type": "text", "text": {"body": "x" * 4097}})
    assert v({"to": "1", "type": "text", "text": {"body": " "}})
    assert v(buttons_body(["Yes", "No"])) == []
    assert v(buttons_body(["A title that is too long"]))
    assert v(buttons_body(["A", "B", "C", "D"]))
    assert v(buttons_body(["A", "A"]))
    assert v(buttons_body(["A", "B"], ids=["x", "x"]))
    assert v(buttons_body(["A"], ids=["x" * 257]))
    assert v(buttons_body(["A"], text="x" * 1025))
    assert v(cta("Open S-mobiili", "https://x.fi/s-mobiili/")) == [] and v(cta("Open", "http://localhost:8000/s-mobiili/")) == []
    assert v(cta("Open", "http://x.fi/s-mobiili/")) and v(cta("Open everything in my money", "https://x.fi"))
    rows = [{"id": f"r{n}", "title": "Row"} for n in range(11)]
    lst = {"to": "1", "type": "interactive", "interactive": {"type": "list", "body": {"text": "Hi"}, "action": {
        "button": "Choose", "sections": [{"rows": rows}]}}}
    assert v(lst)
    lst["interactive"]["action"]["sections"][0]["rows"] = rows[:10]
    assert v(lst) == []
    lst["interactive"]["action"]["sections"][0]["rows"][0]["title"] = "A row title over 24 chars"
    assert v(lst)
    react = lambda e: {"to": "1", "type": "reaction", "reaction": {"message_id": "m", "emoji": e}}
    for ok in ("👀", "🙏", "👍🏽", "❤️", "🇫🇮", "👩‍💻", ""):
        assert v(react(ok)) == [], ok
    for bad in ("👀🙏", "ok", "a", "👍 "):
        assert v(react(bad)), bad


def test_repair_keeps_every_word_and_fits_the_limits():
    long = buttons_body(["Irregular must-pay but longer", "Pay now", "Later"],
                        text="\n\n".join(f"Paragraph {n}. " + "word " * 150 for n in range(5)))
    parts = wa.repair(long)
    assert all(wa.validate_payload(p) == [] for p in parts)
    assert parts[-1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Irregular must-pay…"
    said = " ".join(p["text"]["body"] if p["type"] == "text" else p["interactive"]["body"]["text"] for p in parts)
    assert all(f"Paragraph {n}." in said for n in range(5)) and said.count("word") == 750

    four = wa.repair(buttons_body(["One", "Two", "Three", "Four"]))
    assert len(four) == 1 and four[0]["interactive"]["type"] == "list" and wa.validate_payload(four[0]) == []
    assert [r["id"] for r in four[0]["interactive"]["action"]["sections"][0]["rows"]] == ["id0", "id1", "id2", "id3"]

    one_para = wa.repair(buttons_body(["Ok"], text="x " * 3000))
    assert one_para[-1]["interactive"]["body"]["text"] == wa.OVERFLOW_BODY
    assert all(wa.validate_payload(p) == [] for p in one_para)
    assert wa.repair(cta("Open everything in my money", "https://x.fi"))[0]["interactive"]["action"]["parameters"][
        "display_text"] == "Open everything in…"


def test_post_repairs_instead_of_sending_something_meta_rejects(monkeypatch, caplog):
    out = []
    monkeypatch.setattr(wa, "_send", out.append)
    wa._post(buttons_body(["One", "Two", "Three", "A fourth one, too long"], text="Hi " * 1500))
    assert [b["type"] for b in out] == ["text", "text", "interactive"] and all(wa.validate_payload(b) == [] for b in out)
    assert sum(b["text"]["body"].count("Hi") for b in out[:2]) == 1500
    assert out[-1]["interactive"]["type"] == "list" and "WA payload invalid" in caplog.text
    out.clear()
    wa.send_url_button("1", "Short", "Open S-mobiili", "https://x.fi/s-mobiili/")
    assert len(out) == 1 and out[0]["interactive"]["body"]["text"] == "Short"  # a valid body goes as it is


def test_builders_split_long_texts_instead_of_cutting(monkeypatch):
    out = []
    monkeypatch.setattr(wa, "_post", out.append)
    wa.send_text("1", "word " * 2000)
    assert len(out) == 3 and sum(b["text"]["body"].count("word") for b in out) == 2000
    out.clear()
    wa.send_reply_buttons("1", "\n\n".join(["Intro " * 200, "Which one?"]), [("a", "A")])
    assert [b["type"] for b in out] == ["text", "interactive"]
    assert out[-1]["interactive"]["body"]["text"] == "Which one?"


def test_pay_label_fits_a_button():
    assert main.pay_label("Rent") == "Pay rent now"
    assert main.pay_label("Utilities (several small bills)") == "Pay now"


def test_lopeta_answers_in_finnish_and_the_connect_button_follows_the_language(chat):
    """STOP in Finnish: the goodbye, and the Connect button on the next message, are in Finnish; in English, English.
    The goodbye names where things went (Anthropic through Meta, voice notes to Cloudflare, ElevenLabs only in the app)."""
    connected(chat).say("lopeta")
    bye = chat.texts()[-1]
    assert bye.startswith("Valmis. Oiva on nyt pois päältä") and "Cloudflare" in bye and "ElevenLabs" in bye
    assert "Anthropic" in bye and "/privacy?lang=fi" in bye
    chat.say("Hei")
    assert chat.interactive("cta_url")[-1]["action"]["parameters"]["display_text"] == "Yhdistä S-Pankki"
    assert chat.url().endswith("&lang=fi")
    chat.say("Hi")
    assert chat.interactive("cta_url")[-1]["action"]["parameters"]["display_text"] == "Connect S-Pankki"


def test_stop_in_english_names_where_things_went(chat):
    connected(chat).say("STOP")
    bye = chat.texts()[-1]
    assert bye.startswith("Done. Oiva is switched off") and "Cloudflare" in bye and "ElevenLabs" in bye
    assert "Anthropic" in bye and "/privacy?lang=en" in bye


def test_a_finn_writing_stop_keeps_finnish(chat):
    connected(chat).say("Hei").say("STOP")
    assert chat.texts()[-1].startswith("Valmis. Oiva on nyt pois päältä")
    chat.say("ok")
    assert chat.interactive("cta_url")[-1]["action"]["parameters"]["display_text"] == "Yhdistä S-Pankki"


def test_a_switched_off_voice_note_is_not_transcribed(chat, monkeypatch):
    heard = []
    monkeypatch.setattr(voice, "to_text", lambda media_id: heard.append(media_id) or "Hello there friend")
    connected(chat).say("STOP")
    chat.media("audio")
    assert heard == [] and chat.interactive("cta_url")[-1]["action"]["parameters"]["display_text"] == "Connect S-Pankki"
