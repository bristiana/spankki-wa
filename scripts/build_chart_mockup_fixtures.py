"""Rebuild the chart mockup's spend/budget/compare/recurring fixtures from the hackathon data set.

Usage: python scripts/build_chart_mockup_fixtures.py <spankki-wa-fintech-charts> <a checkout of smobiili-screens-english-first, for its Engine> <Junction-2026-S-pankki dir>
Writes <charts>/app/static/mockups/charts/data/{spend,budget,compare,recurring}.json
"""
import calendar, json, sys
from datetime import date
from pathlib import Path

import pandas as pd

CHARTS, REF, RAW = map(Path, sys.argv[1:4])
OUT = CHARTS / "app/static/mockups/charts/data"
CUST = "CUST01656"
AS_OF = date(2026, 9, 14)           # a day Antti's transactions cover; data ends 30 Sep 2026
MONTH = "2026-09"
DAYS = calendar.monthrange(2026, 9)[1]
LEFT_DAYS = DAYS - AS_OF.day + 1     # today counts as a day still to spend (as on the screens)
PREV = "2026-08"
BUDGET_MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
PEER_MONTHS = [str(pd.Period("2025-09", "M") + i) for i in range(12)]  # Sep 2025 - Aug 2026, the 12 full months before as-of
CATEGORIES = ["Housing", "Utilities", "Groceries", "Transportation", "Health", "Home & Tech", "Travel",
              "Restaurants", "Entertainment", "Lifestyle"]  # app/peers.py
BILLS = ["Housing", "Utilities"]
SPEND_CATS = [c for c in CATEGORIES if c not in BILLS]
BASKET = {"Groceries": "regular", "Transportation": "regular", "Health": "irregular", "Home & Tech": "irregular",
          "Travel": "irregular", "Restaurants": "everyday", "Entertainment": "everyday", "Lifestyle": "everyday"}
LABEL = {"Lifestyle": "Shopping"}
r2 = lambda v: round(float(v) + 0.0, 2)
SRC_TX = f"Computed from Antti's transactions in the hackathon data set (data/personas/{CUST}.csv)"

# ---------- recurring: the Oiva screens' own Engine.commitments(), as of AS_OF ----------
sys.path.insert(0, str(REF))
from app.engine import Engine  # noqa: E402
e = Engine(CHARTS / f"data/personas/{CUST}.csv", today=AS_OF, state_path=OUT.parent / "_state_unused.json")
rec = e.commitments()
rec = {**rec, "fixture": True, "as_of": AS_OF.isoformat(),
       "source": SRC_TX + ", by the Oiva screens' Engine.commitments() with today = " + AS_OF.isoformat()}
(OUT.parent / "_state_unused.json").unlink(missing_ok=True)
P = {p["id"]: p for p in rec["regular_payments"]}

# ---------- Antti's own transactions ----------
tx = pd.read_csv(CHARTS / f"data/personas/{CUST}.csv", dtype={"mcc_code": "string", "merchant_id": "string"})
tx["d"] = pd.to_datetime(tx.tx_date).dt.date
tx["m"] = tx.tx_date.str[:7]
assert tx.d.max() >= AS_OF


def net(df, cat):  # positive = spent (refunds count)
    return -df.loc[df.category == cat, "tx_amount_eur"].sum()


mtd = tx[(tx.m == MONTH) & (tx.d <= AS_OF)]
prev = tx[tx.m == PREV]

# budget rule: per category, the median of the 6 full months before (Mar-Aug 2026), rounded to the nearest 10 EUR;
# a category with a median of 0 gets no budget. Overall budget = the sum of the category budgets.
hist = {c: [net(tx[tx.m == m], c) for m in BUDGET_MONTHS] for c in SPEND_CATS}
limit = {c: (round(float(pd.Series(v).median()) / 10) * 10 or None) for c, v in hist.items()}
budget_total = float(sum(v for v in limit.values() if v))
after_bills = P["salary"]["per_month_eur"] - rec["bills_per_month_eur"] - P["investing"]["per_month_eur"]
assert budget_total <= after_bills, (budget_total, after_bills)

