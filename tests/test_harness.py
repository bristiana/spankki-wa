"""The chat harness: one turn at a time per conversation, only what the person saw is kept, tool results become short
notes, older turns fold into a summary, and conversations survive a restart. A fake client plays the model."""
import json
import threading
import time
from datetime import date
from types import SimpleNamespace as NS

import pytest

from app import agent
from app.agent import Coach
from app.engine import Engine


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def text(t):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=t)])


def tool_call(name="get_safe_to_spend", tid="t1"):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=tid, name=name, input={})])


class Client:
    """Answers each call with the next of `replies` (a response, an exception to raise, or a callable taking the
    request); keeps a copy of every request."""

    def __init__(self, *replies, delay=0.0):
        self.replies, self.delay, self.requests = list(replies), delay, []
        self._lock = threading.Lock()
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        with self._lock:
            self.requests.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
            r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        time.sleep(self.delay)
        if isinstance(r, Exception):
            raise r
        return r(kw) if callable(r) else r


def alternates(msgs):
    return [m["role"] for m in msgs] == ["user", "assistant"] * (len(msgs) // 2) and len(msgs) % 2 == 0


def test_two_quick_messages_wait_for_each_other(e):
    def answer(kw):  # a tool round, then a reply; every request must alternate and pair its tool calls
        msgs = kw["messages"]
        assert all(a["role"] != b["role"] for a, b in zip(msgs, msgs[1:])), [m["role"] for m in msgs]
        return tool_call() if isinstance(msgs[-1]["content"], str) else text("[react]👀\n---\nAll good.")

    c = Coach(e, client=Client(answer, delay=0.05))
    c.remember("u", "hi", "Hi there!")  # an existing conversation: both turns share its history
    threads = [threading.Thread(target=c.ask, args=("u", q)) for q in ("first?", "second?")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    history = c.histories["u"]
    assert alternates(history) and len(history) == 6
    assert {history[2]["content"], history[4]["content"]} == {"first?", "second?"}


def test_a_failed_rewrite_leaves_nothing_behind(e):
    c = Coach(e, client=Client(text("[react]👀\n---\nYou have 99.99 € safe to spend."), text("")))
    r = c.ask("u", "what is safe?")
    assert "99.99" in r.text  # the retry gave nothing: the draft is what they see
    history = c.histories["u"]
    assert alternates(history) and len(history) == 2
    assert "Rewrite your reply" not in json.dumps(history, default=str)


def test_an_api_error_leaves_the_history_unchanged(e):
    c = Coach(e, client=Client(RuntimeError("API down")))
    c.remember("u", "1500 by june", "Your plan is 214.29 € a month.")
    before = json.dumps(c.histories["u"])
    with pytest.raises(RuntimeError):
        c.ask("u", "why so much?")
    assert json.dumps(c.histories["u"]) == before
    c.remember("u", "balanced", "Got it.")  # the interview's next turn is still remembered
    assert len(c.histories["u"]) == 4


def test_tool_json_stays_out_of_the_history_but_its_note_stays(e, monkeypatch):
    out = {"safe_to_spend_eur": 68.6, "per_day_eur": 7.62, "payday": "2026-10-15", "basis": "after bills"}
    monkeypatch.setattr(agent, "run_tool", lambda *a: json.dumps(out))
    c = Coach(e, client=Client(tool_call(), text("[react]👀\n---\nYou have 68.60 € safe to spend.")))
    c.ask("u", "what is safe to spend?")
    history = c.histories["u"]
    assert alternates(history) and all(isinstance(m["content"], str) for m in history)
    assert "safe_to_spend_eur" not in json.dumps(history)
    note = c.notes["u"][-1]
    assert note["asked"] == "what is safe to spend?" and "get_safe_to_spend" in note["note"] and "68.6" in note["note"]


def test_the_next_turn_gets_the_note_and_the_guard_trusts_it(e, monkeypatch):
    monkeypatch.setattr(agent, "run_tool", lambda *a: json.dumps({"safe_to_spend_eur": 68.6}))
    client = Client(tool_call(), text("[react]👀\n---\nYou have 68.60 € safe to spend."),
                    text("[react]🤔\n---\nThose 68.60 € are after rent."))
    c = Coach(e, client=client)
    c.ask("u", "what is safe to spend?")
    r = c.ask("u", "why?")
    assert len(client.requests) == 3 and "68.60" in r.text  # grounded by the note: no rewrite
    context = client.requests[-1]["system"][1]["text"]
    assert "Recent lookups" in context and "safe_to_spend_eur" in context


def test_an_old_reply_is_not_a_source_for_the_guard(e):
    client = Client(text("[react]👀\n---\nStill 42.10 €."), text("[react]👀\n---\nLet me check again."))
    c = Coach(e, client=client)
    c.histories["u"] = [{"role": "user", "content": "taxis?"},
                        {"role": "assistant", "content": "[react]🚕\n---\nYou spent 42.10 € on taxis."}]
    c.ask("u", "and again?")
    assert "Rewrite your reply" in str(client.requests[-1]["messages"][-1]["content"])


def test_system_prompt_is_a_cached_persona_and_a_per_turn_context(e):
    client = Client(text("[react]👋\n---\nHi!"))
    c = Coach(e, client=client)
    c.ask("u", "hi", name="Aino", context="The interview asks again: Which is closest?")
    c.ask("u", "hey", name="Aino")
    first, second = client.requests[0]["system"], client.requests[1]["system"]
    assert first[0] == second[0] and first[0]["cache_control"] == {"type": "ephemeral"}
    assert first[0]["text"] == agent.SYSTEM and "{" not in agent.SYSTEM
    assert "Aino" in first[1]["text"] and "Tuesday 06 October 2026" in first[1]["text"]
    assert "<right_now>" in first[1]["text"] and "<right_now>" not in second[1]["text"]


def test_old_turns_fold_into_a_summary(e):
    client = Client(text("[react]👀\n---\nSure."))
    c = Coach(e, client=client)
    for i in range(16):
        c.ask("u", f"question {i}")
    assert len(c.histories["u"]) <= agent.HISTORY_CAP and len(c.histories["u"]) >= agent.HISTORY_KEEP
    summary = c.summaries["u"]
    assert summary and len(summary) <= agent.SUMMARY_MAX
    c.ask("u", "one more")
    assert "Earlier in this conversation (summary, not verbatim): " + summary in client.requests[-1]["system"][1]["text"]


def test_plain_summary_without_the_model():
    dropped = [{"role": "user", "content": "how much on taxis"}, {"role": "assistant", "content": "[react]🚕\n---\n42 €"}]
    s = agent.plain_summary("", dropped)
    assert s == "User: how much on taxis | Oiva: 42 €"
    assert len(agent.plain_summary("x" * 2000, dropped)) <= agent.SUMMARY_MAX


def test_conversations_survive_a_restart_without_secrets(e, monkeypatch):
    monkeypatch.setattr(agent, "run_tool", lambda *a: json.dumps({"safe_to_spend_eur": 68.6}))
    c = Coach(e, client=Client(tool_call(), text("[react]👀\n---\nYou have 68.60 € safe to spend.")))
    c.ask("u", "my pin is 4821, what is safe to spend?")
    again = Coach(Engine(today=date(2026, 10, 6), state_path=e.state_path), client=Client(text("ok")))
    assert again.histories["u"] == c.histories["u"] and again.notes["u"] == c.notes["u"]
    stored = again.chats_path.read_text(encoding="utf-8")
    assert "4821" not in stored and "[PIN removed]" in stored
    again.reset("u")
    assert "u" not in Coach(Engine(today=date(2026, 10, 6), state_path=e.state_path), client=Client(text("ok"))).histories
