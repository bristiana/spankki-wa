"""MyData explorer: engine.explore(), the explore_my_data tool and the chart in the web chat."""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Coach
from app.engine import EXPLORE_MERCHANT_TYPES, MCC_LABELS, MERCHANT_TYPES, WEEKDAYS, Engine
from app.guard import ungrounded_numbers
from app.tools import TOOLS, run_tool


@pytest.fixture
def engine(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


# ---------- engine.explore: validation ----------
@pytest.mark.parametrize("kw", [
    {"what": "everything"}, {"group_by": "hour"}, {"metric": "median"}, {"period": "forever"},
    {"category": "Casino"}, {"basket": "fun"}, {"merchant_type": "restaurants_cafes"}, {"merchant_type": "1234"},
    {"what": "income", "category": "Restaurants"}, {"what": "income", "merchant_type": "taxis"},
    {"category": "Restaurants", "basket": "everyday"},
    {"start": "yesterday"}, {"start": "2026-05-01", "end": "2026-04-01"},
    {"group_by": "__import__('os')"}, {"category": "Restaurants'; DROP TABLE tx;--"},
])
def test_explore_rejects_anything_outside_the_enums(engine, kw):
    with pytest.raises(ValueError):
        engine.explore(**kw)


def test_explore_rejects_unknown_arguments(engine):
    with pytest.raises(TypeError):
        engine.explore(code="print(1)")


def test_merchant_types_share_get_spendings_map(engine):
    """One MCC map: explore_my_data and get_spending name and count kinds of place the same way."""
    assert set(EXPLORE_MERCHANT_TYPES) == set(MERCHANT_TYPES) | set(MCC_LABELS)
    by_type = engine.explore(group_by="merchant_type", period="last_3_months")
    assert {r["label"] for r in by_type["rows"]} <= set(MCC_LABELS.values()) | {"No merchant code"}
    for word in ("taxi", "uber", "4121", "coffee", "parking"):
        e = engine.explore(merchant_type=word, group_by="month", period="last_12_months")
        s = engine.spending(None, "last_12_months", merchant_type=word)
        assert e["totals"]["total_eur"] == s["total_eur"] and e["totals"]["transactions"] == s["transactions"], word
        assert e["title"].endswith(f"at {s['merchant_type']} by month")
    split = engine.spending("Restaurants", "last_3_months")["by_merchant_type"]
    rest = engine.explore(category="Restaurants", group_by="merchant_type", period="last_3_months")
    assert {r["label"]: r["value"] for r in rest["rows"]} == split


# ---------- engine.explore: grouping ----------
def test_weekday_answers_when_do_i_spend_most(engine):
    r = engine.explore(category="Restaurants", group_by="weekday", period="last_12_months")
    assert [x["label"] for x in r["rows"]] == WEEKDAYS
    assert round(sum(x["value"] for x in r["rows"]), 2) == r["totals"]["value"]
    # the same money the existing spending tool sees
    assert r["totals"]["total_eur"] == engine.spending("Restaurants", "last_12_months")["total_eur"]
    assert r["totals"]["highest"]["value"] == max(x["value"] for x in r["rows"])
    assert r["chart"]["type"] == "bar" and r["chart"]["values"] == [x["value"] for x in r["rows"]]


def test_month_buckets_are_complete_and_zero_filled(engine):
    r = engine.explore(category="Groceries", group_by="month")
    keys = [x["key"] for x in r["rows"]]
    assert keys[0] == "2025-10" and keys[-1] == "2026-09" and len(keys) == 12
    assert any(x["value"] == 0 for x in r["rows"])  # months without groceries are shown, not skipped
    assert r["chart"]["type"] == "bar" and engine.explore(group_by="week")["chart"]["type"] == "line"
    assert r["from"] == "2025-10-01" and r["to"] == "2026-09-30"


def test_metrics_count_and_average(engine):
    s = engine.explore(group_by="category", period="last_3_months")
    c = engine.explore(group_by="category", period="last_3_months", metric="count")
    a = engine.explore(group_by="category", period="last_3_months", metric="average")
    assert sum(x["value"] for x in c["rows"]) == c["totals"]["value"] == s["totals"]["transactions"]
    assert c["unit"] == "" and s["unit"] == "€"
    for row in a["rows"]:
        assert abs(row["value"] - row["total_eur"] / row["transactions"]) <= 0.006
    assert s["rows"] == sorted(s["rows"], key=lambda x: -x["value"])  # biggest first


def test_baskets_merchants_and_merchant_types_add_up(engine):
    total = engine.explore(group_by="month")["totals"]["total_eur"]
    for g in ("basket", "merchant", "merchant_type", "week"):
        r = engine.explore(group_by=g)
        assert r["totals"]["total_eur"] == total
        assert abs(sum(x["total_eur"] for x in r["rows"]) - total) < 0.05
    b = engine.explore(group_by="basket")
    assert [x["key"] for x in b["rows"]] == ["regular", "irregular", "everyday"]
    m = engine.explore(group_by="merchant")
    assert len(m["rows"]) == 11 and m["rows"][-1]["key"] == "other" and m["totals"]["highest"]["label"] != m["rows"][-1]["label"]


def test_filters_by_basket_merchant_type_dates_and_income(engine):
    b = engine.explore(basket="everyday", group_by="category")
    assert {x["key"] for x in b["rows"]} <= {"Restaurants", "Entertainment", "Lifestyle"}
    t = engine.explore(merchant_type="5812", group_by="month", metric="count")
    assert t["totals"]["value"] > 0 and t["title"] == "Number of payments · Spending at Restaurants by month"
    d = engine.explore(group_by="weekday", start="2026-09-01", end="2026-09-30")
    assert d["from"] == "2026-09-01" and d["to"] == "2026-09-30"
    i = engine.explore(what="income", group_by="month")
    assert i["totals"]["value"] > 0 and i["title"].startswith("Income")


def test_empty_view(engine):
    r = engine.explore(category="Travel", group_by="weekday", period="last_7_days")
    assert r["totals"]["value"] == 0 and r["totals"]["highest"] is None and len(r["rows"]) == 7


# ---------- the explore_my_data tool ----------
def test_tool_schema_only_takes_enums():
    t = next(t for t in TOOLS if t["name"] == "explore_my_data")
    props = t["input_schema"]["properties"]
    assert t["input_schema"]["additionalProperties"] is False
    for k in ("what", "category", "basket", "merchant_type", "group_by", "metric", "period"):
        assert "enum" in props[k], k


def test_tool_attaches_the_chart(engine):
    buttons = []
    args = {"what": "spent", "category": "Restaurants", "group_by": "weekday", "metric": "sum", "period": "last_12_months"}
    out = json.loads(run_tool(engine, "explore_my_data", args, buttons))
    assert out["rows"] and out["chart"]["labels"] == WEEKDAYS and "chart is drawn" in out["note"]
    assert buttons == [{"chart": out["chart"]}]
    run_tool(engine, "explore_my_data", args, buttons)
    assert len(buttons) == 1  # the same view twice is one chart
    with pytest.raises(ValueError):
        run_tool(engine, "explore_my_data", {**args, "group_by": "hour"}, [])


class ExploreClient:
    """Plays Claude: asks for a weekday chart, then quotes the busiest day's euros from the tool result."""
    def __init__(self):
        self.calls = 0
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="explore_my_data", input={
                "what": "spent", "category": "Restaurants", "group_by": "weekday", "metric": "sum",
                "period": "last_12_months"})])
        top = json.loads(kw["messages"][-1]["content"][0]["content"])["totals"]["highest"]
        return NS(stop_reason="end_turn", content=[NS(type="text", text=f"*{top['label']}*: {top['value']:.2f} €.")])


