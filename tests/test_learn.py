"""'Why it matters' lessons on tool results, and the Learn in 2 minutes course. No AI, numbers from the engine."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import agent, learn, main, quiz
from app import whatsapp as wa
from app.agent import Reply
from app.engine import Engine
from app.guard import check
from app.lessons import eur, lesson_for
from app.tools import GUIDE, run_tool
from tests.test_whatsapp_payloads import BASE, chat, connected  # noqa: F401  (the signed-webhook fixture)

INDEX = json.loads((main.PERSONA_DIR / "index.json").read_text(encoding="utf-8"))
WITH_FILE = [p for p in INDEX if p.get("file")]  # every persona but Aino (data/persona.csv)
EETU = next(p["id"] for p in INDEX if p["name"] == "Eetu")
SHAMING = ("you should", "must ", "too much", "wasting", "bad ", "irresponsible")


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


# ---------------- lesson_for: one line on a tool result ----------------
@pytest.mark.parametrize("tool,args,expect", [
    ("get_baskets", {"period": "last_3_months"}, ["809.91 €", "38%", "only one you can change today"]),
    ("get_spending", {"category": "Restaurants", "period": "last_month"},
     ["10 payments", "644.63 €", "7 735.56 € a year"]),
    ("get_spending", {"category": "Health", "period": "last_3_months"}, ["157.82 €", "buffer", "non-event"]),
    ("get_safe_to_spend", {}, ["not your budget", "1 001.94 €", "68.60 €"]),
    ("get_safe_to_save", {}, ["restaurants −20%", "97.18 €", "pay yourself first"]),
    ("can_i_afford", {"amount_eur": 120, "what": "jacket"}, ["15%", "809.91 €", "basket 3"]),
    ("simulate_cut", {"category": "Restaurants", "cut_pct": 20, "years": 5},
     ["× 365", "3.19 € a day", "97.18 € a month", "1 166.16 € a year"]),
    ("get_spender_profile", {}, ["38%", "change today"]),
    ("get_regular_payments", {}, ["basket 1 on autopilot", "894.47 € a month", "10 733.64 € a year"]),
])
def test_tool_results_carry_a_lesson_with_their_own_numbers(e, tool, args, expect):
    out = run_tool(e, tool, args, [])
    line = json.loads(out)["lesson"]
    for bit in expect:
        assert bit in line, (bit, line)
    assert len(line) <= 200 and "\n" not in line
    assert not any(w in line.lower() for w in SHAMING)
    assert check(line, [out])[1] == []  # the number guard accepts every figure in it


def test_lesson_skips_what_has_nothing_to_teach(e):
    assert lesson_for("get_upcoming_bills", e.upcoming_bills(14)) is None
    assert lesson_for("get_money_guide", {"text": "x"}) is None
    assert lesson_for("get_baskets", ["not", "a", "dict"]) is None
    assert lesson_for("get_spending", {"total_eur": 0}) is None
    assert lesson_for("get_full_picture", e.full_picture()) is None  # no other bank linked
    assert "lesson" not in json.loads(run_tool(e, "list_funds", {}, []))[0]


def test_lesson_with_a_linked_bank_counts_months_of_buffer(e):
    e.link_bank()
    out = run_tool(e, "get_full_picture", {}, [])
    r = json.loads(out)
    assert f"about {r['savings_cover_irregular_months']} months" in r["lesson"]
    assert check(r["lesson"], [out])[1] == []


def test_system_prompt_asks_for_one_short_why_line():
    assert "at most ONE short line" in agent.SYSTEM and '"lesson"' in agent.SYSTEM
    assert "Never the same idea twice" in agent.SYSTEM and "small talk" in agent.SYSTEM


# ---------------- the course: five lessons on her own numbers ----------------
def all_lessons(e, user="u"):
    turns = [learn.turn(e, user, "learn")]
    for i in range(learn.COUNT - 1):
        turns.append(learn.turn(e, user, "", f"learn:next:{i}"))
    return turns


def test_five_lessons_use_the_engines_numbers(e):
    t = all_lessons(e)
    assert t[0].lead[0].startswith("*Learn in 2 minutes*")
    b = {x["basket"]: x for x in e.baskets("last_3_months")["baskets"]}
    for k in ("regular", "irregular", "everyday"):
        assert f"{eur(b[k]['per_month_eur'])} ({b[k]['share_pct']}%)" in t[0].text
    s = e.safe_to_spend()
    for v in (s["balance_eur"], s["bills_total_eur"], s["safe_to_spend_until_payday_eur"], s["safe_per_day_eur"]):
        assert eur(v) in t[1].text
    assert "due tomorrow" in t[1].text and f"in {s['next_payday']['days_away']} days" in t[1].text
    assert eur(b["irregular"]["per_month_eur"]) in t[2].text and "453.21 €" in t[2].text
    assert GUIDE["emergency-fund"]["sources"][0] in t[2].text  # a guide fact, with its source
    fast = e.spending(merchant_type="5814", period="last_3_months")
    assert f"{fast['transactions']} payments, {eur(fast['total_eur'])}" in t[3].text
    assert "276.21 €" in t[3].text and "3 314.52 € a year" in t[3].text and "9.08 € a day" in t[3].text
    cut = e.simulate_cut("Restaurants", 20, years=10)
    balanced = next(x for x in cut["if_invested"] if x["fund"] == "Demo Fund Balanced")
    assert f"*{eur(cut['saving_per_month_eur'])} a month*" in t[4].text
    assert eur(balanced["paid_in_eur"]) in t[4].text and eur(balanced["value_eur"]) in t[4].text
    assert "scenario, not a promise" in t[4].text
    assert [b[1] for b in t[4].buttons] == ["I'm done", "Save 34 € now", "Ask Oiva"]  # half of 68.60 € free now
    assert learn.turn(e, "u", "", "learn:finish").text.startswith("That's all five")
    assert e.state["learn"]["u"]["done"] and e.state["learn"]["u"]["seen"] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("p", INDEX, ids=lambda p: p["name"])
def test_every_persona_gets_five_short_lessons_with_whatsapp_buttons(p, tmp_path):
    kw = {"csv_path": main.PERSONA_DIR / p["file"]} if p.get("file") else {}
    e = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json", **kw)
    for t in all_lessons(e):
        assert len(t.text) <= learn.MAX_CHARS, t.text
        assert len(t.buttons) == 3 and all(len(title) <= wa.TITLE_MAX for _, title in t.buttons)
        assert "None" not in t.text and not any(w in t.text.lower() for w in SHAMING)
        assert not wa.validate_payload({"messaging_product": "whatsapp", "to": "1", "type": "interactive",
                                        "interactive": {"type": "button", "body": {"text": t.text}, "action": {
                                            "buttons": [{"type": "reply", "reply": {"id": i, "title": ti}}
                                                        for i, ti in t.buttons]}}})


def test_stop_pauses_learn_resumes_and_old_buttons_are_safe(e):
    learn.turn(e, "u", "teach me")
    learn.turn(e, "u", "", "learn:next:0")
    t = learn.turn(e, "u", "stop")
    assert t.text == "Paused at lesson 2/5. Write *learn* when you want to pick it up."
    assert not learn.active(e, "u") and learn.turn(e, "u", "how much is rent?") is None
    t = learn.turn(e, "u", "learn")
    assert t.lead == ["Back to lesson 2/5."] and t.text.startswith("*2/5 ·")
    assert learn.turn(e, "u", "", "learn:next:0").text.startswith("*2/5 ·")  # a button from lesson 1
    assert learn.turn(e, "u", "next").text.startswith("*3/5 ·")
    ask = learn.turn(e, "u", "", "learn:ask:2")
    assert "*the buffer*" in ask.text and "lesson 3/5" in ask.text and not learn.active(e, "u")
    assert learn.turn(e, "u", "", "learn:next:2").text.startswith("*4/5 ·")  # its Next lesson carries on


def test_a_side_question_pauses_once_and_the_coach_gets_the_lesson(e):
    learn.turn(e, "u", "learn")
    assert learn.turn(e, "u", "why is rent in basket one?") is None
    assert "The three baskets" in learn.waiting_lesson(e, "u")
    assert "the short money course" in main.flow_context(e, "u")
    assert learn.paused_note(e, "u") == "Lesson 1/5 is waiting. Write *learn* to pick it up."
    assert learn.paused_note(e, "u") is None and learn.waiting_lesson(e, "u") is None


def test_starting_learn_pauses_the_game_and_the_game_pauses_learn(e):
    quiz.turn(e, "u", "game")
    learn.turn(e, "u", "learn")
    assert not quiz.active(e, "u") and learn.active(e, "u")
    learn.step_away(e, "u", "quiz:start")  # a lesson's action button
    assert not learn.active(e, "u") and not e.state["learn"]["u"]["note"]
    assert learn.turn(e, "u", "", "learn:next:0").text.startswith("*2/5 ·")


def test_progress_is_per_user_and_per_persona(e, tmp_path):
    learn.turn(e, "a", "learn")
    learn.turn(e, "a", "", "learn:next:0")
    learn.turn(e, "b", "learn")
    assert e.state["learn"]["a"]["lesson"] == 1 and e.state["learn"]["b"]["lesson"] == 0
    again = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")  # saved: survives a restart
    assert again.state["learn"]["a"]["lesson"] == 1
    other = Engine(today=date(2026, 10, 6), state_path=tmp_path / "eetu.json",
                   csv_path=main.PERSONA_DIR / WITH_FILE[0]["file"])
    assert "a" not in other.state.get("learn", {})


def test_finished_course_restarts_and_stale_buttons_say_so(e):
    all_lessons(e)
    learn.turn(e, "u", "", "learn:finish")
    assert learn.turn(e, "u", "", "learn:next:4").buttons == [("learn:start", "Start again")]
    assert learn.turn(e, "u", "learn").text.startswith("*1/5 ·")


def test_goal_for_buffer_and_pay_yourself_first(e):
    gid, amount = learn.goal_for(e, "learn:buffer")
    assert gid == "emergencybuffer" and e.state["goals"][gid]["target"] == 453.21 and amount == 34  # half of 68.60
    assert learn.goal_for(e, "learn:save:34") == ("emergencybuffer", 34.0)  # an existing goal gets it
    fresh = Engine(today=date(2026, 10, 6), state_path=e.state_path.with_name("f.json"))
    gid, amount = learn.goal_for(fresh, "learn:save:34")
    assert fresh.state["goals"][gid]["name"] == "Pay yourself first" and amount == 34.0


# ---------------- WhatsApp: signed webhooks, every payload valid ----------------
def test_whatsapp_course_end_to_end(chat):
    connected(chat).say("learn")
    assert any(t.startswith("*Learn in 2 minutes*") for t in chat.texts())
    assert [i for i, _ in chat.buttons()] == ["learn:next:0", "quiz:start", "learn:ask:0"]
    chat.tap("quiz:start")  # lesson 1's action: the basket game, the course waits
    assert chat.interactive("button")[-1]["body"]["text"].startswith("*1/5* ")
    chat.tap("learn:next:0")
    assert chat.interactive("button")[-1]["body"]["text"].startswith("*2/5 ·")
    chat.tap("quiz:money")
    assert chat.url() == f"{BASE}/s-mobiili/"
    chat.tap("learn:next:1").tap("learn:buffer")  # lesson 3: a buffer goal, then the PIN page
    assert "?ready=save:" in chat.url()
    chat.confirm()
    assert any(t.startswith("*Saved") for t in chat.texts())
    chat.tap("learn:next:2")
    chat.say("stop")
    assert "Paused at lesson 4/5" in chat.texts()[-1] and main.connected(chat.phone)
    chat.say("How much did I spend on coffee?")
    assert main._coach.asked == ["How much did I spend on coffee?"]
    assert chat.texts()[-1] == "You have 68.60 € safe to spend until payday."  # a chosen pause: no note
    chat.say("learn")
    assert chat.interactive("button")[-1]["body"]["text"].startswith("*4/5 ·")
    chat.say("what does that mean")  # a side question: the coach answers, then the way back
    assert chat.texts()[-1] == "Lesson 4/5 is waiting. Write *learn* to pick it up."
    chat.tap("learn:next:3")
    (save_id, title), = [b for b in chat.buttons() if b[0].startswith("learn:save:")]
    chat.tap(save_id)
    assert "?ready=save:" in chat.url()
    chat.tap("learn:finish")
    assert chat.interactive("button")[-1]["body"]["text"].startswith("That's all five")


# ---------------- web chat ----------------
class FakeCoach:
    def __init__(self):
        self.asked = []

    def ask(self, user, text, name="there", context=None):
        self.asked.append((text, context))
        return Reply(text="Rent is a must-pay that comes back every month.", tools_used=["get_baskets"])

    def reset(self, user=None):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = FakeCoach()
    return TestClient(main.app)


def test_web_chip_runs_the_course_with_quick_replies(client):
    say = lambda text, **kw: client.post("/api/chat", json={"session": "l", "text": text, **kw}).json()
    r = say("Learn in 2 minutes")
    assert "*1/5 · The three baskets*" in r["text"]
    assert [q["send"] for q in r["quick"]] == ["__btn__:learn:next:0", "__btn__:quiz:start", "__btn__:learn:ask:0"]
    r = say("why is rent there?")
    assert r["text"].endswith("Lesson 1/5 is waiting. Write *learn* to pick it up.")
    assert "The three baskets" in main._coach.asked[-1][1]
    say("learn")
    for i in range(1, 4):
        r = say(f"__btn__:learn:next:{i - 1}")
        assert r["text"].startswith(f"{i} of 5 done.") and f"\n\n*{i + 1}/5 ·" in r["text"]
    r = say("__btn__:learn:next:3")
    save = next(q["send"] for q in r["quick"] if "learn:save:" in q["send"])
    out = say(save)
    url = out["buttons"][0]["url"]
    assert url.startswith("/s-mobiili/?ready=save:") and url.endswith("&web=1")
    assert "Pay yourself first" in out["text"]
    assert len(main._coach.asked) == 1  # only the one real question went to the AI


def test_web_course_uses_the_viewed_persona(client):
    eetu = EETU
    r = client.post("/api/chat", json={"session": "p", "text": "learn", "as": eetu}).json()
    pe = main.engine_for(eetu)
    assert pe.state["learn"]["web:p"]["lesson"] == 0 and "web:p" not in main.engine.state.get("learn", {})
    b = {x["basket"]: x for x in pe.baskets("last_3_months")["baskets"]}
    assert eur(b["everyday"]["per_month_eur"]) in r["text"]
