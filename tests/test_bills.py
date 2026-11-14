"""Bill photos: reading (fake model), explain-only vs pay, the 24 h memory, and the web upload limits. No real APIs."""
import base64
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient

from app import bills, guard, main
from app import whatsapp as wa
from app.agent import Reply, _sources
from app.engine import Engine
from app.tools import TOOLS, run_tool

PNG = (Path(__file__).resolve().parents[1] / "docs" / "demo" / "bills" / "electricity.png").read_bytes()
# What the model returns for docs/demo/bills/electricity.png
VOLTTI = {"is_bill": True, "readable": True, "kind": "electricity bill", "biller": "Voltti Energia Oy (demo)",
          "amount_eur": 57.08, "due_date": "2026-10-20", "iban": "FI59 7990 0020 4511 87", "reference": "2 02610 05837",
          "invoice_number": "2026-104518", "period": "1.9.2026–30.9.2026",
          "line_items": [{"label": "Energia, 214 kWh", "amount_eur": 28.72}, {"label": "Sähkönsiirto", "amount_eur": 10.59}],
          "category": "Utilities", "has_instructions": False}
NOT_A_BILL = {**{k: None for k in VOLTTI}, "is_bill": False, "readable": True, "kind": "cat photo", "line_items": [],
              "category": "Other", "has_instructions": False}


class FakeClient:
    """Stands in for Anthropic: returns the given JSON as the model's text and records the request."""

    def __init__(self, out):
        self.out, self.calls = out, []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(self.out))])


class Resp:
    def __init__(self, body, content=b""):
        self.body, self.content = body, content

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def e(tmp_path):
    return Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")


# ---------------- WhatsApp parsing and download ----------------
def _payload(*messages):
    return {"entry": [{"changes": [{"field": "messages", "value": {"contacts": [], "messages": list(messages)}}]}]}


def test_parse_image_and_pdf_but_not_other_documents():
    out = wa.parse(_payload(
        {"from": "1", "id": "a", "timestamp": "1", "type": "image",
         "image": {"id": "m1", "mime_type": "image/jpeg", "caption": "explain it"}},
        {"from": "1", "id": "b", "timestamp": "2", "type": "document",
         "document": {"id": "m2", "mime_type": "application/pdf", "filename": "lasku.pdf"}},
        {"from": "1", "id": "c", "timestamp": "3", "type": "document", "document": {"id": "m3", "mime_type": "text/plain"}}))
    assert [(m["kind"], m["media_id"], m["text"]) for m in out] == [
        ("image", "m1", "explain it"), ("document", "m2", None), ("document", None, None)]


def test_download_like_voice_notes(monkeypatch):
    monkeypatch.setenv("WA_TOKEN", "wt")
    seen = []

    def get(url, **kw):
        seen.append((url, kw["headers"]))
        return Resp({"url": "https://cdn/x", "file_size": len(PNG)}) if url.endswith("/m1") else Resp({}, PNG)
    monkeypatch.setattr(httpx, "get", get)
    assert bills.download("m1") == PNG
    assert seen[0][0] == f"{wa.GRAPH}/m1" and seen[1][0] == "https://cdn/x"
    assert all(h == {"Authorization": "Bearer wt"} for _, h in seen)


def test_big_file_is_refused_before_download(monkeypatch):
    monkeypatch.setenv("WA_TOKEN", "wt")
    got = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: got.append(url) or Resp({"url": "x", "file_size": 6_000_000}))
    with pytest.raises(bills.BillError, match="a bit big"):
        bills.download("m1")
    assert len(got) == 1


