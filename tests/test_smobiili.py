"""S-mobiili integration: native widget cards, chat deep links, embed mode, the concept page."""
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import Engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WA_TOKEN", raising=False)
    main.engine = Engine(today=date(2026, 10, 6), state_path=tmp_path / "s.json")
    main._coach = None
    monkeypatch.setattr(main.wa, "_post", lambda body: None)
    return TestClient(main.app)


def cards(client) -> dict:
    r = client.get("/api/widgets")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    return {w["id"]: w for w in r.json()["widgets"]}


def question(link: str) -> str:
    u = urlparse(link)
    assert u.path == "/chat"
    return parse_qs(u.query)["q"][0]


def test_widgets_shape(client):
    j = client.get("/api/widgets").json()
    assert j["version"] == 1 and j["as_of"] == "2026-10-06"
    w = {c["id"]: c for c in j["widgets"]}
    assert list(w) == ["safe_to_spend", "next_bill", "baskets", "regular_payments"]  # no goal yet
    for c in j["widgets"]:
        assert {"title", "value", "subtitle", "deep_link"} <= set(c)
        assert question(c["deep_link"])
    assert question(w["next_bill"]["actions"][0]["deep_link"]).startswith("__btn__:pay:")


def test_widgets_match_engine(client):
    e, w = main.engine, cards(client)
    s = e.safe_to_spend()
    assert w["safe_to_spend"]["amount_eur"] == s["safe_to_spend_until_payday_eur"]
    assert w["safe_to_spend"]["per_day_eur"] == s["safe_per_day_eur"]
    assert w["safe_to_spend"]["payday"] == s["next_payday"]["date"]
    rent = next(b for b in e.upcoming_bills(45) if b["category"] == "Housing")
    assert (w["next_bill"]["name"], w["next_bill"]["amount_eur"], w["next_bill"]["due_date"]) == \
           ("Rent", rent["amount_eur"], rent["due_date"])
    b = e.baskets(w["baskets"]["period"])
    assert w["baskets"]["total_eur"] == b["total_spent_eur"] > 0
    assert [x["share_pct"] for x in w["baskets"]["baskets"]] == [x["share_pct"] for x in b["baskets"]]
    util = next(x for x in e.commitments()["regular_payments"] if x["id"] == "utilities")
    assert w["regular_payments"]["kind"] == "unusual_bill"  # the real 75 € utility bill of 28 Aug
    assert w["regular_payments"]["amount_eur"] == util["unusual_bill"]["amount_eur"] == 75.0
    assert w["regular_payments"]["usual_high_eur"] == util["unusual_bill"]["usual_high_eur"]


def test_goal_widget_after_a_goal(client):
    main.engine.create_goal("Lapland trip", 1500, "2027-06-30")
    g = cards(client)["goal"]
    plan = main.engine.goals()[0]
    assert g["progress_pct"] == plan["progress_pct"] and g["target_eur"] == 1500
    assert "Lapland trip" in question(g["deep_link"])


def test_pay_deep_link_prepares_payment_without_ai(client):
    q = question(cards(client)["next_bill"]["actions"][0]["deep_link"])
    j = client.post("/api/chat", json={"session": "m", "text": q}).json()
    assert j["buttons"][0]["url"].startswith("/s-mobiili/?ready=pay:") and "rent" in j["text"]
    assert main._coach is None  # no AI involved


def test_chat_is_an_s_mobiili_screen_and_embed_drops_the_phone(client):
    plain, embed = client.get("/chat").text, client.get("/chat?embed=1").text
    for page in (plain, embed):  # never the demo site's own bar: the chat is a screen of the app
        assert 'class="bar"' not in page and "<footer>" not in page and '<body class="embed">' in page
    assert 'class="phone" id="phone"' in plain and 'class="tabs"' in plain and ">Viestit" in plain
    assert 'class="phone bare" id="phone"' in embed and "safe-area-inset-top" in embed


def test_deep_link_question_is_escaped(client):
    evil = '</script><script>alert(1)</script>'
    html = client.get("/chat", params={"q": evil, "embed": "1"}).text
    assert evil not in html and "<script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html  # JSON-encoded for the script, not raw HTML
    assert 'const deepQ = "What is safe?"' in client.get("/chat?q=What is safe?").text
    assert "const deepQ = \"\"" in client.get("/chat").text


