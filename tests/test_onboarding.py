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
    t = ob.start(e, "u")
    assert "Mandatory & regular" in t.lead[0] or "mandatory" in t.lead[0].lower()
    assert [b[0] for b in t.buttons] == ["ob:goal:trip", "ob:goal:buffer", "ob:goal:home"]
    check_voice(t)
    t = ob.turn(e, "u", "A trip to Lapland")
    assert t.text.startswith("Saving for a trip to Lapland.") and t.buttons == ob.BUTTONS["amount"]
    check_voice(t)
    t = ob.turn(e, "u", "1,500 € by June 2027")
    assert t.text.startswith("1 500 € by June 2027.") and t.buttons == ob.BUTTONS["risk"]
    check_voice(t)
    t = ob.turn(e, "u", "", "ob:risk:prudent")
    assert t.text.startswith("Prudent:") and t.buttons == ob.BUTTONS["checkin"]
    check_voice(t)
    t = ob.turn(e, "u", "", "ob:checkin:yes")
    assert t.done and not ob.active(e, "u")
    check_voice(t)
    plan = e.goal_plan("triptolapland")
    assert plan["goal"]["target"] == 1500 and plan["goal"]["deadline"] == "2027-06-30"
    assert f"{plan['needed_per_month_for_deadline_eur']:.2f} € a month" in t.text
    assert t.buttons == [("save:triptolapland:188", "Save 188 €")]
    saved = json.loads((tmp_path / "s.json").read_text())["onboarding"]["u"]
    assert saved["answers"] == {"goal_name": "Trip to Lapland", "amount": 1500.0, "deadline": "2027-06-30",
                                "risk": "prudent", "weekly_checkin": True}
    assert ob.risk_of(e, "u") == "prudent"


def test_amount_in_first_answer_is_not_asked_again(e):
    ob.start(e, "u")
    t = ob.turn(e, "u", "I'm saving for a new laptop, 900 in 6 months")
    assert t.text.startswith("Saving for a new laptop, 900 € by April 2027.") and t.buttons == ob.BUTTONS["risk"]


def test_skip_gets_no_comment(e):
    ob.start(e, "u")
    t = ob.turn(e, "u", "", "ob:goal:buffer")
    t = ob.turn(e, "u", "", "ob:skip")
    assert t.text == ob.ASK["risk"][0]  # no reflection line, no remark about the skip
    t = ob.turn(e, "u", "skip")
    assert t.text == ob.ASK["checkin"][0]
    t = ob.turn(e, "u", "no")
    assert t.done and "tell me" in t.text.lower() and not e.goals()


def test_pause_and_resume(e):
    ob.start(e, "u")
    t = ob.turn(e, "u", "thanks")
    assert "continue" in t.text and "?" not in t.text and not ob.active(e, "u")
    assert ob.turn(e, "u", "How much did I spend on taxis?") is None  # paused: the coach answers
    assert ob.reask(e, "u") is None
    t = ob.turn(e, "u", "continue")
    assert t.text == ob.ASK["goal"][1] and ob.active(e, "u")
    assert ob.turn(e, "u", "", "ob:goal:home").text.startswith("Saving for a home renovation.")


def test_question_mid_interview_goes_to_coach_then_resumes(e):
    ob.start(e, "u")
    ob.turn(e, "u", "", "ob:goal:trip")
    assert ob.turn(e, "u", "what is safe to spend this week?") is None
    assert ob.turn(e, "u", "how much do I have left") is None
    assert ob.reask(e, "u").text == ob.ASK["amount"][1]
    assert ob.turn(e, "u", "2000 by summer?").text.startswith("2 000 € by June 2027.")  # an answer, not a question


def test_unclear_answers_are_asked_again(e):
    ob.start(e, "u")
    ob.turn(e, "u", "", "ob:goal:trip")
    assert ob.turn(e, "u", "by March").text == ob.ASK["amount"][1]
    ob.turn(e, "u", "800")
    assert e.state["onboarding"]["u"]["answers"]["deadline"] == "2027-03-31"
    assert ob.turn(e, "u", "", "ob:goal:home").text == ob.ASK["risk"][1]  # a stale button from an earlier question
    assert ob.turn(e, "u", "hmm").text == ob.ASK["risk"][1]
    assert ob.turn(e, "u", "something in between").text.startswith("Balanced:")
    assert ob.turn(e, "u", "ok").done  # "ok" to a yes/no question is a yes


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


def test_whatsapp_connect_then_interview(client):
    main._coach = FakeCoach()
    client.post("/connect", data={"u": "358400000000", "scope": ["tx"]})
    kinds = [b["interactive"]["type"] if b["type"] == "interactive" else b["type"] for b in client.sent]
    assert kinds == ["text", "text", "cta_url", "button"]  # baskets, intro, Open My money, first question
    assert client.sent[2]["interactive"]["action"]["parameters"]["url"].endswith("/app")
    assert main._coach.asked == []  # no AI in the welcome any more
    main.handle_message(wa_msg(button="ob:goal:trip", text="A trip"))
    assert client.sent[-1]["interactive"]["body"]["text"].startswith("Saving for a trip.")
    main.handle_message(wa_msg(text="What is safe to spend this week?", mid="m2"))
    assert main._coach.asked == ["What is safe to spend this week?"]
    assert client.sent[-2]["text"]["body"] == "You can spend 50 € this week."
    assert client.sent[-1]["interactive"]["body"]["text"] == ob.ASK["amount"][1]  # the interview resumes


def test_web_chat_runs_the_same_interview(client):
    main._coach = FakeCoach()
    say = lambda text: client.post("/api/chat", json={"session": "t", "text": text}).json()
    r = say("__start__")
    assert r["buttons"] == [{"label": "Open My money", "url": "/app"}]
    assert [q["send"] for q in r["quick"]] == ["__btn__:ob:goal:trip", "__btn__:ob:goal:buffer", "__btn__:ob:goal:home"]
    assert say("__btn__:ob:goal:buffer")["text"].startswith("Saving for a buffer for surprises.")
    r = say("how much do I earn?")
    assert r["text"].startswith("You can spend") and r["text"].endswith(ob.ASK["amount"][1]) and r["quick"]
    say("3k by December 2027")
    say("__btn__:ob:risk:growth")
    r = say("__btn__:ob:checkin:no")
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


def test_confused_answer_goes_to_coach_not_skip(tmp_path):
    from datetime import date
    from app import onboarding as ob
    from app.engine import Engine
    e = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    ob.start(e, "u1")
    ob.turn(e, "u1", "", "ob:goal:trip")
    ob.turn(e, "u1", "2000")
    for text in ("i dont know what it means", "explain me prudent vs balanced", "what's the difference"):
        assert ob.turn(e, "u1", text) is None          # the coach explains...
        assert e.state["onboarding"]["u1"]["step"] == "risk"  # ...and the risk question is still waiting
    again = ob.reask(e, "u1")
    assert "prudent" in again.text.lower() and again.buttons == ob.BUTTONS["risk"]


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
    assert ob.small_talk("how are you") == "All good here, thanks for asking!"
    assert ob.small_talk("hello") == "Hey, good to see you."
    for real in ("a trip to Rome", "Hello Kitty plush collection", "?", "what does prudent mean", "1500 by june", ""):
        assert ob.small_talk(real) is None, real


def test_real_goals_still_parse(e):
    ob.start(e, "u")
    assert ob.turn(e, "u", "Hello Kitty plush collection").text.startswith("Saving for Hello Kitty plush")
