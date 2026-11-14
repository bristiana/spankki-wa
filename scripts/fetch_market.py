"""Snapshot real monthly closing prices of the instruments behind the demo funds into data/market.json.

Source: Yahoo Finance chart API (adjusted close, EUR, Xetra). Run again to refresh:
  python scripts/fetch_market.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

SYMBOLS = {"VWCE.DE": "Vanguard FTSE All-World UCITS ETF (Acc)", "XEON.DE": "Xtrackers EUR Overnight Rate Swap UCITS ETF"}
OUT = Path(__file__).resolve().parents[1] / "data" / "market.json"


def fetch(sym: str) -> dict:
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5y&interval=1mo",
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    d = r.json()["chart"]["result"][0]
    closes = d["indicators"].get("adjclose", [{}])[0].get("adjclose") or d["indicators"]["quote"][0]["close"]
    months = {}
    for ts, c in zip(d["timestamp"], closes):
        if c:
            months[datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")] = round(float(c), 4)
    return {"name": SYMBOLS[sym], "currency": d["meta"].get("currency"), "monthly_close": months,
            "last_price": round(float(d["meta"]["regularMarketPrice"]), 4),
            "last_date": datetime.fromtimestamp(d["meta"]["regularMarketTime"], timezone.utc).date().isoformat()}


if __name__ == "__main__":
    data = {"source": "Yahoo Finance, monthly adjusted close", "fetched": datetime.now(timezone.utc).date().isoformat(),
            "symbols": {s: fetch(s) for s in SYMBOLS}}
    OUT.write_text(json.dumps(data, indent=1))
    for s, v in data["symbols"].items():
        print(s, len(v["monthly_close"]), "months, last", v["last_price"], v["last_date"])
