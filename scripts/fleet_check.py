"""Run the engine on every customer in the S-Pankki dataset, not just the demo persona.

Usage: python scripts/fleet_check.py [--data-dir DIR] [--sample N] [--jobs J]
Reads the 346 MB transactions CSV once, builds an Engine per customer (same demo date), calls every read-only
method the tools and the dashboard use, and records exceptions, impossible numbers and timing.
Writes docs/fleet-check.json. Nothing is saved to data/state.json: each engine gets a throwaway state path.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.engine import Engine  # noqa: E402

TODAY = date(2026, 10, 6)
COLS = ["customer_id", "tx_date", "tx_timestamp", "category", "tx_amount_eur", "balance_after_eur",
        "transaction_type", "merchant_id", "mcc_code"]
ABSURD = 1e7  # no personal-account figure should reach ten million euros

# Read-only calls: (name, function). Same arguments the tools and the dashboard pass by default.
CALLS = [
    ("spending", lambda e: e.spending(None, "last_month")),
    ("spending_category", lambda e: e.spending("Restaurants", "last_3_months")),
    ("spending_merchant_type", lambda e: e.spending(None, "last_month", merchant_type="taxi")),
    ("baskets", lambda e: e.baskets("last_3_months")),
    ("upcoming_bills", lambda e: e.upcoming_bills(14)),
    ("next_payday", lambda e: e.next_payday()),
    ("safe_to_spend", lambda e: e.safe_to_spend()),
    ("safe_to_save", lambda e: e.safe_to_save()),
    ("afford", lambda e: e.afford(50.0, "test")),
    ("simulate_cut", lambda e: e.simulate_cut("Restaurants", 20)),
    ("spender_profile", lambda e: e.spender_profile()),
    ("commitments", lambda e: e.commitments()),
    ("alert_bills", lambda e: e.alert("bills")),
    ("monthly_flow", lambda e: e.monthly_flow()),
    ("full_picture", lambda e: e.full_picture()),
    ("portfolio", lambda e: e.portfolio()),
    ("home_readiness", lambda e: e.home_readiness()),
    ("salary_nudge", lambda e: e.salary_nudge()),
    ("alert_unusual", lambda e: e.alert("unusual")),
    ("overview", lambda e: e.overview()),
    ("explore", lambda e: e.explore()),  # MyData explorer, when present
]


def load(data_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(data_dir / "tx_junction_transactions.csv", encoding="utf-8-sig", usecols=COLS,
                     dtype={"customer_id": "category", "category": "category", "transaction_type": "category",
                            "merchant_id": "string", "mcc_code": "string", "tx_date": "string"})
    df["_ts"] = pd.to_datetime(df["tx_timestamp"], format="%d%b%y:%H:%M:%S")
    return df.sort_values(["customer_id", "_ts"], kind="stable")


def load_ages(data_dir: Path) -> dict[str, int]:
    c = pd.read_csv(data_dir / "tx_customers_junction.csv", encoding="utf-8-sig")
    return dict(zip(c["customer_id"], c["customer_age"].astype(int)))


def numbers(x, path=""):
    """Every float in a nested result, with where it sits."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield from numbers(v, f"{path}.{k}")
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            yield from numbers(v, f"{path}[{i}]")
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        yield path, float(x)


