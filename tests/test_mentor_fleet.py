"""The three mentor questions on customers unlike the demo personas: each issue class scripts/fleet_check_mentor.py
found across the 5,100 customers, rebuilt from a small synthetic history (or a persona already in data/personas)."""
import json
from datetime import date
from pathlib import Path

import pytest

from app import main
from app.engine import Engine, add_months, on_day
from app.peers import MIN_GROUP
from scripts.fleet_check_mentor import mentor_calls, mentor_issues, swept
from tests.test_fleet import TODAY, frame, run_all

ROOT = Path(__file__).resolve().parents[1]


def rows(months=12, salary=2500.0, rent=800.0, rent_day=7, groceries=75.0, restaurants=40.0, sweep=0.0,
         utilities=0.0, weekend_visits=0):
    """(date, category, amount, type, merchant, mcc) rows like the dataset, oldest first."""
    out = []
    first = add_months(date(2026, 10, 1), -months)
    for i in range(months):
        m = add_months(first, i)
        if salary:
            out.append((on_day(m, 15), "Salary", salary, "salary", None, None))
            if sweep:
                out.append((on_day(m, 16), "Salary Sweep", -sweep, "salary_sweep", None, None))
        if rent:
            out.append((on_day(m, rent_day), "Housing", -rent, "housing_payment", None, None))
        if utilities:
            out.append((on_day(m, 20), "Utilities", -utilities, "utilities_payment", "MRC-UTI-0001", None))
        for d in (3, 10, 17, 24):
            out.append((m.replace(day=d), "Groceries", -groceries, "purchase", "MRC-GRO-0001", "5411"))
        if restaurants:
            out.append((m.replace(day=21), "Restaurants", -restaurants, "purchase", "MRC-RES-0002", "5812"))
        for k in range(weekend_visits):  # Saturdays
            sat = m.replace(day=1)
            while sat.weekday() != 5:
                sat = sat.replace(day=sat.day + 1)
            out.append((sat.replace(day=sat.day + 7 * k), "Restaurants", -30.0, "purchase", "MRC-RES-0003", "5812"))
    return out


def eng(tmp_path, r, start=3000.0, age=35, name="s.json"):
    return Engine(today=TODAY, state_path=tmp_path / name, frame=frame(r, start), age=age)


def mentor(e) -> dict:
    """The mentor calls, checked by the same rules as the fleet script: none of them may flag anything."""
    out = {name: fn(e) for name, fn in mentor_calls(e)}
    assert mentor_issues(out, e.age, swept(e), e.monthly_income(6)) == []
    return out


# ---------- safe to spend ----------

def test_bills_above_the_balance_never_give_a_negative_safe_to_spend(tmp_path):
    e = eng(tmp_path, rows(rent=2400.0, salary=2800.0), start=200.0)  # rent due on the 7th, tomorrow
    out = mentor(e)
    s = out["safe_to_spend"]
    assert s["left_after_bills_eur"] < 0 < s["balance_eur"]
    assert s["safe_to_spend_until_payday_eur"] == s["safe_per_day_eur"] == s["safe_this_week_eur"] == 0.0
    assert s["status"] == "nothing_free" and not s["overdrawn"]
    assert s["short_by_eur"] == pytest.approx(-s["left_after_bills_eur"], abs=0.01)
    assert f"{s['short_by_eur']:.2f} EUR more than the balance" in s["basis"]
    assert run_all(e)["afford"]["fits_safe_to_spend"] is False


def test_overdrawn_account_is_flagged_and_safe_is_zero(tmp_path):
    r = rows(salary=3000.0, sweep=2950.0)
    e = eng(tmp_path, r, start=-1800.0)  # the rows end overdrawn
    assert e.balance() < 0
    out = mentor(e)
    s = out["safe_to_spend"]
    assert s["overdrawn"] and s["status"] == "overdrawn" and s["safe_to_spend_until_payday_eur"] == 0.0
    assert "overdrawn" in s["basis"]
    ss, sm = out["safe_to_save"], out["save_more"]
    assert ss["now_max_eur"] == 0.0 and ss["overdrawn"] and ss["overdrawn_note"]
    assert "pay_yourself_first" not in [i["idea"] for i in sm["ideas"]] and sm["overdrawn_note"]


