"""Merchant-type spending (MCC), safe to save, and a second bank linked through a mock PSD2 consent."""
import json
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import ROOT, Engine, merchant_codes
from app.guard import ungrounded_numbers
from app.tools import TOOLS, run_tool


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


# ---------- merchant type (MCC) ----------
def test_merchant_synonyms():
    assert merchant_codes("taxis")[1] == merchant_codes("Uber")[1] == merchant_codes("4121")[1] == ["4121"]
    assert merchant_codes("coffee")[1] == ["5814", "5812"]
    assert merchant_codes("parking")[0] == "Parking"
    with pytest.raises(ValueError):
        merchant_codes("spaceship")


def test_taxis_last_month_matches_pandas(e):
    raw = pd.read_csv(ROOT / "data" / "persona.csv", dtype={"mcc_code": "string"})
    sep = raw[(raw.tx_date >= "2026-09-01") & (raw.tx_date <= "2026-09-30") & (raw.mcc_code == "4121")]
    out = e.spending(None, "last_month", merchant_type="taxi")
    assert out["found"] and out["total_eur"] == round(-sep.tx_amount_eur.sum(), 2) and out["transactions"] == len(sep)
    assert out["within_category"] == "Transportation"
    split = out["category_by_merchant_type"]
    assert split["Taxis and ride-hailing"] == out["total_eur"]
    assert round(sum(split.values()), 2) == e.spending("Transportation", "last_month")["total_eur"]


def test_no_taxis_says_where_transport_went(e):
    out = e.spending(None, start="2026-01-01", end="2026-01-31", merchant_type="taxi")
    assert not out["found"] and out["total_eur"] == 0 and out["transactions"] == 0
    assert "Parking" in out["category_by_merchant_type"] and "No taxis" in out["note"]
    assert "Taxis and ride-hailing" not in out["category_by_merchant_type"]


def test_category_split_by_merchant_type(e):
    out = e.spending("Restaurants", "last_month")
    assert round(sum(out["by_merchant_type"].values()), 2) == out["total_eur"]


def test_spending_tool_takes_merchant_type(e):
    props = next(t for t in TOOLS if t["name"] == "get_spending")["input_schema"]["properties"]
    assert "merchant_type" in props
    out = json.loads(run_tool(e, "get_spending", {"category": None, "period": "last_month", "merchant_type": "taxis"}, []))
    assert out["merchant_type"] == "Taxis and ride-hailing"


# ---------- safe to save ----------
def test_safe_to_save_honest_when_no_surplus(e):
    s = e.safe_to_save()
    safe = e.safe_to_spend()
    assert s["now_max_eur"] == max(0.0, safe["safe_to_spend_until_payday_eur"])
    assert s["now_keeping_usual_fun_eur"] <= s["now_max_eur"]
    m = s["monthly"]
    assert m["surplus_per_month_eur"] == e.monthly_flow()["left_per_month_eur"] < 0
    assert m["sustainable_saving_per_month_eur"] == 0
    cuts = s["where_a_cut_could_come_from"]
    assert cuts[0] == {"category": "Restaurants", "cut_pct": 20,
                       "saving_per_month_eur": e.simulate_cut("Restaurants", 20)["saving_per_month_eur"]}


def test_safe_to_save_positive_surplus(e, monkeypatch):
    monkeypatch.setattr(e, "monthly_flow", lambda months=3: {"income_per_month_eur": 2000.0, "spending_per_month_eur": 1500.0,
                                                             "investing_per_month_eur": 100.0, "left_per_month_eur": 400.5})
    s = e.safe_to_save()
    assert s["monthly"]["sustainable_saving_per_month_eur"] == 400 and "where_a_cut_could_come_from" not in s


def test_safe_to_save_links_to_saving(e):
    e.create_goal("Laptop", 900)
    out = json.loads(run_tool(e, "get_safe_to_save", {}, []))
    assert out["goals"][0]["goal_id"] == "laptop" and "create_saving_link" in out["how_to_act"]
    buttons = []
    run_tool(e, "create_saving_link", {"goal_id": "laptop", "amount_eur": out["now_max_eur"]}, buttons)
    assert "/s-mobiili/?ready=save:" in buttons[0]["url"]


# ---------- multi-bank (mock PSD2) ----------
def test_full_picture_unlinked(e):
    f = e.full_picture()
    assert not f["linked"] and f["net_position_eur"] == e.balance() and "how_to_link" in f


