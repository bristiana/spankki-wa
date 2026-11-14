import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, rules, templates_wa
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
    assert kinds == ["RECURRING_CHARGE_UPCOMING", "RECURRING_CHARGE_UPCOMING", "GOAL_DRIFT",
                     "SALARY_RECEIVED", "WEEKLY_CHECK_IN"]
    assert sigs[0]["dedup_key"] == "due:housing_payment-2026-10-07"  # rent before the 13.99 € renewal
    drift = sigs[2]["data"]
    assert drift["saved_eur"] == 0 and drift["behind_eur"] > 300 and drift["catch_up_eur"] >= drift["behind_eur"]
    assert sigs[3]["data"]["salary_eur"] > 0 and sigs[3]["dedup_key"] == "salary:2026-10"


def test_unusual_and_price_rise_on_their_days(tmp_path):
    top = rules.pick(engine(tmp_path, date(2026, 9, 28)), PHONE)
    assert top["type"] == "UNUSUAL_TRANSACTION" and top["data"]["amount_eur"] == 117.32
    assert rules.pick(engine(tmp_path, date(2026, 10, 6)), PHONE)["type"] != "UNUSUAL_TRANSACTION"  # 10 days old
    top = rules.pick(engine(tmp_path, date(2026, 8, 10)), PHONE)
    assert top["type"] == "SUBSCRIPTION_PRICE_INCREASE" and top["data"]["extra_per_year_eur"] == 24.0


def test_cooldown_and_one_a_day(tmp_path):
    e = engine(tmp_path)
    e.state["sent"] = {PHONE: {"due:housing_payment-2026-10-07": "2026-10-05"}}
    assert rules.pick(e, PHONE)["dedup_key"] == "renewal:StreamPlus:2026-10-07"  # rent cooling down
    e.state["sent"][PHONE]["renewal:StreamPlus:2026-10-07"] = "2026-10-06"
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


# ---------------- templates ----------------
def test_template_params_are_cleaned():
    assert templates_wa.clean("Restaurant #22") == "Restaurant 22"
    assert templates_wa.clean("Stream\nPlus\t  Max") == "Stream Plus Max"
    assert templates_wa.clean("see https://evil.example/x now") == "see now"
    assert templates_wa.clean("www.netflix.com Netflix") == "Netflix"
    assert templates_wa.clean("Ben & Jerry's — Café-Bar") == "Ben & Jerry's Café-Bar"
    assert len(templates_wa.clean("A" * 100)) == 40
    assert templates_wa.clean("https://x.io") is None and templates_wa.clean("🙂🙂") is None


def test_template_for_signals(tmp_path):
    e = engine(tmp_path)
    rent, sub, week = rules.signals(e)
    assert templates_wa.template_for(rent, e.today) == ("kurre_bill_due", ["rent", "tomorrow"])
    assert templates_wa.template_for(sub, e.today) == ("kurre_subscription_renewal", ["StreamPlus", "tomorrow"])
    assert templates_wa.template_for(week, e.today) == ("kurre_weekly_checkin", [])
    sub["data"]["name"] = "http://bad.link"
    assert templates_wa.template_for(sub, e.today) is None  # nothing safe left: no template
    for name, body in templates_wa.TEMPLATES.items():
        assert "\n" not in body and not body.startswith("{{") and not body.endswith("}}")
    docs = (Path(__file__).parents[1] / "docs" / "whatsapp-templates.md").read_text(encoding="utf-8")
    for name, body in templates_wa.TEMPLATES.items():
        assert f"### {name}" in docs and f"> {body}" in docs


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
    assert "rent of 771.82 €" in r["results"][0]["text"]
    assert client.sent == [] and main.inbox == {}
    assert "sent" not in main.engine.state and "held" not in main.engine.state


def test_wake_inside_window_sends_full_message(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    main.last_seen[PHONE] = time.time()
    r = client.post("/cron/wake?key=vt").json()
    assert r["results"][0]["via"] == "text"
    body = client.sent[0]["interactive"]
    assert "rent of 771.82 €" in body["body"]["text"]
    assert [b["reply"]["id"] for b in body["action"]["buttons"]][0] == "pay:housing_payment-2026-10-07"
    assert main.engine.state["sent"][PHONE] == {"due:housing_payment-2026-10-07": "2026-10-06"}
    client.post("/cron/wake?key=vt")
    assert len(client.sent) == 1  # one a day


def test_template_outside_window_then_held_message_on_tap(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    r = client.post("/cron/wake?key=vt").json()["results"][0]
    assert r["via"] == "template"
    t = client.sent[0]["template"]
    assert t["name"] == "kurre_bill_due"
    assert [p["text"] for p in t["components"][0]["parameters"]] == ["rent", "tomorrow"]
    assert t["components"][1]["parameters"][0]["payload"] == templates_wa.SHOW
    assert main.engine.state["held"][PHONE]["signal"]["type"] == "RECURRING_CHARGE_UPCOMING"

    # Same priority (StreamPlus renewal) does not replace the held rent message.
    r = client.post("/cron/wake?key=vt&force=1").json()["results"][0]
    assert r["via"] is None and "already waiting" in r["reason"] and len(client.sent) == 1

    tap = wa_payload(mid="t1")
    m = tap["entry"][0]["changes"][0]["value"]["messages"][0]
    m.update(type="button", button={"text": "Show me", "payload": templates_wa.SHOW})
    m.pop("text")
    post_signed(client, tap)
    full = [b for b in client.sent if b.get("type") == "interactive"]
    assert len(full) == 1 and "rent of 771.82 €" in full[0]["interactive"]["body"]["text"]
    assert PHONE not in main.engine.state["held"]


def test_web_session_gets_inbox_message(client, monkeypatch):
    monkeypatch.setenv("DEMO_PROACTIVE", "1")
    main.engine.state["connected"].clear()
    main.web_sessions.add("web:s1")
    r = client.post("/cron/wake?key=vt").json()["results"]
    assert r[0]["via"] == "inbox"
    assert "rent of 771.82 €" in client.get("/api/inbox?session=s1").json()["messages"][0]
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
