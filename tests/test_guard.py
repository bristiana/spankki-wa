"""Number guard: every figure in a reply comes from a tool or the user."""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.agent import Coach
from app.engine import Engine
from app.guard import parse, ungrounded_numbers


@pytest.mark.parametrize("token,value", [("1,234.50", 1234.5), ("1 234,50", 1234.5), ("1234.5", 1234.5),
                                         ("1.234,50", 1234.5), ("12,5", 12.5), ("3,700", 3700)])
def test_parse_formats(token, value):
    assert parse(token)[0] == value


def test_ungrounded_numbers():
    tool = json.dumps({"safe": 68.6, "per_day": 7.62, "after": -51.4, "total": 1234.5})
    assert ungrounded_numbers("You have 68.60 € left, about 7.6 € a day, 51.40 € short.", [tool]) == []
    assert ungrounded_numbers("That is 1 234,50 € or about 1,235 €.", [tool]) == []
    assert ungrounded_numbers("Payday in 9 days, on 15 October 2026, 3 bills.", []) == []  # whitelist
    assert ungrounded_numbers("A 120€ jacket", ["Can I afford a 120€ jacket?"]) == []  # the user's own number
    assert ungrounded_numbers("Save 250 € a month, 7.5% more, maybe 68.61 €", [tool]) == ["250", "7.5", "68.61"]
    assert ungrounded_numbers("Your id CUST04831", []) == []
    assert ungrounded_numbers("Save 2000 € by 2027, cut 20% for 3 months", []) == ["2000", "20"]  # money is never a year


class GuardClient:
    """First answer invents a number; after the correction, answers with the tool's number."""
    def __init__(self, second="You have 68.60 € safe to spend."):
        self.calls, self.second, self.seen = 0, second, []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls += 1
        self.seen.append(kw["messages"][-1]["content"])
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="get_safe_to_spend", input={})])
        text = "You have 99.99 € safe to spend." if self.calls == 2 else self.second
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text)])


def test_guard_retries_once(tmp_path):
    client = GuardClient()
    coach = Coach(Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json"), client=client)
    r = coach.ask("u", "what is safe?")
    assert r.text == "You have 68.60 € safe to spend." and client.calls == 3
    assert "99.99" in client.seen[-1]
    history = coach.histories["u"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert "99.99" not in str(history)


def test_guard_gives_up_quietly(tmp_path, caplog):
    client = GuardClient(second="Still 99.99 €.")
    coach = Coach(Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json"), client=client)
    with caplog.at_level("WARNING", logger="coach"):
        r = coach.ask("u", "what is safe?")
    assert r.text == "Still 99.99 €." and client.calls == 3
    assert "ungrounded" in caplog.text
