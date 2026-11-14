"""Amounts the way the S-mobiili app writes them: 1 234,56 € (a space between thousands, a decimal comma, a space
before the euro sign) and 12,5 %. Every text people see passes through style() on its way out (WhatsApp, the web
chat, the pages), so the engine and the tool results can keep plain 1234.56 numbers."""
from __future__ import annotations

import re

from .guard import SPACES, parse

NB = " "
MINUS = "−"
_NUM = rf"\d{{1,3}}(?:[{SPACES},.]\d{{3}})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_AFTER = re.compile(rf"(?<![\w.,])({_NUM})[{SPACES}]?€")  # 412.30 € · 1,234.5€ · 412,30 €
_BEFORE = re.compile(rf"€[{SPACES}]?({_NUM})(?![\w.,]?\d)")  # €412.30 · € 1,234
_PCT = re.compile(rf"(?<![\w.,])(\d+)(?:[.,](\d+))?[{SPACES}]?%(?![0-9A-Fa-f]{{2}})")


def eur(x: float, decimals: int = 2, nb: bool = False) -> str:
    """1234.5 -> "1 234,50 €"; nb=True for a web page, where the amount must never break across lines."""
    sp = NB if nb else " "
    s = f"{abs(x):,.{decimals}f}".replace(",", "\0").replace(".", ",").replace("\0", sp)
    return (MINUS if x < 0 else "") + s + sp + "€"


def _amount(token: str) -> str:
    value, decimals = parse(token)
    return eur(value, 2 if decimals in (1, 2) else decimals)


def style(text: str) -> str:
    """The app's style for every amount and percentage in a text. Idempotent; other numbers stay as they are."""
    if not text or ("€" not in text and "%" not in text):
        return text
    text = _AFTER.sub(lambda m: _amount(m.group(1)), text)
    text = _BEFORE.sub(lambda m: _amount(m.group(1)), text)
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
