"""Conversation quality: WhatsApp bubbles, the reply's date and money style, the coach knowing what the interview or
the game just showed, and "deactivate all my subscriptions" (there are none). A fake client plays the model; no real API calls."""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main, quiz
from app import onboarding as ob
from app import whatsapp as wa
from app.agent import SYSTEM, Coach, Reply, date_style, euro_style
from app.engine import Engine
from app.tools import TOOLS, run_tool


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


# ---------------- WhatsApp bubbles ----------------
def test_bubbles_keep_a_lead_line_with_its_list():
    text = "You spent *57.73 €* on taxis.\n\nWhere it went:\n\n• Parking: 112.56 €\n• Taxis: 57.73 €\n\nWant a chart?"
    assert wa.bubbles(text) == ["You spent *57.73 €* on taxis.", "Where it went:\n• Parking: 112.56 €\n• Taxis: 57.73 €",
                                "Want a chart?"]


def test_bubbles_share_the_length_instead_of_one_long_last_bubble():
    long = "x" * 300
    parts = wa.bubbles(f"A.\n\n{long}\n\nB.\n\n{long}\n\nC?")
    assert parts == ["A.", f"{long}\n\nB.", f"{long}\n\nC?"]  # the old split: ["A.", long, "B. + long + C?"]
    assert wa.bubbles("a\n\nb\n\nc\n\nd") == ["a", "b", "c\n\nd"]  # equal lengths: the later pair is joined
    assert wa.bubbles("one line") == ["one line"] and wa.bubbles("") == [""]


# ---------------- how a reply reads ----------------
@pytest.mark.parametrize("raw, shown", [
    ("safe to spend 68.6 € until payday", "safe to spend 68.60 € until payday"),
    ("1 234.5 € in total", "1 234.50 € in total"),
    ("€596.9 on cafés", "596.90 € on cafés"),
    ("68.60 € and 12.5% and 3.5 years", "68.60 € and 12.5% and 3.5 years"),
])
def test_amounts_always_have_two_decimals(raw, shown):
    assert euro_style(raw) == shown


def test_iso_dates_read_like_people_say_them():
    today = date(2026, 10, 6)
    assert date_style("Payday is 2026-10-15.", today) == "Payday is Thu 15 Oct."
    assert date_style("next charge 2027-02-10", today) == "next charge Wed 10 Feb 2027"
    assert date_style("CUST-2026-10-155 and 2026-13-01", today) == "CUST-2026-10-155 and 2026-13-01"


def test_prompt_sets_phone_rules():
    for rule in ("Answer what they asked in your first bubble", "split by a line with only ---",
                 "At most one question per message", "never a menu", "never 2026-10-15",
                 "explain your LAST message again", "never name or invent one", "Say who you are only when", "[react]",
                 "Mirror their tone", "Never mention tools", "means yes"):
        assert rule in SYSTEM, rule
    for section in ("role", "never", "style", "how_you_help", "examples"):
        assert f"<{section}>" in SYSTEM and f"</{section}>" in SYSTEM, section
    assert 5 <= SYSTEM.count("<example>") <= 7


class Recorder:
    """Plays the model: answers with a fixed text and keeps every request."""

    def __init__(self, text="Payday is 2026-10-15, safe to spend 68.6 €."):
        self.text, self.requests = text, []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text=self.text)])


def test_reply_gets_date_and_money_style(e):
    r = Coach(e, client=Recorder()).ask("u", "when is payday?")
    assert r.text == "Payday is Thu 15 Oct, safe to spend 68.60 €."


def test_context_reaches_the_system_prompt(e):
    rec = Recorder("Fine.")
    Coach(e, client=rec).ask("u", "what does prudent mean", context="The interview asks again: Which is closest?")
    assert rec.requests[0]["system"].endswith("<right_now>\nThe interview asks again: Which is closest?\n</right_now>")
    Coach(e, client=rec).ask("u", "hi")
    assert "<right_now>" not in rec.requests[1]["system"]


def test_remembered_flow_turns_are_history_and_number_sources(e):
    rec = Recorder("Your plan is 214.29 € a month.")
    coach = Coach(e, client=rec)
    coach.remember("u", "1500 by june", "*Trip*: 1 500 € by June 2027.\nThat's *214.29 € a month* for 7 months.")
    r = coach.ask("u", "why so much?")
    msgs = rec.requests[0]["messages"]
    assert msgs[0] == {"role": "user", "content": "1500 by june"} and msgs[1]["role"] == "assistant"
    assert "214.29" in msgs[1]["content"] and msgs[2]["content"] == "why so much?"
    assert r.trace == [] and len(rec.requests) == 1  # the plan's number counts as grounded: no rewrite asked for


def test_remember_never_puts_two_user_turns_in_a_row(e):
    coach = Coach(e, client=Recorder())
    coach.histories["u"] = [{"role": "user", "content": "dangling"}]
    coach.remember("u", "x", "y")
    assert coach.histories["u"] == [{"role": "user", "content": "dangling"}]


def test_guard_checks_small_percentages_and_amounts():
    from app.guard import ungrounded_numbers
    tool = json.dumps({"share_pct": 22, "fee_eur": 5})
    assert ungrounded_numbers("so 4% of that, about 3 € a day", [tool]) == ["4", "3"]  # invented, now caught
    assert ungrounded_numbers("you invest 22% and pay 5 € in fees, 9 days to payday, 2 bills", [tool]) == []


