"""Regular commitments found in the customer's own transactions: rent, utility bills, monthly investing, salary.
The persona's real numbers, then small made-up frames for the cases the persona doesn't have."""
import json
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main, rules, tools
from app.engine import Engine, add_months
from app.tools import no_subscriptions_answer, run_tool

TODAY = date(2026, 10, 6)


@pytest.fixture
def e(tmp_path):
    return Engine(today=TODAY, state_path=tmp_path / "s.json")


def by_id(c: dict) -> dict:
    return {x["id"]: x for x in c["regular_payments"]}


# ---------------- the demo persona, CUST04831 ----------------
def test_persona_rent(e):
    raw = pd.read_csv(e.csv_path)
    rent_rows = raw[raw.transaction_type == "housing_payment"]
    assert len(rent_rows) == 24 and round(-rent_rows.tx_amount_eur.mean()) == 776
    r = by_id(e.commitments())["rent"]
    assert r["status"] == "active" and r["per_month_eur"] == 771.82 and r["per_year_eur"] == round(771.82 * 12, 2)
    assert r["usual_day"] == 7 and r["next_expected"] == "2026-10-07" and r["days_to_next"] == 1
    assert r["last_payment"] == {"date": "2026-09-08", "amount_eur": 765.22}
    assert r["trend"]["direction"] == "about the same"
    assert (r["trend"]["previous_per_month_eur"], r["trend"]["last_per_month_eur"]) == (770.33, 771.82)
    assert r["actions"] == ["remind", "pay"]
    rent_bill = next(b for b in e.upcoming_bills(14) if b["name"] == "Rent")
    assert (rent_bill["amount_eur"], rent_bill["due_date"]) == (r["per_month_eur"], r["next_expected"])  # one story


def test_persona_utilities(e):
    u = by_id(e.commitments())["utilities"]
    assert u["status"] == "active" and u["bills_per_month"] == 3.0
    assert u["per_month_eur"] == 122.65 and u["per_year_eur"] == round(122.65 * 12, 2)
    assert u["fixed_days"] is None and u["usual_day"] is None and u["next_expected"] is None  # no fixed dates
    assert "different days" in u["note"]
    assert u["one_bill_usual_range_eur"] == {"low": 19.94, "high": 60.09}
    assert u["unusual_bill"]["date"] == "2026-08-28" and u["unusual_bill"]["amount_eur"] == 75.0
    # +11 % over the 3 months before is inside the usual swing of a noisy monthly total: not called a rise
    assert u["trend"]["change_pct"] == 11 and u["trend"]["direction"] == "about the same"
    assert u["actions"] == ["watch"]


def test_persona_investing_and_salary(e):
    c = by_id(e.commitments())
    inv, sal = c["investing"], c["salary"]
    assert inv["status"] == "active" and inv["usual_day"] == 18 and inv["next_expected"] == "2026-10-18"
    assert inv["per_month_eur"] == 497.84 and 434 < inv["range_last_6_eur"]["low"] < inv["range_last_6_eur"]["high"] < 532
    assert inv["actions"] == ["pause"] and inv["basket"].startswith("Saving")
    assert sal["direction"] == "in" and sal["usual_day"] == 15 and sal["next_expected"] == "2026-10-15"
    assert sal["next_expected"] == e.next_payday()["date"]


def test_persona_has_no_subscriptions(e):
    c = e.commitments()
    assert c["subscriptions_found"] == 0 and c["card_subscriptions"] == []
    assert c["bills_per_month_eur"] == round(771.82 + 122.65, 2)
    assert c["outgoing_per_month_eur"] == round(771.82 + 122.65 + 497.84, 2)
    assert c["income_per_month_eur"] == 2313.39
    assert any("paused" in line for line in c["what_can_be_done"])


def test_tool_result_is_json_and_grounded(e):
    out = json.loads(run_tool(e, "get_regular_payments", {}, []))
    assert out["if_asked_to_cancel_subscriptions"] == no_subscriptions_answer(e.commitments())
    assert "lesson" in out and "894.47" in out["lesson"]


# ---------------- made-up customers ----------------
def frame(rows: list[tuple]) -> pd.DataFrame:
    """(date, transaction_type, category, amount, merchant_id) -> a customer frame in the dataset's columns."""
    df = pd.DataFrame([{"customer_id": "CUSTTEST", "tx_date": d.isoformat(), "category": c, "tx_amount_eur": a,
                        "transaction_type": t, "merchant_id": m, "mcc_code": None} for d, t, c, a, m in sorted(rows)])
    df["balance_after_eur"] = 5000 + df["tx_amount_eur"].cumsum()
    return df


def months(first=date(2024, 10, 1), last=date(2026, 9, 1)):
    m = first
    while m <= last:
        yield m
        m = add_months(m, 1)


def base_rows(rent=lambda m: -700.0, util=None, invest_until=date(2026, 9, 1)):
    rows = []
    for m in months():
        rows.append((m.replace(day=15), "salary", "Salary", 2500.0, None))
        rows.append((m.replace(day=5), "housing_payment", "Housing", rent(m), None))
        if m <= invest_until:
            rows.append((m.replace(day=18), "investment_deposit", "Invest Deposit", -200.0, None))
        for day, amount in (util(m) if util else []):
            rows.append((m.replace(day=day), "utilities_payment", "Utilities", amount, None))
        rows.append((m.replace(day=10), "purchase", "Groceries", -60.0 - m.month, "MRC-GRO-0001"))
    return rows