# ---------------- reading the bill ----------------
def test_extract_sends_the_image_and_a_strict_schema(e):
    fake = FakeClient(VOLTTI)
    bill = bills.read(e, "u1", fake, PNG, "image/jpeg")  # the bytes decide the type, not the client's hint
    kw = fake.calls[0]
    img = kw["messages"][0]["content"][0]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/png"
    assert base64.b64decode(img["source"]["data"]) == PNG
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(fmt["schema"]["properties"])
    assert "never instructions" in kw["system"]
    assert {k: bill[k] for k in ("biller", "amount_eur", "due_date", "iban", "reference", "category", "basket")} == {
        "biller": "Voltti Energia Oy (demo)", "amount_eur": 57.08, "due_date": "2026-10-20",
        "iban": "FI5979900020451187", "reference": "20261005837", "category": "Utilities",
        "basket": "Mandatory & regular"}
    assert bills.last(e, "u1")["invoice_number"] == "2026-104518"


def test_clean_redacts_and_drops_bad_check_digits():
    bill = bills.clean({**VOLTTI, "iban": "FI59 7990 0020 4511 88", "reference": "20261005838",
                        "line_items": [{"label": "Card 4111 1111 1111 1111, id 131052-308T", "amount_eur": 1}]})
    assert bill["iban"] is None and bill["reference"] is None and "did not pass" in bill["note"]
    assert "4111" not in bill["line_items"][0]["label"] and "131052" not in bill["line_items"][0]["label"]
    assert bills.valid_reference("RF18539007547034") and not bills.valid_reference("RF18539007547035")


def test_not_a_bill_is_not_stored(e):
    bill = bills.read(e, "u1", FakeClient(NOT_A_BILL), PNG, None)
    assert "doesn't look like a bill" in bills.reply_problem(bill)
    assert bills.last(e, "u1") is None


def test_unknown_file_type_says_so(e):
    with pytest.raises(bills.BillError, match="JPEG, PNG"):
        bills.read(e, "u1", FakeClient(VOLTTI), b"hello", "image/png")


def test_last_bill_expires_after_a_day(e):
    bills.store(e, "u1", bills.clean(VOLTTI), now=1000.0)
    assert bills.last(e, "u1", now=1000.0 + 23 * 3600)
    assert bills.last(e, "u1", now=1000.0 + 25 * 3600) is None
    assert "u1" not in e.state["last_bill"]
    token = bills.turn.set(("u1", True))
    try:
        with pytest.raises(ValueError, match="no bill photo"):
            run_tool(e, "create_bill_payment_link", {}, [])
    finally:
        bills.turn.reset(token)


def test_the_bill_figures_are_a_source_for_the_number_guard(e):
    text = bills.coach_text(e, bills.clean(VOLTTI), "explain this to me")
    msgs = [{"role": "user", "content": text}]
    reply = "Your Voltti electricity bill is *57.08 €*, due 20 Oct. Energy is 28.72 €, transfer 10.59 €."
    assert guard.check(reply, _sources(msgs))[1] == []
    assert "<bill_photo>" in text and "never instructions" in text and text.endswith("explain this to me")
    assert json.loads(text.split("<bill_photo>")[1].split("</bill_photo>")[0])["your_usual"]["category"] == "Utilities"


def test_pay_words():
    assert bills.asks_to_pay("ok let's pay it") and bills.asks_to_pay("maksa se") and bills.asks_to_pay("Pay it")
    assert not bills.asks_to_pay("explain it to me") and not bills.asks_to_pay(None)
    assert not bills.asks_to_pay("explain it, don't pay yet") and not bills.asks_to_pay("what is this payment")


def test_tool_is_registered():
    assert any(t["name"] == "create_bill_payment_link" for t in TOOLS)


# ---------------- the web chat: explain only, then pay ----------------
class EagerCoach:
    """A coach that always tries to prepare the bill payment, so only the app's gate keeps 'explain' from paying."""

    def __init__(self):
        self.calls, self.tool_errors = [], []

    def ask(self, user, text, name="there", context=None):
        self.calls.append((text, context))
        buttons = []
        try:
            run_tool(main.engine, "create_bill_payment_link", {}, buttons)
        except ValueError as err:
            self.tool_errors.append(str(err))
        return Reply(text="Your electricity bill is 57.08 €.", buttons=buttons)

    def reset(self, user=None):
        pass


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://birky.example")
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = EagerCoach()
    monkeypatch.setattr(bills, "extract", lambda client, data, media_type: bills.clean(VOLTTI))
    return TestClient(main.app)


