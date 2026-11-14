"""The hero persona (the demo default, ~40) and the three questions S-Pankki's mentors say matter most."""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import SYSTEM, Coach
from app.engine import Engine
from app.explain import explain
from app.tools import TOOLS, run_tool

THREE = ["What kind of spender am I?", "What can I safely spend this week?", "How could I save more?"]


@pytest.fixture
def hero(tmp_path) -> Engine:
    return main.persona_engine(main.HERO, date(2026, 10, 6), tmp_path / "h.json")


def test_hero_fits_the_mentor_brief(hero):
    p = main.PERSONAS[main.HERO]
    assert 38 <= p["age"] <= 45 and p["name"] != "Aino" and "why" in p
    pay = {x["id"]: x for x in hero.commitments()["regular_payments"] if x["status"] == "active"}
    assert {"rent", "utilities", "investing", "salary"} <= set(pay)
    s = hero.safe_to_spend()
    assert s["next_payday"]["date"] and 50 <= s["safe_this_week_eur"] <= 300
    taxi = hero.spending(None, "last_3_months", merchant_type="taxi")
    assert taxi["transactions"] >= 3 and hero.monthly_avg("Restaurants", 3) > 0
    assert hero.balance() > 0


def test_save_more_gives_three_ideas_with_one_number_each(hero):
    out = hero.save_more()
    kinds = [i["idea"] for i in out["ideas"]]
    assert kinds == ["biggest_lever", "move_what_built_up", "review_payment"]
    lever, built, review = out["ideas"]
    cut = hero.simulate_cut(lever["category"], 20)
    assert lever["saving_per_year_eur"] == cut["saving_per_year_eur"] >= 500
    assert all(hero.simulate_cut(c, 20)["saving_per_year_eur"] <= cut["saving_per_year_eur"]
               for c in ("Restaurants", "Entertainment", "Lifestyle"))
    assert built["amount_eur"] == hero.safe_to_save()["reserve_eur"] > 0
    assert review["name"] == "Utility bills" and all(i["number_to_say"] in i for i in out["ideas"])
    assert "Emergency buffer" in out["next_step"] and "note" in out  # spending ran above income lately


def test_spender_profile_compares_the_hero_with_his_own_age_and_area(hero, tmp_path):
    p = hero.spender_profile()["peers"]
    assert p["group"] == "S-Pankki customers aged 30-39 in the Vantaa area" and p["customers"] >= 30
    assert p["biggest_differences"] and "12 full months" in p["compared_over"]
    aino = Engine(today=date(2026, 10, 6), state_path=tmp_path / "a.json").spender_profile()["peers"]
    assert aino["group"] == "S-Pankki customers aged 20-29" and aino["customers"] == 535  # unchanged


def test_landing_shows_the_hero_and_the_three_questions(tmp_path):
    main.engine = main.persona_engine(main.DEFAULT_PERSONA, date(2026, 10, 6), tmp_path / "s.json")
    page = TestClient(main.app).get("/").text
    assert "synthetic" not in page and "Demo customer" not in page
    assert all(q in page for q in THREE) and "the average Finnish household" in page and "any age" in page


def test_save_more_on_aino_skips_what_she_does_not_have(tmp_path):
    out = Engine(today=date(2026, 10, 6), state_path=tmp_path / "a.json").save_more()
    assert [i["idea"] for i in out["ideas"]] == ["biggest_lever", "review_payment"]
    assert out["ideas"][0]["category"] == "Restaurants" and out["ideas"][0]["saving_per_year_eur"] == 1166.16
    assert "eating out" in out["next_step"]


def test_save_more_is_a_tool_with_a_trace_step(hero):
    assert any(t["name"] == "get_save_more_ideas" for t in TOOLS)
    out = json.loads(run_tool(hero, "get_save_more_ideas", {}, []))
    step = explain("get_save_more_ideas", {}, out)
    assert step["step"] == "Looked for where you could save more" and len(step["figures"]) == 3
    assert "get_save_more_ideas" in SYSTEM and "What kind of spender am I?" in SYSTEM


class Script:
    """Fake API: asks for one tool, then says its headline numbers."""
    def __init__(self, tool):
        self.tool, self.beta = tool, NS(messages=NS(create=self.create))

    def create(self, **kw):
        last = kw["messages"][-1]["content"]
        if isinstance(last, str):
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name=self.tool, input={})])
        out = json.loads(last[0]["content"])
        n = [i[i["number_to_say"]] for i in out["ideas"]] if "ideas" in out else [out["safe_this_week_eur"]]
        return NS(stop_reason="end_turn", content=[NS(type="text", text="[react]🌰\n---\n" + " · ".join(f"{x} €" for x in n))])


@pytest.mark.parametrize("q,tool", [(THREE[1], "get_safe_to_spend"), (THREE[2], "get_save_more_ideas")])
def test_web_chat_answers_as_the_hero_by_default(tmp_path, monkeypatch, q, tool):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = main.persona_engine(main.DEFAULT_PERSONA, date(2026, 10, 6), tmp_path / "s.json")
    main._coach = Coach(main.engine, client=Script(tool))
    j = TestClient(main.app).post("/api/chat", json={"session": "h", "text": q}).json()
    want = main.engine.save_more()["ideas"][0]["saving_per_year_eur"] if tool == "get_save_more_ideas" else \
        main.engine.safe_to_spend()["safe_this_week_eur"]
    assert f"{want:.2f} €" in j["text"]