cats = []
for c in SPEND_CATS:
    sp, lim = r2(net(mtd, c)), limit[c]
    cats.append({"category": c, "label": LABEL.get(c, c), "basket": BASKET[c], "spent_eur": sp, "prev_eur": r2(net(prev, c)),
                 "budget_eur": lim, "pct_of_budget": round(sp / lim * 100) if lim else None,
                 "history_eur": {m: r2(v) for m, v in zip(BUDGET_MONTHS, hist[c])}})
cats.sort(key=lambda x: (-x["spent_eur"], -x["prev_eur"]))
spendrows = mtd[mtd.category.isin(SPEND_CATS)]
total = r2(-spendrows.tx_amount_eur.sum())
daily, run = [], 0.0
for d in range(1, AS_OF.day + 1):
    run += -spendrows.loc[spendrows.d == date(2026, 9, d), "tx_amount_eur"].sum()
    daily.append({"day": d, "cumulative_eur": r2(run)})
expected = r2(budget_total * AS_OF.day / DAYS)
bills = [{"name": "Rent" if r.category == "Housing" else "Utility bill", "date": r.tx_date, "amount_eur": r2(-r.tx_amount_eur)}
         for r in mtd[mtd.category.isin(BILLS)].itertuples()]
rule = (f"Each category's budget is its median monthly spending over the 6 full months before ({BUDGET_MONTHS[0]} to "
        f"{BUDGET_MONTHS[-1]}), rounded to the nearest 10 EUR; a category with a median of 0 gets none. The overall budget "
        f"is the sum of the category budgets ({budget_total:.0f} EUR), and stays under what is left after bills and "
        f"investing ({after_bills:.2f} EUR).")
spend = {"fixture": True, "as_of": AS_OF.isoformat(), "source": SRC_TX, "month": MONTH, "total_eur": total,
         "prev_total_eur": r2(-prev[prev.category.isin(SPEND_CATS)].tx_amount_eur.sum()),
         "income_eur": P["salary"]["per_month_eur"], "budget_total_eur": budget_total, "budget_rule": rule,
         "pace": {"expected_by_today_eur": expected, "spent_by_today_eur": total,
                  "status": "over" if total > expected else "under"},
         "categories": cats, "daily": daily, "bills": bills}

# ---------- the data set's peer groups (full transactions) ----------
cust = pd.read_csv(RAW / "tx_customers_junction.csv", encoding="utf-8-sig", dtype={"customer_postal_code": "string"})
cust["area"] = cust.customer_postal_code.str[:2]
me = cust[cust.customer_id == CUST].iloc[0]
band = (30, 39) if 30 <= me.customer_age <= 39 else None
assert me.area == "01" and band
grp_ids = set(cust[(cust.area == "01") & cust.customer_age.between(*band)].customer_id)
hel_ids = set(cust[cust.area == "00"].customer_id)
want = grp_ids | hel_ids
parts = []
for ch in pd.read_csv(RAW / "tx_junction_transactions.csv", encoding="utf-8-sig", chunksize=1_000_000,
                      usecols=["customer_id", "tx_date", "category", "tx_amount_eur", "transaction_type", "balance_after_eur"]):
    parts.append(ch[ch.customer_id.isin(want) & (ch.tx_date <= AS_OF.isoformat())])
    print("progress: chunk read", flush=True)
allx = pd.concat(parts)
allx["m"] = allx.tx_date.str[:7]
first = allx.groupby("customer_id").m.min()
win = allx[allx.m.isin(PEER_MONTHS)]
covered = first.map(lambda f: sum(1 for m in PEER_MONTHS if m >= f)).clip(lower=1)
spendm = (-win[win.category.isin(CATEGORIES)].pivot_table(index="customer_id", columns="category", values="tx_amount_eur",
                                                          aggfunc="sum", fill_value=0.0)).reindex(columns=CATEGORIES, fill_value=0.0)
