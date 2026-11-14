"""The basket game: five of your own payments, which basket? No AI, numbers from the engine."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main, quiz
from app.agent import Reply
from app.engine import BASKETS, Engine

INDEX = json.loads((main.PERSONA_DIR / "index.json").read_text(encoding="utf-8"))
WITH_FILE = [p for p in INDEX if p.get("file")]  # every persona but Aino (data/persona.csv)
EETU = next(p["id"] for p in INDEX if p["name"] == "Eetu")
SHAMING = ("wrong", "incorrect", "oops", "fail", "bad", "should have", "mistake")


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def money(x):
    return quiz._money(x)


def play(e, user, answers):
    """Answer each round with answers(pick) and return the turns."""
    turns = []
    for _ in range(quiz.ROUNDS):
        p = e.state["quiz"][user]["picks"][e.state["quiz"][user]["round"]]
        r = e.state["quiz"][user]
        turns.append(quiz.turn(e, user, "", f"quiz:{r['game']}:{r['round']}:{answers(p)}"))
    return turns


# ---------------- picking ----------------
def test_picks_five_real_payments_across_all_three_baskets(e):
    picks = quiz.pick(e)
    assert len(picks) == 5 and {p["basket"] for p in picks} == set(BASKETS)
    assert len({(p["category"], p["kind"]) for p in picks}) == 5  # five different kinds of place
    tx = e.tx
    for p in picks:
        row = tx[(tx["date"] == date.fromisoformat(p["date"])) & (tx["category"] == p["category"])
                 & ((tx["tx_amount_eur"] + p["amount_eur"]).abs() < 0.005)]
        assert len(row) >= 1, p  # a payment that is really in her history
        assert (e.today - date.fromisoformat(p["date"])).days <= quiz.IRREGULAR_WINDOW_DAYS
    # the first game starts with where most of the money went: her rent and her fast food
    assert {p["kind"] for p in picks} >= {"Housing", "Fast food and cafés"}
    fast = next(p for p in picks if p["kind"] == "Fast food and cafés")
    assert fast["total_eur"] == e.spending(merchant_type="5814", period="last_month")["total_eur"] == 449.87


def test_picks_are_deterministic_for_a_date_and_change_on_replay(e, tmp_path):
    same = Engine(today=date(2026, 10, 6), state_path=tmp_path / "other.json")
    assert quiz.pick(e) == quiz.pick(same) and quiz.pick(e, 1) == quiz.pick(same, 1)
    assert quiz.pick(e, 1) != quiz.pick(e)


@pytest.mark.parametrize("p", WITH_FILE, ids=lambda p: p["name"])
def test_every_persona_gets_a_full_diverse_game(p, tmp_path):
    e = Engine(csv_path=main.PERSONA_DIR / p["file"], today=date(2026, 10, 6), state_path=tmp_path / "s.json",
               age=p["age"])
    picks = quiz.pick(e)
    assert len(picks) == 5 and {x["basket"] for x in picks} == set(BASKETS)
    assert all(x["amount_eur"] > 0 and x["total_eur"] > 0 for x in picks)


# ---------------- answers ----------------
def test_question_and_buttons_fit_whatsapp(e):
    t = quiz.start(e, "u")
    assert "basket game" in t.lead[0] and t.text.startswith("*1/5* ") and t.text.endswith("Which basket?")
    assert [b[0] for b in t.buttons] == ["quiz:0:0:regular", "quiz:0:0:irregular", "quiz:0:0:everyday"]
    assert [b[1] for b in t.buttons] == ["Regular must-pay", "Irregular must-pay", "Slips through"]
    assert all(len(label) <= 20 for _, label in t.buttons)
    p = e.state["quiz"]["u"]["picks"][0]
    assert money(p["amount_eur"]) in t.text and p["name"] in t.text


def test_right_answer_teaches_with_her_numbers(e):
    quiz.start(e, "u")
    p = e.state["quiz"]["u"]["picks"][0]
    t = quiz.turn(e, "u", "", f"quiz:0:0:{p['basket']}")
    fb = t.lead[0]
    assert fb.startswith("✅ ") and p["kind"] in fb and f"{money(p['total_eur'])} of yours {p['period']}." in fb
    assert t.text.startswith("*2/5* ") and e.state["quiz"]["u"]["right"] == 1


def test_wrong_answer_is_kind_and_names_the_basket(e):
    quiz.start(e, "u")
    p = e.state["quiz"]["u"]["picks"][0]
    wrong = next(b for b in BASKETS if b != p["basket"])
    fb = quiz.turn(e, "u", "", f"quiz:0:0:{wrong}").lead[0]
    assert fb.startswith("🤔 ") and f"*{quiz.ANSWER[p['basket']]}*" in fb and money(p["total_eur"]) in fb
    assert not any(w in fb.lower() for w in SHAMING)
    assert e.state["quiz"]["u"]["right"] == 0 and e.state["quiz"]["u"]["picks"][0]["guess"] == wrong


def test_typed_answers_work_too(e):
    quiz.start(e, "u")
    for text in ("1", "Irregular must-pay", "slips through"):
        r = e.state["quiz"]["u"]
        rnd, p = r["round"], r["picks"][r["round"]]
        t = quiz.turn(e, "u", text)
        assert t and r["round"] == rnd + 1
        assert p["guess"] == {"1": "regular", "Irregular must-pay": "irregular", "slips through": "everyday"}[text]


def test_summary_numbers_are_the_engines(e):
    quiz.start(e, "u")
    turns = play(e, "u", lambda p: p["basket"] if p["basket"] != "irregular" else "everyday")  # 4 right
    t = turns[-1]
    assert t.done and not quiz.active(e, "u") and t.text.startswith("🏁 *4/5*")
    b = e.baskets("last_month")
    assert "Your real split for September:" in t.text
    for x in b["baskets"]:
        assert f"• {x['label']}: {money(x['total_eur'])} ({x['share_pct']}%)" in t.text
    cuts = [e.simulate_cut(c, 20) for c in BASKETS["everyday"]["categories"]]
    year = round(sum(c["saving_per_year_eur"] for c in cuts), 2)
    month = round(sum(c["current_per_month_eur"] for c in cuts), 2)
    assert f"averaged {money(month)} a month" in t.text and f"*{money(year)} a year*" in t.text
    assert t.buttons == [("ask:everyday", "Where does it go?"), ("quiz:again", "Play again"),
                         ("quiz:money", "Open My money")]
    assert all(len(label) <= 20 for _, label in t.buttons)
    assert "?" not in t.text.split("\n")[0] and not any(w in t.text.lower() for w in SHAMING)


def test_low_score_is_never_shamed(e):
    quiz.start(e, "u")
    t = play(e, "u", lambda p: next(b for b in BASKETS if b != p["basket"]))[-1]
    assert t.text.startswith("🏁 *0/5*. Now you know where to look") and all("🤔" in x.lead[0] for x in [t])


def test_play_again_starts_a_new_game_and_old_buttons_are_safe(e):
    quiz.start(e, "u")
    play(e, "u", lambda p: p["basket"])
    t = quiz.turn(e, "u", "", "quiz:again")
    assert e.state["quiz"]["u"]["game"] == 1 and t.buttons[0][0] == "quiz:1:0:regular"
    assert quiz.turn(e, "u", "", "quiz:0:2:regular").text.startswith("That game is over")
    stale = quiz.turn(e, "u", "", "quiz:1:3:regular")  # a button from another round: the question again
    assert stale.text == t.text and e.state["quiz"]["u"]["round"] == 0


# ---------------- pause and exit ----------------
def test_stop_pauses_and_game_resumes_where_it_was(e):
    quiz.start(e, "u")
    p = e.state["quiz"]["u"]["picks"][0]
    quiz.turn(e, "u", "", f"quiz:0:0:{p['basket']}")
    t = quiz.turn(e, "u", "stop")
    assert t.text.startswith("👍 Paused at 2/5") and not t.buttons and not quiz.active(e, "u")
    assert quiz.paused_note(e, "u") is None  # a pause they asked for needs no reminder
    assert quiz.turn(e, "u", "How much did I spend on taxis?") is None  # paused: the coach answers
    t = quiz.turn(e, "u", "game")
    assert t.lead == ["🧺 Back to the game."] and t.text.startswith("*2/5* ") and quiz.active(e, "u")
    assert e.state["quiz"]["u"]["right"] == 1


def test_a_side_question_pauses_and_the_coach_answers(e):
    quiz.start(e, "u")
    assert quiz.turn(e, "u", "what is safe to spend this week?") is None
    assert not quiz.active(e, "u")
    assert quiz.paused_note(e, "u") == "🧺 The basket game is paused at 1/5. Write *game* to pick it up."
    assert quiz.paused_note(e, "u") is None  # said once
    r = e.state["quiz"]["u"]
    t = quiz.turn(e, "u", "", f"quiz:0:0:{r['picks'][0]['basket']}")  # tapping an answer resumes too
    assert t.lead[0].startswith("✅") and quiz.active(e, "u")


def test_not_a_game_message_is_left_alone(e):
    assert quiz.turn(e, "u", "how much did I spend on taxis?") is None
    assert quiz.turn(e, "u", "", "ob:goal:trip") is None
    assert quiz.turn(e, "u", "1") is None  # no game running
    assert quiz.turn(e, "u", "🧺 Play the basket game").text.startswith("*1/5*")


# ---------------- web chat and WhatsApp ----------------
class FakeCoach:
    def __init__(self):
        self.asked = []

    def ask(self, user, text, name="there", context=None):
        self.asked.append(text)
        return Reply(text="You can spend 50 € this week.", tools_used=["get_safe_to_spend"])

    def reset(self, user=None):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = FakeCoach()
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    monkeypatch.setattr(main.wa, "BUBBLE_PAUSE_S", 0)
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_web_chat_plays_the_whole_game(client):
    say = lambda text, **kw: client.post("/api/chat", json={"session": "t", "text": text, **kw}).json()
    assert "🧺 Play the basket game" in client.get("/chat").text
    r = say("🧺 Play the basket game")
    assert "basket game" in r["text"] and r["text"].endswith("Which basket?")
    assert [q["send"] for q in r["quick"]] == ["__btn__:quiz:0:0:regular", "__btn__:quiz:0:0:irregular",
                                               "__btn__:quiz:0:0:everyday"]
    r = say("how much do I earn?")  # a side question: the coach answers and the game waits
    assert r["text"] == "You can spend 50 € this week.\n\n🧺 The basket game is paused at 1/5. Write *game* to pick it up."
    assert main._coach.asked == ["how much do I earn?"]
    r = say("game")
    assert r["text"].startswith("🧺 Back to the game.") and r["quick"][0]["send"] == "__btn__:quiz:0:0:regular"
    picks = main.engine.state["quiz"]["web:t"]["picks"]
    for i, p in enumerate(picks):
        r = say(f"__btn__:quiz:0:{i}:{p['basket']}")
        assert r["text"].startswith("✅ Right.")
    assert "🏁 *5/5*" in r["text"] and "Your real split for September" in r["text"]
    assert [q["send"] for q in r["quick"]] == ["__btn__:ask:everyday", "__btn__:quiz:again", "__btn__:quiz:money"]
    assert say("__btn__:quiz:money")["buttons"] == [{"label": "Open My money", "url": "/app?web=1"}]
    say("__btn__:ask:everyday")
    assert main._coach.asked[-1] == main.ASK["ask:everyday"]
    assert say("__btn__:quiz:again")["quick"][0]["send"] == "__btn__:quiz:1:0:regular"
    assert len(main._coach.asked) == 2  # only the two real questions went to the AI


def test_web_game_uses_the_viewed_persona(client):
    eetu = EETU
    r = client.post("/api/chat", json={"session": "p", "text": "quiz", "as": eetu}).json()
    pe = main.engine_for(eetu)
    assert pe.state["quiz"]["web:p"]["picks"] == quiz.pick(pe)
    assert "quiz" not in main.engine.state or "web:p" not in main.engine.state["quiz"]
    assert r["text"].endswith("Which basket?")


def test_whatsapp_game_with_reply_buttons_and_stop(client):
    to = "358400000000"
    main.engine.state.setdefault("connected", {})[to] = {"scope": ["tx"]}
    msg = lambda text=None, button=None, mid="m": {"from": to, "id": mid, "name": "Aino", "text": text,
                                                     "button_id": button}
    main.handle_message(msg("game", mid="m1"))
    q = client.sent[-1]["interactive"]
    assert q["type"] == "button" and q["body"]["text"].startswith("*1/5* ")
    assert [b["reply"]["title"] for b in q["action"]["buttons"]] == ["Regular must-pay", "Irregular must-pay",
                                                                      "Slips through"]
    assert client.sent[-2]["text"]["body"].startswith("🧺 *The basket game.*")
    p = main.engine.state["quiz"][to]["picks"][0]
    main.handle_message(msg("Slips through", f"quiz:0:0:{p['basket']}", "m2"))
    assert client.sent[-2]["text"]["body"].startswith("✅ Right.")
    main.handle_message(msg("stop", mid="m3"))  # mid-game, STOP pauses the game, Oiva stays on
    assert main.connected(to) and not quiz.active(main.engine, to)
    assert "Paused at 2/5" in client.sent[-1]["text"]["body"] and "write *STOP* again" in client.sent[-1]["text"]["body"]
    main.handle_message(msg("What is safe to spend?", mid="m4"))
    assert client.sent[-1]["text"]["body"] == "You can spend 50 € this week."  # no game note after a chosen pause
    main.handle_message(msg("stop", mid="m5"))  # no game running: STOP switches Oiva off as before
    assert not main.connected(to) and to not in main.engine.state["quiz"]
