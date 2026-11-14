"""Trading 212 Practice account (virtual money): places the real order behind a confirmed investment.

Inert until T212_API_KEY and T212_API_SECRET are set. Practice only: the base URL is the demo host,
so this can never move real money. The API takes quantities, not cash, so each leg is priced first
(Trading 212 position price, else Yahoo Finance) and we buy the largest fraction that fits.
"""
from __future__ import annotations

import base64
import logging
import math
import os

import httpx

from .engine import FUND_MIX

log = logging.getLogger("broker")
BASE = "https://demo.trading212.com/api/v0"
YAHOO = {"VWCEd_EQ": "VWCE.DE", "DBXTd_EQ": "XEON.DE"}
VALUE_BUFFER = 0.985  # leave room for price moves so the order never exceeds the amount


def configured() -> bool:
    return bool(os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET"))


def _headers() -> dict:
    tok = base64.b64encode(f"{os.environ['T212_API_KEY']}:{os.environ['T212_API_SECRET']}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Accept": "application/json"}


def _call(method: str, path: str, body: dict | None = None):
    r = httpx.request(method, BASE + path, headers=_headers(), json=body, timeout=10)
    try:
        data = r.json()
    except ValueError:
        data = None
    if r.status_code >= 400 or data is None:
        detail = (data or {}).get("clarification") or (data or {}).get("message") or r.text[:160]
        raise RuntimeError(f"Trading 212 {method} {path}: HTTP {r.status_code} {detail}")
    return data


def decimals_for(price: float) -> int:
    """Trading 212 fractional precision by share price."""
    if price < 0.1:
        return 0
    return min(7, math.floor(math.log10(price)) + 2)


def shares_for(value: float, price: float) -> float:
    d = decimals_for(price)
    return math.floor(value / price * 10 ** d + 1e-9) / 10 ** d


def price_of(ticker: str, positions: dict) -> float | None:
    p = positions.get(ticker, {}).get("currentPrice")
    if p:
        return float(p)
    sym = YAHOO.get(ticker)
    if not sym:
        return None
    try:
        r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d",
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        return None


def account() -> dict:
    return _call("GET", "/equity/account/cash")


def place(fund_id: str, amount_eur: float) -> dict:
    """Buy the fund's instruments for amount_eur. Returns a summary; never raises (errors are reported)."""
    if not configured():
        return {"status": "not_configured", "platform": "Trading 212 Practice"}
    try:
        positions = {p.get("ticker"): p for p in (_call("GET", "/equity/positions") or [])}
    except Exception as e:
        log.warning("positions failed: %s", e)
        positions = {}
    legs = []
    for leg in FUND_MIX[fund_id]:
        value = amount_eur * leg["pct"] / 100
        price = price_of(leg["ticker"], positions)
        if not price:
            legs.append({**leg, "status": "skipped", "reason": "no price"})
            continue
        qty = shares_for(value * VALUE_BUFFER, price)
        if qty <= 0:
            legs.append({**leg, "status": "skipped", "reason": "amount below one fraction"})
            continue
        try:
            o = _call("POST", "/equity/orders/market", {"ticker": leg["ticker"], "quantity": qty, "extendedHours": False})
            legs.append({**leg, "status": o.get("status", "PLACED"), "order_id": o.get("id"), "quantity": qty,
                         "price_eur": round(price, 2)})
        except Exception as e:
            log.warning("order failed: %s", e)
            legs.append({**leg, "status": "failed", "reason": str(e)[:160], "quantity": qty})
    ok = [l for l in legs if l["status"] not in ("failed", "skipped")]
    return {"status": "placed" if ok else "failed", "platform": "Trading 212 Practice", "legs": legs}