# ---------------- tools ----------------
def test_deactivate_all_subscriptions_gets_an_honest_answer(e):
    """There are no subscriptions in the data: the tool says so, names the real regular payments and offers to pause
    the investing, and nothing is prepared until the person asks."""
    buttons = []
    out = json.loads(run_tool(e, "get_regular_payments", {}, buttons))
    assert buttons == [] and not e.state["pending"]
    assert out["subscriptions_found"] == 0 and out["card_subscriptions"] == []
    answer = out["if_asked_to_cancel_subscriptions"]
    assert answer.startswith("I don't see any subscriptions on your account.")
    assert "rent (771.82 € a month)" in answer and "about 3 utility bills a month (122.65 € in total)" in answer
    assert "your monthly investing (497.84 € a month)" in answer and answer.endswith("Want to pause the investing?")
    from app.guard import ungrounded_numbers
    assert ungrounded_numbers(answer, [json.dumps(out)]) == []
    assert not any(t["name"] in ("get_subscriptions", "create_stop_subscription_link") for t in TOOLS)


def test_deactivate_all_reply_through_the_coach(e):
    """The fake model asks for the regular payments and repeats the honest line: no buttons, no stop links."""
    class Honest:
        def __init__(self):
            self.beta = NS(messages=NS(create=self.create))

        def create(self, messages, **kw):
            last = messages[-1]["content"]
            if isinstance(last, str):
                return NS(stop_reason="tool_use",
                          content=[NS(type="tool_use", id="t1", name="get_regular_payments", input={})])
            said = json.loads(last[0]["content"])["if_asked_to_cancel_subscriptions"]
            return NS(stop_reason="end_turn", content=[NS(type="text", text=said)])

    r = Coach(e, client=Honest()).ask("u", "deactivate all my subscriptions")
    assert r.text.startswith("I don't see any subscriptions") and r.buttons == []
    assert r.tools_used == ["get_regular_payments"]


def test_pause_investing_link_needs_the_pin(e):
    buttons = []
    out = json.loads(run_tool(e, "create_pause_investing_link", {}, buttons))
    assert [b["label"] for b in buttons] == ["Pause investing"] and "/s-mobiili/?ready=stop:" in buttons[0]["url"]
    assert out["amount"] == 497.84 and out["next_charge"] == "2026-10-18" and out["kind"] == "pause_investing"
    inv = next(x for x in e.commitments()["regular_payments"] if x["id"] == "investing")
    assert inv["status"] == "active" and not e.state.get("paused")  # nothing paused before the PIN


def test_explore_flags_a_running_month_and_names_the_lowest(e):
    out = e.explore(what="spent", group_by="month", metric="sum", period="all", start="2026-01-01", end="2026-10-06")
    assert out["rows"][-1]["label"] == "Oct 2026" and "still running" in out["note"]
    full = out["rows"][:-1]
    low = min(full, key=lambda r: r["value"])
    assert out["totals"]["lowest"] == {"label": low["label"], "value": low["value"]}
    closed = e.explore(what="spent", group_by="month", metric="sum", period="last_12_months")
    assert "still running" not in closed["note"]


# ---------------- the chat knows what the interview or the game just showed ----------------
class ContextCoach:
    def __init__(self):
        self.calls, self.remembered = [], []

    def ask(self, user, text, name="there", context=None):
        self.calls.append((text, context))
        return Reply(text="Prudent means your money barely moves.")

    def remember(self, user, said, shown):
        self.remembered.append((said, shown))

    def reset(self, user=None):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = ContextCoach()
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    monkeypatch.setattr(main.wa, "BUBBLE_PAUSE_S", 0)
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_side_question_mid_interview_gets_the_waiting_question_as_context(client):
    say = lambda text: client.post("/api/chat", json={"session": "cx", "text": text}).json()
    say("__start__")
    say("__btn__:ob:goal:trip")
    say("1500 by june")
    r = say("what does prudent mean")
    text, context = main._coach.calls[-1]
    assert text == "what does prudent mean"
    assert ob.ASK["risk"][1] in context and "end without a question" in context
    assert r["text"].endswith(ob.ASK["risk"][1])  # the interview question comes back after the answer
    said = [s for s, _ in main._coach.remembered]
    assert said == ["[connected S-Pankki]", "[tapped ob:goal:trip]", "1500 by june"]
    assert "Prudent" in main._coach.remembered[-1][1]  # the risk question the person saw


def test_question_mid_game_gets_the_paused_question_as_context(client):
    say = lambda text: client.post("/api/chat", json={"session": "gx", "text": text}).json()
    say("game")
    say("what is a basket")
    _, context = main._coach.calls[-1]
    assert "basket game" in context and "Which basket?" in context
    assert quiz.waiting_question(main.engine, "web:gx") is None  # the paused note went out, nothing waits now


def test_plain_question_has_no_context(client):
    client.post("/api/chat", json={"session": "px", "text": "what is safe to spend?"})
    assert main._coach.calls[-1][1] is None


def test_whatsapp_numbers_extra_buttons(client, monkeypatch):
    texts = []
    monkeypatch.setattr(main.wa, "send_url_button", lambda to, text, label, url: texts.append((text, label)))
    monkeypatch.setattr(main.wa, "send_bubbles", lambda to, parts: None)
    reply = Reply(text="Three stop links are ready.", buttons=[{"label": f"Stop S{i}", "url": f"u{i}"} for i in range(3)])
    main.send_reply("358400000000", reply)
    assert texts == [("Three stop links are ready.", "Stop S0"), ("2/3", "Stop S1"), ("3/3", "Stop S2")]
