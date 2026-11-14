"""Follow-ups that make people finish: the OPEN_THREAD signal (something they started and left), which comes back
once, the next day, inside WhatsApp's 24 hours; and the learning streak in the Learn course."""
import time
from datetime import date

import pytest

from app import learn, quiz, rules
from app import onboarding as ob
from app.engine import Engine

H = 3600


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def quiet(e, who, hours):
    e.state.setdefault("last_msg", {})[who] = time.time() - hours * H
    return e


def threads(e, who="u", **kw):
    return [s for s in rules.signals(e, who, **kw) if s["type"] == "OPEN_THREAD"]


def test_thread_needs_a_quiet_day_inside_the_window(e):
    learn.start(e, "u")
    learn.turn(e, "u", "", "learn:next:0")
    learn.turn(e, "u", "", "learn:next:1")
    assert threads(quiet(e, "u", 2)) == []  # still talking
    assert threads(quiet(e, "u", 30)) == []  # outside WhatsApp's 24 hours
    (t,) = threads(quiet(e, "u", 20))
    assert t["data"] == {"thread": "learn", "lesson": 3, "of": 5, "title": "The buffer", "done": 2}
    assert t["dedup_key"] == "thread:learn:1:2"
    assert threads(e, force=True)  # the demo panel skips the wait
    assert threads(Engine(today=e.today, state_path=e.state_path)) == []  # never heard from them: nothing
    assert [s for s in rules.signals(e) if s["type"] == "OPEN_THREAD"] == []  # threads are per person


def test_whatsapp_last_seen_counts_too(e):
    quiz.start(e, "358401234567")
    e.state["wa_last"] = {"358401234567": time.time() - 19 * H}
    assert [s["data"]["thread"] for s in threads(e, "358401234567")] == ["quiz"]


def test_thread_comes_back_once(e):
    quiz.start(e, "u")
    quiet(e, "u", 20)
    (t,) = threads(e)
    sent = e.state.setdefault("sent", {}).setdefault("u", {})
    picked = []
    for _ in range(12):  # everything Birky would send, one after the other
        s = rules.pick(e, "u", force=True)
        if not s:
            break
        picked.append(s["dedup_key"])
        sent[s["dedup_key"]] = e.today.isoformat()
    assert picked.count(t["dedup_key"]) == 1
    old = {k: "2026-09-01" for k in sent}  # weeks later: the check-in may come again, the thread never
    assert rules.cooled(old, "checkin", e.today) and not rules.cooled(old, t["dedup_key"], e.today)


def test_thread_priority_sits_after_urgent_before_drift(e):
    p = rules.PRIORITY
    assert p["RECURRING_CHARGE_UPCOMING"] < p["BILL_INCREASE"] < p["OPEN_THREAD"] < p["GOAL_DRIFT"] < p["WEEKLY_CHECK_IN"]
    learn.start(e, "u")
    kinds = [s["type"] for s in rules.signals(quiet(e, "u", 20), "u")]
    assert kinds.index("OPEN_THREAD") > kinds.index("RECURRING_CHARGE_UPCOMING")  # rent tomorrow comes first
    assert kinds.index("OPEN_THREAD") < kinds.index("WEEKLY_CHECK_IN")


def test_every_kind_of_thread(e):
    ob.start(e, "u")  # the interview, paused at the goal question
    quiz.start(e, "u")
    learn.start(e, "u")
    bill = next(b for b in e.upcoming_bills() if b["name"] == "Rent")
    p = e.create_payment(bill["bill_id"])  # a PIN link not confirmed
    e.create_goal("Trip", 900)  # a goal with nothing in it
    e.state["later"] = {"u": {"key": f"later:{bill['bill_id']}", "offer": None, "at": time.time()}}
    kinds = [s["data"]["thread"] for s in threads(quiet(e, "u", 20))]
    assert kinds == ["learn", "onboarding", "quiz", "pin", "goal", "later"]  # most useful first
    pin = next(s for s in threads(e) if s["data"]["thread"] == "pin")
    assert pin["data"]["pending_id"] == p["pending_id"] and pin["dedup_key"] == f"thread:pin:{p['pending_id']}"
    goal = next(s for s in threads(e) if s["data"]["thread"] == "goal")["data"]
    assert goal["goal_id"] == "trip" and 5 <= goal["amount_eur"] <= e.safe_to_save()["now_max_eur"]


def test_no_thread_once_finished(e):
    learn.start(e, "u")
    e.state["learn"]["u"]["done"] = True
    g = e.create_goal("Trip", 900)["goal"]
    e.state["goals"][g["id"]]["saved"] = 50.0
    bill = next(b for b in e.upcoming_bills() if b["name"] == "Rent")
    e.confirm(e.create_payment(bill["bill_id"])["pending_id"])
    assert threads(quiet(e, "u", 20)) == []


def test_open_threads_are_read_only(e):
    learn.start(e, "u")
    quiet(e, "u", 20)
    before = repr(e.state)
    rules.signals(e, "u")
    assert repr(e.state) == before


def test_a_new_run_of_the_course_can_come_back_again(e):
    learn.start(e, "u")
    first = threads(quiet(e, "u", 20))[0]["dedup_key"]
    e.state["learn"]["u"]["done"] = True
    learn.start(e, "u")
    assert threads(e)[0]["dedup_key"] != first


def test_quiz_resume_button_keeps_the_same_game(e):
    quiz.start(e, "u")
    quiz.turn(e, "u", "", "quiz:0:0:regular")
    t = quiz.turn(e, "u", "", "quiz:resume")
    assert t.lead == ["🧺 Back to the game."] and t.text.startswith("*2/")
    assert e.state["quiz"]["u"]["game"] == 0


# ---------------- learning streak ----------------
def test_learn_progress_and_finish_celebration(e):
    learn.start(e, "u")
    assert learn.turn(e, "u", "", "learn:next:0").lead == ["✅ 1 of 5 done 🌰"]
    assert learn.turn(e, "u", "", "learn:next:1").lead == ["✅ 2 of 5 done 🌰"]
    assert learn.turn(e, "u", "", "learn:next:2").lead == ["✅ 3 of 5 done 🌰 Past halfway 🙌"]
    assert learn.turn(e, "u", "next").lead == ["✅ 4 of 5 done 🌰 Last one, 30 seconds 💪"]
    done = learn.turn(e, "u", "", "learn:finish")
    assert done.lead == ["🌰 5 of 5 done!"] and "course done! 🎉" in done.text