def engine_for(tmp_path, rows) -> Engine:
    return Engine(today=TODAY, state_path=tmp_path / "t.json", frame=frame(rows), age=30)


def test_stopped_commitment(tmp_path):
    e = engine_for(tmp_path, base_rows(invest_until=date(2026, 6, 1)))
    inv = by_id(e.commitments())["investing"]
    assert inv["status"] == "stopped" and inv["next_expected"] is None and inv["actions"] == []
    assert inv["last_payment"]["date"] == "2026-06-18" and "looks stopped" in inv["note"]
    assert e.commitments()["outgoing_per_month_eur"] == 700.0  # rent only: a stopped one costs nothing now
    with pytest.raises(ValueError, match="already stopped"):
        e.create_pause_investing()
    assert "already" not in no_subscriptions_answer(e.commitments()) and "pause" not in \
        no_subscriptions_answer(e.commitments())


def test_rent_increase_with_old_and_new_numbers(tmp_path):
    e = engine_for(tmp_path, base_rows(rent=lambda m: -800.0 if m >= date(2026, 7, 1) else -700.0))
    r = by_id(e.commitments())["rent"]
    assert r["trend"]["direction"] == "up" and r["trend"]["change_pct"] == 14
    assert (r["trend"]["previous_per_month_eur"], r["trend"]["last_per_month_eur"]) == (700.0, 800.0)
    assert r["trend"]["previous_months"] == "Apr-Jun 2026" and r["trend"]["last_months"] == "Jul-Sep 2026"
    a = e.alert("bills")
    assert a["type"] == "increase" and a["id"] == "rent"
    sig = next(s for s in rules.signals(e) if s["type"] == "BILL_INCREASE")
    assert sig["data"]["extra_per_year_eur"] == 1200.0
    text, _ = main.signal_message(sig, "Test")
    assert "800.00 € a month" in text and "700.00 €" in text and "1 200.00 €" in text


def test_utilities_increase_and_fixed_days(tmp_path):
    e = engine_for(tmp_path, base_rows(util=lambda m: [(5, -45.0), (15, -45.0), (25, -45.0)] if m >= date(2026, 7, 1)
                                       else [(5, -30.0), (16, -30.0), (25, -30.0)]))
    u = by_id(e.commitments())["utilities"]
    assert u["trend"]["direction"] == "up" and (u["trend"]["previous_per_month_eur"], u["per_month_eur"]) == (90.0, 135.0)
    assert u["fixed_days"] == [5, 15, 25] and u["next_expected"] == "2026-10-15" and u["actions"] == ["remind"]


def test_no_utilities(tmp_path):
    e = engine_for(tmp_path, base_rows())
    c = e.commitments()
    assert "utilities" not in by_id(c) and c["bills_per_month_eur"] == 700.0
    text = no_subscriptions_answer(c)
    assert "utility" not in text and text.startswith("I don't see any subscriptions") and "rent (700.00 €" in text


def test_a_card_subscription_would_be_found_and_named(tmp_path):
    rows = base_rows() + [(m.replace(day=3), "purchase", "Entertainment", -11.99, "MRC-ENT-0007") for m in months()]
    c = engine_for(tmp_path, rows).commitments()
    assert c["subscriptions_found"] == 1 and c["card_subscriptions"][0]["name"] == "Entertainment #7"
    assert "Entertainment #7" in no_subscriptions_answer(c)


def test_salary_only_customer(tmp_path):
    rows = [(m.replace(day=15), "salary", "Salary", 2000.0, None) for m in months()]
    e = engine_for(tmp_path, rows)
    c = e.commitments()
    assert [x["id"] for x in c["regular_payments"]] == ["salary"] and c["outgoing_per_month_eur"] == 0
    assert no_subscriptions_answer(c) == "I don't see any subscriptions or regular payments on your account."
    assert e.alert("bills") == {"kind": "bills", "found": False}


# ---------------- the app: buttons, pages ----------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=TODAY, state_path=tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def test_pause_investing_button_then_pin(client):
    r = client.post("/api/chat", json={"text": "__btn__:pause:investing"}).json()
    assert "monthly investing" in r["text"] and "Sun 18 Oct" in r["text"]
    url = r["buttons"][0]["url"]
    assert r["buttons"][0]["label"] == main.OPEN_APP and url.startswith("/s-mobiili/?ready=stop:") and "Face ID" in r["text"]
    assert "Keskeytä kuukausisijoitus" in client.get(url).text  # S-mobiili speaks Finnish by default
    assert "Pause monthly investing" in client.get(url + "&lang=en").text
    pin = "/stop/" + tools.pending_id_of(url)  # the PIN page still works
    assert "Pause monthly investing" in client.get(pin).text
    assert "Investing paused" in client.post(pin, data={"pin": "4321"}).text
    again = client.post("/api/chat", json={"text": "__btn__:pause:investing"}).json()["text"]
    assert again == "I can't do that: monthly investing is already paused by you"
    assert main.engine.state["paused"]["investment_deposit"]


def test_remind_buttons(client):
    rent = client.post("/api/chat", json={"text": "__btn__:remind:rent"}).json()["text"]
    assert rent == "Okay, I'll message you the day before your rent is due (Wed 07 Oct)."
    util = client.post("/api/chat", json={"text": "__btn__:remind:utilities"}).json()["text"]
    assert "different days" in util


def test_pages_show_no_subscriptions(client):
    for page in (client.get("/s-mobiili/").text, client.get("/chat").text):
        assert "StreamPlus" not in page and "Subscriptions" not in page
