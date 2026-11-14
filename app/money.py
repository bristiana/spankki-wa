"""Amounts the way the S-mobiili app writes them: 1 234,56 € (a space between thousands, a decimal comma, a space
before the euro sign) and 12,5 %. Every text people see passes through style() on its way out (WhatsApp, the web
chat, the pages), so the engine and the tool results can keep plain 1234.56 numbers."""
from __future__ import annotations

import re

from .guard import SPACES, parse

NB = " "
MINUS = "−"
_NUM = rf"\d{{1,3}}(?:[{SPACES},.]\d{{3}})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
# One pass, so an amount already handled never lends its € to the next number ("42,10 € 3 times").
_AMOUNT = re.compile(rf"(?<![0-9A-Za-z.,])({_NUM})[{SPACES}]?€|(?<!\d[{SPACES}])(?<!\d)€[{SPACES}]?({_NUM})(?![\w.,]?\d)")
_PCT = re.compile(rf"(?<![0-9A-Za-z.,])(\d+)(?:[.,](\d+))?[{SPACES}]?%(?![0-9A-Fa-f]{{2}})")
_URL = re.compile(r"https?://\S+")


def eur(x: float, decimals: int = 2, nb: bool = False) -> str:
    """1234.5 -> "1 234,50 €"; nb=True for a web page, where the amount must never break across lines."""
    sp = NB if nb else " "
    s = f"{abs(x):,.{decimals}f}".replace(",", "\0").replace(".", ",").replace("\0", sp)
    return (MINUS if x < 0 else "") + s + sp + "€"


def _amount(m: re.Match) -> str:
    value, decimals = parse(m.group(1) or m.group(2))
    if decimals > 2:  # "1.234 €" is ambiguous once written with a comma: leave it as it came
        return m.group(0)
    return eur(value, 2 if decimals else 0)


def style(text: str) -> str:
    """The app's style for every amount and percentage in a text. Idempotent; other numbers stay as they are."""
    if not text or ("€" not in text and "%" not in text):
        return text
    out, at = [], 0
    for u in _URL.finditer(text):  # links inside a text stay exactly as they are
        out += [_style_words(text[at:u.start()]), u.group(0)]
        at = u.end()
    return "".join(out + [_style_words(text[at:])])


def _style_words(text: str) -> str:
    text = _AMOUNT.sub(_amount, text)
    return _PCT.sub(lambda m: m.group(1) + ("," + m.group(2) if m.group(2) else "") + " %", text)


def style_all(obj):
    """style() on every string inside a JSON-like value (a WhatsApp payload, a web chat answer). URLs carry no €."""
    if isinstance(obj, str):
        return obj if obj.startswith(("http://", "https://", "/")) else style(obj)
    if isinstance(obj, list):
        return [style_all(v) for v in obj]
    if isinstance(obj, dict):
        return {k: v if k in ("url", "link", "to", "id") else style_all(v) for k, v in obj.items()}
    return obj
