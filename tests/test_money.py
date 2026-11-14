"""Amounts the way S-mobiili writes them (1 234,56 €), and the number guard still reading them."""
import pytest

from app import money
from app.guard import check, parse

NB = " "


@pytest.mark.parametrize("x, decimals, shown", [
    (412.3, 2, "412,30 €"), (1234.56, 2, "1 234,56 €"), (1065.17, 2, "1 065,17 €"), (296, 0, "296 €"),
    (-12.4, 2, "−12,40 €"), (1234567.891, 2, "1 234 567,89 €"),
])
def test_eur_is_the_app_style(x, decimals, shown):
    assert money.eur(x, decimals) == shown
    assert money.eur(x, decimals, nb=True) == shown.replace(" ", NB)


@pytest.mark.parametrize("raw, shown", [
    ("You have 68.60 € safe to spend.", "You have 68,60 € safe to spend."),
    ("safe to spend 68.6 € until payday", "safe to spend 68,60 € until payday"),
    ("loans of about €379,899.51, for homes", "loans of about 379 899,51 €, for homes"),
    ("a median of €15,000 (72 applications)", "a median of 15 000 € (72 applications)"),
    ("1,234.50€ in total", "1 234,50 € in total"),
    ("rent 1 065.17 € on the 3rd", "rent 1 065,17 € on the 3rd"),
    ("keep 7% and 12.5% of income", "keep 7 % and 12,5 % of income"),
    ("Payday is Thu 15 Oct, in 9 days, 3.5 years", "Payday is Thu 15 Oct, in 9 days, 3.5 years"),
    ("items 1, 2, 3", "items 1, 2, 3"),
])
def test_style_rewrites_amounts_and_nothing_else(raw, shown):
    assert money.style(raw) == shown
    assert money.style(shown) == shown  # already in the app's style: unchanged


def test_style_all_leaves_links_and_ids_alone():
    body = {"to": "358401234567", "type": "interactive", "interactive": {
        "body": {"text": "Pay 771.82 € rent?"},
        "action": {"name": "cta_url", "parameters": {"display_text": "Pay 771.82 €", "url": "https://x.example/pay?a=50%20b&v=12.5%"}}}}
    out = money.style_all(body)
    assert out["to"] == "358401234567"
    assert out["interactive"]["body"]["text"] == "Pay 771,82 € rent?"
    p = out["interactive"]["action"]["parameters"]
    assert p["display_text"] == "Pay 771,82 €" and p["url"] == body["interactive"]["action"]["parameters"]["url"]
    assert money.style("Hi%20Oiva") == "Hi%20Oiva"


@pytest.mark.parametrize("token, value, decimals", [
    ("412,30", 412.3, 2), ("1 234,56", 1234.56, 2), (f"1{NB}234,5", 1234.5, 1), ("1,234.50", 1234.5, 2),
    ("1.234,50", 1234.5, 2), ("12,5", 12.5, 1),
])
def test_guard_parses_comma_decimals(token, value, decimals):
    assert parse(token) == (value, decimals)


def test_guard_backs_app_style_amounts_with_tool_numbers():
    tool = ['{"safe_to_spend_eur": 412.3, "rent_eur": 1234.56, "share": 12.5}']
    checked, bad = check("You have *412,30 €* left; rent is 1 234,56 € and 12,5 % goes to food.", tool)
    assert bad == [] and len(checked) == 3
    assert check(f"About 1{NB}234,6 € in rent.", tool)[1] == []  # rounding to one decimal is fine
    assert check("You have 412,31 € left.", tool)[1] == ["412,31"]  # a cent off is caught
    assert check("Rent is 1 243,56 €.", tool)[1] == ["1 243,56"]


def test_guard_reads_the_persons_own_comma_amounts():
    assert check("A 250,50 € budget, as you said.", ["set my budget to 250,50"])[1] == []
