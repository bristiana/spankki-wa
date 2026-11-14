"""Pick extra demo customers from the 346 MB S-Pankki CSV by their real stats, so judges can switch the coach to them.

Usage: python scripts/extract_personas.py [--data-dir DIR] [--months 24]
Writes data/personas/<CUSTID>.csv (the last --months of each one's rows) and data/personas/index.json. The hero (the
app's demo default) comes first; the original persona CUST04831 ("Aino", data/persona.csv) stays second and is not
rewritten.

One pass collects per-customer stats over the 12 months before the demo date; each pick is a rule on those stats:
- hero (the demo default, the age of the median customer): age 38-45, a regular salary in the middle fifth for that
  age, a steady housing payment, utilities and monthly investing every month, never a negative balance, and in the last
  3 full months restaurants, fast food and at least 3 taxi rides. The engine then keeps the ones with a payday, an
  active housing payment, a safe to spend this week that is positive but modest (50-300 EUR) and a biggest "slips
  through your fingers" category where a 20% cut is worth at least 500 EUR a year; the pick has the salary closest to
  the age group's median;
- student: age <= 22 with a regular salary and rent, the lowest income;
- high earner: a regular salary in the top 2% and the largest balance among them;
- irregular income: salary in at most 6 of the 12 months, rent paid, the most uneven salary;
- older homeowner: age >= 60 with the largest steady housing payment (mortgage-like), regular income.
A second pass writes the chosen customers' rows.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "personas"
TODAY = pd.Timestamp("2026-10-06")
COLS = ["customer_id", "bank_account_id", "tx_date", "tx_timestamp", "category", "tx_amount_eur",
        "balance_after_eur", "transaction_type", "merchant_id", "mcc_code"]
DEFAULT = {"id": "CUST04831", "name": "Aino", "file": None, "age": 24, "label": "First job, 24, Espoo area",
           "description": "The original demo persona: steady salary, rent, monthly investing and dining out."}
HERO_AGES = (38, 45)
NAMES = {"hero": "Antti", "student": "Eetu", "high_earner": "Helena", "irregular": "Mikko", "homeowner": "Pirkko"}
OUTFLOW = {"purchase", "utilities_payment", "housing_payment"}
FAST_FOOD, TAXI = "5814", "4121"  # merchant category codes


def read(d: Path, **kw):
    return pd.read_csv(d / "tx_junction_transactions.csv", encoding="utf-8-sig", usecols=COLS,
                       dtype={"mcc_code": "string", "merchant_id": "string"}, chunksize=500_000, **kw)


def by_time(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.sort_values("tx_timestamp", key=lambda x: pd.to_datetime(x, format="%d%b%y:%H:%M:%S"))


def stats(d: Path, cust: pd.DataFrame) -> pd.DataFrame:
    since = (TODAY - pd.DateOffset(months=12)).strftime("%Y-%m-%d")
    q0, q1 = (TODAY - pd.DateOffset(months=3)).strftime("%Y-%m-01"), TODAY.strftime("%Y-%m-01")  # last 3 full months
    monthly, last, low, recent = [], [], [], []
    for ch in read(d):
        ts = pd.to_datetime(ch.tx_timestamp, format="%d%b%y:%H:%M:%S")
        last.append(ch.assign(ts=ts).sort_values("ts").groupby("customer_id").tail(1)[["customer_id", "ts", "balance_after_eur"]])
        c = ch[ch.tx_date >= since]
        monthly.append(c.assign(month=c.tx_date.str[:7]).groupby(["customer_id", "month", "transaction_type"])
                       .tx_amount_eur.sum())
        low.append(c.groupby("customer_id").balance_after_eur.min())
        r = ch[(ch.tx_date >= q0) & (ch.tx_date < q1)]
        recent.append(pd.DataFrame({
            "restaurants_3m": -r[r.category == "Restaurants"].groupby("customer_id").tx_amount_eur.sum(),
            "fast_food_3m": r[r.mcc_code == FAST_FOOD].groupby("customer_id").size(),
            "taxi_3m": r[r.mcc_code == TAXI].groupby("customer_id").size()}))
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
        "utilities_months": (m["utilities_payment"] < 0).groupby(level=0).sum(),
        "invest_months": (m["investment_deposit"] < 0).groupby(level=0).sum(),
        "balance": bal,
        "min_balance": pd.concat(low).groupby(level=0).min(),
    })
    s = s.join(pd.concat(recent).groupby(level=0).sum())
    s[["restaurants_3m", "fast_food_3m", "taxi_3m"]] = s[["restaurants_3m", "fast_food_3m", "taxi_3m"]].fillna(0)
    s["left_pm"] = s.income_pm - s.spend_pm
    return s.join(cust.set_index("customer_id"))


def hero_shortlist(s: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Customers who look like a typical ~40-year-old with a regular life, before the engine checks; and the age
    group's median salary."""
    s = s.drop(index=DEFAULT["id"], errors="ignore")
    lo, hi = HERO_AGES
    age = s[(s.customer_age >= lo) & (s.customer_age <= hi) & (s.salary_months >= 11) & (s.salary_cv.fillna(1) < 0.25)]
    mid = age.salary_pm.quantile([0.4, 0.6])
    pool = age[age.salary_pm.between(mid.iloc[0], mid.iloc[1]) & (age.housing_months >= 11)
               & (age.housing_cv.fillna(1) < 0.1) & (age.utilities_months >= 11) & (age.invest_months >= 11)
               & (age.min_balance >= 0) & (age.restaurants_3m > 0) & (age.fast_food_3m >= 1) & (age.taxi_3m >= 3)]
    return pool, float(age.salary_pm.median())


