"""Pick extra demo customers from the 346 MB S-Pankki CSV by their real stats, so judges can switch the coach to them.

Usage: python scripts/extract_personas.py [--data-dir DIR] [--months 24]
Writes data/personas/<CUSTID>.csv (the last --months of each one's rows) and data/personas/index.json. The default
persona CUST04831 ("Aino", data/persona.csv) stays first in the index and is not rewritten.

One pass collects per-customer stats over the 12 months before the demo date; each pick is a rule on those stats:
- student: age <= 22 with a regular salary and rent, the lowest income;
- high earner: a regular salary in the top 2% and the largest balance among them;
- irregular income: salary in at most 6 of the 12 months, rent paid, the most uneven salary;
- older homeowner: age >= 60 with the largest steady housing payment (mortgage-like), regular income.
A second pass writes the chosen customers' rows.
"""
import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "personas"
TODAY = pd.Timestamp("2026-10-06")
COLS = ["customer_id", "bank_account_id", "tx_date", "tx_timestamp", "category", "tx_amount_eur",
        "balance_after_eur", "transaction_type", "merchant_id", "mcc_code"]
DEFAULT = {"id": "CUST04831", "name": "Aino", "file": None, "age": 24, "label": "Demo persona, 24",
           "description": "The demo persona: steady salary, rent, monthly investing and dining out."}
NAMES = {"student": "Eetu", "high_earner": "Helena", "irregular": "Mikko", "homeowner": "Pirkko"}
OUTFLOW = {"purchase", "utilities_payment", "housing_payment"}


def read(d: Path, **kw):
    return pd.read_csv(d / "tx_junction_transactions.csv", encoding="utf-8-sig", usecols=COLS,
                       dtype={"mcc_code": "string", "merchant_id": "string"}, chunksize=500_000, **kw)


def stats(d: Path, cust: pd.DataFrame) -> pd.DataFrame:
    since = (TODAY - pd.DateOffset(months=12)).strftime("%Y-%m-%d")
    monthly, last = [], []
    for ch in read(d):
        ts = pd.to_datetime(ch.tx_timestamp, format="%d%b%y:%H:%M:%S")
        last.append(ch.assign(ts=ts).sort_values("ts").groupby("customer_id").tail(1)[["customer_id", "ts", "balance_after_eur"]])
        c = ch[ch.tx_date >= since]
        monthly.append(c.assign(month=c.tx_date.str[:7]).groupby(["customer_id", "month", "transaction_type"])
                       .tx_amount_eur.sum())
    m = pd.concat(monthly).groupby(level=[0, 1, 2]).sum().unstack(fill_value=0.0)
    bal = pd.concat(last).sort_values("ts").groupby("customer_id").balance_after_eur.last()
    g = m.groupby(level=0)
    sal = m["salary"]
    s = pd.DataFrame({
        "salary_months": (sal > 0).groupby(level=0).sum(),
        "salary_pm": g["salary"].sum() / 12,
        "salary_cv": sal[sal > 0].groupby(level=0).std() / sal[sal > 0].groupby(level=0).mean(),
        "income_pm": (g["salary"].sum() + g["other_income"].sum()) / 12,
        "other_pm": g["other_income"].sum() / 12,
        "housing_months": (m["housing_payment"] < 0).groupby(level=0).sum(),
        "housing_pm": -g["housing_payment"].sum() / 12,
        "housing_cv": (-m["housing_payment"][m["housing_payment"] < 0]).groupby(level=0).std()
                      / (-m["housing_payment"][m["housing_payment"] < 0]).groupby(level=0).mean(),
        "spend_pm": -sum(g[t].sum() for t in OUTFLOW if t in m) / 12,
        "balance": bal,
    })
    s["left_pm"] = s.income_pm - s.spend_pm
    return s.join(cust.set_index("customer_id"))


def pick(s: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """kind -> (customer id, why). Rules only; no hand-picked ids."""
    s = s.drop(index=DEFAULT["id"], errors="ignore")
    regular = s[(s.salary_months >= 11) & (s.salary_cv.fillna(1) < 0.25) & (s.housing_months >= 11)]
    out = {}

    young = regular[regular.customer_age <= 22]
    st = young.sort_values("income_pm").iloc[0]
    out["student"] = (st.name, f"age {st.customer_age}, {st.income_pm:,.0f} € income a month, the lowest of the "
                               f"{len(young)} customers aged 22 or under with a regular salary; {st.left_pm:,.0f} € a month "
                               "left after rent, bills and spending")
    top = regular[regular.salary_pm >= regular.salary_pm.quantile(0.98)].sort_values("balance").iloc[-1]
    out["high_earner"] = (top.name, f"salary {top.salary_pm:,.0f} € a month (top 2%) and the largest balance among "
                                    f"them, {top.balance:,.0f} €")
    pool = s[(s.salary_months <= 6) & (s.housing_months >= 11)]
    irr = pool.sort_values("salary_cv").iloc[-1]
    out["irregular"] = (irr.name, f"salary in only {int(irr.salary_months)} of the last 12 months and the most uneven "
                                  f"amounts of the {len(pool)} such customers who pay rent (varies {irr.salary_cv:.0%}), plus "
                                  f"{irr.other_pm:,.0f} € a month of other income; the engine finds no regular payday")
    old = regular[(regular.customer_age >= 60) & (regular.housing_cv.fillna(1) < 0.05)].sort_values("housing_pm").iloc[-1]
    out["homeowner"] = (old.name, f"age {old.customer_age}, the largest steady housing payment among customers aged 60+, "
                                  f"{old.housing_pm:,.0f} € every month, like a mortgage")
    return out


def place(postal: str) -> str:
    """Only the part of the postal code we are sure of: 00xxx is the city of Helsinki."""
    return ", Helsinki" if str(postal).startswith("00") else ""


LABEL = {"student": "Student-age", "high_earner": "High earner", "irregular": "Irregular income",
         "homeowner": "Homeowner"}
DESCRIBE = {"student": "Student-age, tight budget: little left over each month.",
            "high_earner": "High earner with a big balance.",
            "irregular": "No regular salary: income comes in irregularly.",
            "homeowner": "Older customer with a large, steady housing payment (mortgage-like)."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT.parent / "ref" / "Junction-2026-S-pankki"))
    ap.add_argument("--months", type=int, default=24)
    a = ap.parse_args()
    d = Path(a.data_dir)
    cust = pd.read_csv(d / "tx_customers_junction.csv", encoding="utf-8-sig", dtype={"customer_postal_code": str})
    s = stats(d, cust)
    picks = pick(s)
    ids = {cid for cid, _ in picks.values()}

    since = (TODAY - pd.DateOffset(months=a.months)).strftime("%Y-%m-%d")
    rows = [ch[ch.customer_id.isin(ids) & (ch.tx_date >= since)] for ch in read(d)]
    rows = pd.concat(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    index = [DEFAULT]
    for kind, (cid, why) in picks.items():
        p = rows[rows.customer_id == cid].sort_values(
            "tx_timestamp", key=lambda x: pd.to_datetime(x, format="%d%b%y:%H:%M:%S"))
        p.to_csv(OUT / f"{cid}.csv", index=False)
        r = s.loc[cid]
        age = int(r.customer_age)
        index.append({"id": cid, "name": NAMES[kind], "file": f"{cid}.csv", "age": age,
                      "label": f"{LABEL[kind]}, {age}{place(r.customer_postal_code)}",
                      "description": DESCRIBE[kind], "why": why})
        print(f"{kind:12} {cid} {len(p):5} rows  {why}")
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