def test_link_bank_full_picture(e):
    before = e.safe_to_spend()
    f = e.link_bank()
    bank = e.state["linked_banks"]["demo"]
    assert bank["synthetic"] and "Synthetic" in f["note"]
    assert f["total_balances_eur"] == round(e.balance() + bank["savings_eur"], 2)
    assert f["net_position_eur"] == round(f["total_balances_eur"] - f["card_debt_eur"], 2)
    bill = f["next_card_bill"]
    sept = -sum(t["amount_eur"] for t in bank["card_transactions"] if t["date"].startswith("2026-09"))
    assert bill["due_date"] == "2026-10-12" and bill["amount_eur"] == round(sept, 2)
    assert f["savings_cover_irregular_months"] == round(bank["savings_eur"] / f["irregular_basket_per_month_eur"], 1)
    # The card bill is due before payday (15 Oct), so safe to spend and safe to save count it.
    after = e.safe_to_spend()
    assert after["bills_total_eur"] == round(before["bills_total_eur"] + bill["amount_eur"], 2)
    assert after["safe_to_spend_until_payday_eur"] == pytest.approx(
        before["safe_to_spend_until_payday_eur"] - bill["amount_eur"], abs=0.02)
    assert e.safe_to_save()["monthly"]["linked_card_spending_per_month_eur"] == e.card_spend_per_month() > 0


def test_card_bill_paid_with_pin(e):
    e.link_bank()
    bill = next(b for b in e.upcoming_bills(14) if b["category"] == "Credit card")
    debt = e.full_picture()["card_debt_eur"]
    p = e.create_payment(bill["bill_id"])
    e.confirm(p["pending_id"])
    assert not any(b["category"] == "Credit card" for b in e.upcoming_bills(14))
    assert e.full_picture()["card_debt_eur"] == round(debt - bill["amount_eur"], 2)
    assert e.card_bill()["due_date"] == "2026-11-12"


def test_link_flow_dashboard_and_reset(client):
    assert 'href="/connect/bank' in client.get("/connect").text
    page = client.get("/connect/bank").text
    assert "Demo Bank" in page and "synthetic" not in page
    assert "Add another bank" in client.get("/app").text
    r = client.post("/connect/bank", data={"u": "", "scope": ["accounts", "transactions"]})
    assert "Demo Bank linked" in r.text and main.engine.linked_bank()
    dash = client.get("/app").text
    assert "net, after card debt" in dash and "Card bill" in dash
    assert "Demo Bank" in client.get("/connect").text
    client.post("/demo/reset")
    assert main.engine.linked_bank() is None and "Add another bank" in client.get("/app").text


def test_link_needs_accounts_scope(client):
    r = client.post("/connect/bank", data={"u": "", "scope": ["transactions"]}, follow_redirects=False)
    assert r.status_code == 303 and main.engine.linked_bank() is None


# ---------- the number guard still passes on typical replies ----------
def test_guard_passes_typical_replies(e):
    taxi = run_tool(e, "get_spending", {"category": None, "period": "last_month", "merchant_type": "taxi"}, [])
    save = run_tool(e, "get_safe_to_save", {}, [])
    e.link_bank()
    full = run_tool(e, "get_full_picture", {}, [])
    t, s, f = json.loads(taxi), json.loads(save), json.loads(full)
    replies = [
        f"You spent *{t['total_eur']:.2f} €* on taxis last month, {t['transactions']} ride. "
        f"Parking was {t['category_by_merchant_type']['Parking']:.2f} €.",
        f"Right now you could move up to *{s['now_max_eur']:.2f} €* without touching rent. Your last 3 months left "
        f"{s['monthly']['surplus_per_month_eur']:.2f} € a month, so a steady saving needs a cut: dining out −20% frees "
        f"{s['where_a_cut_could_come_from'][0]['saving_per_month_eur']:.2f} € a month.",
        f"Across both banks you have {f['total_balances_eur']:,.2f} €, {f['net_position_eur']:,.2f} € after the card. "
        f"Your Demo Bank savings could cover {f['savings_cover_irregular_months']} months of the irregular basket; the "
        f"card bill of {f['next_card_bill']['amount_eur']:.2f} € is due on 12 October.",
    ]
    for reply, src in zip(replies, [taxi, save, full]):
        assert ungrounded_numbers(reply, [src]) == [], reply