def sanity(out: dict, age: int | None = None) -> list[str]:
    """Impossible or misleading results. Each string is a failure class."""
    bad = []
    for name, res in out.items():
        for path, v in numbers(res):
            if not math.isfinite(v):
                bad.append(f"{name}: NaN/inf at {re.sub(r'[0-9]+', 'N', path)}")
            elif abs(v) > ABSURD:
                bad.append(f"{name}: absurd number at {re.sub(r'[0-9]+', 'N', path)}")
    s = out.get("safe_to_spend")
    if s:
        pay = s["next_payday"]
        if pay.get("date") is not None and not 0 <= pay["days_away"] <= 45:
            bad.append("safe_to_spend: payday outside 0-45 days")
        if s["safe_to_spend_until_payday_eur"] < 0 and (s["safe_per_day_eur"] > 0 or s["safe_this_week_eur"] > 0):
            bad.append("safe_to_spend: negative total shown as a positive daily amount")
        if any(b["due_date"] < TODAY.isoformat() for b in s["bills_before_payday"]):
            bad.append("safe_to_spend: bill due in the past")
        if s["safe_to_spend_until_payday_eur"] > s["cycle_budget_eur"] + 0.01:
            bad.append("safe_to_spend: more than the pay cycle sustains")
        if s["reserve_eur"] < 0 or (s["reserve_eur"] > 0 and abs(
                s["safe_to_spend_until_payday_eur"] + s["reserve_eur"] - s["left_after_bills_eur"]) > 0.02):
            bad.append("safe_to_spend: safe + reserve is not what is left after bills")
    for b in out.get("upcoming_bills") or []:
        if b["due_date"] and b["due_date"] < TODAY.isoformat():
            bad.append("upcoming_bills: bill due in the past")
            break
    for c in (out.get("commitments") or {}).get("regular_payments", []):
        if c["next_expected"] and c["next_expected"] < TODAY.isoformat():
            bad.append(f"commitments: {c['id']} expected in the past")
        if c["status"] == "active" and c["per_month_eur"] <= 0:
            bad.append(f"commitments: active {c['id']} costs nothing")
        if abs(c["per_year_eur"] - c["per_month_eur"] * 12) > 0.01:
            bad.append(f"commitments: {c['id']} yearly is not 12 x monthly")
    b = out.get("baskets")
    if b and b["total_spent_eur"] > 0:
        shares = [x["share_pct"] for x in b["baskets"]]
        if not 98 <= sum(shares) <= 102 or min(shares) < 0:
            bad.append("baskets: shares do not add up to 100%")
    a, safe = out.get("afford"), out.get("safe_to_spend")
    if a and safe and a["fits_safe_to_spend"] and safe["safe_to_spend_until_payday_eur"] < a["price_eur"]:
        bad.append("afford: says it fits while safe to spend is lower")
    ss = out.get("safe_to_save")
    if ss and ss["now_max_eur"] > max(0.0, ss["safe_to_spend_until_payday_eur"]) + ss["reserve_eur"] + 0.01:
        bad.append("safe_to_save: more than safe to spend plus the reserve")
    h = out.get("home_readiness")
    if h and "deposit" in h and not 0 <= h["deposit"]["covered_pct"] <= 100:
        bad.append("home_readiness: covered share outside 0-100%")
    sp = out.get("spender_profile")
    if sp and sp["savings_rate_pct"] is not None and sp["savings_rate_pct"] > 1000:
        bad.append("spender_profile: savings rate above 1000%")
    if sp and age is not None and sp["peers"]["group"]:
        lo, hi = map(int, sp["peers"]["group"].rsplit(" ", 1)[1].split("-"))
        if not lo <= age <= hi:
            bad.append("spender_profile: peers from another age band")
    return bad


