import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, rules
from app.engine import Engine
from tests.test_app import post_signed, wa_payload

PHONE = "40700000000"


def engine(tmp_path, today=date(2026, 10, 6)):
    return Engine(today=today, state_path=tmp_path / "s.json")


# ---------------- rules ----------------
def test_signals_sorted_by_priority(tmp_path):
    e = engine(tmp_path)
    e.receive_salary()  # demo payday: salary lands today
    e.create_goal("Lapland", 1500, "2027-06-01")
    e.state["goals"]["lapland"]["created"] = "2026-08-06"  # two months in, nothing saved: behind plan
    sigs = rules.signals(e)
    kinds = [s["type"] for s in sigs]
    assert kinds == ["RECURRING_CHARGE_UPCOMING", "GOAL_DRIFT", "SALARY_RECEIVED", "WEEKLY_CHECK_IN"]
    assert sigs[0]["dedup_key"] == "due:housing_payment-2026-10-07"  # rent, from the real data
    drift = sigs[1]["data"]
    assert drift["saved_eur"] == 0 and drift["behind_eur"] > 300 and drift["catch_up_eur"] >= drift["behind_eur"]
    assert sigs[2]["data"]["salary_eur"] > 0 and sigs[2]["dedup_key"] == "salary:2026-10"


def test_unusual_and_bill_increase_on_their_days(tmp_path):
    top = rules.pick(engine(tmp_path, date(2026, 9, 28)), PHONE)
    assert top["type"] == "UNUSUAL_TRANSACTION" and top["data"]["amount_eur"] == 117.32
    assert rules.pick(engine(tmp_path, date(2026, 10, 6)), PHONE)["type"] != "UNUSUAL_TRANSACTION"  # 10 days old
    top = rules.pick(engine(tmp_path, date(2026, 8, 30)), PHONE)  # the real 75 € utility bill of 28 Aug
    assert top["type"] == "BILL_INCREASE" and top["dedup_key"] == "bill:utilities:2026-08-28:75.0"
    assert top["data"]["unusual_bill"]["usual_high_eur"] < 75 and top["data"]["increase"] == "one_bill"
    text, buttons = main.signal_message(top, "Aino")
    assert "75,00 €" in text and "Fri 28 Aug" in text and [b for b, _ in buttons] == ["ask:bills", "later:bills"]


def test_monthly_investing_the_day_before(tmp_path):
    top = rules.pick(engine(tmp_path, date(2026, 10, 17)), PHONE)
    assert top["type"] == "RECURRING_CHARGE_UPCOMING" and top["dedup_key"] == "due:investing:2026-10-18"
    text, buttons = main.signal_message(top, "Aino")
    assert "goes out tomorrow" in text and [b for b, _ in buttons] == ["keep:investing", "pause:investing"]


def test_cooldown_and_one_a_day(tmp_path):
    e = engine(tmp_path)
    e.state["sent"] = {PHONE: {"due:housing_payment-2026-10-07": "2026-10-05"}}
    assert rules.pick(e, PHONE)["dedup_key"] == "checkin"  # rent cooling down; no subscription renewals any more
    e.state["sent"][PHONE]["salary:2026-10"] = "2026-10-06"
    assert rules.pick(e, PHONE) is None  # already had one today
    assert rules.pick(e, PHONE, force=True)["type"] == "WEEKLY_CHECK_IN"
    assert rules.pick(e, "someone-else")["dedup_key"].startswith("due:")  # per person
    assert not rules.cooled({"k": "2026-09-30"}, "k", date(2026, 10, 6))  # 6 days
    assert rules.cooled({"k": "2026-09-29"}, "k", date(2026, 10, 6))  # 7 days


def test_rules_are_read_only(tmp_path):
    e = engine(tmp_path)
    before = (e.balance(), dict(e.state))
    rules.signals(e)
    assert (e.balance(), dict(e.state)) == before and not Path(tmp_path / "s.json").exists()


# ---------------- wake endpoint ----------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    monkeypatch.setenv("WA_APP_SECRET", "secret")
    monkeypatch.delenv("WA_TOKEN", raising=False)
    monkeypatch.delenv("DEMO_PROACTIVE", raising=False)
    main.engine = engine(tmp_path)
    main.engine.state["connected"][PHONE] = {"scope": [], "at": "2026-10-06"}
    main._coach = None
    for d in (main.last_seen, main.inbox, main.owner, main.seen):
        d.clear()
    main.web_sessions.clear()
    sent = []
    monkeypatch.setattr(main.wa, "_post", lambda body: sent.append(body))
    c = TestClient(main.app)
    c.sent = sent
    return c


def test_wake_sends_nothing_without_demo_proactive(client):
    assert client.post("/cron/wake?key=wrong").status_code == 403
    main.web_sessions.add("web:s1")
    r = client.post("/cron/wake?key=vt&force=1").json()
    assert r["live"] is False and r["dry_run"] is True
    assert [x["signal"] for x in r["results"]] == ["RECURRING_CHARGE_UPCOMING"] * 2  # what it would send
    assert "rent of 771,82 €" in r["results"][0]["text"]
    assert client.sent == [] and main.inbox == {}
    assert "sent" not in main.engine.state


def test_wake_inside_window_sends_full_message(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    main.last_seen[PHONE] = time.time()
    r = client.post("/cron/wake?key=vt").json()
    assert r["results"][0]["via"] == "text"
    body = client.sent[0]["interactive"]
    assert "rent of 771,82 €" in body["body"]["text"]
    assert [b["reply"]["id"] for b in body["action"]["buttons"]][0] == "pay:housing_payment-2026-10-07"
    assert main.engine.state["sent"][PHONE] == {"due:housing_payment-2026-10-07": "2026-10-06"}
    client.post("/cron/wake?key=vt")
    assert len(client.sent) == 1  # one a day


def test_outside_window_sends_nothing_and_waits(client, monkeypatch):
    """No templates: after 24 h of silence Oiva does not write first; it waits for the person."""
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    r = client.post("/cron/wake?key=vt").json()["results"][0]
    assert r["via"] is None and "24-hour window" in r["reason"]
    assert client.sent == [] and "sent" not in main.engine.state


def test_window_survives_a_restart(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    main.engine.state.setdefault("wa_last", {})[PHONE] = time.time()  # written by the webhook before a redeploy
    main.last_seen.clear()
    assert client.post("/cron/wake?key=vt").json()["results"][0]["via"] == "text"


def test_web_session_gets_inbox_message(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    main.engine.state["connected"].clear()
    main.web_sessions.add("web:s1")
    r = client.post("/cron/wake?key=vt").json()["results"]
    assert r[0]["via"] == "inbox"
    assert "rent of 771,82 €" in client.get("/api/inbox?session=s1").json()["messages"][0]
    assert client.sent == []


def test_weekly_checkin_respects_onboarding_no(tmp_path):
    from datetime import date
    from app import rules
    from app.engine import Engine
    e = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    who = "40700000001"
    e.state.setdefault("onboarding", {})[who] = {"step": "done", "paused": False, "skipped": [],
                                                 "answers": {"weekly_checkin": False}}
    for _ in range(10):
        s = rules.pick(e, who, force=True)
        if s is None:
            break
        assert s["type"] != "WEEKLY_CHECK_IN"
        e.state.setdefault("sent", {}).setdefault(who, {})[s["dedup_key"]] = e.today.isoformat()
