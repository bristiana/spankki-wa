"""Pull one persona's transactions and an age-cohort benchmark out of the 346 MB S-Pankki CSV.

Usage: python scripts/extract_persona.py [--data-dir DIR] [--persona CUST04831]
Writes data/persona.csv and data/benchmark.json (monthly spend per category, median over the cohort).
"""
import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COLS = ["customer_id", "bank_account_id", "tx_date", "tx_timestamp", "category", "tx_amount_eur",
        "balance_after_eur", "transaction_type", "merchant_id", "mcc_code"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT.parent / "ref" / "Junction-2026-S-pankki"))
    ap.add_argument("--persona", default="CUST04831")
    a = ap.parse_args()
    d = Path(a.data_dir)

    cust = pd.read_csv(d / "tx_customers_junction.csv", encoding="utf-8-sig", dtype={"customer_postal_code": str})
    age = int(cust.loc[cust.customer_id == a.persona, "customer_age"].iloc[0])
    lo, hi = (age // 10) * 10, (age // 10) * 10 + 9  # e.g. 20-29
    cohort = set(cust[(cust.customer_age >= lo) & (cust.customer_age <= hi)].customer_id)

    persona, monthly = [], []
    for ch in pd.read_csv(d / "tx_junction_transactions.csv", encoding="utf-8-sig", usecols=COLS,
                          dtype={"mcc_code": "string", "merchant_id": "string"}, chunksize=500_000):
        persona.append(ch[ch.customer_id == a.persona])
        c = ch[ch.customer_id.isin(cohort)]
        monthly.append(c.assign(month=c.tx_date.str[:7]).groupby(["customer_id", "month", "category"])
                       .tx_amount_eur.sum())

    p = pd.concat(persona).sort_values("tx_timestamp", key=lambda s: pd.to_datetime(s, format="%d%b%y:%H:%M:%S"))
    p.to_csv(ROOT / "data" / "persona.csv", index=False)

    m = pd.concat(monthly).groupby(level=[0, 1, 2]).sum().unstack(fill_value=0.0)
    per_cust = m.groupby(level=0).mean()  # avg monthly amount per category, per customer
    bench = {
        "age_band": f"{lo}-{hi}",
        "customers": int(per_cust.shape[0]),
        "median_monthly": {k: round(float(v), 2) for k, v in per_cust.median().items()},
        "p25_monthly": {k: round(float(v), 2) for k, v in per_cust.quantile(0.25).items()},
        "p75_monthly": {k: round(float(v), 2) for k, v in per_cust.quantile(0.75).items()},
    }
    (ROOT / "data" / "benchmark.json").write_text(json.dumps(bench, indent=2))
    print(f"persona {a.persona}: {len(p)} tx, age {age}; cohort {bench['age_band']}: {bench['customers']} customers")


if __name__ == "__main__":
    main()
