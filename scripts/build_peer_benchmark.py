"""'How people like you live here': monthly budgets of S-Pankki customers by area (first two digits of the postal
code) and age band, from the challenge dataset. Writes the small data/peer_benchmark.json that Engine.new_life_budget
reads, and data/persona_areas.json (the app's demo customers' own postal area, so the card can default to it).

  python scripts/build_peer_benchmark.py [--data-dir DIR]

Per customer: monthly averages over the last 12 full months of the data (net salary, net income, spending per
category and basket, taxis and fast food by merchant code). Per group: p25 / median / p75 over its customers, only
for groups of at least 30 customers (app/peers.py MIN_GROUP). Smaller groups are left out entirely.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.engine import BASKETS  # noqa: E402
from app.peers import (AGE_BANDS, AREAS, BASKET_KEYS, CATEGORIES, MCC_METRICS, METRICS, MIN_GROUP,  # noqa: E402
                       SALARY_MONTHS, STARTING_OUT, WIDE_BANDS, WINDOW_MONTHS, band_of, group_key)

OUT = ROOT / "data" / "peer_benchmark.json"
AREAS_OUT = ROOT / "data" / "persona_areas.json"
COLS = ["customer_id", "tx_date", "category", "tx_amount_eur", "transaction_type", "mcc_code"]


def window(last_date: str) -> list[str]:
    """The 12 full months ending with the last complete month of the data ('YYYY-MM', oldest first)."""
    end = pd.Period(last_date[:7], "M")
    if pd.Timestamp(last_date) != end.to_timestamp(how="end").normalize():
        end -= 1  # the last month is not complete
    return [str(end - i) for i in range(WINDOW_MONTHS - 1, -1, -1)]


def per_customer(tx_csv: Path, chunksize: int = 1_000_000) -> tuple[pd.DataFrame, list[str]]:
    """One row per customer with monthly averages (positive euros for spending) over the window."""
    # Pass 1 is folded into pass 2: keep sums per customer and month, then cut the window once the end is known.
    sums, first, last = [], {}, ""
    for ch in pd.read_csv(tx_csv, encoding="utf-8-sig", usecols=COLS, chunksize=chunksize,
                          dtype={"mcc_code": "string", "customer_id": "string"}):
        ch["m"] = ch.tx_date.str[:7]
        last = max(last, ch.tx_date.max())
        for c, d in ch.groupby("customer_id").tx_date.min().items():
            first[c] = min(first.get(c, d), d)
        key = ch.category.where(ch.category.isin(CATEGORIES))
        parts = [ch.assign(k=key).dropna(subset=["k"])]
        for name, codes in MCC_METRICS.items():
            parts.append(ch[ch.mcc_code.isin(codes)].assign(k=name))
        parts.append(ch[ch.transaction_type.isin(["salary", "other_income"])].assign(k="income"))
        sal = ch[ch.transaction_type == "salary"]
        parts.append(sal.assign(k="salary"))
        sums.append(pd.concat(parts).groupby(["customer_id", "m", "k"]).tx_amount_eur.sum())
    months = window(last)
    s = pd.concat(sums).groupby(level=[0, 1, 2]).sum().reset_index()
    s = s[s.m.isin(months)]
    salary_months = s[(s.k == "salary") & (s.tx_amount_eur > 0)].groupby("customer_id").m.nunique()
    total = s.groupby(["customer_id", "k"]).tx_amount_eur.sum().unstack(fill_value=0.0)
    for k in CATEGORIES + list(MCC_METRICS) + ["income", "salary"]:
        if k not in total:
            total[k] = 0.0
    # A short history must not dilute the averages: divide by the window months the history covers.
    covered = pd.Series({c: sum(1 for m in months if m >= f[:7]) for c, f in first.items()}, dtype=float)
    covered = covered.reindex(total.index).clip(lower=1)
    out = pd.DataFrame(index=total.index)
    for k in CATEGORIES + list(MCC_METRICS):
        out[k] = -total[k] / covered
    out["income"] = total["income"] / covered
    out["salary"] = total["salary"] / covered
    for b in BASKET_KEYS:
        out[b] = out[BASKETS[b]["categories"]].sum(axis=1)
    out["total"] = out[CATEGORIES].sum(axis=1)
    out["salary_months"] = salary_months.reindex(out.index).fillna(0).astype(int)
    out["months_covered"] = covered.astype(int)
    return out.reset_index().rename(columns={"index": "customer_id"}), months


def q(s: pd.Series) -> dict:
    return {k: round(float(s.quantile(p)), 2) for k, p in (("p25", .25), ("median", .5), ("p75", .75))}


def summarise(rows: pd.DataFrame) -> dict:
    return {"n": int(len(rows)), "metrics": {m: q(rows[m]) for m in METRICS}}


def groups(pc: pd.DataFrame) -> dict:
    """Every group of at least MIN_GROUP customers. pc: per-customer rows with area, age, salary_months, metrics."""
    pc = pc.assign(band=pc.customer_age.map(band_of),
                   wide=pc.customer_age.map(lambda a: band_of(a, WIDE_BANDS)),
                   starting=(pc.customer_age.between(*STARTING_OUT)) & (pc.salary_months >= SALARY_MONTHS))
    out: dict[str, dict] = {}

    def add(key, rows, **meta):
        if len(rows) >= MIN_GROUP:  # the privacy rule: smaller groups never leave this script
            out[key] = {**meta, **summarise(rows)}

    add("all", pc, area=None, age_band=None, starting_out=False)
    for area, ga in pc.groupby("area"):
        add(group_key(area), ga, area=area, age_band=None, starting_out=False)
        for col in ("band", "wide"):
            for band, gb in ga.groupby(col):
                add(group_key(area, band), gb, area=area, age_band=band, starting_out=False)
        add(group_key(area, starting_out=True), ga[ga.starting], area=area, age_band=None, starting_out=True)
    for col in ("band", "wide"):
        for band, gb in pc.groupby(col):
            add(group_key(None, band), gb, area=None, age_band=band, starting_out=False)
    add(group_key(starting_out=True), pc[pc.starting], area=None, age_band=None, starting_out=True)
    return out


def build(d: Path, personas: list[str] | None = None) -> tuple[dict, dict]:
    cust = pd.read_csv(d / "tx_customers_junction.csv", encoding="utf-8-sig",
                       dtype={"customer_postal_code": str, "customer_id": str})
    cust["area"] = cust.customer_postal_code.str.zfill(5).str[:2]
    cust = cust[cust.area.isin(AREAS)]
    pc, months = per_customer(d / "tx_junction_transactions.csv")
    pc = pc.merge(cust[["customer_id", "area", "customer_age"]], on="customer_id")
    g = groups(pc)
    bench = {
        "source": "S-Pankki Junction 2026 challenge dataset: transactions of 5,100 customers in the capital region "
                  "(synthetic, modelled on real S-Bank data)",
        "period": {"from": months[0], "to": months[-1], "months": len(months)},
        "min_group_size": MIN_GROUP,
        "customers": int(len(pc)),
        "areas": {k: {**v, "customers": int((pc.area == k).sum())} for k, v in AREAS.items()},
        "age_bands": [band_of(lo) for lo, _ in AGE_BANDS],
        "wide_age_bands": [band_of(lo, WIDE_BANDS) for lo, _ in WIDE_BANDS],
        "starting_out": {"ages": list(STARTING_OUT), "salary_months_at_least": SALARY_MONTHS,
                         "note": "Aged 18-29 with a salary credited in at least 10 of the 12 months"},
        "metric_notes": {
            "spending": "Net euros a month per category (refunds count), positive = spent",
            "income": "Salary and other income credited a month (net, after tax)",
            "baskets": {b: BASKETS[b]["categories"] for b in BASKET_KEYS},
            "taxi": "Merchant code 4121", "fast_food": "Merchant code 5814"},
        "groups": dict(sorted(g.items())),
    }
    areas = {}
    for cid in personas or []:
        r = cust[cust.customer_id == cid]
        if len(r):
            areas[cid] = {"area": r.area.iloc[0], "age": int(r.customer_age.iloc[0])}
    return bench, {"note": "The app's demo customers' own postal area (first two digits) and age, so 'people like "
                           "you' starts from their own group. No transactions.", "customers": areas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT.parent / "ref" / "Junction-2026-S-pankki"))
    a = ap.parse_args()
    index = json.loads((ROOT / "data" / "personas" / "index.json").read_text(encoding="utf-8"))
    bench, areas = build(Path(a.data_dir), [p["id"] for p in index])
    OUT.write_text(json.dumps(bench, indent=1, ensure_ascii=False), encoding="utf-8")
    AREAS_OUT.write_text(json.dumps(areas, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, v in bench["groups"].items():
        m = v["metrics"]
        print(f"{k:32s} n={v['n']:5d}  total {m['total']['median']:8.2f}  housing {m['Housing']['median']:8.2f}  "
              f"income {m['income']['median']:8.2f}")
    print(json.dumps(areas, indent=1))


if __name__ == "__main__":
    main()