spendm = spendm.reindex(covered.index, fill_value=0.0).div(covered, axis=0)
income = (win[win.transaction_type.isin(["salary", "other_income"])].groupby("customer_id").tx_amount_eur.sum()
          .reindex(covered.index, fill_value=0.0) / covered)
bal = allx.sort_values(["customer_id", "tx_date"]).groupby("customer_id").balance_after_eur.last()  # last balance on or before AS_OF
pc = pd.DataFrame({"income": income, "Housing": spendm.Housing, "Restaurants": spendm.Restaurants, "Lifestyle": spendm.Lifestyle,
                   "total": spendm.sum(axis=1), "essentials": spendm[["Housing", "Utilities", "Groceries", "Transportation"]].sum(axis=1),
                   "balance": bal})
pc["rent_share"] = pc.Housing / pc.income.where(pc.income > 0)
pc["savings_rate"] = (pc.income - pc.total) / pc.income.where(pc.income > 0)
pc["buffer_months"] = pc.balance / pc.essentials.where(pc.essentials > 0)
g = pc.loc[sorted(grp_ids & set(pc.index))]
hel = pc.loc[sorted(hel_ids & set(pc.index))]
you = pc.loc[CUST]


def stats(col, rnd):
    s = g[col].dropna()
    v = you[col]
    pctl = round(100 * ((s < v).sum() + 0.5 * (s == v).sum()) / len(s))
    return {"you": round(float(v), rnd), "median": round(float(s.median()), rnd), "p25": round(float(s.quantile(.25)), rnd),
            "p75": round(float(s.quantile(.75)), rnd), "percentile": int(pctl), "n_with_value": int(len(s))}


grp = "people aged 30–39 in the Vantaa area"
fig = []
for fid, col, label, unit, better, rnd in [("income", "income", "Income", "eur", "none", 2),
                                          ("rent_share", "rent_share", "Housing as share of income", "pct", "lower", 3),
                                          ("savings_rate", "savings_rate", "Kept of income", "pct", "higher", 3),
                                          ("restaurants", "Restaurants", "Restaurants", "eur", "none", 2),
                                          ("shopping", "Lifestyle", "Shopping", "eur", "none", 2),
                                          ("buffer_months", "buffer_months", "Buffer", "months", "higher", 1)]:
    s = stats(col, rnd)
    fig.append({"id": fid, "label": label, "unit": unit, "better": better, **s, "sentence": ""})
DEF = {"income": "salary and other income credited, a month (net, after tax)",
       "rent_share": "housing payments ÷ income",
       "savings_rate": "(income − spending in all 10 categories) ÷ income; investing counts as kept",
       "restaurants": "net Restaurants spending a month", "shopping": "net Lifestyle spending a month",
       "buffer_months": f"account balance on {AS_OF.isoformat()} ÷ essentials a month (housing, utilities, groceries, transport)"}
def show(f, v):
    return (f"{v * 100:.1f} %" if f["unit"] == "pct" else f"{v:.1f} months" if f["unit"] == "months" else f"{v:.2f} EUR a month")
for f in fig:
    f["definition"] = DEF[f["id"]]
    f["sentence"] = (f"{f['label']}: yours is {show(f, f['you'])}; for {grp} it is typically {show(f, f['median'])} "
                     f"(middle half {show(f, f['p25'])} to {show(f, f['p75'])}).")