def signature(name: str, exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else ""
    msg = re.sub(r"CUST\d+", "CUST", re.sub(r"-?\d+(\.\d+)?", "N", msg))[:100]
    return f"{name}: {type(exc).__name__}: {msg}"


def check_customer(cid: str, rows: pd.DataFrame, state_dir: str, age: int | None = None) -> dict:
    t0 = time.perf_counter()
    rec = {"customer_id": cid, "transactions": len(rows), "exceptions": [], "issues": [], "timing_ms": {}}
    state = Path(state_dir) / f"{cid}.json"
    try:
        e = Engine(today=TODAY, state_path=state, frame=rows, age=age)
    except Exception as exc:  # the engine could not even load this customer
        rec["exceptions"].append({"method": "init", "class": signature("init", exc),
                                  "trace": traceback.format_exc(limit=-3)[-600:]})
        return rec
    out = {}
    for name, fn in CALLS:
        if name == "explore" and not hasattr(e, "explore"):
            continue
        t = time.perf_counter()
        try:
            out[name] = fn(e)
        except Exception as exc:
            rec["exceptions"].append({"method": name, "class": signature(name, exc),
                                      "trace": traceback.format_exc(limit=-3)[-600:]})
        rec["timing_ms"][name] = round((time.perf_counter() - t) * 1000, 1)
    rec["issues"] = sanity(out, age)
    if state.exists():
        rec["issues"].append("state: a read-only call wrote state")
    rec["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    # Figures for the fleet distribution
    s, b, f = out.get("safe_to_spend"), out.get("baskets"), out.get("monthly_flow")
    rec["figures"] = {
        "balance_eur": e.balance() if s else None,
        "safe_to_spend_eur": s["safe_to_spend_until_payday_eur"] if s else None,
        "safe_per_day_eur": s["safe_per_day_eur"] if s else None,
        "safe_this_week_eur": s["safe_this_week_eur"] if s else None,
        "cycle_budget_eur": s["cycle_budget_eur"] if s else None,
        "reserve_eur": s["reserve_eur"] if s else None,
        "limited_by_cycle": bool(s and s["limited_by"] == "pay_cycle"),
        "days_to_payday": s["next_payday"]["days_away"] if s else None,
        "payday_found": bool(s and s["next_payday"].get("date")),
        "rent_found": any(r.transaction_type == "housing_payment" for r in e.recurring()),
        "commitments": sorted(f"{x['id']}:{x['status']}" for x in (out.get("commitments") or {}).get(
            "regular_payments", [])),
        "card_subscriptions": (out.get("commitments") or {}).get("subscriptions_found", 0),
        "utilities_fixed_days": any(x.get("fixed_days") for x in (out.get("commitments") or {}).get(
            "regular_payments", [])),
        "utilities_up": any(x["id"] == "utilities" and (x.get("trend") or {}).get("direction") == "up"
                            for x in (out.get("commitments") or {}).get("regular_payments", [])),
        "utilities_unusual_bill": any(x.get("unusual_bill") for x in (out.get("commitments") or {}).get(
            "regular_payments", [])),
        "bill_alert": (out.get("alert_bills") or {}).get("type"),
        "left_per_month_eur": f["left_per_month_eur"] if f else None,
        **({f"share_{x['basket']}_pct": x["share_pct"] for x in b["baskets"]} if b else {}),
        "notes": sorted(name for name, res in out.items() if isinstance(res, dict) and res.get("not_enough_data")),
    }
    return rec


def _work(args):
    chunk, state_dir = args
    return [check_customer(cid, rows, state_dir, age) for cid, rows, age in chunk]


def stats(values: list[float]) -> dict | None:
    v = pd.Series([x for x in values if x is not None], dtype=float)
    if v.empty:
        return None
    q = v.quantile([0, 0.05, 0.25, 0.5, 0.75, 0.95, 1]).round(2).tolist()
    return {"n": int(len(v)), "min": q[0], "p5": q[1], "p25": q[2], "median": q[3], "p75": q[4], "p95": q[5],
            "max": q[6], "mean": round(float(v.mean()), 2), "negative": int((v < 0).sum())}


def summarise(recs: list[dict], seconds: float) -> dict:
    exc_classes, issue_classes = defaultdict(list), defaultdict(list)
    for r in recs:
        for x in r["exceptions"]:
            exc_classes[x["class"]].append(r["customer_id"])
        for i in sorted(set(r["issues"])):
            issue_classes[i].append(r["customer_id"])
    traces = {}
    for r in recs:
        for x in r["exceptions"]:
            traces.setdefault(x["class"], x["trace"])
    fig = [r.get("figures", {}) for r in recs]
    per_method = defaultdict(list)
    for r in recs:
        for k, v in r["timing_ms"].items():
            per_method[k].append(v)
    return {
        "as_of": TODAY.isoformat(),
        "customers": len(recs),
        "transactions": int(sum(r["transactions"] for r in recs)),
        "customers_with_exceptions": sum(1 for r in recs if r["exceptions"]),
        "exceptions": sum(len(r["exceptions"]) for r in recs),
        "customers_with_issues": sum(1 for r in recs if r["issues"]),
        "exception_classes": {k: {"customers": len(v), "examples": v[:5], "trace": traces[k]}
                              for k, v in sorted(exc_classes.items(), key=lambda kv: -len(kv[1]))},
        "issue_classes": {k: {"customers": len(v), "examples": v[:5]}
                          for k, v in sorted(issue_classes.items(), key=lambda kv: -len(kv[1]))},
        "data_facts": {
            "payday_found": sum(1 for f in fig if f.get("payday_found")),
            "rent_found": sum(1 for f in fig if f.get("rent_found")),
            "negative_balance": sum(1 for f in fig if (f.get("balance_eur") or 0) < 0),
            "negative_safe_to_spend": sum(1 for f in fig if (f.get("safe_to_spend_eur") or 0) < 0),
            "safe_to_spend_limited_by_pay_cycle": sum(1 for f in fig if f.get("limited_by_cycle")),
            "zero_cycle_budget": sum(1 for f in fig if f.get("cycle_budget_eur") == 0),
            "not_enough_data_notes": dict(Counter(n for f in fig for n in f.get("notes", []))),
            "commitments": dict(sorted(Counter(c for f in fig for c in f.get("commitments", [])).items())),
            "customers_with_card_subscriptions": sum(1 for f in fig if f.get("card_subscriptions")),
            "utilities_on_fixed_days": sum(1 for f in fig if f.get("utilities_fixed_days")),
            "utilities_went_up": sum(1 for f in fig if f.get("utilities_up")),
            "utilities_unusual_bill": sum(1 for f in fig if f.get("utilities_unusual_bill")),
            "bill_alert_types": dict(Counter(str(f.get("bill_alert")) for f in fig)),
        },
        "distribution": {k: stats([f.get(k) for f in fig]) for k in (
            "balance_eur", "safe_to_spend_eur", "safe_per_day_eur", "safe_this_week_eur", "cycle_budget_eur",
            "reserve_eur", "days_to_payday", "left_per_month_eur",
            "share_regular_pct", "share_irregular_pct", "share_everyday_pct")},
        "timing": {"wall_seconds": round(seconds, 1),
                   "per_customer_ms": stats([r.get("total_ms") for r in recs]),
                   "per_method_median_ms": {k: round(float(pd.Series(v).median()), 1) for k, v in per_method.items()}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT.parent / "ref" / "Junction-2026-S-pankki"))
    ap.add_argument("--sample", type=int, default=0, help="check N random customers (0 = all)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "docs" / "fleet-check.json"))
    a = ap.parse_args()

    t0 = time.perf_counter()
    df = load(Path(a.data_dir))
    ids = list(df["customer_id"].cat.categories)
    if a.sample:
        ids = sorted(pd.Series(ids).sample(min(a.sample, len(ids)), random_state=a.seed))
    keep = set(ids)
    print(f"loaded {len(df):,} rows in {time.perf_counter() - t0:.0f}s; checking {len(ids):,} customers", flush=True)

    ages = load_ages(Path(a.data_dir))
    groups = [(str(cid), g.drop(columns="_ts").astype({c: object for c in ("customer_id", "category",
                                                                             "transaction_type")}),
               ages.get(str(cid)))
              for cid, g in df.groupby("customer_id", observed=True, sort=True) if cid in keep]
    del df
    with tempfile.TemporaryDirectory() as state_dir:
        chunks = [(groups[i::a.jobs * 8], state_dir) for i in range(a.jobs * 8)]
        recs = []
        if a.jobs > 1:
            with ProcessPoolExecutor(a.jobs) as pool:
                for part in pool.map(_work, chunks):
                    recs += part
                    print(f"  {len(recs):,}/{len(groups):,}", flush=True)
        else:
            for c in chunks:
                recs += _work(c)
    recs.sort(key=lambda r: r["customer_id"])
    summary = summarise(recs, time.perf_counter() - t0)
    summary["sample"] = a.sample or None
    Path(a.out).write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({k: summary[k] for k in ("customers", "customers_with_exceptions", "exceptions",
                                               "customers_with_issues")}, indent=2))
    for k, v in summary["exception_classes"].items():
        print(f"  EXC {v['customers']:5d}  {k}")
    for k, v in summary["issue_classes"].items():
        print(f"  BAD {v['customers']:5d}  {k}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