def test_no_regular_salary_gives_a_7_day_estimate_from_balance_minus_bills_due(tmp_path):
    e = eng(tmp_path, rows(salary=0.0, rent=600.0, rent_day=10), start=12180.0)
    out = mentor(e)
    s = out["safe_to_spend"]
    assert not s["payday_known"] and s["horizon_days"] == 30  # the total keeps the 30-day horizon
    week = s["next_7_days"]
    assert week["bills_due_eur"] == 600.0  # rent on the 10th is inside the week
    assert s["balance_eur"] == 900.0
    assert week["left_eur"] == pytest.approx(900.0 - 600.0 - week["usual_needs_and_buffer_eur"], abs=0.02)
    assert 0 <= s["safe_this_week_eur"] <= max(0.0, week["left_eur"])
    assert "can't be worked out until a payday" in s["not_enough_data"] and "next_7_days" in s["not_enough_data"]


def test_no_salary_week_never_exceeds_what_the_week_leaves(tmp_path):
    # A big other income keeps the 30-day amount high, but rent tomorrow eats this week's money.
    r = rows(salary=0.0, rent=1500.0) + [(date(2026, m, 25), "Other Income", 4000.0, "other_income", None, None)
                                         for m in range(4, 10)]
    s = mentor(eng(tmp_path, r, start=1600.0))["safe_to_spend"]
    assert s["safe_this_week_eur"] <= max(0.0, s["next_7_days"]["left_eur"])


# ---------- salary sweeps ----------

def test_salary_swept_to_another_account_is_not_this_cycles_money(tmp_path):
    e = eng(tmp_path, rows(salary=3000.0, sweep=2700.0, rent=0.0), start=500.0)
    out = mentor(e)
    s = out["safe_to_spend"]
    assert s["moved_to_other_account_per_month_eur"] == 2700.0
    assert s["income_per_month_eur"] == 300.0  # what stays on the account, not the 3,000 salary
    flow = out["safe_to_save"]["monthly"]
    assert flow["moved_to_other_account_per_month_eur"] == 2700.0
    assert flow["left_per_month_eur"] == pytest.approx(3000 - 2700 - 300 - 40, abs=0.01)
    sm = out["save_more"]
    assert all(i.get("per_month_eur", 0) < 300 for i in sm["ideas"] if i["idea"] == "pay_yourself_first")
    assert sm["moved_note"] and sm["monthly_picture"]["moved_to_other_account_per_month_eur"] == 2700.0
    assert out["spender_profile"]["kind"]["key"] == "salary_mover"
    assert "90% of your salary" in out["spender_profile"]["traits"][0]


# ---------- save more ----------

def test_nothing_to_cut_points_to_regular_payments_or_the_buffer(tmp_path):
    e = eng(tmp_path, rows(restaurants=2.0, utilities=60.0), start=6000.0)  # 2 EUR a month of eating out
    out = mentor(e)
    sm = out["save_more"]
    kinds = [i["idea"] for i in sm["ideas"]]
    assert "biggest_lever" not in kinds and kinds  # still something to say
    assert set(kinds) <= {"move_what_built_up", "pay_yourself_first", "review_payment"}
    assert "nothing worth cutting" in sm["no_lever_reason"] and "2.00 EUR a month" in sm["no_lever_reason"]
    assert "20% lighter" not in sm["next_step"]


def test_no_lever_and_no_reserve_offers_the_regular_payment_review(tmp_path):
    e = eng(tmp_path, rows(salary=1500.0, rent=900.0, restaurants=0.0, utilities=80.0), start=-2540.0)
    sm = mentor(e)["save_more"]
    assert "biggest_lever" not in [i["idea"] for i in sm["ideas"]]
    assert "review_payment" in [i["idea"] for i in sm["ideas"]] and "check the utility bills" in sm["next_step"]


def test_no_surplus_and_nothing_to_cut_says_so(tmp_path):
    e = eng(tmp_path, rows(salary=1100.0, rent=800.0, restaurants=0.0), start=100.0)
    out = mentor(e)
    ss, sm = out["safe_to_save"], out["save_more"]
    assert ss["where_a_cut_could_come_from"] == [] and "next to no eating out" in ss["note"]
    # nothing to cut, nothing built up, no surplus, no bill to review: the buffer is the one idea
    assert [i["idea"] for i in sm["ideas"]] == ["start_a_buffer"]
    assert sm["ideas"][0]["target_eur"] == round(out["safe_to_spend"]["mandatory_costs_per_month_eur"])
    assert "Emergency buffer" in sm["next_step"]


# ---------- what kind of spender ----------

@pytest.mark.parametrize("kw,key", [
    ({"restaurants": 0.0}, "essentials_first"),
    ({"restaurants": 0.0, "rent": 0.0, "groceries": 0.0}, "quiet"),
])
def test_every_customer_gets_a_kind_of_spender(tmp_path, kw, key):
    p = mentor(eng(tmp_path, rows(**kw)))["spender_profile"]
    assert p["kind"]["key"] == key and p["traits"] == [p["kind"]["in_words"]]


