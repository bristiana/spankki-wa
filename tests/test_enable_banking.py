"""Another bank, for real: the parts that run without the Enable Banking API."""
import pytest

from app import enable_banking as eb, main
from app.tools import run_tool


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "DATA_DIR", tmp_path / "eb")
    monkeypatch.setenv("EB_LINK_SECRET", "test-secret")
    monkeypatch.delenv("STATE_REMOTE", raising=False)


def test_signed_link_round_trip_and_tamper():
    t = eb.sign("358401234567")
    assert eb.verify(t) == "358401234567"
    body, mac = t.rsplit(".", 1)
    assert eb.verify(body + "." + ("0" * len(mac))) is None
    assert eb.verify(eb.sign("358401234567", days=-1)) is None
    assert eb.verify(None) is None and eb.verify("junk") is None


def test_simplify_signs_and_categories():
    out = eb.simplify({"transaction_amount": {"amount": "12.50", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
                       "booking_date": "2026-11-01", "creditor": {"name": "Wolt Helsinki"}, "status": "BOOK"})
    assert out["amount_eur"] == -12.5 and out["category"] == "Restaurants"
    inc = eb.simplify({"transaction_amount": {"amount": "100", "currency": "EUR"}, "credit_debit_indicator": "CRDT",
                       "booking_date": "2026-11-02", "debtor": {"name": "Friend"}})
    assert inc["amount_eur"] == 100 and inc["category"] == "Income"
    assert eb.simplify({"transaction_amount": {"amount": "5", "currency": "USD"}, "booking_date": "2026-11-02"}) is None
    mcc = eb.simplify({"transaction_amount": {"amount": "3", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
                       "booking_date": "2026-11-03", "merchant_category_code": "4121"})
    assert mcc["category"] == "Transportation"


def test_balance_prefers_available_eur():
    assert eb.pick_balance([{"balance_type": "CLBD", "balance_amount": {"amount": "10", "currency": "EUR"}},
                            {"balance_type": "ITAV", "balance_amount": {"amount": "12.3", "currency": "EUR"}}]) == 12.3
    assert eb.pick_balance([{"balance_type": "ITAV", "balance_amount": {"amount": "1", "currency": "USD"}}]) is None


def _link(phone):
    eb.save(phone, {"Revolut": {"bank": "Revolut", "linked": "2026-11-14", "accounts": [
        {"uid": "u1", "product": "Current", "balance_eur": 250.0, "transactions": [
            {"date": "2026-11-10", "what": "Wolt", "amount_eur": -30.0, "category": "Restaurants"},
            {"date": "2026-11-11", "what": "Lidl", "amount_eur": -60.0, "category": "Groceries"}]}]}})


def test_full_picture_only_for_the_owner():
    _link("358401111111")
    e = main.engine
    tok = eb.viewer.set("358401111111")
    try:
        mine = run_tool(e, "get_full_picture", {}, [])
    finally:
        eb.viewer.reset(tok)
    assert "Revolut (linked via Enable Banking, real data)" in mine and "250.0" in mine
    tok = eb.viewer.set("358409999999")
    try:
        other = run_tool(e, "get_full_picture", {}, [])
    finally:
        eb.viewer.reset(tok)
    assert "real data" not in other and "250.0" not in other


def test_merge_adds_balance_and_spending():
    _link("358401111111")
    full = {"linked": False, "banks": [{"bank": "S-Pankki", "accounts": [{"balance_eur": 1000.0}]}],
            "total_balances_eur": 1000.0, "net_position_eur": 1000.0, "how_to_link": "x"}
    out = eb.merge(full, "358401111111")
    assert out["total_balances_eur"] == 1250.0 and out["net_position_eur"] == 1250.0 and out["linked"]
    real = out["banks"][1]
    assert real["spending_per_month_by_category_eur"]["Groceries"] == round(60 / (90 / 30.4), 2)
    assert "how_to_link" not in out
    assert eb.merge(full, None) is full


def test_bank_page_needs_whatsapp_login(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("EB_APP_ID", "app")
    monkeypatch.setenv("EB_PRIVATE_KEY", "key")
    monkeypatch.setattr(eb, "banks", lambda country="FI": ["Revolut", "OP"])
    c = TestClient(main.app)
    assert c.get("/bank", follow_redirects=False).headers["location"] == "/login?next=/bank"
    r = c.get("/bank", params={"t": eb.sign("358401111111")})
    assert "Revolut" in r.text and eb.COOKIE in r.headers.get("set-cookie", "")
    assert "Link expired" in c.get("/eb/callback", params={"code": "x", "state": "unknown"}).text


def test_abroad_bank_uses_its_country(monkeypatch):
    sent = {}
    monkeypatch.setattr(eb, "call", lambda path, method="GET", body=None: sent.update(body or {}) or {"url": "u"})
    assert eb.start("358401111111", "Revolut", "https://x/eb/callback") == "u"
    assert sent["aspsp"] == {"name": "Revolut", "country": "RO"}


def test_login_with_whatsapp_and_dashboard(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("WA_DISPLAY_NUMBER", "15550001111")
    _link("358401111111")
    c = TestClient(main.app)
    r = c.get("/login")
    assert "wa.me/15550001111?text=login" in r.text and "Log in" in c.get("/app").text
    assert "Log in with WhatsApp" in c.get("/app").text
    r = c.get("/login", params={"t": eb.sign("358401111111"), "next": "/app"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app"
    c.cookies.set(eb.COOKIE, eb.sign("358401111111"))
    page = c.get("/app").text
    assert "•••1111" in page and "Revolut" in page and "Manage banks" in page
    c.cookies.clear()
    assert c.get("/bank", follow_redirects=False).headers["location"] == "/login?next=/bank"
    assert "expired" in c.get("/login", params={"t": "junk"}).text


def test_login_next_cannot_leave_the_site():
    from fastapi.testclient import TestClient
    r = TestClient(main.app).get("/login", params={"t": eb.sign("358401111111"), "next": "//evil.com"},
                                 follow_redirects=False)
    assert r.headers["location"] == "/app"


def test_revolut_shown_without_country(monkeypatch):
    monkeypatch.setattr(eb, "_banks_cache", None)
    monkeypatch.setattr(eb, "call", lambda path, method="GET", body=None: {"aspsps": [{"name": n} for n in
                                                                         ["Revolut", "OP", "S-Pankki", "Nordea"]]})
    assert eb.banks() == ["Revolut", "OP", "Nordea"]


def test_web_chat_viewer_reaches_tool():
    _link("358401111111")
    tok = eb.viewer.set("358401111111")
    try:
        assert "real data" in run_tool(main.engine, "get_full_picture", {}, [])
    finally:
        eb.viewer.reset(tok)


def test_privacy_and_terms_pages():
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    assert "Enable Banking" in c.get("/privacy").text and c.get("/terms").status_code == 200


def test_add_another_bank_goes_to_the_real_one(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("EB_APP_ID", "app")
    monkeypatch.setenv("EB_PRIVATE_KEY", "key")
    c = TestClient(main.app)
    assert c.get("/connect/bank", follow_redirects=False).headers["location"] == "/bank"
    assert "Demo Bank" in c.get("/connect/bank", params={"demo": 1}).text