def hero_check(rows: pd.DataFrame, age: int) -> dict | None:
    """The engine's own numbers for one candidate; None when they would not make a meaningful demo."""
    sys.path.insert(0, str(ROOT))
    from app.engine import BASKETS, Engine
    with tempfile.TemporaryDirectory() as tmp:
        e = Engine(frame=rows, today=TODAY.date(), state_path=Path(tmp) / "s.json", age=age)
        safe = e.safe_to_spend()
        cuts = sorted((e.simulate_cut(c, 20) for c in BASKETS["everyday"]["categories"]),
                      key=lambda c: -c["saving_per_year_eur"])
        rent = any(x["id"] == "rent" and x["status"] == "active" for x in e.commitments()["regular_payments"])
        if not (safe["next_payday"]["date"] and rent and 50 <= safe["safe_this_week_eur"] <= 300
                and cuts[0]["saving_per_year_eur"] >= 500):
            return None
        return {"week": safe["safe_this_week_eur"], "cut": cuts[0]}


def pick_hero(s: pd.DataFrame, rows: pd.DataFrame) -> tuple[str, str]:
    pool, median = hero_shortlist(s)
    ok = []
    for cid in pool.index:
        chk = hero_check(by_time(rows[rows.customer_id == cid]), int(s.loc[cid].customer_age))
        if chk:
            ok.append((abs(s.loc[cid].salary_pm - median), cid, chk))
    _, cid, chk = sorted(ok, key=lambda x: (x[0], x[1]))[0]
    r, cut = s.loc[cid], chk["cut"]
    return cid, (f"age {int(r.customer_age)}, the age group of the median customer: salary {r.salary_pm:,.0f} € a "
                 f"month, the closest to the {median:,.0f} € median of customers aged {HERO_AGES[0]}-{HERO_AGES[1]}, "
                 f"among the {len(ok)} of {len(pool)} candidates that pass every check (a {r.housing_pm:,.0f} € "
                 f"housing payment and utilities every month, monthly investing, never a negative balance, "
                 f"{int(r.taxi_3m)} taxi rides and {int(r.fast_food_3m)} fast-food buys in the last 3 full months); "
                 f"safe to spend this week {chk['week']:,.0f} €, and 20% less on {cut['category'].lower()} would "
                 f"save {cut['saving_per_year_eur']:,.0f} € a year")


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
    """The postal prefix: 00 is Helsinki, 01 the Vantaa area, 02 the Espoo area."""
    return {"00": ", Helsinki", "01": ", Vantaa area", "02": ", Espoo area"}.get(str(postal)[:2], "")


LABEL = {"hero": "Demo default", "student": "Student-age", "high_earner": "High earner",
         "irregular": "Irregular income", "homeowner": "Homeowner"}
DESCRIBE = {"hero": "The demo default: steady salary, housing payment, bills and monthly investing, plus the everyday "
                    "spending most of us have (eating out, fast food, the odd taxi).",
            "student": "Student-age, tight budget: little left over each month.",
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
    ids = {cid for cid, _ in picks.values()} | set(hero_shortlist(s)[0].index)

    since = (TODAY - pd.DateOffset(months=a.months)).strftime("%Y-%m-%d")
    rows = [ch[ch.customer_id.isin(ids) & (ch.tx_date >= since)] for ch in read(d)]
    rows = pd.concat(rows)
    picks = {"hero": pick_hero(s, rows), **picks}
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for kind, (cid, why) in picks.items():
        p = by_time(rows[rows.customer_id == cid])
        p.to_csv(OUT / f"{cid}.csv", index=False)
        r = s.loc[cid]
        age = int(r.customer_age)
        index.append({"id": cid, "name": NAMES[kind], "file": f"{cid}.csv", "age": age,
                      "label": f"{LABEL[kind]}, {age}{place(r.customer_postal_code)}",
                      "description": DESCRIBE[kind], "why": why})
        if kind == "hero":
            index[-1]["hero"] = True
            index.append(DEFAULT)  # Aino stays in the switcher, right after the hero
        print(f"{kind:12} {cid} {len(p):5} rows  {why}")
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
