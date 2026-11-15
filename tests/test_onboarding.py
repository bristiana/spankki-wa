"""The first-chat interview (no AI) and the Finland money guide tool."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, onboarding as ob
from app.agent import Reply
from app.engine import Engine
from app.tools import GUIDE, money_guide, run_tool


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def check_voice(t):
    """One question per message, short (the risk question explains its three options, a final plan may be longer)."""
    assert t.text.count("?") <= 1
    if not t.done:
        limit = 420 if "prudent" in t.text.lower() else 240
        assert len(t.text) < limit and t.text.count("?") == 1, t.text
    assert not any(w in t.text.lower() for w in ("great", "awesome", "perfect"))


def test_full_interview_creates_goal(e, tmp_path):
    """Only on request ("set a goal"): what for, how much and by when, then the plan. No risk or check-in questions."""
    t = ob.turn(e, "u", "set a goal")
    assert t.text == ob.ASK["goal"][0] and not t.lead
    assert [b[0] for b in t.buttons] == ["ob:goal:trip", "ob:goal:buffer", "ob:goal:home"]
    check_voice(t)
    t = ob.turn(e, "u", "A trip to Lapland")
    assert t.text.startswith("Saving for a trip to Lapland.") and t.buttons == ob.BUTTONS["amount"]
    check_voice(t)
    t = ob.turn(e, "u", "1,500 € by June 2027")
    assert t.done and not ob.active(e, "u")
    assert "prudent" not in t.text.lower() and "check-in" not in t.text.lower()
    check_voice(t)
    plan = e.goal_plan("triptolapland")
    assert plan["goal"]["target"] == 1500 and plan["goal"]["deadline"] == "2027-06-30"
    assert f"{plan['needed_per_month_for_deadline_eur']:.2f} € a month" in t.text
    assert t.buttons == [("save:triptolapland:188", "Save 188 €")]  # the one next step for the goal they asked for
    saved = json.loads((tmp_path / "s.json").read_text())["onboarding"]["u"]
    assert saved["answers"] == {"goal_name": "Trip to Lapland", "amount": 1500.0, "deadline": "2027-06-30"}
    assert ob.risk_of(e, "u") is None


def test_nothing_starts_by_itself(e):
    assert ob.turn(e, "u", "How much did I spend on food?") is None
    assert ob.turn(e, "u", "I want to invest 50 a month") is None
    assert ob.turn(e, "u", "what is a goal anyway?") is None
    assert "u" not in e.state.get("onboarding", {})
    lead = ob.intro(e)
    assert "3 baskets" in lead[0] and lead[-1] == ob.HELLO and all("?" not in x for x in lead)


def test_risk_question_and_checkin_only_on_request(e):
    t = ob.turn(e, "u", "set my risk profile")
    assert t.text == ob.ASK["risk"][0] and t.buttons == ob.BUTTONS["risk"]
    t = ob.turn(e, "u", "", "ob:risk:growth")
    assert t.done and t.text.startswith(ob.RISK_LINE["growth"]) and "?" not in t.text  # nothing chained after it
    assert ob.risk_of(e, "u") == "growth" and not ob.active(e, "u")
    t = ob.turn(e, "u", "remind me weekly")
    assert t.done and "once a week" in t.text and e.state["onboarding"]["u"]["answers"]["weekly_checkin"] is True
    ob.turn(e, "u", "stop check-ins")
    assert e.state["onboarding"]["u"]["answers"]["weekly_checkin"] is False


def test_amount_in_first_answer_is_not_asked_again(e):
    ob.start(e, "u")
    t = ob.turn(e, "u", "I'm saving for a new laptop, 900 in 6 months")
    assert t.text.startswith("Saving for a new laptop, 900 € by April 2027.") and t.done


def test_skip_gets_no_comment(e):
    ob.start(e, "u")
    ob.turn(e, "u", "", "ob:goal:buffer")
    t = ob.turn(e, "u", "", "ob:skip")
    assert t.done and "tell me" in t.text.lower() and not e.goals()


def test_pause_and_resume(e):
    ob.start(e, "u")
    t = ob.turn(e, "u", "thanks")
    assert "continue" in t.text and "?" not in t.text and not ob.active(e, "u")
    assert ob.turn(e, "u", "How much did I spend on taxis?") is None  # paused: the coach answers
    assert ob.waiting(e, "u", "How much did I spend on taxis?") is None
    t = ob.turn(e, "u", "continue")
    assert t.text == ob.ASK["goal"][1] and ob.active(e, "u")
    assert ob.turn(e, "u", "", "ob:goal:home").text.startswith("Saving for a home renovation.")


def test_question_mid_interview_goes_to_coach_and_pauses(e):
    ob.start(e, "u")
    ob.turn(e, "u", "", "ob:goal:trip")
    assert ob.turn(e, "u", "what is safe to spend this week?") is None
    assert ob.side_question(e, "u", "what is safe to spend this week?") is None  # nothing re-sent
    assert not ob.active(e, "u")
    assert ob.turn(e, "u", "continue").text == ob.ASK["amount"][1]
    assert ob.turn(e, "u", "2000 by summer?").text.startswith("2 000 € by June 2027.")  # an answer, not a question


def test_unclear_answers_are_asked_again(e):
    ob.start(e, "u")
    ob.turn(e, "u", "", "ob:goal:trip")
    assert ob.turn(e, "u", "by March").text == ob.ASK["amount"][1]
    assert ob.turn(e, "u", "", "ob:goal:home").text == "Got it, that one is already answered."  # stale: no re-ask
    t = ob.turn(e, "u", "800")
    assert t.done and e.state["onboarding"]["u"]["answers"]["deadline"] == "2027-03-31"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    monkeypatch.setattr(main.wa, "BUBBLE_PAUSE_S", 0)
    c = TestClient(main.app)
    c.sent = sent
    return c


class FakeCoach:
    def __init__(self):
        self.asked = []

    def ask(self, user, text, name="there", context=None):
        self.asked.append(text)
        return Reply(text="You can spend 50 € this week.", tools_used=["get_safe_to_spend"])

    def reset(self, user=None):
        pass


def wa_msg(text=None, button=None, mid="m1"):
    m = {"from": "358400000000", "id": mid, "name": "Aino", "text": text, "button_id": button}
    return m


def test_whatsapp_connect_says_hello_and_asks_nothing(client):
    main._coach = FakeCoach()
    client.post("/connect", data={"u": "358400000000", "scope": ["tx"]})
    kinds = [b["interactive"]["type"] if b["type"] == "interactive" else b["type"] for b in client.sent]
    assert kinds == ["text", "cta_url", "text"]  # baskets, Open S-mobiili, hello: no question, no buttons
    assert client.sent[1]["interactive"]["action"]["parameters"]["url"].endswith("/s-mobiili/")
    assert client.sent[-1]["text"]["body"] == ob.HELLO
    assert main._coach.asked == []  # no AI in the welcome
    main.handle_message(wa_msg(text="What is safe to spend this week?", mid="m2"))
    assert main._coach.asked == ["What is safe to spend this week?"]
    assert client.sent[-1]["text"]["body"] == "You can spend 50 € this week."  # and nothing after it
    main.handle_message(wa_msg(text="set a goal", mid="m3"))  # on request, the goal interview
    assert client.sent[-1]["interactive"]["body"]["text"] == ob.ASK["goal"][0]
    main.handle_message(wa_msg(button="ob:goal:trip", text="A trip", mid="m4"))
    assert client.sent[-1]["interactive"]["body"]["text"].startswith("Saving for a trip.")


def test_web_chat_runs_the_same_interview_on_request(client):
    main._coach = FakeCoach()
    say = lambda text: client.post("/api/chat", json={"session": "t", "text": text}).json()
    r = say("__start__")
    assert r["buttons"] == [] and r["quick"] == []
    assert r["bubbles"][-1] == ob.HELLO
    r = say("set a goal")
    assert [q["send"] for q in r["quick"]] == ["__btn__:ob:goal:trip", "__btn__:ob:goal:buffer", "__btn__:ob:goal:home"]
    assert say("__btn__:ob:goal:buffer")["text"].startswith("Saving for a buffer for surprises.")
    r = say("how much do I earn?")
    assert r["text"] == "You can spend 50 € this week." and r["quick"] == []  # answered, the question not re-sent
    assert say("continue")["text"] == ob.ASK["amount"][1]
    r = say("3k by December 2027")
    assert "Emergency buffer" in r["text"] and r["quick"][0]["send"].startswith("__btn__:save:emergencybuffer:")
    assert main.engine.goal_plan("emergencybuffer")["goal"]["target"] == 3000
    assert "Move" in say(r["quick"][0]["send"])["text"]  # the save button goes to the PIN page as before


# ---------------- money guide ----------------
def test_guide_has_every_topic_with_an_official_source():
    assert list(GUIDE) == ["emergency-fund", "housing-allowance", "students", "asp", "equity-savings-account",
                           "capital-income-tax", "deposit-guarantee", "consumer-credit", "s-bank"]
    for topic, s in GUIDE.items():
        assert s["text"] and s["checked"] == "2026-11-14", topic
        assert s["sources"] and all(u.startswith("https://") for u in s["sources"]), topic


def test_get_money_guide_tool(e):
    out = json.loads(run_tool(e, "get_money_guide", {"topic": "deposit-guarantee"}, []))
    assert "100,000 €" in out["text"] and out["sources"] == ["https://rvv.fi/en/deposit-guarantee/"]
    assert "100,000" in money_guide("equity savings account")["text"]
    assert money_guide("credit")["topic"] == "consumer-credit"
    assert "30%" in money_guide("capital-income-tax")["text"] and "vero.fi" in money_guide("capital-income-tax")["sources"][0]
    with pytest.raises(ValueError):
        money_guide("crypto")


def test_confused_answer_goes_to_coach_not_skip(e):
    ob.ask_risk(e, "u1")
    for text in ("i dont know what it means", "explain me prudent vs balanced", "what's the difference"):
        assert ob.turn(e, "u1", text) is None          # the coach explains...
        assert e.state["onboarding"]["u1"]["step"] == "risk"  # ...and the risk question is still waiting
    assert "Which is closest to you?" in ob.waiting(e, "u1", "explain me prudent vs balanced")
    ob.side_question(e, "u1", "explain me prudent vs balanced")
    assert ob.active(e, "u1")  # open for their answer, nothing re-sent
    assert ob.turn(e, "u1", "balanced I think").text.startswith(ob.RISK_LINE["balanced"])


# ---------------- greetings and small talk are never answers ("Saving for hello") ----------------
@pytest.mark.parametrize("hello", ["hello", "Hi!", "hey there", "moi", "how are you?", "haha", "👋", "good morning"])
def test_small_talk_at_the_goal_question_is_never_the_goal(e, hello):
    ob.start(e, "u")
    t = ob.turn(e, "u", hello)
    assert t is not None and "Saving for" not in t.text
    assert t.text.endswith(ob.ASK["goal"][1]) and t.buttons == ob.BUTTONS["goal"]
    check_voice(t)
    r = e.state["onboarding"]["u"]
    assert r["step"] == "goal" and "goal_name" not in r["answers"]
    assert ob.turn(e, "u", "a trip to Rome").text.startswith("Saving for a trip to Rome.")


@pytest.mark.parametrize("hello", ["hello", "hey"])
def test_small_talk_at_later_questions_reasks_warmly(e, hello):
    ob.start(e, "u")
    ob.turn(e, "u", "", "ob:goal:trip")
    t = ob.turn(e, "u", hello)
    assert t.text.startswith("Hey, good to see you.") and t.text.endswith(ob.ASK["amount"][1])
    assert e.state["onboarding"]["u"]["step"] == "amount"


def test_small_talk_words():
    assert ob.small_talk("how are you") == "Good, thanks!"
    assert ob.small_talk("hello") == "Hey, good to see you."
    for real in ("a trip to Rome", "Hello Kitty plush collection", "?", "what does prudent mean", "1500 by june", ""):
        assert ob.small_talk(real) is None, real


def test_real_goals_still_parse(e):
    ob.start(e, "u")
    assert ob.turn(e, "u", "Hello Kitty plush collection").text.startswith("Saving for Hello Kitty plush")


# ---------------- side questions: the waiting question is never re-sent ----------------
def _at_risk(e, user="u"):
    ob.ask_risk(e, user)
    assert e.state["onboarding"][user]["step"] == "risk"


def test_two_side_questions_never_resend_the_question(e):
    _at_risk(e)
    for q in ("How can I spend less?", "Do people my age save more than me?", "Can you explain how savings works?"):
        assert ob.turn(e, "u", q) is None
        assert ob.waiting(e, "u", q) is None  # the coach is not told a question is waiting
        assert ob.side_question(e, "u", q) is None
    assert not ob.active(e, "u") and e.state["onboarding"]["u"]["step"] == "risk"
    t = ob.turn(e, "u", "continue")  # picks up where it was, on request
    assert t.text == ob.ASK["risk"][1] and ob.active(e, "u")
    assert ob.turn(e, "u", "", "ob:risk:balanced").text.startswith(ob.RISK_LINE["balanced"])


def test_question_about_the_waiting_question_keeps_it_open(e):
    _at_risk(e)
    for q in ("what does prudent mean?", "and growth?", "what's the risk with balanced?"):
        assert ob.turn(e, "u", q) is None
        assert ob.side_question(e, "u", q) is None
    assert ob.active(e, "u")


def test_paused_interview_resumes_from_a_button(e):
    _at_risk(e)
    ob.side_question(e, "u", "a")
    assert not ob.active(e, "u")
    assert ob.turn(e, "u", "", "ob:risk:growth").text.startswith(ob.RISK_LINE["growth"])