compare = {"fixture": True, "as_of": AS_OF.isoformat(),
           "source": ("Computed from the hackathon data set (tx_junction_transactions.csv, tx_customers_junction.csv): Antti and every "
                      "customer with a postal code starting 01 aged 30–39, monthly averages over Sep 2025–Aug 2026, as in "
                      "scripts/build_peer_benchmark.py"),
           "cohort": {"age_band": "30-39", "city": "the Vantaa area", "area": "01", "n": int(len(g)),
                      "from": "2025-09-01", "to": "2026-08-31", "widened": False},
           "figures": fig,
           "web": [{"id": "income_vantaa_all_adults", "label": "Vantaa, all adults: median personal disposable cash income",
                    "value_eur_year": 28085, "year": 2024,
                    "source": "Statistics Finland, Income distribution statistics, table 14ww",
                    "url": "https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__tjt/statfin_tjt_pxt_14ww.px/"},
                   {"id": "income_finland_35_49", "label": "Finland, aged 35–49: median personal disposable monetary income",
                    "value_eur_year": 33214, "year": 2024,
                    "source": "Statistics Finland, Income distribution statistics, table 11py",
                    "url": "https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__tjt/statfin_tjt_pxt_11py.px/"},
                   {"id": "consumption_35_44", "label": "Finland, households with a reference person aged 35–44: consumption a month",
                    "per_household_eur": {"Total": 3872, "Food and non-alcoholic beverages": 575, "Housing, water, energy": 1151,
                                          "Transport": 545, "Recreation, sport and culture": 240, "Restaurants and accommodation": 189},
                    "per_consumption_unit_eur": {"Total": 2362, "Food and non-alcoholic beverages": 332, "Housing, water, energy": 713,
                                                 "Transport": 326, "Recreation, sport and culture": 139, "Restaurants and accommodation": 127},
                    "year": 2022, "source": "Statistics Finland, Household Budget Survey, table 14q4",
                    "url": "https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__ktutk/statfin_ktutk_pxt_14q4.px/"}]}

# ---------- budget ----------
items = []
for c in cats:
    lim = c["budget_eur"]
    items.append({"category": c["category"], "label": c["label"], "limit_eur": lim, "source": "rule" if lim else None,
                  "spent_eur": c["spent_eur"], "left_eur": r2(lim - c["spent_eur"]) if lim else None,
                  "left_per_day_eur": r2(max(lim - c["spent_eur"], 0) / LEFT_DAYS) if lim else None,
                  "status": ("over" if c["spent_eur"] > lim else "on_track") if lim else "none"})
peer_bench = json.loads((CHARTS / "data/peer_benchmark.json").read_text())
statfin = json.loads((CHARTS / "data/statfin_benchmark.json").read_text())
budget = {"fixture": True, "as_of": AS_OF.isoformat(), "source": SRC_TX + "; limits by the median rule above",
          "month": MONTH, "budget_rule": rule, "items": items,
          "presets": {"peers": {"cohort": {"age_band": "30-39", "city": "the Vantaa area", "n": int(len(g))},
                                "source": "the data set's Vantaa-area 30–39 group, median a month Sep 2025–Aug 2026 (this script)",
                                "per_category": {c: r2(g_c) for c, g_c in
                                                 spendm.loc[sorted(grp_ids & set(spendm.index)), SPEND_CATS].median().items()}},
                      "helsinki": {"n": int(len(hel)), "source": "the data set's Helsinki customers (postal codes 00), median a month Sep 2025–Aug 2026",
                                   "per_category": {c: r2(v) for c, v in
                                                    spendm.loc[sorted(hel_ids & set(spendm.index)), SPEND_CATS].median().items()}},
                      "finland": {"year": statfin["year"], "per_person": False, "source": statfin["source"] + ", all ages, per household; Restaurants is sub-class 11.1 food and drink serving services, not all of division 11", "url": statfin["url"],
                                  "per_category": {c: v for c, v in statfin["by_age"]["all"].items() if c in SPEND_CATS}}},
          "undo": None}

for name, d in [("spend", spend), ("budget", budget), ("compare", compare), ("recurring", rec)]:
    (OUT / f"{name}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
print(json.dumps({"total": total, "budget_total": budget_total, "after_bills": after_bills, "limits": limit,
                  "n": len(g), "fig": [(f["id"], f["you"], f["median"], f["percentile"]) for f in fig]}, indent=1, default=str))