@pytest.mark.parametrize("path", ["/", "/app", "/app/explore", "/mobile-demo"])
def test_old_web_pages_lead_to_s_mobiili(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/s-mobiili/"
    assert client.get(path, params={"lang": "en"}, follow_redirects=False).headers["location"] == "/s-mobiili/?lang=en"
    assert client.get(path).status_code == 200
    assert client.get("/app/explore.csv").status_code == 404


def test_your_money_screen(client):
    """Pankki -> Your money: the seven Oiva charts, on Antti's own figures, so the tile is on his account only."""
    from pathlib import Path
    pankki = client.get("/s-mobiili/?tab=pankki&persona=CUST01656&lang=en").text
    assert 'id="your-money"' in pankki and "tab=raha" in pankki
    assert 'id="your-money"' not in client.get("/s-mobiili/?tab=pankki&persona=CUST04831&lang=en").text
    r = client.get("/s-mobiili/?tab=raha&persona=CUST01656&lang=en")
    assert r.status_code == 200
    for card in ("trend", "cats", "budget", "sit", "fig", "income", "cal"):
        assert f'id="{card}"' in r.text
    assert "/static/oiva/money.js" in r.text and "/static/oiva/money.css" in r.text
    src = r.text.split('data-src="')[1].split('"')[0]
    assert src == "/s-mobiili/money-data/"
    for name in ("spend", "budget", "compare", "recurring"):
        assert client.get(f"{src}{name}.json").status_code == 200
    assert client.get(f"{src}nope.json").status_code == 404


def test_your_money_and_oiva_share_one_set_of_numbers(client):
    """Each Your money headline is the figure the engine gives Oiva for that card's own question."""
    e = main.engine_for(main.MONEY_PERSONA)
    sp = client.get("/s-mobiili/money-data/spend.json").json()
    bu = client.get("/s-mobiili/money-data/budget.json").json()
    rec = client.get("/s-mobiili/money-data/recurring.json").json()
    d0, d1, whole = e.money_month()
    assert whole and sp["month"] == "2026-09" and sp["as_of"] == e.today.isoformat()  # 6 Oct: September, whole
    oiva = e.spending(None, "last_month", exclude_bills=True)
    assert sp["total_eur"] == oiva["total_eur"] and sp["prev_total_eur"] == oiva["previous_period"]["total_eur"]
    assert bu["left_in_budgets_eur"] == e.budgets()["left_in_budgets_eur"]
    assert round(sum(i["left_eur"] for i in bu["items"] if i["limit_eur"]), 2) == bu["left_in_budgets_eur"]
    rest = rec["left_after_bills_and_investing_per_month_eur"]
    assert rest == e.commitments()["left_after_bills_and_investing_per_month_eur"]
    rest_c = {c["category"]: c["avg_3m_eur"] for c in sp["categories"]}["Restaurants"]
    assert rest_c == e.simulate_cut("Restaurants", 20)["current_per_month_eur"]  # the what-if's monthly figure
    # last month is the same however the month is asked for (FI and EN answers used different windows)
    by_dates = e.spending(None, start=d0.isoformat(), end=d1.isoformat())
    assert by_dates["previous_period"] == e.spending(None, "last_month")["previous_period"]


@pytest.mark.parametrize("lang, oiva, cats, say", [
    ("en", "Tell me about my spending in September, not counting rent and bills", "Where did my money go in September, not counting rent and bills?",
     "Ask me about this"),
    ("fi", "Kerro kulutuksestani syyskuussa, kun vuokraa ja laskuja ei lasketa", "Mihin rahani menivät syyskuussa, kun vuokraa ja laskuja ei lasketa?",
     "Kysy minulta tästä")])
def test_your_money_asks_oiva(client, lang, oiva, cats, say):
    """Oiva floats on Your money; tapping her or a chart card asks her about it in the chat, in the screen's language."""
    import re
    from html import unescape
    page = client.get(f"/s-mobiili/?tab=raha&persona={main.MONEY_PERSONA}&web=1&lang={lang}").text
    href = lambda id: unescape(re.search(rf'id="{id}" href="([^"]+)"', page).group(1))  # noqa: E731
    assert 'class="oiva wave once"' in page and say in page
    assert question(href("ask-oiva")) == oiva and question(href("cats")) == cats
    assert parse_qs(urlparse(href("cats")).query)["embed"] == ["1"]
    for card in ("trend", "budget", "sit", "fig", "income", "cal"):
        assert question(href(card)).endswith("?")


def test_your_money_is_only_for_its_own_customer(client):
    """Another customer asking for ?tab=raha gets Pankki, never Antti's figures."""
    r = client.get("/s-mobiili/?tab=raha&persona=CUST04831&lang=en")
    assert r.status_code == 200 and 'id="money"' not in r.text and "money.js" not in r.text


# "Ask Oiva" on every S-mobiili screen: one row (or the baskets card) that opens the Helper asking that question, in
# the page's language, as the shown customer under ?persona=. None inside the chat's confirm sheet (?web=1).
ASK_ROWS = {"": ["ask-spend", "ask-save", "ask-recent"], "?tab=pankki": ["ask-bills"], "?tab=mina": ["ask-me"],
            "?tab=kortti": ["ask-card-spend"], "?tab=etukortti": ["ask-groceries"]}


@pytest.mark.parametrize("lang", ["fi", "en"])
def test_every_screen_has_an_ask_oiva_row_in_its_language(client, lang):
    from html import unescape
    from urllib.parse import parse_qs, urlsplit
    pid = main.DEFAULT_PERSONA
    for tab, ids in ASK_ROWS.items():
        url = "/s-mobiili/" + (tab + "&" if tab else "?") + f"lang={lang}&persona={pid}"
        page = client.get(url).text
        for i in ids:
            m = re.search(rf'<a class="ask-o[^"]*" id="{i}" href="([^"]+)" aria-label="([^"]+)"', page)
            assert m, (tab, i)
            q = parse_qs(urlsplit(unescape(m.group(1))).query)
            assert "embed" not in q and q["as"] == [pid] and q["q"][0]
            assert unescape(m.group(2)).startswith("Kysy Oivalta: " if lang == "fi" else "Ask Oiva: ")
            assert q["q"][0] in unescape(m.group(2))  # the row shows the question it asks
        assert page.count('class="ask-o') <= 4  # one per section, not a wall of buttons
    rec = client.get(f"/s-mobiili/kuitit?lang={lang}&persona={pid}").text
    assert 'id="ask-receipts"' in rec


def test_chat_back_keeps_the_customer(client):
    pid = main.DEFAULT_PERSONA
    assert f'href="/s-mobiili/?persona={pid}"' in client.get(f"/chat?as={pid}").text
    assert 'href="/s-mobiili/"' in client.get("/chat").text


def test_no_ask_oiva_rows_inside_the_chats_confirm_sheet(client):
    for tab in ASK_ROWS:
        page = client.get("/s-mobiili/" + (tab + "&" if tab else "?") + "web=1").text
        assert 'class="ask-o' not in page or 'class="ask-o"><img' in page  # only the baskets card's inner span
        assert not re.search(r'<a class="ask-o', page)
        assert 'href="/chat' not in page  # no chat inside the chat


def test_chat_tab_bar_keeps_the_customer(client):
    pid = main.DEFAULT_PERSONA
    page = client.get(f"/chat?as={pid}").text
    nav = page[page.index('<nav class="tabs"'):]
    nav = nav[:nav.index("</nav>")]
    assert nav.count(f"persona={pid}") == 5


def test_finnish_pay_page_and_koti_have_no_english_or_iso_dates(client):
    q = question(cards(client)["next_bill"]["actions"][0]["deep_link"])
    j = client.post("/api/chat", json={"session": "m", "text": q}).json()
    pid = j["buttons"][0]["url"].split("ready=pay:")[1].split("&")[0]
    fi = "lang=fi"
    pay = client.get(f"/s-mobiili/pay/{pid}?{fi}").text
    koti = client.get(f"/s-mobiili/?{fi}&ready=pay:{pid}").text
    for page in (pay, koti):
        for en in ("Landlord", "Pay 1", "Skip guide", "Got it", "Demo Fund", "Grocery store", "Restaurant #"):
            assert en not in page.replace("Pay a bill", ""), en
        assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b(?![^<]*>)", re.sub(r"<script.*?</script>", "", page, flags=re.S))
        assert not re.search(r"\d\.\d{2}(&nbsp;|\s| )?€", page)
    assert "Vuokranantaja" in pay and "Vuokra" in koti


def test_demo_reset_link_needs_a_tap(client, monkeypatch):
    """GET only shows the button (a link preview can't wipe the demo); the button resets and opens S-mobiili."""
    called = []
    monkeypatch.setattr(main, "demo_reset", lambda: called.append("reset"))
    monkeypatch.setattr(main, "warm_up", lambda *a: called.append("warm"))
    page = client.get("/demo/reset?lang=fi").text
    assert "Palauta demo" in page and 'action="/demo/reset/go"' in page and not called
    r = client.post("/demo/reset/go", data={"lang": "fi"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/s-mobiili/?persona={main.DEFAULT_PERSONA}&lang=fi"
    assert called == ["reset", "warm"]