def test_coach_reply_carries_the_chart_and_passes_the_number_guard(engine):
    coach = Coach(engine, client=ExploreClient())
    r = coach.ask("u", "When do I spend most on restaurants?")
    assert r.tools_used == ["explore_my_data"] and r.buttons[0]["chart"]["type"] == "bar"
    assert r.text.startswith("*Saturday*") and coach.client.calls == 2  # no guard rewrite
    note = coach.notes["u"][-1]["note"]  # the tool JSON stays out of the history; its note keeps the figures
    assert note.startswith("explore_my_data: ") and ungrounded_numbers(r.text, [note]) == []


# ---------- web chat and WhatsApp ----------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    monkeypatch.setenv("PUBLIC_URL", "https://example.test")
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = Coach(main.engine, client=ExploreClient())
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    c = TestClient(main.app)
    c.sent = sent
    yield c
    main._coach = None


def test_web_chat_returns_the_chart_inline(client):
    j = client.post("/api/chat", json={"session": "x", "text": "When do I spend most on restaurants?"}).json()
    assert j["buttons"] == [] and len(j["charts"]) == 1
    assert j["charts"][0]["labels"] == WEEKDAYS and "url" not in j["charts"][0]
    chat = client.get("/chat").text
    assert "/static/kchart.js" in chat and "Open in Explore" not in chat
    assert client.get("/static/kchart.js").status_code == 200


def test_whatsapp_gets_the_words_without_a_chart_link(client):
    main.engine.state["connected"]["40700000000"] = {}
    main.handle_message({"from": "40700000000", "name": "Aino", "id": "m1", "text": "When do I spend most on restaurants?"})
    assert not [b for b in client.sent if b.get("type") == "interactive" and b["interactive"]["type"] == "cta_url"]
    assert "Saturday" in client.sent[-1]["text"]["body"]