def test_one_off_and_balanced_spenders(tmp_path):
    big = [(date(2026, m, 12), "Home & Tech", -450.0, "purchase", "MRC-TEC-0001", "5732") for m in range(7, 10)]
    p = mentor(eng(tmp_path, rows(rent=300.0) + big))["spender_profile"]
    assert p["kind"]["key"] == "one_offs" and "health, home & tech and travel" in p["kind"]["in_words"]
    # 600 essentials, 250 one-offs, 290 eating out and fun a month: no basket dominates
    mix = [(date(2026, m, d), c, -250.0, "purchase", mid, mcc) for m in range(7, 10)
           for d, c, mid, mcc in ((5, "Entertainment", "MRC-ENT-0001", "7832"), (12, "Home & Tech", "MRC-TEC-0001", "5732"))]
    p = mentor(eng(tmp_path, rows(rent=300.0) + mix, name="b.json"))["spender_profile"]
    assert p["kind"]["key"] == "balanced" and "no basket dominates" in p["kind"]["in_words"]


def test_weekend_effect_needs_more_than_one_visit(tmp_path):
    one = [(date(2026, 9, 5), "Restaurants", -30.0, "purchase", "MRC-RES-0003", "5812")]  # a Saturday
    p = mentor(eng(tmp_path, rows(restaurants=0.0) + one))["spender_profile"]
    assert p["weekend_restaurant_share_pct"] == 100 and not any(t.startswith("Weekend") for t in p["traits"])
    p = mentor(eng(tmp_path, rows(restaurants=0.0, weekend_visits=2), name="b.json"))["spender_profile"]
    assert any(t.startswith("Weekend effect") for t in p["traits"])


def test_unknown_age_is_never_compared_with_the_demo_personas_age_group(tmp_path):
    e = eng(tmp_path, rows(), age=None)
    assert e.age is None and e.benchmark is None
    p = mentor(e)["spender_profile"]["peers"]
    assert p["group"] is None and p["customers"] is None and "No age on file" in p["no_peers_reason"]
    assert Engine(today=TODAY, state_path=tmp_path / "a.json").age == 24  # the default persona keeps hers


# ---------- real customers already extracted ----------

PERSONAS = json.loads((ROOT / "data" / "personas" / "index.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("pid", [p["id"] for p in PERSONAS] + [main.AINO])
def test_personas_pass_every_mentor_check_with_peer_groups_of_30_or_more(tmp_path, pid):
    e = main.persona_engine(pid, TODAY, tmp_path / f"{pid}.json")
    out = mentor(e)
    peers = out["spender_profile"]["peers"]
    assert peers["group"] and peers["customers"] >= MIN_GROUP
    assert out["save_more"]["ideas"] and out["spender_profile"]["kind"]["in_words"]


def test_antti_and_aino_mentor_numbers_unchanged(tmp_path):
    antti = main.persona_engine("CUST01656", TODAY, tmp_path / "a.json")
    s = antti.safe_to_spend()
    assert (s["safe_to_spend_until_payday_eur"], s["safe_this_week_eur"], s["reserve_eur"]) == (235.68, 183.31, 6314.43)
    assert [(i["idea"], i[i["number_to_say"]]) for i in antti.save_more()["ideas"]] == [
        ("biggest_lever", 1224.5), ("move_what_built_up", 6314.43), ("review_payment", 2476.44)]
    p = antti.spender_profile()
    assert p["kind"]["key"] == "experience" and p["peers"]["customers"] == 228 and len(p["traits"]) == 2
    aino = main.persona_engine(main.AINO, TODAY, tmp_path / "b.json")
    s = aino.safe_to_spend()
    assert (s["safe_to_spend_until_payday_eur"], s["safe_this_week_eur"], s["reserve_eur"]) == (68.6, 53.36, 0.0)
    assert [(i["idea"], i[i["number_to_say"]]) for i in aino.save_more()["ideas"]] == [
        ("biggest_lever", 1166.16), ("review_payment", 1471.8)]
    p = aino.spender_profile()
    assert p["peers"]["customers"] == 535 and len(p["traits"]) == 3 and p["savings_rate_pct"] == 22


def test_fleet_summary_is_committed_and_clean():
    doc = json.loads((ROOT / "docs" / "fleet-check-mentor.json").read_text(encoding="utf-8"))
    assert doc["before"]["customers"] == doc["after"]["customers"] == 5100
    assert doc["after"]["customers_with_exceptions"] == 0
    assert doc["after"]["smallest_peer_group_used"] >= MIN_GROUP
    unexplained = {k for k in doc["after"]["issues"] if k not in doc.get("explained_edge_cases", {})}
    assert not unexplained, unexplained
