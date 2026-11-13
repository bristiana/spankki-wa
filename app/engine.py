"""Deterministic money engine. Every number the assistant says comes from here, never from the LLM.

Works on one customer's transactions (data/persona.csv) plus the actions taken during the demo
(payments and investments the user confirmed), kept in data/state.json.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# The pedagogical frame from the challenge: every euro you spend lands in one of three baskets.
BASKETS = {
    "regular": {
        "label": "Mandatory & regular",
        "hint": "Bills you know are coming: rent, utilities, groceries, getting to work.",
        "categories": ["Housing", "Utilities", "Groceries", "Transportation"],
    },
    "irregular": {
        "label": "Mandatory & irregular",
        "hint": "Needed, but you can't predict when: a doctor, a broken laptop, a trip home.",
        "categories": ["Health", "Home & Tech", "Travel"],
    },
    "everyday": {
        "label": "Slips through your fingers",
        "hint": "Small choices that add up: dining out, entertainment, shopping.",
        "categories": ["Restaurants", "Entertainment", "Lifestyle"],
    },
}
CATEGORY_BASKET = {c: k for k, b in BASKETS.items() for c in b["categories"]}
INCOME_TYPES = {"salary", "other_income", "investment_withdrawal"}
SPEND_CATEGORIES = list(CATEGORY_BASKET)

# Demo funds. Clearly labelled as demo products; the assistant explains them but never picks one.
FUNDS = [
    {"id": "prudent", "name": "Demo Fund Prudent", "risk": "low", "risk_level": 2,
     "what": "Mostly bonds and money market. Small ups and downs, lower expected growth.",
     "scenario_return": 0.02},
    {"id": "balanced", "name": "Demo Fund Balanced", "risk": "medium", "risk_level": 4,
     "what": "About half shares, half bonds. Can drop in a bad year, grows more over time.",
     "scenario_return": 0.045},
    {"id": "growth", "name": "Demo Fund Growth", "risk": "high", "risk_level": 6,
     "what": "Global shares. Can fall 20-30% in a bad year; highest expected growth over 10+ years.",
     "scenario_return": 0.065},
]
FUNDS_BY_ID = {f["id"]: f for f in FUNDS}

PERIODS = ["this_month", "last_month", "last_7_days", "last_30_days", "last_3_months", "last_12_months", "all"]


def merchant_name(mid) -> str | None:
    """Dataset merchants are anonymous ids (MRC-RES-0022). Show them as 'Restaurant #22'."""
    if not isinstance(mid, str) or not mid.startswith("MRC-"):
        return None
    kinds = {"RES": "Restaurant", "GRO": "Grocery store", "ENT": "Entertainment", "TRN": "Transport",
             "HMT": "Home & tech store", "HEA": "Health", "LIF": "Shop", "TRV": "Travel"}
    _, kind, num = mid.split("-")
    return f"{kinds.get(kind, 'Merchant')} #{int(num)}"


def eur(x: float) -> float:
    return round(float(x), 2)


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


@dataclass
class Recurring:
    key: str
    name: str
    category: str
    transaction_type: str
    typical_day: int
    avg_amount: float
    direction: str  # "out" or "in"


class Engine:
    def __init__(self, csv_path: Path | None = None, today: date | None = None,
                 state_path: Path | None = None, benchmark_path: Path | None = None):
        self.csv_path = csv_path or ROOT / "data" / "persona.csv"
        self.state_path = state_path or ROOT / "data" / "state.json"
        self.today = today or date(2026, 10, 6)
        bp = benchmark_path or ROOT / "data" / "benchmark.json"
        self.benchmark = json.loads(bp.read_text()) if bp.exists() else None
        base = pd.read_csv(self.csv_path, dtype={"merchant_id": "string", "mcc_code": "string"})
        base["date"] = pd.to_datetime(base["tx_date"]).dt.date
        self.base = base[["date", "category", "tx_amount_eur", "transaction_type", "merchant_id", "balance_after_eur"]]
        self.customer_id = str(base["customer_id"].iloc[0])
        self.state = self._load_state()

    # ---------- state (demo actions) ----------
    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"actions": [], "pending": {}}

    def _save(self):
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str))

    def reset(self):
        self.state = {"actions": [], "pending": {}}
        self._save()

    @property
    def tx(self) -> pd.DataFrame:
        """All transactions: dataset plus confirmed demo actions."""
        extra = [{"date": date.fromisoformat(a["date"]), "category": a["category"], "tx_amount_eur": a["amount"],
                  "transaction_type": a["transaction_type"], "merchant_id": pd.NA, "balance_after_eur": None}
                 for a in self.state["actions"]]
        df = self.base if not extra else pd.concat([self.base, pd.DataFrame(extra)], ignore_index=True)
        return df[df["date"] <= self.today]

    def balance(self) -> float:
        last = float(self.base[self.base["date"] <= self.today]["balance_after_eur"].iloc[-1])
        return eur(last + sum(a["amount"] for a in self.state["actions"]))

    # ---------- periods ----------
    def period_range(self, period: str = "last_month", start: str | None = None, end: str | None = None):
        t = self.today
        if start or end:
            d0 = date.fromisoformat(start) if start else date(2000, 1, 1)
            d1 = date.fromisoformat(end) if end else t
        elif period == "this_month":
            d0, d1 = month_start(t), t
        elif period == "last_month":
            d0 = add_months(month_start(t), -1)
            d1 = month_start(t) - timedelta(days=1)
        elif period == "last_7_days":
            d0, d1 = t - timedelta(days=6), t
        elif period == "last_30_days":
            d0, d1 = t - timedelta(days=29), t
        elif period == "last_3_months":
            d0, d1 = add_months(month_start(t), -3), month_start(t) - timedelta(days=1)
        elif period == "last_12_months":
            d0, d1 = add_months(month_start(t), -12), month_start(t) - timedelta(days=1)
        elif period == "all":
            d0, d1 = self.base["date"].min(), t
        else:
            raise ValueError(f"unknown period {period!r}; use one of {PERIODS} or start/end dates")
        return d0, d1

    def _slice(self, d0, d1) -> pd.DataFrame:
        df = self.tx
        return df[(df["date"] >= d0) & (df["date"] <= d1)]

    def _full_months(self, n: int) -> tuple[date, date]:
        d1 = month_start(self.today) - timedelta(days=1)
        return add_months(month_start(self.today), -n), d1

    def monthly_avg(self, category: str, months: int = 3) -> float:
        d0, d1 = self._full_months(months)
        s = self._slice(d0, d1)
        return eur(-s[s["category"] == category]["tx_amount_eur"].sum() / months)

    # ---------- questions ----------
    def spending(self, category: str | None = None, period: str = "last_month",
                 start: str | None = None, end: str | None = None) -> dict:
        d0, d1 = self.period_range(period, start, end)
        s = self._slice(d0, d1)
        s = s[s["tx_amount_eur"] < 0]
        if category:
            if category not in SPEND_CATEGORIES + ["Invest Deposit"]:
                raise ValueError(f"unknown category {category!r}; use one of {SPEND_CATEGORIES}")
            s = s[s["category"] == category]
        else:
            s = s[s["category"].isin(SPEND_CATEGORIES)]
        total = -s["tx_amount_eur"].sum()
        # Same-length window right before, for "vs before"
        span = (d1 - d0).days + 1
        if period in ("last_month", "this_month") and not (start or end):
            p0 = add_months(d0, -1)
            p1 = d0 - timedelta(days=1) if period == "last_month" else p0 + timedelta(days=span - 1)
        else:
            p0, p1 = d0 - timedelta(days=span), d0 - timedelta(days=1)
        ps = self._slice(p0, p1)
        ps = ps[ps["tx_amount_eur"] < 0]
        ps = ps[ps["category"] == category] if category else ps[ps["category"].isin(SPEND_CATEGORIES)]
        prev = -ps["tx_amount_eur"].sum()
        top = (s.dropna(subset=["merchant_id"]).groupby("merchant_id")["tx_amount_eur"]
               .agg(["sum", "count"]).sort_values("sum").head(3))
        out = {
            "category": category or "all spending",
            "basket": BASKETS[CATEGORY_BASKET[category]]["label"] if category in CATEGORY_BASKET else None,
            "from": d0.isoformat(), "to": d1.isoformat(),
            "total_eur": eur(total), "transactions": int(len(s)),
            "average_per_transaction_eur": eur(total / len(s)) if len(s) else 0.0,
            "previous_period": {"from": p0.isoformat(), "to": p1.isoformat(), "total_eur": eur(prev)},
            "change_pct": round((total - prev) / prev * 100) if prev else None,
            "top_merchants": [{"name": merchant_name(m), "total_eur": eur(-r["sum"]), "visits": int(r["count"])}
                              for m, r in top.iterrows()],
        }
        if not category:
            by = s.groupby("category")["tx_amount_eur"].sum().sort_values()
            out["by_category"] = {c: eur(-v) for c, v in by.items()}
        return out

    def baskets(self, period: str = "last_3_months", start: str | None = None, end: str | None = None) -> dict:
        d0, d1 = self.period_range(period, start, end)
        s = self._slice(d0, d1)
        months = max(1, round(((d1 - d0).days + 1) / 30.4))
        spend = s[(s["tx_amount_eur"] < 0) & s["category"].isin(SPEND_CATEGORIES)]
        total = -spend["tx_amount_eur"].sum()
        out = {"from": d0.isoformat(), "to": d1.isoformat(), "months": months, "total_spent_eur": eur(total),
               "baskets": []}
        for key, b in BASKETS.items():
            bs = spend[spend["category"].isin(b["categories"])]
            amt = -bs["tx_amount_eur"].sum()
            out["baskets"].append({
                "basket": key, "label": b["label"], "explanation": b["hint"],
                "total_eur": eur(amt), "per_month_eur": eur(amt / months),
                "share_pct": round(amt / total * 100) if total else 0,
                "by_category": {c: eur(-v) for c, v in bs.groupby("category")["tx_amount_eur"].sum().sort_values().items()},
            })
        inc = s[s["transaction_type"].isin(INCOME_TYPES)]["tx_amount_eur"].sum()
        inv = -s[s["category"] == "Invest Deposit"]["tx_amount_eur"].sum()
        out["income_eur"] = eur(inc)
        out["invested_eur"] = eur(inv)
        out["left_over_eur"] = eur(inc - total - inv)
        return out

    # ---------- recurring ----------
    def recurring(self) -> list[Recurring]:
        """Payments that come back every month on about the same day for about the same amount."""
        d0, d1 = self._full_months(6)
        s = self.base[(self.base["date"] >= d0) & (self.base["date"] <= d1)].copy()
        s["m"] = s["date"].map(lambda d: (d.year, d.month))
        s["day"] = s["date"].map(lambda d: d.day)
        s["key"] = s["transaction_type"] + "|" + s["category"] + "|" + s["merchant_id"].fillna("")
        found = []
        for key, g in s.groupby("key"):
            ttype, cat, mid = key.split("|")
            if ttype == "purchase" or cat == "Utilities":
                continue
            per_month = g.groupby("m").size()
            if len(per_month) < 4 or per_month.max() > 1:
                continue
            amt = g["tx_amount_eur"]
            if abs(amt.std() / amt.mean()) > 0.25:
                continue
            names = {"housing_payment": "Rent", "salary": "Salary", "investment_deposit": "Monthly investment"}
            found.append(Recurring(key=ttype, name=names.get(ttype, merchant_name(mid) or cat), category=cat,
                                   transaction_type=ttype, typical_day=int(g["day"].median()),
                                   avg_amount=eur(abs(amt.tail(3).mean())), direction="in" if amt.mean() > 0 else "out"))
        return found

    def _rec(self, ttype: str) -> Recurring | None:
        return next((r for r in self.recurring() if r.transaction_type == ttype), None)

    def _next_due(self, r: Recurring) -> date:
        """Next date this recurring item is due, skipping a month already paid."""
        t = self.today
        this = t.replace(day=min(r.typical_day, 28))
        paid_this_month = ((self.tx["transaction_type"] == r.transaction_type)
                           & (self.tx["date"] >= month_start(t))).any()
        if paid_this_month or this < t:
            return add_months(month_start(t), 1).replace(day=min(r.typical_day, 28))
        return this

    def next_payday(self) -> dict:
        r = self._rec("salary")
        d = self._next_due(r)
        return {"date": d.isoformat(), "days_away": (d - self.today).days, "expected_eur": r.avg_amount}

    def upcoming_bills(self, days: int = 14) -> list[dict]:
        out = []
        for r in self.recurring():
            if r.direction != "out":
                continue
            d = self._next_due(r)
            if (d - self.today).days <= days:
                bill_id = f"{r.key}-{d.isoformat()}"
                out.append({"bill_id": bill_id, "name": r.name, "category": r.category,
                            "amount_eur": r.avg_amount, "due_date": d.isoformat(),
                            "days_away": (d - self.today).days,
                            "basket": BASKETS[CATEGORY_BASKET[r.category]]["label"] if r.category in CATEGORY_BASKET else "Saving"})
        util = self.monthly_avg("Utilities", 6)
        out.append({"bill_id": None, "name": "Utilities (several small bills)", "category": "Utilities",
                    "amount_eur": util, "due_date": None, "days_away": None, "basket": BASKETS["regular"]["label"],
                    "note": "Varies; this is your average per month."})
        return sorted(out, key=lambda b: (b["days_away"] is None, b["days_away"] or 0))

    def safe_to_spend(self) -> dict:
        pay = self.next_payday()
        days = max(1, pay["days_away"])
        frac = days / 30.4
        bal = self.balance()
        committed = [b for b in self.upcoming_bills(days) if b["bill_id"] and b["days_away"] < days
                     and b["category"] != "Invest Deposit"]
        bills = sum(b["amount_eur"] for b in committed)
        util = self.monthly_avg("Utilities", 6) * frac
        needs = sum(self.monthly_avg(c, 3) for c in ("Groceries", "Transportation")) * frac
        buffer = sum(self.monthly_avg(c, 6) for c in BASKETS["irregular"]["categories"]) * frac
        free = bal - bills - util - needs - buffer
        per_day = max(0.0, free) / days
        return {
            "balance_eur": eur(bal),
            "next_payday": pay,
            "bills_before_payday": [{"name": b["name"], "amount_eur": b["amount_eur"], "due_date": b["due_date"]} for b in committed],
            "bills_total_eur": eur(bills),
            "utilities_estimate_eur": eur(util),
            "groceries_and_transport_estimate_eur": eur(needs),
            "surprise_buffer_eur": eur(buffer),
            "safe_to_spend_until_payday_eur": eur(free),
            "safe_per_day_eur": eur(per_day),
            "safe_this_week_eur": eur(per_day * min(7, days)),
            "method": "balance - bills due before payday - usual utilities, groceries and transport until payday "
                      "- a buffer for irregular costs (your average health, home & tech and travel spend)",
        }

    def simulate_cut(self, category: str, cut_pct: float, fund_id: str | None = None, years: int = 5) -> dict:
        if category not in SPEND_CATEGORIES:
            raise ValueError(f"unknown category {category!r}")
        if not 0 < cut_pct <= 100:
            raise ValueError("cut_pct must be between 0 and 100")
        now = self.monthly_avg(category, 3)
        save = now * cut_pct / 100
        out = {"category": category, "current_per_month_eur": eur(now), "cut_pct": cut_pct,
               "new_per_month_eur": eur(now - save), "saving_per_month_eur": eur(save),
               "saving_per_year_eur": eur(save * 12), "based_on": "average of the last 3 full months",
               "if_invested": []}
        for f in ([FUNDS_BY_ID[fund_id]] if fund_id else FUNDS):
            r = f["scenario_return"] / 12
            n = years * 12
            fv = save * (((1 + r) ** n - 1) / r)
            out["if_invested"].append({"fund": f["name"], "risk": f["risk"], "years": years,
                                       "paid_in_eur": eur(save * n), "value_eur": eur(fv),
                                       "scenario_return_pct": f["scenario_return"] * 100})
        out["disclaimer"] = "Scenario, not a promise. Funds can lose value."
        return out

    def spender_profile(self) -> dict:
        b = self.baskets("last_3_months")
        everyday_share = next(x["share_pct"] for x in b["baskets"] if x["basket"] == "everyday")
        traits, comparisons = [], []
        if self.benchmark:
            med = self.benchmark["median_monthly"]
            p25 = self.benchmark["p25_monthly"]  # amounts are negative: p25 = the biggest spenders
            for c in SPEND_CATEGORIES:
                mine = self.monthly_avg(c, 3)
                m = -med.get(c, 0.0)
                if m > 5:
                    comparisons.append({"category": c, "you_per_month_eur": mine, "peers_median_eur": eur(m),
                                        "vs_peers_pct": round((mine - m) / m * 100),
                                        "in_top_quarter_of_spenders": mine > -p25.get(c, 0.0)})
            comparisons.sort(key=lambda x: -x["vs_peers_pct"])
        inc = b["income_eur"] or 1
        save_rate = round(b["invested_eur"] / inc * 100)
        r = self.spending("Restaurants", "last_3_months")
        if everyday_share >= 30:
            traits.append("Experience spender: a big part of your money goes to eating out and fun.")
        if save_rate >= 10:
            traits.append(f"Steady saver: you invest about {save_rate}% of what comes in.")
        late = self._slice(*self._full_months(3))
        late = late[(late["category"] == "Restaurants")]
        weekend = late[[d.weekday() >= 4 for d in late["date"]]]
        wk_share = round(-weekend["tx_amount_eur"].sum() / max(1e-9, -late["tx_amount_eur"].sum()) * 100) if len(late) else 0
        if wk_share >= 50:
            traits.append(f"Weekend effect: {wk_share}% of your restaurant money goes out Friday to Sunday.")
        return {
            "everyday_share_pct": everyday_share,
            "savings_rate_pct": save_rate,
            "restaurant_visits_last_3_months": r["transactions"],
            "weekend_restaurant_share_pct": wk_share,
            "traits": traits,
            "peers": {"group": f"S-Pankki customers aged {self.benchmark['age_band']}" if self.benchmark else None,
                      "customers": self.benchmark["customers"] if self.benchmark else None,
                      "biggest_differences": comparisons[:4]},
            "baskets": [{k: x[k] for k in ("label", "per_month_eur", "share_pct")} for x in b["baskets"]],
        }

    # ---------- money moves (only after the user confirms) ----------
    def create_payment(self, bill_id: str) -> dict:
        bill = next((b for b in self.upcoming_bills(45) if b["bill_id"] == bill_id), None)
        if not bill:
            raise ValueError(f"no upcoming bill {bill_id!r}")
        pid = uuid.uuid4().hex[:10]
        self.state["pending"][pid] = {"kind": "payment", "bill_id": bill_id, "name": bill["name"],
                                      "category": bill["category"], "amount": bill["amount_eur"],
                                      "due_date": bill["due_date"], "transaction_type": "housing_payment"
                                      if bill["category"] == "Housing" else "utilities_payment", "status": "pending"}
        self._save()
        return {"pending_id": pid, **self.state["pending"][pid]}

    def create_investment(self, amount: float, fund_id: str, monthly: bool) -> dict:
        if fund_id not in FUNDS_BY_ID:
            raise ValueError(f"unknown fund {fund_id!r}; use one of {list(FUNDS_BY_ID)}")
        if not 5 <= amount <= 10000:
            raise ValueError("amount must be between 5 and 10000 EUR")
        pid = uuid.uuid4().hex[:10]
        f = FUNDS_BY_ID[fund_id]
        self.state["pending"][pid] = {"kind": "investment", "fund_id": fund_id, "name": f["name"], "risk": f["risk"],
                                      "category": "Invest Deposit", "amount": eur(amount), "monthly": bool(monthly),
                                      "transaction_type": "investment_deposit", "status": "pending"}
        self._save()
        return {"pending_id": pid, **self.state["pending"][pid]}

    def pending(self, pid: str) -> dict | None:
        p = self.state["pending"].get(pid)
        return {"pending_id": pid, **p} if p else None

    def confirm(self, pid: str) -> dict:
        p = self.state["pending"].get(pid)
        if not p:
            raise KeyError(pid)
        if p["status"] == "done":
            return {"pending_id": pid, **p, "balance_eur": self.balance()}
        self.state["actions"].append({"date": self.today.isoformat(), "category": p["category"],
                                      "amount": -p["amount"], "transaction_type": p["transaction_type"],
                                      "note": p["name"], "pending_id": pid})
        p["status"] = "done"
        self._save()
        return {"pending_id": pid, **p, "balance_eur": self.balance()}