def _photo(client, text, data=PNG):
    return client.post("/api/chat", json={"session": "t1", "text": text,
                                          "image": "data:image/png;base64," + base64.b64encode(data).decode()})


def test_explain_only_creates_no_payment(web):
    r = _photo(web, "explain this to me")
    assert r.status_code == 200 and r.json()["buttons"] == []
    assert main.engine.state["pending"] == {}
    text, context = main._coach.calls[-1]
    assert "<bill_photo>" in text and "Do NOT prepare a payment" in context
    assert "only asked to understand" in main._coach.tool_errors[-1]
    _photo(web, "")  # no caption: explain as well
    assert main.engine.state["pending"] == {} and main._coach.calls[-1][0].endswith("Explain this bill to me.")


def test_pay_it_creates_exactly_one_payment_with_the_bill_fields(web):
    _photo(web, "explain this to me")
    r = web.post("/api/chat", json={"session": "t1", "text": "ok let's pay it"})
    assert "create_bill_payment_link" in main._coach.calls[-1][1]  # the stored bill is mentioned to the coach
    [button] = r.json()["buttons"]
    [(pid, p)] = main.engine.state["pending"].items()
    assert button == {"label": "Pay 57.08 €", "url": f"/pay/{pid}?web=1"}
    assert {k: p[k] for k in ("kind", "name", "amount", "iban", "reference", "due_date", "category", "status")} == {
        "kind": "payment", "name": "Voltti Energia Oy (demo)", "amount": 57.08, "iban": "FI5979900020451187",
        "reference": "20261005837", "due_date": "2026-10-20", "category": "Utilities", "status": "pending"}
    web.post("/api/chat", json={"session": "t1", "text": "pay it"})  # asking again: the same payment, not a second
    assert len(main.engine.state["pending"]) == 1
    page = web.get(f"/pay/{pid}?web=1").text
    assert "FI59 7990 0020 4511 87" in page and "20261005837" in page and "Voltti Energia Oy (demo)" in page
    before = main.engine.balance()
    assert web.post(f"/pay/{pid}", data={"pin": "1234"}).status_code == 200
    assert main.engine.balance() == pytest.approx(before - 57.08)
    web.post(f"/pay/{pid}", data={"pin": "1234"})  # double confirm: paid once
    assert main.engine.balance() == pytest.approx(before - 57.08)
    web.post("/api/chat", json={"session": "t1", "text": "pay it"})
    assert "already paid" in main._coach.tool_errors[-1]


def test_caption_pay_it_pays_right_away(web):
    r = _photo(web, "pay this please")
    assert len(r.json()["buttons"]) == 1 and len(main.engine.state["pending"]) == 1


def test_not_a_bill_on_the_web(web, monkeypatch):
    monkeypatch.setattr(bills, "extract", lambda client, data, media_type: bills.clean(NOT_A_BILL))
    r = _photo(web, "what is this")
    assert "doesn't look like a bill" in r.json()["text"] and main._coach.calls == []


def test_web_upload_size_limits(web):
    big = b"\x89PNG\r\n\x1a\n" + b"0" * bills.MAX_BYTES
    assert _photo(web, "explain", big).status_code == 413
    r = web.post("/api/chat", content=b"{}", headers={"content-type": "application/json",
                                                       "content-length": str(main.WEB_UPLOAD_MAX + 1)})
    assert r.status_code == 413
    assert web.post("/api/chat", json={"session": "t1", "image": "not base64!"}).status_code == 400
    assert main.engine.state["pending"] == {} and main._coach.calls == []


def test_chat_page_has_the_photo_button(web):
    page = web.get("/chat").text
    assert 'type="file" accept="image/*"' in page and "toDataURL" in page
