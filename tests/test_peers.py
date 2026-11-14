"""'People like you': the peer benchmark builder (n >= 30 only), fallbacks, the engine numbers for the demo persona and
the get_people_like_me tool."""
import copy
import csv
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from app import peers
from app.engine import BASKETS, Engine
from app.explain import explain
from app.tools import TOOLS, run_tool

ROOT = Path(__file__).resolve().parents[1]
BENCH = json.loads((ROOT / "data" / "peer_benchmark.json").read_text(encoding="utf-8"))
spec = importlib.util.spec_from_file_location("build_peer_benchmark", ROOT / "scripts" / "build_peer_benchmark.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


# ---------- builder ----------
def write_dataset(d: Path, people: list[tuple[str, str, int, str]]):
    """people: (customer_id, postal code, age, first month 'YYYY-MM'). Every customer: rent 700, groceries 300,
    a 20 € taxi, salary 2000 a month, Jan 2025 to Jan 2026 (so the window is Feb 2025 to Jan 2026)."""
    with open(d / "tx_customers_junction.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "customer_postal_code", "customer_age"])
        w.writerows((c, pc, age) for c, pc, age, _ in people)
    months = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01"]
    with open(d / "tx_junction_transactions.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "bank_account_id", "tx_date", "tx_timestamp", "category", "tx_amount_eur",
                    "balance_after_eur", "transaction_type", "merchant_id", "mcc_code"])
        for i, (c, _, _, start) in enumerate(people):
            for m in months:
                if m < start:
                    continue
                rent = -700 - i  # different amounts, so quantiles are not all equal
                w.writerow([c, "A", f"{m}-05", "", "Housing", rent, 0, "housing_payment", "", ""])
                w.writerow([c, "A", f"{m}-10", "", "Groceries", -300, 0, "purchase", "MRC-GRO-0001", "5411"])
                w.writerow([c, "A", f"{m}-12", "", "Transportation", -20, 0, "purchase", "MRC-TRN-0001", "4121"])
                w.writerow([c, "A", f"{m}-15", "", "Salary", 2000, 0, "salary", "", ""])
            w.writerow([c, "A", "2026-01-31", "", "Groceries", -1, 0, "purchase", "", "5411"])


@pytest.fixture
def built(tmp_path):
    people = ([(f"H{i:03d}", "00100", 26, "2025-01") for i in range(40)]     # Helsinki 25-29: 40
              + [(f"E{i:03d}", "02780", 27, "2025-01") for i in range(10)]   # Espoo 25-29: 10, too few
              + [(f"V{i:03d}", "01300", 45, "2025-01") for i in range(29)]   # Vantaa 40-49: 29, one short
              + [("LATE", "00100", 26, "2025-08")])                          # joined mid-window
    write_dataset(tmp_path, people)
    bench, areas = builder.build(tmp_path, ["E000", "NOBODY"])
    return bench, areas


def test_builder_shape(built):
    bench, areas = built
    assert bench["period"] == {"from": "2025-02", "to": "2026-01", "months": 12}
    assert bench["min_group_size"] == 30 and bench["customers"] == 80
    g = bench["groups"]["area=00|age=25-29"]
    assert g["n"] == 41 and g["area"] == "00" and g["age_band"] == "25-29"
    assert set(g["metrics"]) == set(peers.METRICS)
    for q in g["metrics"].values():
        assert q["p25"] <= q["median"] <= q["p75"]
    m = g["metrics"]
    assert m["Groceries"]["median"] == pytest.approx(300 + 1 / 12, abs=0.01)  # the last day's 1 € counts
    assert m["taxi"]["median"] == 20 and m["Transportation"]["median"] == 20 and m["salary"]["median"] == 2000
    assert m["regular"]["median"] == pytest.approx(m["Housing"]["median"] + 300 + 20 + 1 / 12, abs=0.02)
    assert m["Restaurants"]["median"] == 0
    assert areas["customers"] == {"E000": {"area": "02", "age": 27}}


def test_builder_divides_by_the_months_a_customer_has(tmp_path):
    write_dataset(tmp_path, [("LATE", "00100", 26, "2025-08")])
    pc, months = builder.per_customer(tmp_path / "tx_junction_transactions.csv")
    r = pc.set_index("customer_id").loc["LATE"]
    assert r.months_covered == 6 and r.Groceries == pytest.approx(300 + 1 / 6) and r.salary == 2000


def test_groups_under_30_never_appear(built):
    bench, _ = built
    g = bench["groups"]
    assert all(v["n"] >= 30 for v in g.values())
    for key in ("area=02|age=25-29", "area=02", "area=02|age=18-29", "area=01", "area=01|age=40-49", "age=40-49"):
        assert key not in g
    assert g["age=25-29"]["n"] == 51 and g["all"]["n"] == 80 and g["area=00"]["n"] == 41
    assert "starting_out" in g and g["starting_out"]["n"] == 50  # the late joiner has 6 salary months, not 10
    text = json.dumps(bench)
    assert "E000" not in text and "H001" not in text  # no customer ids in the benchmark


def test_real_benchmark_respects_the_rule():
    assert BENCH["min_group_size"] == 30 and BENCH["customers"] == 5100
    assert all(g["n"] >= 30 for g in BENCH["groups"].values())
    assert set(BENCH["areas"]) == {"00", "01", "02"} and BENCH["areas"]["02"]["name"] == "Espoo area"
    assert BENCH["period"] == {"from": "2025-10", "to": "2026-09", "months": 12}
    assert "CUST" not in json.dumps(BENCH)


# ---------- fallbacks ----------
def small(*drop):
    b = copy.deepcopy(BENCH)
    for k in drop:
        b["groups"].pop(k)
    return b


def test_own_group_first():
    g = peers.pick_group(BENCH, "02", 24)
    assert g["key"] == "area=02|age=18-24" and g["fallback_reason"] is None


def test_narrow_band_falls_back_to_wider_band():
    g = peers.pick_group(small("area=02|age=18-24"), "02", 24)
    assert g["key"] == "area=02|age=18-29"
    assert g["fallback_reason"] == ("Fewer than 30 S-Pankki customers are customers aged 18-24 in the Espoo area, "
                                    "so this uses customers aged 18-29 in the Espoo area instead.")


def test_area_falls_back_to_whole_region_then_everyone():
    g = peers.pick_group(small("area=02|age=18-24", "area=02|age=18-29"), "02", 24)
    assert g["key"] == "age=18-24" and "across the capital region" in g["fallback_reason"]
    b = small(*[k for k in BENCH["groups"] if k != "all"])
    g = peers.pick_group(b, "02", 24)
    assert g["key"] == "all" and g["fallback_reason"].startswith("Fewer than 30")
    with pytest.raises(ValueError):
        peers.pick_group({"groups": {"all": {**BENCH["groups"]["all"], "n": 29}}}, None, None)


def test_starting_out_group_and_its_fallback():
    assert peers.pick_group(BENCH, "00", 24, starting_out=True)["key"] == "starting_out|area=00"
    assert peers.pick_group(small("starting_out|area=00"), "00", 24, True)["key"] == "starting_out"


@pytest.mark.parametrize("text,code", [("Espoo", "02"), ("espoossa", "02"), ("Helsinki", "00"), ("HELSINGISSÄ", "00"),
                                       ("Vantaa", "01"), ("02780", "02"), ("00", "00"), ("Kauniainen", "02"),
                                       (None, None), ("", None)])
def test_area_words(text, code):
    assert peers.area_code(text) == code


def test_area_outside_the_data():
    with pytest.raises(ValueError, match="capital region"):
        peers.area_code("Tampere")


# ---------- engine: the demo persona (CUST04831, 24, postal area 02) ----------
def test_persona_budget(e):
    r = e.new_life_budget()
    assert r["group"] == {"who": "customers aged 18-24 in the Espoo area", "area": "Espoo area", "age_band": "18-24",
                          "starting_out": False, "customers": 128}
    assert r["fallback_reason"] is None and r["asked_for"]["what_if"] is False
    assert "at least 30 S-Pankki customers" in r["privacy"]
    gm = BENCH["groups"]["area=02|age=18-24"]["metrics"]
    assert r["typical_month"]["total"]["median_eur"] == gm["total"]["median"] == 1865.2
    assert [b["key"] for b in r["typical_month"]["baskets"]] == ["regular", "irregular", "everyday"]
    assert r["heaviest_cost_for_most"]["category"] == "Housing"
    rows = {x["key"]: x for x in r["you"]["comparison"]}
    assert rows["total"]["you_per_month_eur"] == 1923.0 and rows["total"]["where_you_are"] == "in line"
    for c in ("Housing", "Groceries", "Restaurants"):
        assert rows[c]["you_per_month_eur"] == e.monthly_avg(c, 12)
    assert rows["everyday"]["you_per_month_eur"] == pytest.approx(
        sum(e.monthly_avg(c, 12) for c in BASKETS["everyday"]["categories"]), abs=0.02)
    assert rows["Housing"]["where_you_are"] == "in line"
    assert rows["Groceries"]["where_you_are"] == "below"
    assert rows["Restaurants"]["where_you_are"] == "a bit above"
    assert rows["Utilities"]["where_you_are"] == "in line"  # 1.12 € past p75 is still in line (5% slack)
    assert set(r["you"]["in_line"]) & {"Housing", "Spending in total"} == {"Housing", "Spending in total"}
    assert {x["where_you_are"] for x in rows.values()} <= {"in line", "a bit above", "below"}
    assert r["you"]["income_per_month_eur"] == e.monthly_income(12)


def test_peers_alias(e):
    assert e.peers() == e.new_life_budget()


def test_what_if_another_area_or_age(e):
    r = e.new_life_budget(area="Helsinki")
    assert r["group"]["who"] == "customers aged 18-24 in Helsinki" and r["asked_for"]["what_if"]
    assert r["you"] is None and "what-if" in r["note"]
    assert e.new_life_budget(age=27)["group"]["age_band"] == "25-29"
    assert e.new_life_budget(area="Espoo")["you"] is not None  # own area is not a what-if
    r = e.new_life_budget(area="Espoo", starting_out=True)
    assert r["group"]["starting_out"] and r["group"]["customers"] == BENCH["groups"]["starting_out|area=02"]["n"]
    with pytest.raises(ValueError):
        e.new_life_budget(age=12)


def test_other_personas_use_their_own_area(tmp_path):
    p = next(x for x in json.loads((ROOT / "data" / "personas" / "index.json").read_text()) if x["id"] == "CUST05042")
    e = Engine(csv_path=ROOT / "data" / "personas" / p["file"], today=date(2026, 10, 6),
               state_path=tmp_path / "m.json", age=p["age"])
    assert e.new_life_budget()["group"]["who"] == "customers aged 40-49 in Helsinki"


def test_fallback_reaches_the_tool_result(e):
    r = peers.new_life_budget(e, bench=small("area=02|age=18-24"))
    assert r["group"]["age_band"] == "18-29" and r["fallback_reason"].startswith("Fewer than 30")


# ---------- tool ----------
def test_tool(e):
    t = next(t for t in TOOLS if t["name"] == "get_people_like_me")
    assert set(t["input_schema"]["required"]) == {"area", "age", "starting_out"}
    out = json.loads(run_tool(e, "get_people_like_me", {"area": None, "age": None, "starting_out": False}, []))
    assert out["group"]["customers"] == 128 and out["you"]["comparison"]
    assert "never one person" in out["say"] and "at least 30" in out["say"]
    out = json.loads(run_tool(e, "get_people_like_me", {"area": "Tampere", "age": None, "starting_out": False}, []))
    assert "capital region" in out["area_not_covered"] and out["group"]["area"] == "Espoo area"
    out = json.loads(run_tool(e, "get_people_like_me", {"area": "Vantaa", "age": 26, "starting_out": False}, []))
    assert out["group"]["who"] == "customers aged 25-29 in the Vantaa area" and out["you"] is None


def test_tool_explained(e):
    out = json.loads(run_tool(e, "get_people_like_me", {"area": None, "age": None, "starting_out": False}, []))
    s = explain("get_people_like_me", {}, out)
    assert s["step"] == "Looked up how customers aged 18-24 in the Espoo area live"
    assert {f["label"]: f["value"] for f in s["figures"]} == {
        "Typical month": "1 865.20 €", "Your month": "1 923.00 €", "Customers in the group": "128"}
    assert any("at least 30" in x for x in s["sources"])


def test_prompt_rule():
    from app.agent import SYSTEM
    assert "get_people_like_me" in SYSTEM and "never to shame" in SYSTEM and "at least 30" in SYSTEM
