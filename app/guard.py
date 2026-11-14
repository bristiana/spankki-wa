"""Number guard: every figure in a reply must come from a tool result or from the user's own message."""
from __future__ import annotations

import re

SPACES = "   "  # space, no-break space, narrow no-break space
# 1,234.50 · 1 234,50 · 1234.5 · 12,5 · 68.60 (not digits glued to letters, like CUST04831)
NUMBER = re.compile(rf"(?<![\w.,])(\d{{1,3}}(?:[{SPACES},.]\d{{3}})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?!\w)")
MONEY = re.compile(rf"^[{SPACES}]?(€|%|eur|euro)", re.I)


def parse(token: str) -> tuple[float, int]:
    """Number and its decimals, whatever the thousands and decimal separators."""
    t = re.sub(f"[{SPACES}]", "", token)
    if "," in t and "." in t:  # the last one is the decimal separator
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:  # 1,234 = thousands; 12,5 = decimal comma
        parts = t.split(",")
        t = "".join(parts) if len(parts) > 2 or len(parts[-1]) == 3 else t.replace(",", ".")
    elif t.count(".") > 1:  # 1.234.567
        t = t.replace(".", "")
    return float(t), len(t.split(".")[1]) if "." in t else 0


def numbers(text: str) -> list[tuple[str, float, int]]:
    return [(m.group(1), *parse(m.group(1))) for m in NUMBER.finditer(text or "")]


def ungrounded_numbers(reply: str, sources: list[str]) -> list[str]:
    """Numbers in `reply` that no source backs. A reply number may round a source number to its own decimals.
    Days and dates (up to 31), small counts and years need no source, unless written as money or a percentage."""
    known = {v for src in sources for _, v, _ in numbers(src)}
    out = []
    for m in NUMBER.finditer(reply or ""):
        token = m.group(1)
        value, decimals = parse(token)
        money = bool(MONEY.match(reply[m.end():])) or reply[:m.start()].rstrip().endswith("€")
        if decimals == 0 and (value <= 12 or (not money and (value <= 31 or 1990 <= value <= 2100))):
            continue
        if not any(abs(k - value) <= 0.5 * 10 ** -decimals + 1e-9 for k in known) and token not in out:
            out.append(token)
    return out
