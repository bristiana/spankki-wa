"""'How I worked this out': each reply's tool steps, key figures, sources and the number guard's check."""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Coach
from app.engine import Engine
from app.explain import STATS_FI, TX, explain
from app.tools import run_tool

TRACE_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "trace.js"


@pytest.fixture
def engine(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


def step(engine, name, args):
    return explain(name, args, json.loads(run_tool(engine, name, args, [])))


def figures(s):
    return {f["label"]: f["value"] for f in s["figures"]}


def test_safe_to_spend_explained(engine):
    s = step(engine, "get_safe_to_spend", {})
    assert s["step"] == "Looked up what's safe to spend until payday" and s["sources"] == [TX]
    assert figures(s) == {"Balance": "1 001.94 €", "Rent due": "771.82 €", "Usual needs until payday": "118.55 €",
                          "Buffer for surprises": "42.96 €", "Safe until payday": "68.60 €"}


def test_afford_spending_and_cut_explained(engine):
    s = step(engine, "can_i_afford", {"amount_eur": 120, "what": "jacket"})
    assert s["step"] == "Checked whether the jacket for 120 € fits your budget"
    assert figures(s)["Price"] == "120.00 €" and figures(s)["Safe left after it"] == "-51.40 €"
    s = step(engine, "get_spending", {"category": "Restaurants", "period": "last_month"})
    assert s["step"] == "Added up Restaurants spending for last month"
    assert figures(s)["Total"] == "644.63 €" and figures(s)["Payments"] == "10"
    s = step(engine, "simulate_cut", {"category": "Restaurants", "cut_pct": 20, "years": 5})
    assert figures(s)["Saved per month"] == "97.18 €" and "Yahoo Finance monthly closes" in s["sources"]


def test_home_profile_and_guide_sources(engine):
    s = step(engine, "get_home_readiness", {"price_eur": None})
    assert "S-Pankki mortgage applications (challenge data)" in s["sources"]
    assert any(x.startswith("official guide: https://") for x in s["sources"])
    assert figures(s)["Monthly salary"] == "2 313.39 €"
    s = step(engine, "get_spender_profile", {})
    assert STATS_FI in s["sources"] and figures(s)["Savings rate"] == "22%"
    s = step(engine, "get_money_guide", {"topic": "asp"})
    assert s["figures"] == [] and all(x.startswith("official guide: https://") for x in s["sources"])


def test_generic_fallback_and_broken_results():
    s = explain("some_new_tool", {"x": 1}, {"total_eur": 12.5, "share_pct": 40, "ok": True, "name": "a"})
    assert s["step"] == "Ran some new tool"
    assert s["figures"] == [{"label": "Total", "value": "12.50 €"}, {"label": "Share", "value": "40%"}]
    assert explain("get_spending", {}, "not a dict")["figures"] == []  # never breaks a reply


class TraceClient:
    """Plays Claude: asks for a tool, then answers with `answer(result)`; an optional first draft invents a number."""
    def __init__(self, name, args, answer, draft=None):
        self.calls, self.name, self.args, self.answer, self.draft = 0, name, args, answer, draft
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name=self.name, input=self.args)])
        if self.draft and self.calls == 2:
            return NS(stop_reason="end_turn", content=[NS(type="text", text=self.draft)])
        result = next(json.loads(r["content"]) for m in kw["messages"][::-1] if m["role"] == "user"
                      and isinstance(m["content"], list) for r in m["content"])
        return NS(stop_reason="end_turn", content=[NS(type="text", text=self.answer(result))])


def safe_answer(r):
    return (f"You have {r['balance_eur']:.2f} €; rent {r['bills_total_eur']:.2f} € is due. Safe until payday: "
            f"{r['safe_to_spend_until_payday_eur']:.2f} €, {r['safe_per_day_eur']:.2f} € a day, in 9 days.")


def test_coach_trace_counts_the_checked_figures(engine):
    coach = Coach(engine, client=TraceClient("get_safe_to_spend", {}, safe_answer))
    r = coach.ask("u", "What is safe for me to spend this week?")
    tool, check = r.trace
    assert tool["tool"] == "get_safe_to_spend" and tool["args"] == {}
    assert check == {"check": "4 of 4 figures checked against the calculations", "checked": 4, "matched": 4,
                     "rewritten": False}  # "9 days" needs no source


def test_coach_trace_notes_a_rewrite(engine):
    client = TraceClient("get_safe_to_spend", {}, safe_answer, draft="You have 99.99 € safe.")
    r = Coach(engine, client=client).ask("u", "what is safe?")
    assert client.calls == 3 and len(r.trace) == 2  # the same tool call is listed once
    assert r.trace[-1]["rewritten"] and "rewritten" in r.trace[-1]["check"] and "figures checked against" in r.trace[-1]["check"]


def test_no_tools_no_trace(engine):
    client = NS(beta=NS(messages=NS(create=lambda **kw: NS(stop_reason="end_turn", content=[NS(type="text", text="Hi!")]))))
    assert Coach(engine, client=client).ask("u", "hello").trace == []


XSS = '<img src=x onerror="alert(1)"><script>alert(2)</script>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = Coach(main.engine, client=TraceClient(
        "can_i_afford", {"amount_eur": 120, "what": XSS},
        lambda r: f"Not quite: it leaves {r['safe_left_after_eur']:.2f} € until payday."))
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    c = TestClient(main.app)
    c.sent = sent
    yield c
    main._coach = None


def test_chat_api_returns_the_trace(client):
    j = client.post("/api/chat", json={"session": "t", "text": "Can I afford a 120€ jacket?"}).json()
    tool, check = j["trace"]
    assert tool["tool"] == "can_i_afford" and tool["args"]["amount_eur"] == 120
    assert {"label": "Safe left after it", "value": "-51.40 €"} in tool["figures"] and tool["sources"] == [TX]
    assert check["checked"] == check["matched"] == 1
    assert '<script src="/static/trace.js">' in client.get("/chat").text
    assert client.get("/static/trace.js").status_code == 200


def test_whatsapp_gets_no_trace_messages(client):
    main.engine.state["connected"]["40700000000"] = {}
    main.handle_message({"from": "40700000000", "name": "Aino", "id": "m1", "text": "Can I afford a 120€ jacket?"})
    bodies = json.dumps(client.sent, ensure_ascii=False)
    assert "51.40" in bodies and "How I worked this out" not in bodies and "figures checked" not in bodies and "Sources" not in bodies


@pytest.mark.skipif(not shutil.which("node"), reason="needs node to run the page's renderer")
def test_panel_escapes_what_the_customer_typed(client):
    trace = client.post("/api/chat", json={"session": "x", "text": "afford it?"}).json()["trace"]
    trace[0]["sources"].append('official guide: https://example.fi/"><script>alert(3)</script>')
    js = f"const t = require({json.dumps(str(TRACE_JS))}); process.stdout.write(t({json.dumps(trace)}));"
    html = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert html.startswith('<details class="how"><summary>') and "<details class=\"how\" open" not in html
    assert "<script" not in html and "<img" not in html and 'onerror="' not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&lt;script&gt;" in html
    assert "120.00 €" in html and "1 of 1 figures checked" in html
