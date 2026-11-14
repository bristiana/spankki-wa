"""Deterministic money engine. Every number the assistant says comes from here, never from the LLM.

Works on one customer's transactions (data/persona.csv) plus the actions taken during the demo
(payments and investments the user confirmed), kept in data/state.json.
"""
from __future__ import annotations

import calendar
import json
import threading
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import store

ROOT = Path(__file__).resolve().parents[1]

# The pedagogical frame from the challenge: every euro you spend lands in one of three baskets.
BASKETS = {
    "regular": {
        "label": "Mandatory & regular", "example": "rent",
        "hint": "Bills you know are coming: rent, utilities, groceries, getting to work.",
        "categories": ["Housing", "Utilities", "Groceries", "Transportation"],
    },
    "irregular": {
        "label": "Mandatory & irregular", "example": "a broken washing machine",
        "hint": "Needed, but you can't predict when: a doctor, a broken laptop, a trip home.",
        "categories": ["Health", "Home & Tech", "Travel"],
    },
    "everyday": {
        "label": "Slips through your fingers", "example": "coffees",
        "hint": "Small choices that add up: dining out, entertainment, shopping.",
        "categories": ["Restaurants", "Entertainment", "Lifestyle"],
    },
}
CATEGORY_BASKET = {c: k for k, b in BASKETS.items() for c in b["categories"]}
EVERYDAY_WORDS = {"Restaurants": "eating out", "Entertainment": "entertainment", "Lifestyle": "shopping"}
NO_PAYDAY_DAYS = 30  # safe-to-spend horizon when no regular salary is found
INCOME_TYPES = {"salary", "other_income", "investment_withdrawal"}
CYCLE_INCOME_TYPES = {"salary", "other_income"}  # what a pay cycle earns (the cycle budget in safe to spend)
RESERVE_NOTE_EUR = 100  # messages mention the reserve from this much
SWEEP_TYPE = "salary_sweep"  # an automatic transfer of most of the salary to another account, the day it lands
WEEK_DAYS = 7  # "this week" in safe to spend
LEVER_MIN_EUR_YEAR = 60  # a 20% everyday cut smaller than this a year is not a lever worth naming
WEEKEND_MIN_VISITS = 4  # restaurant visits in 3 months before a weekend share means anything
SPEND_CATEGORIES = list(CATEGORY_BASKET)

# Demo funds. Clearly labelled as demo products; the assistant explains them but never picks one.
FUNDS = [
    {"id": "prudent", "name": "Demo Fund Prudent", "risk": "low", "risk_level": 2,
     "what": "Euro money market (tracks the XEON ETF). Very small ups and downs, lowest expected growth.",
     "scenario_return": 0.02},
    {"id": "balanced", "name": "Demo Fund Balanced", "risk": "medium", "risk_level": 4,
     "what": "Half global shares, half euro money market (VWCE + XEON). Can drop in a bad year, grows more over time.",
     "scenario_return": 0.045},
    {"id": "growth", "name": "Demo Fund Growth", "risk": "high", "risk_level": 6,
     "what": "Global shares, about 3,700 companies (tracks the VWCE ETF). Can fall 20-30% in a bad year; highest expected growth over 10+ years.",
     "scenario_return": 0.065},
]
FUNDS_BY_ID = {f["id"]: f for f in FUNDS}
# Real instruments behind each demo fund (Yahoo symbols, prices in data/market.json), percent of each euro.
FUND_SYMBOLS = {"prudent": {"XEON.DE": 100}, "balanced": {"VWCE.DE": 50, "XEON.DE": 50}, "growth": {"VWCE.DE": 100}}
# What each fund buys at the linked broker (Trading 212 Practice). EUR instruments on Xetra.
FUND_MIX = {
    "prudent": [{"name": "Xtrackers EUR Overnight Rate Swap (XEON)", "ticker": "DBXTd_EQ", "pct": 100}],
    "balanced": [{"name": "Vanguard FTSE All-World (VWCE)", "ticker": "VWCEd_EQ", "pct": 50},
                 {"name": "Xtrackers EUR Overnight Rate Swap (XEON)", "ticker": "DBXTd_EQ", "pct": 50}],
    "growth": [{"name": "Vanguard FTSE All-World (VWCE)", "ticker": "VWCEd_EQ", "pct": 100}],
}

# Regular commitments: what comes back every month in the customer's own transactions. The S-Pankki dataset has no
# subscription merchants (no streaming, gym or app charges), so nothing here is invented: rent, the utility bills,
# the monthly investment and the salary are found from each customer's own rows.
COMMITMENT_TYPES = {  # transaction type -> id, name, what it is, direction, what the person can do about it
    "housing_payment": ("rent", "Rent", "Housing payment", "out", ["remind", "pay"]),
    "investment_deposit": ("investing", "Monthly investing", "Your monthly deposit to investments", "out", ["pause"]),
    "salary": ("salary", "Salary", "Income", "in", []),
}
GRACE_DAYS = 10  # stopped = no payment for longer than the usual gap + this
TREND_PCT = {"single": 5, "utilities": 15}  # a real change: last 3 full months vs the 3 before, at least this many %
TREND_MIN_EUR = 10.0  # ... and at least this many euros a month
UNUSUAL_BILL_FACTOR = 1.2  # one utility bill this far above the top of the usual range (its 90th percentile)
CARD_REPEAT_MIN = 4  # card charges from one merchant, about a month apart, same amount: a card subscription

# Merchant category codes (ISO 18245) found in the persona data, as plain English.
MCC_LABELS = {
    "4111": "Public transport", "4121": "Taxis and ride-hailing", "4722": "Travel agencies",
    "5200": "Home supply stores", "5411": "Supermarkets", "5422": "Butchers", "5451": "Dairy shops",
    "5499": "Food shops", "5541": "Fuel stations", "5542": "Fuel (self-service pumps)", "5712": "Furniture stores",
    "5732": "Electronics stores", "5812": "Restaurants", "5813": "Bars and pubs", "5814": "Fast food and cafés",
    "5912": "Pharmacies", "5941": "Sports shops", "5942": "Book shops", "5945": "Toy and game shops",
    "5999": "Other shops", "7011": "Hotels", "7523": "Parking", "7832": "Cinema", "7922": "Theatre and event tickets",
    "7991": "Tourist attractions", "7996": "Amusement parks", "7999": "Recreation", "8011": "Doctors",
    "8021": "Dentists", "8099": "Health services",
}
# Words people use -> codes. "coffee" has no code of its own: cafés file as fast food or restaurants.
MERCHANT_TYPES = {
    "taxi": ["4121"], "uber": ["4121"], "bolt": ["4121"], "cab": ["4121"], "ride-hailing": ["4121"],
    "public transport": ["4111"], "bus": ["4111"], "train": ["4111"], "tram": ["4111"], "metro": ["4111"],
    "hsl": ["4111"], "fuel": ["5541", "5542"], "petrol": ["5541", "5542"], "gas": ["5541", "5542"],
    "parking": ["7523"], "fast food": ["5814"], "coffee": ["5814", "5812"], "cafe": ["5814", "5812"],
    "café": ["5814", "5812"], "restaurant": ["5812"], "bar": ["5813"], "pub": ["5813"], "drink": ["5813"],
    "supermarket": ["5411"], "groceries": ["5411", "5422", "5451", "5499"], "cinema": ["7832"], "movie": ["7832"],
    "event": ["7922"], "concert": ["7922"], "theatre": ["7922"], "recreation": ["7999", "7991", "7996"],
    "pharmacy": ["5912"], "doctor": ["8011"], "dentist": ["8021"], "hotel": ["7011"], "furniture": ["5712"],
    "electronics": ["5732"], "book": ["5942"], "sport": ["5941"],
}


def merchant_codes(kind: str) -> tuple[str, list[str]]:
    """'taxis' / 'uber' / '4121' / 'Parking' -> (label, MCC codes). Raises ValueError for an unknown type."""
    k = (kind or "").strip().lower()
    if k in MCC_LABELS:
        codes = [k]
    else:
        codes = MERCHANT_TYPES.get(k) or MERCHANT_TYPES.get(k.removesuffix("s")) or [
            c for c, label in MCC_LABELS.items() if k and (k == label.lower() or k.removesuffix("s") in label.lower())]
    if not codes:
        raise ValueError(f"unknown merchant type {kind!r}; try one of {sorted(MERCHANT_TYPES)}")
    return " and ".join(MCC_LABELS[c] for c in codes), codes


# PSD2 account information: a second bank the customer can link. SYNTHETIC: no real bank, no partnership.
DEMO_BANK = {
    "id": "demo", "name": "Demo Bank", "savings_eur": 2450.00, "card_limit_eur": 1500.00, "card_due_day": 12,
    # day of month, what, category, amount: card purchases repeated every month
    "card_monthly": [(3, "Online shop", "Lifestyle", 34.90), (14, "Café", "Restaurants", 18.50),
                     (22, "Cinema", "Entertainment", 12.00)],
    "note": "Synthetic demo bank linked through a mock PSD2 consent. Not a real bank, not real data.",
}

PERIODS = ["this_month", "last_month", "last_7_days", "last_30_days", "last_3_months", "last_12_months", "all"]


def merchant_name(mid) -> str | None:
    """Dataset merchants are anonymous ids (MRC-RES-0022). Show them as 'Restaurant #22'."""
    if not isinstance(mid, str) or not mid.startswith("MRC-"):
        return None
    kinds = {"RES": "Restaurant", "GRO": "Grocery store", "ENT": "Entertainment", "TRN": "Transport",
             "HMT": "Home & tech store", "HEA": "Health", "LIF": "Shop", "TRV": "Travel"}
    _, kind, num = mid.split("-")
    return f"{kinds.get(kind, 'Merchant')} #{int(num)}"


# MyData explorer: the only knobs a query can turn. Every value is checked against these lists.
EXPLORE_WHAT = ["spent", "income"]
EXPLORE_GROUP_BY = ["month", "week", "weekday", "category", "basket", "merchant", "merchant_type"]
EXPLORE_METRICS = ["sum", "count", "average"]
# Kinds of place: the same words and codes get_spending understands (MERCHANT_TYPES synonyms, MCC_LABELS codes).
EXPLORE_MERCHANT_TYPES = sorted(MERCHANT_TYPES) + sorted(MCC_LABELS)
NO_MCC = "No merchant code"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EXPLORE_TOP = 10  # merchants listed one by one; the rest become one "other places" row


def eur(x: float) -> float:
    return round(float(x), 2) + 0.0  # no -0.0


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def on_day(d: date, day: int) -> date:
    """Day `day` of d's month, or its last day (a salary on the 31st lands on 30 Nov, not 28 Nov)."""
    return d.replace(day=min(day, calendar.monthrange(d.year, d.month)[1]))


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
                 state_path: Path | None = None, benchmark_path: Path | None = None, frame: pd.DataFrame | None = None,
                 age: int | None = None):
        """frame: one customer's rows (dataset columns, oldest first) instead of reading csv_path; age: that
        customer's age (default: the persona's, from the benchmark)."""
        self.csv_path = csv_path or ROOT / "data" / "persona.csv"
        self.state_path = state_path or ROOT / "data" / "state.json"
        self.today = today or date(2026, 10, 6)
        bp = benchmark_path or ROOT / "data" / "benchmark.json"
        self.benchmark = json.loads(bp.read_text()) if bp.exists() else None
        # The benchmark's persona age belongs to the default persona only: another customer of unknown age must not
        # be compared with that persona's age group.
        default_persona = frame is None and csv_path is None
        self.age = age if age is not None else (self.benchmark or {}).get("persona_age") if default_persona else None
        if self.benchmark and self.age is None:
            self.benchmark = None  # age unknown: no age group to compare with
        if self.benchmark and self.age is not None:
            lo, hi = map(int, self.benchmark["age_band"].split("-"))
            if not lo <= self.age <= hi:
                self.benchmark = None  # the peer group is another age band: comparing would mislead
        base = (frame.reset_index(drop=True).astype({"merchant_id": "string", "mcc_code": "string"}) if frame is not None
                else pd.read_csv(self.csv_path, dtype={"merchant_id": "string", "mcc_code": "string"}))
        base["date"] = pd.to_datetime(base["tx_date"]).dt.date
        self.base = base[["date", "category", "tx_amount_eur", "transaction_type", "merchant_id", "mcc_code",
                          "balance_after_eur"]]
        # Which spending category each merchant code belongs to (for "where did the transport money go instead").
        self.mcc_category = (base.dropna(subset=["mcc_code"]).groupby("mcc_code")["category"]
                             .agg(lambda c: c.mode()[0]).to_dict())
        self.customer_id = str(base["customer_id"].iloc[0])
        sp = ROOT / "data" / "statfin_benchmark.json"
        self.statfin = json.loads(sp.read_text()) if sp.exists() else None
        mp = ROOT / "data" / "market.json"
        self.market = json.loads(mp.read_text()) if mp.exists() else None
        mb = ROOT / "data" / "mortgage_benchmark.json"
        self.mortgage = json.loads(mb.read_text()) if mb.exists() else None
        self.state = self._load_state()
        self._lock = threading.Lock()  # confirm pages run in a thread pool

    # ---------- state (demo actions) ----------
    def _store_name(self) -> str:
        try:
            return str(self.state_path.resolve().relative_to((ROOT / "data").resolve())).replace("\\", "/")
        except ValueError:
            return self.state_path.name

    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        mirrored = store.load(self._store_name())  # a fresh container after a deploy: pick up where we were
        if mirrored:
            try:
                return json.loads(mirrored)
            except ValueError:
                pass
        return {"actions": [], "pending": {}, "plans": [], "connected": {}, "goals": {}, "paused": {}, "orders": [],
                "linked_banks": {}}

    def _save(self):
        text = json.dumps(self.state, indent=2, default=str)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(text)
        store.save(self._store_name(), text)

    def reset(self):
        self.state = {"actions": [], "pending": {}, "plans": [], "connected": {}, "goals": {}, "paused": {}, "orders": [],
                      "linked_banks": {}}
        self._save()

    @property
    def tx(self) -> pd.DataFrame:
        """All transactions: dataset plus confirmed demo actions."""
        extra = [{"date": date.fromisoformat(a["date"]), "category": a["category"], "tx_amount_eur": a["amount"],
                  "transaction_type": a["transaction_type"], "merchant_id": pd.NA, "mcc_code": pd.NA,
                  "balance_after_eur": None}
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

    def _covered(self, d0: date, months: int) -> int:
        """How many of the `months` months from d0 the history reaches, so a short history does not dilute averages."""
        first = month_start(self.base["date"].min())
        return sum(1 for i in range(months) if add_months(d0, i) >= first)

    def monthly_avg(self, category: str, months: int = 3) -> float:
        d0, d1 = self._full_months(months)
        n = self._covered(d0, months)
        s = self._slice(d0, d1)
        return eur(-s[s["category"] == category]["tx_amount_eur"].sum() / n) if n else 0.0

    def _month_after(self, n: int) -> str | None:
        """'YYYY-MM' n months from now; None past 50 years (a pace of cents a month never gets there)."""
        return add_months(month_start(self.today), n).isoformat()[:7] if n <= 600 else None

    # ---------- questions ----------
    def spending(self, category: str | None = None, period: str = "last_month",
                 start: str | None = None, end: str | None = None, merchant_type: str | None = None) -> dict:
        d0, d1 = self.period_range(period, start, end)
        codes = None
        if merchant_type:
            label, codes = merchant_codes(merchant_type)
            category = None  # the merchant type is the narrower filter
        elif category and category not in SPEND_CATEGORIES + ["Invest Deposit"]:
            raise ValueError(f"unknown category {category!r}; use one of {SPEND_CATEGORIES}")

        def pick(df):
            df = df[df["tx_amount_eur"] < 0]
            if codes:
                return df[df["mcc_code"].isin(codes)]
            return df[df["category"] == category] if category else df[df["category"].isin(SPEND_CATEGORIES)]

        s = pick(self._slice(d0, d1))
        total = -s["tx_amount_eur"].sum()
        # Same-length window right before, for "vs before"
        span = (d1 - d0).days + 1
        if period in ("last_month", "this_month") and not (start or end):
            p0 = add_months(d0, -1)
            p1 = d0 - timedelta(days=1) if period == "last_month" else p0 + timedelta(days=span - 1)
        else:
            p0, p1 = d0 - timedelta(days=span), d0 - timedelta(days=1)
        prev = -pick(self._slice(p0, p1))["tx_amount_eur"].sum()
        top = (s.dropna(subset=["merchant_id"]).groupby("merchant_id")["tx_amount_eur"]
               .agg(["sum", "count"]).sort_values("sum").head(3))
        out = {
            "category": category or (None if codes else "all spending"),
            "basket": BASKETS[CATEGORY_BASKET[category]]["label"] if category in CATEGORY_BASKET else None,
            "from": d0.isoformat(), "to": d1.isoformat(),
            "total_eur": eur(total), "transactions": int(len(s)),
            "average_per_transaction_eur": eur(total / len(s)) if len(s) else 0.0,
            "previous_period": {"from": p0.isoformat(), "to": p1.isoformat(), "total_eur": eur(prev)},
            "change_pct": round((total - prev) / prev * 100) if prev else None,
            "top_merchants": [{"name": merchant_name(m), "total_eur": eur(-r["sum"]), "visits": int(r["count"])}
                              for m, r in top.iterrows()],
        }
        if codes:
            parent = self.mcc_category.get(codes[0])
            out |= {"merchant_type": label, "mcc_codes": codes, "found": bool(len(s)), "within_category": parent}
            if parent:
                out["category_by_merchant_type"] = self._by_mcc(self._slice(d0, d1), parent)
            if not len(s):
                instead = (f"category_by_merchant_type shows where the {parent} money went instead"
                           if out.get("category_by_merchant_type") else f"there was no {parent or 'such'} spending at all")
                out["note"] = f"No {label.lower()} payments in this period; {instead}. Never guess an amount."
        elif category in SPEND_CATEGORIES:
            out["by_merchant_type"] = self._by_mcc(s, category)
        if not category and not codes:
            by = s.groupby("category")["tx_amount_eur"].sum().sort_values()
            out["by_category"] = {c: eur(-v) for c, v in by.items()}
        return out

    def _by_mcc(self, df: pd.DataFrame, category: str) -> dict:
        """Spending in one category split by merchant type, biggest first."""
        df = df[(df["category"] == category) & (df["tx_amount_eur"] < 0)]
        names = df["mcc_code"].map(lambda c: MCC_LABELS.get(c, f"Code {c}") if isinstance(c, str) else "No merchant code")
        return {k: eur(-v) for k, v in df["tx_amount_eur"].groupby(names).sum().sort_values().items()}

    def baskets(self, period: str = "last_3_months", start: str | None = None, end: str | None = None) -> dict:
        d0, d1 = self.period_range(period, start, end)
        s = self._slice(d0, d1)
        first = month_start(self.base["date"].min())  # a short history must not dilute per-month amounts
        months = max(1, round(((d1 - max(d0, first)).days + 1) / 30.4))
        spend = s[(s["tx_amount_eur"] < 0) & s["category"].isin(SPEND_CATEGORIES)]
        total = -spend["tx_amount_eur"].sum()
        out = {"from": d0.isoformat(), "to": d1.isoformat(), "months": months, "total_spent_eur": eur(total),
               "baskets": []}
        for key, b in BASKETS.items():
            bs = spend[spend["category"].isin(b["categories"])]
            amt = -bs["tx_amount_eur"].sum()
            out["baskets"].append({
                "basket": key, "label": b["label"], "example": b["example"], "explanation": b["hint"],
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

    # ---------- MyData explorer ----------
    def explore(self, what: str = "spent", category: str | None = None, basket: str | None = None,
                merchant_type: str | None = None, group_by: str = "month", metric: str = "sum",
                period: str = "last_12_months", start: str | None = None, end: str | None = None) -> dict:
        """The customer's own view of their transactions: filter, group, measure. Only the enum values above are
        accepted, so a query can never run code. Returns rows (every bucket, zeros included for time groups),
        totals and a chart spec that the web chat and /app/explore draw."""
        if what not in EXPLORE_WHAT:
            raise ValueError(f"unknown what {what!r}; use one of {EXPLORE_WHAT}")
        if group_by not in EXPLORE_GROUP_BY:
            raise ValueError(f"unknown group_by {group_by!r}; use one of {EXPLORE_GROUP_BY}")
        if metric not in EXPLORE_METRICS:
            raise ValueError(f"unknown metric {metric!r}; use one of {EXPLORE_METRICS}")
        if period not in PERIODS:
            raise ValueError(f"unknown period {period!r}; use one of {PERIODS}")
        category, basket, merchant_type = category or None, basket or None, merchant_type or None
        if category and category not in SPEND_CATEGORIES:
            raise ValueError(f"unknown category {category!r}; use one of {SPEND_CATEGORIES}")
        if basket and basket not in BASKETS:
            raise ValueError(f"unknown basket {basket!r}; use one of {list(BASKETS)}")
        if merchant_type and merchant_type not in EXPLORE_MERCHANT_TYPES:
            raise ValueError(f"unknown merchant_type {merchant_type!r}; use a word like taxi, fuel or parking "
                             f"({', '.join(sorted(MERCHANT_TYPES))}) or a merchant code ({', '.join(sorted(MCC_LABELS))})")
        place, codes = merchant_codes(merchant_type) if merchant_type else (None, [])
        if sum(bool(x) for x in (category, basket, merchant_type)) > 1:
            raise ValueError("use one filter at a time: category, basket or merchant type")
        if what == "income" and (category or basket or merchant_type):
            raise ValueError("category, basket and merchant type filters apply to spending, not income")
        try:
            d0, d1 = self.period_range(period, start or None, end or None)
        except ValueError:
            raise ValueError("start and end must be dates like 2026-03-31") from None
        if d0 > d1:
            raise ValueError("start must be on or before end")

        s = self._slice(d0, d1)
        if what == "spent":
            s = s[(s["tx_amount_eur"] < 0) & s["category"].isin(SPEND_CATEGORIES)]
        else:
            s = s[s["transaction_type"].isin(INCOME_TYPES) & (s["tx_amount_eur"] > 0)]
        s = s.assign(amount=s["tx_amount_eur"].abs().astype(float),
                     mtype=[MCC_LABELS.get(m, f"Code {m}") if isinstance(m, str) else NO_MCC for m in s["mcc_code"]])
        if category:
            s = s[s["category"] == category]
        if basket:
            s = s[s["category"].isin(BASKETS[basket]["categories"])]
        if merchant_type:
            s = s[s["mcc_code"].isin(codes)]

        # Bucket of every transaction, and every bucket in display order (None = biggest first).
        if group_by == "month":
            keys = [d.strftime("%Y-%m") for d in s["date"]]
            order, m = [], month_start(d0)
            while m <= d1:
                order.append((m.strftime("%Y-%m"), m.strftime("%b %Y")))
                m = add_months(m, 1)
        elif group_by == "week":
            keys = [(d - timedelta(days=d.weekday())).isoformat() for d in s["date"]]
            order, w = [], d0 - timedelta(days=d0.weekday())
            while w <= d1:
                order.append((w.isoformat(), f"Week of {w.day} {w.strftime('%b %Y')}"))
                w += timedelta(weeks=1)
        elif group_by == "weekday":
            keys = [WEEKDAYS[d.weekday()] for d in s["date"]]
            order = [(d, d) for d in WEEKDAYS]
        elif group_by == "category":
            keys, order = list(s["category"]), None
        elif group_by == "basket":
            keys = [CATEGORY_BASKET.get(c, "other") for c in s["category"]]
            order = [(k, b["label"]) for k, b in BASKETS.items()] if what == "spent" else None
        elif group_by == "merchant":
            keys, order = [merchant_name(mid) or "Bills & transfers (no merchant)" for mid in s["merchant_id"]], None
        else:  # merchant_type: the same labels get_spending uses (MCC_LABELS)
            keys, order = list(s["mtype"]), None
        s = s.assign(bucket=keys)

        def measure(amounts: pd.Series):
            if metric == "count":
                return int(len(amounts))
            if metric == "average":
                return eur(amounts.mean()) if len(amounts) else 0.0
            return eur(amounts.sum())

        stats = {k: (measure(v), int(len(v)), eur(v.sum())) for k, v in s.groupby("bucket")["amount"]}
        if order is None:
            name = (lambda k: BASKETS[k]["label"] if k in BASKETS else "Not in a basket") if group_by == "basket" else (
                lambda k: k)
            order = [(k, name(k)) for k in sorted(stats, key=lambda k: (-stats[k][0], k))]
        empty = (0 if metric == "count" else 0.0, 0, 0.0)
        rows = [{"key": k, "label": label, "value": stats.get(k, empty)[0], "transactions": stats.get(k, empty)[1],
                 "total_eur": stats.get(k, empty)[2]} for k, label in order]
        if group_by == "merchant" and len(rows) > EXPLORE_TOP + 1:
            rest = s[s["bucket"].isin([r["key"] for r in rows[EXPLORE_TOP:]])]["amount"]
            rows = rows[:EXPLORE_TOP] + [{"key": "other", "label": f"{len(rows) - EXPLORE_TOP} other places",
                                          "value": measure(rest), "transactions": int(len(rest)),
                                          "total_eur": eur(rest.sum())}]

        total, n = eur(s["amount"].sum()), int(len(s))
        ranked = [r for r in rows if r["key"] != "other"]  # the "other places" lump is not one place
        top = max(ranked, key=lambda r: r["value"]) if any(r["value"] for r in ranked) else None
        unit = "" if metric == "count" else "€"
        subject = ("Income" if what == "income" else "Spending") + (
            f" on {category}" if category else f" in {BASKETS[basket]['label']}" if basket else
            f" at {place}" if merchant_type else "")
        prefix = {"sum": "", "count": "Number of payments · ", "average": "Average payment · "}[metric]
        title = f"{prefix}{subject} by {group_by.replace('_', ' ')}"
        span = f"{d0.day} {d0.strftime('%b %Y')} to {d1.day} {d1.strftime('%b %Y')}"
        note = ("Spending leaves out money moved to investments." if what == "spent" else
                "Income: salary, other income and investment withdrawals.")
        running = group_by in ("month", "week") and rows and d1 >= (
            month_start(self.today) if group_by == "month" else self.today - timedelta(days=self.today.weekday()))
        if running:  # the last bar is a period still going on: never read as a drop
            note += f" The last row ({rows[-1]['label']}) is still running, so it is not comparable with full ones."
        full = rows[:-1] if running else rows
        low = min((r for r in full if r["key"] != "other"), key=lambda r: r["value"], default=None)
        return {
            "query": {"what": what, "category": category, "basket": basket, "merchant_type": merchant_type,
                      "group_by": group_by, "metric": metric, "period": period,
                      "start": start or None, "end": end or None},
            "title": title, "from": d0.isoformat(), "to": d1.isoformat(), "unit": unit,
            "rows": rows,
            "totals": {"value": measure(s["amount"]), "total_eur": total, "transactions": n,
                       "average_per_transaction_eur": eur(total / n) if n else 0.0,
                       "highest": {"label": top["label"], "value": top["value"]} if top else None,
                       "lowest": {"label": low["label"], "value": low["value"]} if low and len(full) > 1 else None},
            "chart": {"type": "line" if group_by == "week" else "bar", "title": title, "subtitle": span,
                      "unit": unit, "labels": [r["label"] for r in rows], "values": [r["value"] for r in rows]},
            "note": note,
        }

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
        this = on_day(t, r.typical_day)
        paid_this_month = ((self.tx["transaction_type"] == r.transaction_type)
                           & (self.tx["date"] >= month_start(t))).any()
        if paid_this_month or this < t:
            return on_day(add_months(month_start(t), 1), r.typical_day)
        return this

    def next_payday(self) -> dict:
        r = self._rec("salary")
        if not r:
            return {"date": None, "days_away": None, "expected_eur": None,
                    "not_enough_data": "No regular monthly salary in the last 6 full months (needs 4 months with one "
                                       "payment of a similar amount), so the next payday is not known. Never guess it."}
        d = self._next_due(r)
        return {"date": d.isoformat(), "days_away": (d - self.today).days, "expected_eur": r.avg_amount}

    def upcoming_bills(self, days: int = 14) -> list[dict]:
        out = []
        for r in self.recurring():
            if r.direction != "out" or self.state.get("paused", {}).get(r.transaction_type):
                continue  # a paused monthly investment is not coming up
            d = self._next_due(r)
            if (d - self.today).days <= days:
                bill_id = f"{r.key}-{d.isoformat()}"
                out.append({"bill_id": bill_id, "name": r.name, "category": r.category,
                            "amount_eur": r.avg_amount, "due_date": d.isoformat(),
                            "days_away": (d - self.today).days,
                            "basket": BASKETS[CATEGORY_BASKET[r.category]]["label"] if r.category in CATEGORY_BASKET else "Saving"})
        card = self.card_bill()
        if card and card["amount_eur"] > 0 and card["days_away"] <= days:
            out.append({"bill_id": f"card_repayment-{card['due_date']}", "name": card["name"], "category": "Credit card",
                        "amount_eur": card["amount_eur"], "due_date": card["due_date"], "days_away": card["days_away"],
                        "basket": "Card repayment", "note": "Demo Bank (synthetic, linked via PSD2)"})
        util = self.monthly_avg("Utilities", 6)
        out.append({"bill_id": None, "name": "Utilities (several small bills)", "category": "Utilities",
                    "amount_eur": util, "due_date": None, "days_away": None, "basket": BASKETS["regular"]["label"],
                    "note": "Varies; this is your average per month."})
        return sorted(out, key=lambda b: (b["days_away"] is None, b["days_away"] or 0))

    def safe_to_spend(self) -> dict:
        pay = self.next_payday()
        days = max(1, pay["days_away"]) if pay["date"] else NO_PAYDAY_DAYS
        frac = days / 30.4
        bal = self.balance()
        committed = [b for b in self.upcoming_bills(days) if b["bill_id"] and b["days_away"] < days
                     and b["category"] != "Invest Deposit"]
        # A monthly bill can fall due more than once before payday (e.g. right after salary landed).
        for b in list(committed):
            d = date.fromisoformat(b["due_date"])
            while True:
                d = on_day(add_months(month_start(d), 1), d.day)
                if (d - self.today).days >= days:
                    break
                committed.append({**b, "due_date": d.isoformat(), "days_away": (d - self.today).days})
        bills = sum(b["amount_eur"] for b in committed)
        util_m = self.monthly_avg("Utilities", 6)
        needs_m = sum(self.monthly_avg(c, 3) for c in ("Groceries", "Transportation"))
        irregular_m = sum(self.monthly_avg(c, 6) for c in BASKETS["irregular"]["categories"])
        util, needs, buffer = util_m * frac, needs_m * frac, irregular_m * frac
        after = bal - bills - util - needs - buffer
        # The balance is not all this cycle's money: what usually comes in minus the usual mandatory costs is what
        # a pay cycle can sustain for flexible spending. Anything on the account beyond that is reserve (savings).
        # A salary sweep moves most of the salary on to another account the day it lands: it never stays here.
        moved_m = self.moved_out_per_month(6)
        income_m = self.monthly_income(6) - moved_m
        mandatory_m = self.monthly_avg("Housing", 3) + util_m + needs_m + irregular_m
        cycle = max(0.0, income_m - mandatory_m) * frac
        # Never a negative amount to spend: when bills and needs come to more than the balance, nothing is free and
        # the gap is reported separately (short_by_eur).
        free = max(0.0, min(after, cycle))
        reserve = max(0.0, after - cycle)
        per_day = free / days
        week = per_day * min(WEEK_DAYS, days)
        span = "until payday" if pay["date"] else f"in the next {days} days"
        overdrawn = bal < 0
        if overdrawn:
            basis = (f"The account is overdrawn ({bal:.2f} EUR), so nothing is safe to spend until money comes in; "
                     f"bills, usual needs and the buffer {span} need another {bal - after:.2f} "
                     "EUR on top of getting back to zero.")
        elif after < 0:
            basis = (f"Bills, usual needs and the buffer {span} come to {-after:.2f} EUR more than the balance, so "
                     "nothing is free to spend.")
        elif after <= cycle:
            basis = (f"Limited by the balance: after bills, usual needs and the buffer, {after:.2f} EUR is left, "
                     f"less than the {cycle:.2f} EUR your usual income leaves for flexible spending {span}.")
        elif not cycle:
            basis = (f"Usual income ({income_m:.2f} EUR a month) does not cover usual mandatory costs ({mandatory_m:.2f} "
                     f"EUR a month), so the pay cycle leaves nothing for flexible spending; any spending {span} comes "
                     f"out of the {reserve:.2f} EUR reserve that has built up on the account (see get_safe_to_save).")
        else:
            basis = (f"Limited by your usual pay cycle: average income minus usual mandatory costs leaves {cycle:.2f} "
                     f"EUR for flexible spending {span}; the other {reserve:.2f} EUR on the account has built up "
                     "beyond the cycle and looks like savings (see get_safe_to_save).")
        out = {
            "balance_eur": eur(bal),
            "next_payday": pay,
            "bills_before_payday": [{"name": b["name"], "amount_eur": b["amount_eur"], "due_date": b["due_date"]} for b in committed],
            "bills_total_eur": eur(bills),
            "utilities_estimate_eur": eur(util),
            "groceries_and_transport_estimate_eur": eur(needs),
            "surprise_buffer_eur": eur(buffer),
            "safe_to_spend_until_payday_eur": eur(free),
            "safe_per_day_eur": eur(per_day),
            "safe_this_week_eur": eur(week),
            "left_after_bills_eur": eur(after),
            "income_per_month_eur": eur(income_m),
            "mandatory_costs_per_month_eur": eur(mandatory_m),
            "cycle_budget_eur": eur(cycle),
            "reserve_eur": eur(reserve),
            "limited_by": "balance" if after <= cycle else "pay_cycle",
            "basis": basis,
            "method": "the smaller of (a) balance - bills due before payday (incl. a linked bank's card bill) - usual "
                      "utilities, groceries and transport until payday - a buffer for irregular costs (your average "
                      "health, home & tech and travel spend) and (b) the cycle budget: average monthly income (last 6 "
                      "months) - average housing, utilities, groceries, transport and irregular costs, for the days "
                      "until payday. What (a) has beyond (b) is the reserve: money built up beyond the pay cycle",
            "horizon_days": days,
            "payday_known": bool(pay["date"]),
            "overdrawn": overdrawn,
            "short_by_eur": eur(max(0.0, -after)),
            "status": ("overdrawn" if overdrawn else "nothing_free" if after < 0 else
                       "no_cycle_budget" if not cycle else "ok"),
        }
        if moved_m > 0:
            out["moved_to_other_account_per_month_eur"] = moved_m
        if not pay["date"]:
            # No pay cycle to plan to: say so, and give the plain next-7-days view, balance minus what falls due.
            soon = [b for b in committed if b["days_away"] < WEEK_DAYS]
            wk = WEEK_DAYS / 30.4
            needs7 = (util_m + needs_m + irregular_m) * wk
            left7 = bal - sum(b["amount_eur"] for b in soon) - needs7
            out["next_7_days"] = {"bills_due_eur": eur(sum(b["amount_eur"] for b in soon)),
                                  "usual_needs_and_buffer_eur": eur(needs7), "left_eur": eur(left7)}
            week = max(0.0, min(week, left7))
            out["safe_this_week_eur"] = eur(week)
            out["not_enough_data"] = (f"No regular salary found, so safe to spend can't be worked out until a payday. "
                                      f"The total covers the next {days} days instead, and this week's amount is the "
                                      "balance minus bills due and usual needs over the next 7 days (next_7_days), "
                                      "kept within what the next 30 days allow. Say so; never name a payday.")
        return out

    def safe_to_save(self) -> dict:
        """What can go to savings now without touching bills before payday or the irregular buffer, and per month."""
        safe = self.safe_to_spend()
        days = safe["horizon_days"]
        free = safe["safe_to_spend_until_payday_eur"]
        fun = sum(self.monthly_avg(c, 3) for c in BASKETS["everyday"]["categories"]) * days / 30.4
        flow = self.monthly_flow()
        card = self.linked_bank()
        card_monthly = self.card_spend_per_month() if card else 0.0
        surplus = flow["left_per_month_eur"] - card_monthly
        reserve = safe["reserve_eur"]
        out = {
            "now_max_eur": eur(max(0.0, free) + reserve),
            "now_keeping_usual_fun_eur": eur(max(0.0, free - fun) + reserve),
            "usual_fun_until_payday_eur": eur(fun),
            "reserve_eur": reserve,
            "safe_to_spend_until_payday_eur": free,
            "cycle_budget_eur": safe["cycle_budget_eur"],
            "next_payday": safe["next_payday"],
            "bills_before_payday": safe["bills_before_payday"],
            "surprise_buffer_kept_eur": safe["surprise_buffer_eur"],
            "monthly": {**flow, "linked_card_spending_per_month_eur": eur(card_monthly),
                        "surplus_per_month_eur": eur(surplus),
                        "sustainable_saving_per_month_eur": float(max(0, int(surplus)))},
            "goals": [{"goal_id": g["goal"]["id"], "name": g["goal"]["name"], "left_to_save_eur": g["left_to_save_eur"]}
                      for g in self.goals()],
            "how_to_act": "create_saving_link(goal_id, amount) prepares a move to a goal's savings pocket; the person "
                          "confirms with their PIN. With no goal yet, create_goal first.",
            "method": "now = the reserve (money on the account beyond this pay cycle) + safe to spend until payday "
                      "(bills, usual needs and the irregular buffer already kept aside); per month = average income - "
                      "spending - investing over the last 3 full months"
                      + (" - card spending at the linked bank" if card else ""),
        }
        if reserve > 0:
            out["reserve_note"] = (f"{reserve:.2f} EUR has built up on the current account beyond what this pay cycle "
                                   "needs. It could move to a savings goal or be invested; that is the person's "
                                   "choice. Investing can lose value, so money that may be needed soon (an emergency "
                                   "fund) usually stays in savings.")
        if surplus <= 0:
            cuts = [self.simulate_cut(c, 20) for c in BASKETS["everyday"]["categories"] if self.monthly_avg(c, 3) > 0]
            out["where_a_cut_could_come_from"] = sorted(
                ({"category": c["category"], "cut_pct": 20, "saving_per_month_eur": c["saving_per_month_eur"]}
                 for c in cuts), key=lambda c: -c["saving_per_month_eur"])
            moved = flow["moved_to_other_account_per_month_eur"]
            out["note"] = (("Most of the salary moves to another account the day it lands (about "
                            f"{moved:.2f} EUR a month) and what stays here was spent, so this account has no steady "
                            "surplus. That other account may already be the saving; Birky can't see it."
                            if moved > 0 else
                            "The last 3 months spent and invested more than came in, so there is no steady surplus.")
                           + (" A monthly saving has to come from a cut (see where_a_cut_could_come_from)"
                              if any(c["saving_per_month_eur"] > 0 for c in out["where_a_cut_could_come_from"]) else
                              " There is next to no eating out, entertainment or shopping to trim, so a saving would "
                              "come from the regular payments (get_regular_payments), not a cut")
                           + (f"; investing already takes {flow['investing_per_month_eur']} EUR a month."
                              if flow["investing_per_month_eur"] > 0 else "."))
        if safe["overdrawn"]:
            out["overdrawn"] = True
            out["overdrawn_note"] = ("The account is overdrawn right now, so nothing can go to savings today; the "
                                     "first step is getting back above zero.")
        return out

    def save_more(self) -> dict:
        """'How could I save more?': at most three ideas, each with one number. The biggest real lever (a 20% cut on
        the largest 'slips through your fingers' category), what can go to savings now or every payday, and one
        regular payment worth a review."""
        ideas = []
        cuts = [self.simulate_cut(c, 20) for c in BASKETS["everyday"]["categories"] if self.monthly_avg(c, 3) > 0]
        top = max(cuts, key=lambda c: c["saving_per_month_eur"]) if cuts else None
        lever = top if top and top["saving_per_year_eur"] >= LEVER_MIN_EUR_YEAR else None
        if lever:
            ideas.append({"idea": "biggest_lever", "category": top["category"],
                          "in_words": EVERYDAY_WORDS[top["category"]], "basket": BASKETS["everyday"]["label"],
                          "cut_pct": 20, "now_per_month_eur": top["current_per_month_eur"],
                          "saving_per_month_eur": top["saving_per_month_eur"],
                          "saving_per_year_eur": top["saving_per_year_eur"],
                          "number_to_say": "saving_per_year_eur",
                          "why": "the largest category in the 'slips through your fingers' basket over the last 3 full "
                                 "months; a fifth less there is a habit change, not a sacrifice"})
        save = self.safe_to_save()
        m = save["monthly"]
        if save["reserve_eur"] >= RESERVE_NOTE_EUR:
            ideas.append({"idea": "move_what_built_up", "amount_eur": save["reserve_eur"], "number_to_say": "amount_eur",
                          "why": "money on the current account beyond what this pay cycle needs: in a savings goal "
                                 "(an emergency buffer first) it stops being spendable by accident",
                          "how": "create_goal, then create_saving_link (the person confirms with their PIN)"})
        elif m["sustainable_saving_per_month_eur"] > 0 and not save.get("overdrawn"):
            ideas.append({"idea": "pay_yourself_first", "per_month_eur": m["sustainable_saving_per_month_eur"],
                          "number_to_say": "per_month_eur",
                          "why": "the average monthly surplus of the last 3 full months, moved on payday before "
                                 "spending starts"})
        review = self._payment_to_review()
        if review:
            ideas.append(review)
        if not ideas:  # nothing to cut, nothing built up, no surplus, no bill to review: the buffer is the goal
            target = self.safe_to_spend()["mandatory_costs_per_month_eur"]
            ideas.append({"idea": "start_a_buffer", "target_eur": float(round(target)), "number_to_say": "target_eur",
                          "why": "nothing obvious to cut and no steady surplus yet; one month of essential costs "
                                 "(rent, bills, groceries, getting around, the usual one-offs) is a first emergency "
                                 "buffer to build in small steps, even 10-20 EUR on payday"})
        if any(i["idea"] == "move_what_built_up" for i in ideas) and not save["goals"]:
            step = ("Offer one thing: an 'Emergency buffer' savings goal to move part of what has built up into "
                    "(create_goal, then create_saving_link; the PIN is theirs).")
        elif lever:
            step = f"Offer one thing: try {EVERYDAY_WORDS[lever['category']]} 20% lighter for one month."
        elif review:
            step = f"Offer one thing: a reminder to check the {review['name'].lower()} once this year."
        elif save.get("overdrawn"):
            step = "Offer one thing: a reminder for when money next comes in, to get back above zero first."
        elif ideas[0]["idea"] == "start_a_buffer" and not save["goals"]:
            step = "Offer one thing: an 'Emergency buffer' savings goal to fill in small steps on payday (create_goal)."
        else:
            step = "Offer one thing: a savings goal to give the money a name."
        out = {
            "ideas": ideas[:3],
            "monthly_picture": {k: m[k] for k in ("income_per_month_eur", "spending_per_month_eur",
                                                  "investing_per_month_eur", "left_per_month_eur")},
            "goals": save["goals"],
            "next_step": step,
            "method": "biggest lever = simulate_cut(20%) on the largest 'slips through your fingers' category, last 3 "
                      "full months; what built up = the reserve from get_safe_to_save; the review = a regular payment "
                      "from get_regular_payments (one that went up, else the utility bills)",
        }
        if not lever:
            everyday = sum(c["current_per_month_eur"] for c in cuts)
            out["no_lever_reason"] = (f"Eating out, entertainment and shopping come to only {everyday:.2f} EUR a "
                                      "month over the last 3 full months: a 20% cut there would save less than "
                                      f"{LEVER_MIN_EUR_YEAR} EUR a year, so there is nothing worth cutting. Point to "
                                      "the regular payments or the buffer instead, and say kindly that everyday "
                                      "spending is already low.")
        moved = m.get("moved_to_other_account_per_month_eur", 0.0)
        if moved > 0:
            out["monthly_picture"]["moved_to_other_account_per_month_eur"] = moved
            out["moved_note"] = m["moved_note"]
        if save.get("overdrawn"):
            out["overdrawn_note"] = save["overdrawn_note"]
        if m["left_per_month_eur"] < 0:
            out["note"] = (("After the salary moved on to the other account, a bit more went out of this one than "
                            "stayed in over the last 3 full months." if moved > 0 else
                            "The last 3 full months spent and invested more than came in.") + " Say it kindly in a few words "
                           "('lately a bit more has gone out than came in'), without the number unless they ask"
                           + ("; the lever is what turns it around." if lever else "."))
        return out

    def _payment_to_review(self) -> dict | None:
        """One regular payment worth a look: one that went up (old and new numbers), else the utility bills. Monthly
        investing is saving, so it is never the one to cut; rent changes only with the lease."""
        items = [x for x in self.commitments()["regular_payments"]
                 if x["direction"] == "out" and x["status"] == "active" and x["id"] != "investing"]
        up = next((x for x in items if (x.get("trend") or {}).get("direction") == "up"), None)
        if up:
            t = up["trend"]
            return {"idea": "review_payment", "name": up["name"], "per_month_eur": t["last_per_month_eur"],
                    "before_per_month_eur": t["previous_per_month_eur"], "number_to_say": "per_month_eur",
                    "why": f"went up from {t['previous_months']} to {t['last_months']}; worth checking why"}
        util = next((x for x in items if x["id"] == "utilities"), None)
        if util and util["per_month_eur"] >= 30:
            return {"idea": "review_payment", "name": util["name"], "per_month_eur": util["per_month_eur"],
                    "per_year_eur": util["per_year_eur"], "number_to_say": "per_year_eur",
                    "why": "a regular payment that runs on its own; comparing the contracts behind it once a year "
                           "(the data names no company) is a quick check"}
        return None

    # ---------- other banks (PSD2 account information, mock) ----------
    def link_bank(self, scope: list[str] | None = None) -> dict:
        """Mock PSD2 consent: store a synthetic second bank (savings + credit card + 3 months of card purchases)."""
        b = DEMO_BANK
        tx = []
        for i in range(-3, 1):
            m = add_months(month_start(self.today), i)
            for day, what, cat, amt in b["card_monthly"]:
                d = m.replace(day=day)
                if d <= self.today:
                    tx.append({"date": d.isoformat(), "what": what, "category": cat, "amount_eur": -amt})
        self.state.setdefault("linked_banks", {})[b["id"]] = {
            "bank": b["name"], "synthetic": True, "linked": self.today.isoformat(),
            "scope": scope or ["accounts", "balances", "transactions"], "consent_valid_days": 180,
            "savings_eur": b["savings_eur"], "card": {"limit_eur": b["card_limit_eur"], "due_day": b["card_due_day"]},
            "card_transactions": tx, "note": b["note"]}
        self._save()
        return self.full_picture()

    def linked_bank(self) -> dict | None:
        return self.state.get("linked_banks", {}).get(DEMO_BANK["id"])

    def _card_tx(self) -> list[dict]:
        bank = self.linked_bank()
        return [{**t, "date": date.fromisoformat(t["date"])} for t in (bank or {}).get("card_transactions", [])]

    def card_spend_per_month(self, months: int = 3) -> float:
        d0, d1 = self._full_months(months)
        return eur(-sum(t["amount_eur"] for t in self._card_tx() if d0 <= t["date"] <= d1) / months)

    def card_bill(self) -> dict | None:
        """The linked card's next bill: last month's purchases, due on the due day (none once repaid this month)."""
        bank = self.linked_bank()
        if not bank:
            return None
        t, day = self.today, bank["card"]["due_day"]
        paid = any(a["transaction_type"] == "card_repayment" and a["date"][:7] == t.isoformat()[:7]
                   for a in self.state["actions"])
        due = t.replace(day=day) if t.day <= day and not paid else add_months(month_start(t), 1).replace(day=day)
        stmt0 = add_months(month_start(due), -1)
        amount = -sum(x["amount_eur"] for x in self._card_tx() if stmt0 <= x["date"] < month_start(due))
        return {"name": f"{bank['bank']} credit card", "amount_eur": eur(amount), "due_date": due.isoformat(),
                "days_away": (due - t).days, "statement_month": stmt0.isoformat()[:7],
                "statement_closed": month_start(due) <= t}

    def full_picture(self) -> dict:
        """All accounts across banks: balances, card debt, net position."""
        bal = self.balance()
        banks = [{"bank": "S-Pankki", "accounts": [{"type": "current account", "balance_eur": bal}]}]
        bank = self.linked_bank()
        if not bank:
            return {"linked": False, "banks": banks, "total_balances_eur": bal, "net_position_eur": bal,
                    "how_to_link": "The customer writes 'add bank' on WhatsApp (or taps Add a bank on the site) to link "
                                   "Revolut or another bank through Enable Banking (PSD2, read only)."}
        bill = self.card_bill()
        repaid = -sum(a["amount"] for a in self.state["actions"] if a["transaction_type"] == "card_repayment")
        spent = -sum(x["amount_eur"] for x in self._card_tx() if x["date"] >= add_months(month_start(self.today), -1))
        owed = max(0.0, spent - repaid)
        irregular = sum(self.monthly_avg(c, 6) for c in BASKETS["irregular"]["categories"])
        banks.append({"bank": f"{bank['bank']} (synthetic demo bank)", "linked_since": bank["linked"],
                      "accounts": [{"type": "savings account", "balance_eur": bank["savings_eur"]},
                                   {"type": "credit card", "outstanding_eur": eur(owed),
                                    "limit_eur": bank["card"]["limit_eur"]}],
                      "card_spending_per_month_eur": self.card_spend_per_month(),
                      "recent_card_purchases": [{**x, "date": x["date"].isoformat()} for x in self._card_tx()[-5:][::-1]]})
        total = bal + bank["savings_eur"]
        return {
            "linked": True, "banks": banks, "total_balances_eur": eur(total), "card_debt_eur": eur(owed),
            "net_position_eur": eur(total - owed), "next_card_bill": bill,
            "irregular_basket_per_month_eur": eur(irregular),
            "savings_cover_irregular_months": round(bank["savings_eur"] / irregular, 1) if irregular else None,
            "note": bank["note"] + " The card bill is counted in safe to spend when it falls due before payday.",
        }

    def afford(self, amount: float, what: str = "") -> dict:
        """'Can I afford it?': a purchase against this month's flexible budget and safe to spend. Informs, never decides."""
        if not 0 < amount <= 1_000_000:
            raise ValueError("amount must be between 0 and 1,000,000 EUR")
        safe = self.safe_to_spend()
        days = safe["horizon_days"]
        cats = BASKETS["everyday"]["categories"]
        budget = sum(self.monthly_avg(c, 3) for c in cats)
        s = self._slice(month_start(self.today), self.today)
        spent = -s[(s["tx_amount_eur"] < 0) & s["category"].isin(cats)]["tx_amount_eur"].sum()
        left = budget - spent
        safe_left = safe["safe_to_spend_until_payday_eur"]
        goals = []
        for g in self.goals():
            if g["left_to_save_eur"] <= 0 or not g["options"]:
                continue
            pace = g.get("needed_per_month_for_deadline_eur") or g["options"][0]["monthly_eur"]
            goals.append({"name": g["goal"]["name"], "monthly_pace_eur": eur(pace),
                          "pushes_back_days": max(1, round(amount / pace * 30.4))})
        return {
            "item": what.strip()[:60] or None,
            "price_eur": eur(amount),
            "fits": bool(amount <= left and amount <= safe_left),
            "fits_flexible_budget": bool(amount <= left),
            "fits_safe_to_spend": bool(amount <= safe_left),
            "flexible_budget_per_month_eur": eur(budget),
            "spent_this_month_eur": eur(spent),
            "flexible_left_this_month_eur": eur(left),
            "flexible_left_after_eur": eur(left - amount),
            "safe_to_spend_until_payday_eur": eur(safe_left),
            "safe_left_after_eur": eur(safe_left - amount),
            "reserve_eur": safe["reserve_eur"],
            "fits_with_reserve": bool(amount <= max(0.0, safe_left) + safe["reserve_eur"]),
            "days_to_payday": days if safe["next_payday"]["date"] else None,
            **({"not_enough_data": safe["not_enough_data"]} if "not_enough_data" in safe else {}),
            "per_day_after_eur": eur(max(0.0, safe_left - amount) / days),
            "goals": goals,
            "method": "flexible budget = your average monthly 'slips through your fingers' spending (last 3 months) "
                      "- spent on it this month; safe to spend as in get_safe_to_spend; "
                      "fits_with_reserve: it could be paid by also using the reserve (savings built up beyond the "
                      "pay cycle), which shrinks those savings",
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
               "if_invested": self.grow_monthly(save, years, fund_id)}
        out["disclaimer"] = "Scenario, not a promise. Funds can lose value."
        past = self.backtest_monthly(save, 24)
        if past:
            out["real_past_check"] = past
        return out

    @staticmethod
    def grow_monthly(monthly: float, years: int, fund_id: str | None = None) -> list[dict]:
        """The same amount put in every month for `years`, at each demo fund's scenario return (not a promise)."""
        out = []
        for f in ([FUNDS_BY_ID[fund_id]] if fund_id else FUNDS):
            r, n = f["scenario_return"] / 12, years * 12
            out.append({"fund": f["name"], "risk": f["risk"], "years": years, "paid_in_eur": eur(monthly * n),
                        "value_eur": eur(monthly * (((1 + r) ** n - 1) / r)),
                        "scenario_return_pct": f["scenario_return"] * 100})
        return out

    # ---------- real market prices (data/market.json) ----------
    def _price(self, sym: str, month: str) -> float | None:
        closes = self.market["symbols"][sym]["monthly_close"]
        ok = [m for m in closes if m <= month]
        return closes[max(ok)] if ok else None

    def backtest(self, deposits: list[tuple[date, float]], fund_id: str) -> dict | None:
        """What these deposits would be worth today if each had bought the fund at that month's real price."""
        if not self.market or not deposits or sum(a for _, a in deposits) <= 0:
            return None
        units = {}
        for d, amt in deposits:
            for sym, pct in FUND_SYMBOLS[fund_id].items():
                p = self._price(sym, d.isoformat()[:7])
                if not p:
                    return None
                units[sym] = units.get(sym, 0) + amt * pct / 100 / p
        value = sum(u * self.market["symbols"][s]["last_price"] for s, u in units.items())
        paid = sum(a for _, a in deposits)
        return {"fund": FUNDS_BY_ID[fund_id]["name"], "paid_in_eur": eur(paid), "value_today_eur": eur(value),
                "gain_eur": eur(value - paid), "gain_pct": round((value - paid) / paid * 100, 1)}

    def backtest_monthly(self, monthly: float, months: int) -> dict | None:
        """The same monthly amount invested over the last `months` real months, valued at today's real price."""
        if not self.market or monthly <= 0:
            return None
        last = max(self.market["symbols"]["VWCE.DE"]["monthly_close"])
        y, m = map(int, last.split("-"))
        start = add_months(date(y, m, 1), -months)
        deps = [(add_months(start, i), monthly) for i in range(months)]
        return {"what": f"{eur(monthly)} EUR invested every month for the last {months} months, real prices",
                "as_of": self.market["symbols"]["VWCE.DE"]["last_date"],
                "results": [self.backtest(deps, f["id"]) for f in FUNDS],
                "source": self.market["source"]}

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
        over, band = "the last 3 full months", self.benchmark["age_band"] if self.benchmark else None
        group, customers = ((f"S-Pankki customers aged {band}", self.benchmark["customers"])
                            if self.benchmark else (None, None))
        if not self.benchmark:  # another age band than benchmark.json: the same area and age from "people like you"
            try:
                nl = self.new_life_budget()
            except (ValueError, KeyError, TypeError):
                nl = None
            rows = [r for r in ((nl or {}).get("you") or {}).get("comparison", []) if r["key"] in SPEND_CATEGORIES
                    and r["typical_median_eur"] > 5]
            if rows and nl["group"].get("age_band") and not nl["group"].get("starting_out"):  # always an age group
                band, over = nl["group"]["age_band"], "the last 12 full months, for you and for them"
                group, customers = f"S-Pankki customers {nl['group']['who'].removeprefix('customers ')}", \
                    nl["group"]["customers"]
                comparisons = sorted(
                    ({"category": r["key"], "you_per_month_eur": r["you_per_month_eur"],
                      "peers_median_eur": r["typical_median_eur"],
                      "vs_peers_pct": round((r["you_per_month_eur"] - r["typical_median_eur"]) / r["typical_median_eur"] * 100),
                      "in_top_quarter_of_spenders": r["you_per_month_eur"] > r["typical_range_eur"][1],
                      "where_you_are": r["where_you_are"]} for r in rows),
                    key=lambda x: -x["vs_peers_pct"])
        # No income in the window: a savings rate would be invented (or divide by zero).
        save_rate = round(b["invested_eur"] / b["income_eur"] * 100) if b["income_eur"] > 0 else None
        r = self.spending("Restaurants", "last_3_months")
        kinds = []  # (key, sentence), strongest first; the first one is the answer to "what kind of spender am I?"
        salary_m = self.monthly_avg("Salary", 3) * -1  # monthly_avg flips the sign: salary is money in
        moved_pct = round(self.moved_out_per_month(3) / salary_m * 100) if salary_m > 0 else 0
        if moved_pct >= 50:
            kinds.append(("salary_mover", f"Salary mover: about {moved_pct}% of your salary moves on to another "
                                          "account the day it lands, so this account only sees what is left."))
        if everyday_share >= 30:
            kinds.append(("experience", "Experience spender: a big part of your money goes to eating out and fun."))
        if save_rate is not None and save_rate >= 10:
            kinds.append(("saver", f"Steady saver: you invest about {save_rate}% of what comes in."))
        late = self._slice(*self._full_months(3))
        late = late[(late["category"] == "Restaurants")]
        weekend = late[[d.weekday() >= 4 for d in late["date"]]]
        wk_share = round(-weekend["tx_amount_eur"].sum() / max(1e-9, -late["tx_amount_eur"].sum()) * 100) if len(late) else 0
        if wk_share >= 50 and len(late) >= WEEKEND_MIN_VISITS:  # one Saturday lunch is not a pattern
            kinds.append(("weekend", f"Weekend effect: {wk_share}% of your restaurant money goes out Friday to "
                                     "Sunday."))
        traits = [t for _, t in kinds]
        # Every customer gets a kind, even with none of the traits above: the largest basket says it.
        share = {x["basket"]: x["share_pct"] for x in b["baskets"]}
        if b["total_spent_eur"] <= 0:
            kind = ("quiet", "Quiet account: almost no card or bill spending here in the last 3 full months.")
        elif kinds:
            kind = kinds[0]
        elif share["irregular"] >= 25:
            kind = ("one_offs", f"One-off spender: {share['irregular']}% of your spending goes to health, home & tech "
                                "and travel, costs that come when they come.")
        elif share["regular"] >= 60:
            kind = ("essentials_first", f"Essentials first: {share['regular']}% of your spending is rent, bills, "
                                        "groceries and getting around; little slips through your fingers.")
        else:
            kind = ("balanced", f"Balanced spender: no basket dominates ({share['regular']}% essentials, "
                                f"{share['irregular']}% one-offs, {share['everyday']}% eating out, fun and shopping).")
        if not traits:
            traits = [kind[1]]
        return {
            "kind": {"key": kind[0], "in_words": kind[1]},
            "everyday_share_pct": everyday_share,
            "savings_rate_pct": save_rate,
            "restaurant_visits_last_3_months": r["transactions"],
            "weekend_restaurant_share_pct": wk_share,
            "traits": traits,
            "peers": {"group": group, "age_band": band if group else None, "customers": customers,
                      "compared_over": over if group else None,
                      "biggest_differences": comparisons[:4],
                      **({} if group else {"no_peers_reason": (
                          "No age on file, so there is no group of people the same age to compare with."
                          if self.age is None else
                          "No group of at least 30 S-Pankki customers like you to compare with.")})},
            "baskets": [{k: x[k] for k in ("label", "per_month_eur", "share_pct")} for x in b["baskets"]],
            "finland_official": self.official_benchmark(),
        }

    def official_benchmark(self) -> dict | None:
        """Monthly spend vs Finnish households of the same age group (Statistics Finland, real open data)."""
        if not self.statfin or self.age is None:
            return None
        age = self.age
        band = "17-24" if age <= 24 else "25-34" if age <= 34 else "all"
        ref = self.statfin["by_age"][band]
        rows = []
        for c in ("Restaurants", "Groceries", "Housing", "Transportation", "Entertainment", "Lifestyle"):
            mine, theirs = self.monthly_avg(c, 3), ref[c]
            rows.append({"category": c, "you_per_month_eur": mine, "finnish_households_eur": theirs,
                         "times": round(mine / theirs, 1) if theirs else None})
        rows.sort(key=lambda r: -(r["times"] or 0))
        return {"group": f"Finnish households, reference person aged {band}", "year": self.statfin["year"],
                "source": self.statfin["source"], "comparison": rows,
                "note": "Household figures; a household can be more than one person."}

    # ---------- dashboard ----------
    def balance_series(self, days: int = 90) -> list[tuple[str, float]]:
        """End-of-day balance for the last `days` days (dataset balances, then demo actions today)."""
        b = self.base[self.base["date"] <= self.today].groupby("date")["balance_after_eur"].last()
        start = self.today - timedelta(days=days - 1)
        before = b[b.index < start]
        cur = float(before.iloc[-1]) if len(before) else float(b.iloc[0])
        out = []
        for i in range(days):
            d = start + timedelta(days=i)
            if d in b.index:
                cur = float(b[d])
            out.append((d.isoformat(), eur(cur)))
        out[-1] = (out[-1][0], self.balance())
        return out

    def weekly_flow(self, weeks: int = 12) -> list[dict]:
        t = self.tx
        start = self.today - timedelta(days=self.today.weekday()) - timedelta(weeks=weeks - 1)
        rows = []
        for i in range(weeks):
            w0 = start + timedelta(weeks=i)
            s = t[(t["date"] >= w0) & (t["date"] < w0 + timedelta(days=7))]
            rows.append({"week": w0.isoformat(), "in": eur(s[s["tx_amount_eur"] > 0]["tx_amount_eur"].sum()),
                         "out": eur(-s[s["tx_amount_eur"] < 0]["tx_amount_eur"].sum())})
        return rows

    def recent(self, n: int = 12) -> list[dict]:
        t = self.tx.tail(n).iloc[::-1]
        return [{"date": r["date"].isoformat(), "what": merchant_name(r["merchant_id"]) or r["category"],
                 "category": r["category"], "basket": BASKETS[CATEGORY_BASKET[r["category"]]]["label"]
                 if r["category"] in CATEGORY_BASKET else None, "amount_eur": eur(r["tx_amount_eur"])}
                for _, r in t.iterrows()]

    def overview(self) -> dict:
        port = self.portfolio()
        return {"today": self.today.isoformat(), "balance_eur": self.balance(), "safe": self.safe_to_spend(),
                "full": self.full_picture(),
                "baskets": self.baskets("last_3_months"), "flow": self.weekly_flow(), "series": self.balance_series(),
                "bills": self.upcoming_bills(), "regular": self.commitments(), "goals": self.goals(),
                "invest": {"put_in": port["net_put_in_eur"], "plan": port["monthly_plan_in_data"],
                           "real": port["if_your_deposits_were_in_each_fund"]},
                "official": self.official_benchmark(), "recent": self.recent()}

    def export(self) -> dict:
        """MyData: everything Birky knows about this customer, in one portable file."""
        rows = [{"date": r["date"].isoformat(), "category": r["category"],
                 "basket": BASKETS[CATEGORY_BASKET[r["category"]]]["label"] if r["category"] in CATEGORY_BASKET else None,
                 "amount_eur": eur(r["tx_amount_eur"]), "type": r["transaction_type"],
                 "merchant": merchant_name(r["merchant_id"])} for _, r in self.tx.iterrows()]
        return {"customer_id": self.customer_id, "as_of": self.today.isoformat(), "balance_eur": self.balance(),
                "transactions": rows, "baskets_last_3_months": self.baskets("last_3_months"),
                "regular_payments": self.commitments(), "goals": self.state.get("goals", {}),
                "investment_plans": self.state.get("plans", []), "consents": self.state.get("connected", {}),
                "paused": self.state.get("paused", {}), "linked_banks": self.state.get("linked_banks", {}),
                "note": "S-Pankki's synthetic challenge data. A linked Demo Bank, if any, is made up."}

    # ---------- money moves (only after the user confirms) ----------
    def create_payment(self, bill_id: str) -> dict:
        bill = next((b for b in self.upcoming_bills(45) if b["bill_id"] == bill_id), None)
        if not bill:
            raise ValueError(f"bill {bill_id!r} is already paid or not due; see get_upcoming_bills")
        pid = uuid.uuid4().hex[:10]
        self.state["pending"][pid] = {"kind": "payment", "bill_id": bill_id, "name": bill["name"],
                                      "category": bill["category"], "amount": bill["amount_eur"],
                                      "due_date": bill["due_date"], "transaction_type": bill_id.rsplit("-", 3)[0],
                                      "status": "pending"}
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
        """Carry out a pending move once. first_time is False when it was already done (double submit)."""
        with self._lock:
            return self._confirm(pid)

    def _confirm(self, pid: str) -> dict:
        p = self.state["pending"].get(pid)
        if not p:
            raise KeyError(pid)
        if p["status"] == "done":
            return {"pending_id": pid, **p, "balance_eur": self.balance(), "first_time": False}
        kind = p["kind"]
        if kind == "payment" and p.get("source") == "photo":  # a bill from a photo (app/bills.py): paid once
            if any(q is not p and q.get("bill_id") == p["bill_id"] and q["status"] == "done"
                   for q in self.state["pending"].values()):
                raise ValueError(f"the {p['name']} bill is already paid")
        elif kind == "payment" and not any(b["bill_id"] == p["bill_id"] for b in self.upcoming_bills(45)):
            raise ValueError(f"{p['name']} due {p['due_date']} is already paid")
        moves_now = kind in ("payment", "saving") or (kind == "investment" and not p.get("monthly"))
        if moves_now and p["amount"] > self.balance():
            raise ValueError(f"not enough money: your balance is {self.balance():.2f} EUR")
        if kind == "pause_investing":
            if self.state.setdefault("paused", {}).get("investment_deposit"):
                raise ValueError("monthly investing is already paused")
            self.state["paused"]["investment_deposit"] = self.today.isoformat()
        elif kind == "investment" and p.get("monthly"):
            # A monthly plan moves money on payday, not now.
            p["starts"] = self.next_payday()["date"]
            self.state.setdefault("plans", []).append({"pending_id": pid, **p})
        else:
            self.state["actions"].append({"date": self.today.isoformat(), "category": p["category"],
                                          "amount": -p["amount"], "transaction_type": p["transaction_type"],
                                          "note": p["name"], "pending_id": pid})
            if kind == "saving":
                g = self.state.setdefault("goals", {}).get(p["goal_id"])
                if g:
                    g["saved"] = eur(g["saved"] + p["amount"])
        p["status"] = "done"
        self._save()
        return {"pending_id": pid, **p, "balance_eur": self.balance(), "first_time": True}

    def _pend(self, entry: dict) -> dict:
        pid = uuid.uuid4().hex[:10]
        self.state["pending"][pid] = {**entry, "status": "pending"}
        self._save()
        return {"pending_id": pid, **self.state["pending"][pid]}

    # ---------- regular commitments (F1): found in the customer's own transactions ----------
    def _month_totals(self, rows: pd.DataFrame, d0: date, months: int) -> list[float]:
        """Euros (positive) per calendar month, for `months` months from d0; a month without a payment is 0."""
        by: dict[tuple, float] = {}
        for d, a in zip(rows["date"], rows["tx_amount_eur"]):
            by[(d.year, d.month)] = by.get((d.year, d.month), 0.0) + abs(float(a))
        return [by.get((m.year, m.month), 0.0) for m in (add_months(d0, i) for i in range(months))]

    def _trend(self, rows: pd.DataFrame, kind: str) -> dict | None:
        """The last 3 full months vs the 3 before, per month. None when the history is shorter than 6 full months.
        A change counts only when it is bigger than the usual month-to-month swing could make it by chance (twice
        the standard error of the difference of two 3-month averages), at least TREND_PCT and TREND_MIN_EUR."""
        d0, _ = self._full_months(6)
        if self._covered(d0, 6) < 6:
            return None
        totals = self._month_totals(rows, d0, 6)
        d12, _ = self._full_months(12)
        n12 = self._covered(d12, 12)
        year = self._month_totals(rows, add_months(d12, 12 - n12), n12)
        swing = float(pd.Series(year).std()) if n12 >= 6 else 0.0
        old, new = sum(totals[:3]) / 3, sum(totals[3:]) / 3
        change = new - old
        noise = 2 * swing * (2 / 3) ** 0.5
        if not old:
            direction = "started" if new else "about the same"
        elif (abs(change) >= max(TREND_MIN_EUR, noise)) and abs(change) / old * 100 >= TREND_PCT[kind]:
            direction = "up" if change > 0 else "down"
        else:
            direction = "about the same"

        def span(a: int, b: int) -> str:
            return f"{add_months(d0, a).strftime('%b')}-{add_months(d0, b).strftime('%b %Y')}"
        return {"previous_months": span(0, 2), "previous_per_month_eur": eur(old),
                "last_months": span(3, 5), "last_per_month_eur": eur(new), "change_per_month_eur": eur(change),
                "change_pct": round(change / old * 100) if old else None, "direction": direction,
                "usual_swing_per_month_eur": eur(swing),
                "rule": f"up or down = at least {TREND_PCT[kind]}%, {TREND_MIN_EUR:.0f} EUR a month and more than the "
                        "usual month-to-month swing explains"}

    def _next_on(self, last: date, day: int) -> date:
        """The first `day`-of-month after the month of `last` that is not in the past."""
        d = on_day(add_months(month_start(last), 1), day)
        while d < self.today:
            d = on_day(add_months(month_start(d), 1), day)
        return d

    def _monthly(self, ttype: str, t: pd.DataFrame | None = None) -> dict | None:
        """One payment a month of this type (rent, the investment deposit, the salary), or None."""
        cid, name, what, direction, actions = COMMITMENT_TYPES[ttype]
        t = self.tx if t is None else t
        rows = t[(t["transaction_type"] == ttype) & ((t["tx_amount_eur"] < 0) if direction == "out"
                                                     else (t["tx_amount_eur"] > 0))].sort_values("date", kind="stable")
        if len(rows) < 3:
            return None
        dates = list(rows["date"])
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
        gap = gaps[len(gaps) // 2]
        if not 20 <= gap <= 40:
            return None  # comes, but not once a month
        amounts = [float(x) for x in rows["tx_amount_eur"].abs()]
        day = int(pd.Series([d.day for d in dates[-6:]]).median())
        last = dates[-1]
        paused = self.state.get("paused", {}).get(ttype)
        status = ("paused_by_you" if paused else "stopped" if (self.today - last).days > gap + GRACE_DAYS
                  else "active")
        nxt = self._next_on(last, day) if status == "active" else None
        per_month = sum(amounts[-3:]) / len(amounts[-3:])
        category = rows["category"].mode()[0]
        item = {"id": cid, "name": name, "what": what, "direction": direction, "status": status,
                "basket": (BASKETS[CATEGORY_BASKET[category]]["label"] if category in CATEGORY_BASKET else
                           "Saving (your choice, not spending)" if direction == "out" else None),
                "per_month_eur": eur(per_month), "per_year_eur": eur(eur(per_month) * 12), "usual_day": day,
                "next_expected": nxt.isoformat() if nxt else None,
                "days_to_next": (nxt - self.today).days if nxt else None,
                "last_payment": {"date": last.isoformat(), "amount_eur": eur(amounts[-1])},
                "range_last_6_eur": {"low": eur(min(amounts[-6:])), "high": eur(max(amounts[-6:]))},
                "payments": len(rows), "since": dates[0].isoformat(), "usual_gap_days": gap,
                "trend": self._trend(rows, "single"), "actions": actions if status == "active" else []}
        if paused:
            item["paused_since"] = paused
            item["note"] = "Paused by you; what is already invested stays invested."
        elif status == "stopped":
            item["note"] = (f"No payment since {last.isoformat()}: longer than the usual {gap} days plus "
                            f"{GRACE_DAYS}, so it looks stopped.")
        return item

    def _utilities(self, t: pd.DataFrame | None = None) -> dict | None:
        """The utility bills: about 3 a month, no merchant, different days. Treated as one monthly total."""
        t = self.tx if t is None else t
        rows = t[(t["transaction_type"] == "utilities_payment") & (t["tx_amount_eur"] < 0)].sort_values(
            "date", kind="stable")
        if len(rows) < 3:
            return None
        d3, d1 = self._full_months(3)
        d6, _ = self._full_months(6)
        n3, n6 = self._covered(d3, 3), self._covered(d6, 6)
        last3 = rows[(rows["date"] >= d3) & (rows["date"] <= d1)]
        last6 = rows[(rows["date"] >= d6) & (rows["date"] <= d1)]
        per_month = float(last3["tx_amount_eur"].abs().sum()) / n3 if n3 else 0.0
        # The usual range of one bill: the 12 months before the last 3 full months (all bills on a short history).
        earlier = rows[(rows["date"] < d3) & (rows["date"] >= add_months(d3, -12))]
        if len(earlier) < 6:
            earlier = rows[rows["date"] < d3]
        ref = (earlier if len(earlier) >= 6 else rows)["tx_amount_eur"].abs().astype(float)
        lo, hi, med = float(ref.quantile(0.1)), float(ref.quantile(0.9)), float(ref.median())
        unusual = None
        recent = rows[rows["date"] >= d3]
        if len(earlier) >= 6 and len(recent):
            top = recent.loc[recent["tx_amount_eur"].abs().idxmax()]
            amt = abs(float(top["tx_amount_eur"]))
            if amt > hi * UNUSUAL_BILL_FACTOR and amt > float(ref.max()):  # and bigger than any bill of that year
                unusual = {"date": top["date"].isoformat(), "amount_eur": eur(amt), "usual_low_eur": eur(lo),
                           "usual_high_eur": eur(hi), "usual_median_eur": eur(med),
                           "times_usual": round(amt / med, 1) if med else None}
        # Fixed days: only when most months have the same number of bills and each one lands within 4 days.
        by_month: dict[tuple, list[int]] = {}
        for d in last6["date"]:
            by_month.setdefault((d.year, d.month), []).append(d.day)
        counts = [len(v) for v in by_month.values()]
        fixed = None
        if len(by_month) >= 4:
            n = max(set(counts), key=counts.count)
            same = [sorted(v) for v in by_month.values() if len(v) == n]
            if len(same) >= 4 and max(max(m[k] for m in same) - min(m[k] for m in same) for k in range(n)) <= 4:
                fixed = [int(pd.Series([m[k] for m in same]).median()) for k in range(n)]
        last = rows["date"].iloc[-1]
        status = "stopped" if (self.today - last).days > 31 + GRACE_DAYS else "active"
        nxt = None
        if fixed and status == "active":
            paid_now = int((rows["date"] >= month_start(self.today)).sum())
            this = [on_day(self.today, d) for k, d in enumerate(fixed) if k >= paid_now]
            nxt = min([d for d in this if d >= self.today] or [on_day(add_months(month_start(self.today), 1), fixed[0])])
        days = [d.day for d in (last6["date"] if len(last6) else rows["date"])]
        per_bills = len(last6) / n6 if n6 else 0.0
        item = {"id": "utilities", "name": "Utility bills", "what": "Utility bills (the data names no company)",
                "direction": "out", "status": status, "basket": BASKETS["regular"]["label"],
                "per_month_eur": eur(per_month), "per_year_eur": eur(eur(per_month) * 12),
                "bills_per_month": round(per_bills, 1),
                "one_bill_usual_range_eur": {"low": eur(lo), "high": eur(hi)},
                "fixed_days": fixed, "usual_day": fixed[0] if fixed and len(fixed) == 1 else None,
                "day_range": {"first": min(days), "last": max(days)},
                "next_expected": nxt.isoformat() if nxt else None,
                "days_to_next": (nxt - self.today).days if nxt else None,
                "last_payment": {"date": last.isoformat(), "amount_eur": eur(abs(float(rows["tx_amount_eur"].iloc[-1])))},
                "payments": len(rows), "since": rows["date"].iloc[0].isoformat(),
                "trend": self._trend(rows, "utilities"), "unusual_bill": unusual,
                "actions": (["remind"] if fixed else ["watch"]) if status == "active" else []}
        if not fixed:
            item["note"] = (f"About {round(per_bills)} bills a month on different days (day {min(days)} to "
                            f"{max(days)}), so there is no fixed date to expect; this is the monthly total.")
        if status == "stopped":
            item["note"] = f"No utility bill since {last.isoformat()}."
        return item

    def _card_repeats(self, t: pd.DataFrame | None = None) -> list[dict]:
        """Card subscriptions, if any: the same merchant charging the same amount about once a month."""
        t = self.tx if t is None else t
        p = t[(t["transaction_type"] == "purchase") & t["merchant_id"].notna() & (t["tx_amount_eur"] < 0)]
        many = p["merchant_id"].value_counts()
        out = []
        for mid, g in p[p["merchant_id"].isin(many[many >= CARD_REPEAT_MIN].index)].groupby("merchant_id"):
            amts = g["tx_amount_eur"].abs().astype(float)
            med = float(amts.median())
            same = g[(amts - med).abs() <= 0.02 * med + 0.01]
            if len(same) < CARD_REPEAT_MIN:
                continue
            ds = sorted(same["date"])
            if sum(25 <= (b - a).days <= 35 for a, b in zip(ds, ds[1:])) < CARD_REPEAT_MIN - 1:
                continue
            out.append({"name": merchant_name(mid), "amount_eur": eur(med), "charges": len(ds),
                        "last_charge": ds[-1].isoformat(), "active": (self.today - ds[-1]).days <= 31 + GRACE_DAYS})
        return out

    def commitments(self) -> dict:
        """Regular payments from the customer's own data: rent, utility bills, the monthly investment and the salary,
        with monthly and yearly cost, usual day, next date, the trend (real old vs new numbers) and active/stopped."""
        t = self.tx
        items = [x for x in (self._monthly("housing_payment", t), self._utilities(t),
                             self._monthly("investment_deposit", t), self._monthly("salary", t)) if x]
        active = [x for x in items if x["direction"] == "out" and x["status"] == "active"]
        bills = [x for x in active if x["id"] in ("rent", "utilities")]
        salary = next((x for x in items if x["id"] == "salary" and x["status"] == "active"), None)
        cards = self._card_repeats(t)
        can = []
        inv = next((x for x in items if x["id"] == "investing"), None)
        if inv and inv["status"] == "active":
            can.append(f"Monthly investing ({inv['per_month_eur']:.2f} EUR a month) is the regular payment the person "
                       "chose: it can be paused with their PIN (create_pause_investing_link). What is already invested "
                       "stays invested.")
        elif inv:
            can.append(f"Monthly investing is {inv['status'].replace('_', ' ')}; a new monthly plan can be set up with "
                       "create_investment_link.")
        if any(x["id"] == "rent" for x in bills):
            can.append("Rent can't be stopped here. Birky reminds the day before it is due, and it can be paid with "
                       "the PIN (create_payment_link with its bill_id from get_upcoming_bills).")
        if any(x["id"] == "utilities" for x in bills):
            can.append("Utility bills can't be stopped here. Birky flags a bill well above the usual range.")
        return {
            "regular_payments": items,
            "outgoing_per_month_eur": eur(sum(x["per_month_eur"] for x in active)),
            "outgoing_per_year_eur": eur(sum(x["per_year_eur"] for x in active)),
            "bills_per_month_eur": eur(sum(x["per_month_eur"] for x in bills)),
            "bills_per_year_eur": eur(sum(x["per_year_eur"] for x in bills)),
            "income_per_month_eur": salary["per_month_eur"] if salary else None,
            "subscriptions_found": len(cards),
            "card_subscriptions": cards,
            "subscriptions_note": (
                "No subscriptions on this account: no card payment repeats from the same merchant every month for "
                "the same amount (no streaming, gym or app charges). Say so plainly; never invent one."
                if not cards else "Card subscriptions are cancelled with the service itself; Birky can't stop them."),
            "what_can_be_done": can,
            "method": "From your S-Pankki transactions: a payment type that comes about once a month (median gap 20-40 "
                      "days); per month = the average of the last 3 payments (utilities: of the last 3 full months); "
                      "usual day = median day of the last 6; stopped = no payment for longer than the usual gap + "
                      f"{GRACE_DAYS} days; trend = last 3 full months vs the 3 before.",
            "source": "Your S-Pankki transactions",
        }

    def create_pause_investing(self) -> dict:
        """Prepare pausing the monthly investment deposit. The person confirms with their PIN."""
        inv = self._monthly("investment_deposit")
        if not inv:
            raise ValueError("no monthly investing in your account history, so there is nothing to pause")
        if inv["status"] != "active":
            raise ValueError(f"monthly investing is already {inv['status'].replace('_', ' ')}")
        return self._pend({"kind": "pause_investing", "name": inv["name"], "amount": inv["per_month_eur"],
                           "usual_day": inv["usual_day"], "next_charge": inv["next_expected"],
                           "per_year_eur": inv["per_year_eur"], "category": "Invest Deposit"})

    # ---------- savings goals (F2) ----------
    def monthly_flow(self, months: int = 3) -> dict:
        d0, d1 = self._full_months(months)
        n = self._covered(d0, months)
        s = self._slice(d0, d1)
        inc = s[s["transaction_type"].isin(INCOME_TYPES)]["tx_amount_eur"].sum() / max(1, n)
        spend = -s[(s["tx_amount_eur"] < 0) & s["category"].isin(SPEND_CATEGORIES)]["tx_amount_eur"].sum() / max(1, n)
        inv = -s[s["category"] == "Invest Deposit"]["tx_amount_eur"].sum() / max(1, n)
        moved = -s[s["transaction_type"] == SWEEP_TYPE]["tx_amount_eur"].sum() / max(1, n)
        out = {"income_per_month_eur": eur(inc), "spending_per_month_eur": eur(spend),
               "investing_per_month_eur": eur(inv), "moved_to_other_account_per_month_eur": eur(moved),
               "left_per_month_eur": eur(inc - spend - inv - moved)}
        if moved > 0:
            out["moved_note"] = (f"About {eur(moved):.2f} EUR a month of the salary moves automatically to another "
                                 "account the day it lands (a salary sweep). That money is not on this account, so it "
                                 "is not counted as left over; Birky cannot see where it goes.")
        if n < months:
            out["not_enough_data"] = (f"Only {n} full month(s) of history; averages use those." if n else
                                      "No full month of history yet, so there is no monthly average.")
        return out

    def monthly_income(self, months: int = 6) -> float:
        """Average salary and other income per full month. Investment withdrawals are left out: they are savings
        coming back, not money a pay cycle earns."""
        d0, d1 = self._full_months(months)
        n = self._covered(d0, months)
        s = self._slice(d0, d1)
        return eur(s[s["transaction_type"].isin(CYCLE_INCOME_TYPES)]["tx_amount_eur"].sum() / n) if n else 0.0

    def moved_out_per_month(self, months: int = 6) -> float:
        """Average salary sweep per full month: salary that moves on to another account the day it lands, so it is
        never this account's money to spend."""
        d0, d1 = self._full_months(months)
        n = self._covered(d0, months)
        s = self._slice(d0, d1)
        return eur(-s[s["transaction_type"] == SWEEP_TYPE]["tx_amount_eur"].sum() / n) if n else 0.0

    def create_goal(self, name: str, target_eur: float, deadline: str | None = None) -> dict:
        if not 10 <= target_eur <= 1_000_000:
            raise ValueError("target must be between 10 and 1,000,000 EUR")
        if deadline:
            date.fromisoformat(deadline)
        gid = "".join(ch for ch in name.lower() if ch.isalnum())[:20] or uuid.uuid4().hex[:6]
        old = self.state.setdefault("goals", {}).get(gid, {})  # same name again = update it, keep what's saved
        self.state["goals"][gid] = {"id": gid, "name": name.strip()[:40], "target": eur(target_eur),
                                    "deadline": deadline, "saved": old.get("saved", 0.0),
                                    "created": old.get("created", self.today.isoformat())}
        self._save()
        return self.goal_plan(gid)

    def goal_plan(self, goal_id: str, monthly_eur: float | None = None) -> dict:
        goals = self.state.get("goals", {})
        g = goals.get(goal_id)
        if not g:
            raise ValueError(f"no goal {goal_id!r}; goals: {list(goals)}")
        left = max(0.0, g["target"] - g["saved"])
        flow = self.monthly_flow()
        cut = self.simulate_cut("Restaurants", 20)["saving_per_month_eur"]
        out = {"goal": g, "left_to_save_eur": eur(left), "progress_pct": round(g["saved"] / g["target"] * 100),
               "your_monthly_flow": flow, "cutting_restaurants_20pct_frees_per_month_eur": cut, "options": []}
        if g.get("deadline"):
            dl = date.fromisoformat(g["deadline"])
            months = max(1, (dl.year - self.today.year) * 12 + dl.month - self.today.month)
            out["months_to_deadline"] = months
            out["needed_per_month_for_deadline_eur"] = eur(left / months)
        for m in sorted({round(x, 2) for x in [monthly_eur or 0, cut, 50.0, 100.0] if x > 0}):
            n = int(-(-left // m)) if left else 0
            out["options"].append({"monthly_eur": eur(m), "months": n,
                                   "reached_by": self._month_after(n)})
        out["note"] = "Savings pocket keeps the money apart from everyday spending."
        if flow["left_per_month_eur"] <= 0:
            out["note"] += " The last 3 months left nothing over, so the saving has to come from a cut."
        return out

    def goals(self) -> list[dict]:
        return [self.goal_plan(gid) for gid in self.state.get("goals", {})]

    def create_saving(self, goal_id: str, amount: float) -> dict:
        g = self.state.get("goals", {}).get(goal_id)
        if not g:
            raise ValueError(f"no goal {goal_id!r}; goals: {list(self.state.get('goals', {}))}")
        if not 1 <= amount <= self.balance():
            raise ValueError(f"amount must be between 1 and your balance {self.balance()} EUR")
        return self._pend({"kind": "saving", "goal_id": goal_id, "name": f"Savings pocket: {g['name']}",
                           "amount": eur(amount), "category": "Savings", "transaction_type": "savings_transfer"})

    # ---------- investing overview (F3) ----------
    def portfolio(self) -> dict:
        t = self.tx
        deps = t[t["transaction_type"] == "investment_deposit"]
        dep = -deps["tx_amount_eur"].sum()
        wd = t[t["transaction_type"] == "investment_withdrawal"]["tx_amount_eur"].sum()
        yearly = deps.groupby(deps["date"].map(lambda x: x.year))["tx_amount_eur"].sum()
        rec = self._rec("investment_deposit")
        return {"invested_total_eur": eur(dep), "withdrawn_total_eur": eur(wd), "net_put_in_eur": eur(dep - wd),
                "by_year_eur": {int(y): eur(-v) for y, v in yearly.items()},
                "monthly_plan_in_data": {"amount_eur": rec.avg_amount, "day": rec.typical_day} if rec else None,
                "new_plans": self.state.get("plans", []), "broker_orders": self.state.get("orders", []),
                "platforms": [{"name": "S-Pankki funds", "status": "connected", "how": "your S-Pankki account"},
                              {"name": "Trading 212 (Practice account)", "status": "linked",
                               "how": "orders are placed through its API only after you confirm with your PIN"}],
                "if_your_deposits_were_in_each_fund": {
                    "what": "Each of your real deposit dates and amounts, bought at that month's real price, valued today",
                    "as_of": self.market["symbols"]["VWCE.DE"]["last_date"] if self.market else None,
                    "results": [self.backtest(list(zip(deps["date"], -deps["tx_amount_eur"])), f["id"]) for f in FUNDS],
                } if self.market else None,
                "note": "The dataset does not say which fund your deposits went into, so we show each one."}

    def receive_salary(self) -> dict:
        """Demo: payday arrives now (adds the usual salary once per month)."""
        got = any(a["transaction_type"] == "salary" and a["date"][:7] == self.today.isoformat()[:7]
                  for a in self.state["actions"])
        r = self._rec("salary")
        if not got and r:
            self.state["actions"].append({"date": self.today.isoformat(), "category": "Salary", "amount": r.avg_amount,
                                          "transaction_type": "salary", "note": "Salary (demo payday)"})
            self._save()
        return {"salary_eur": r.avg_amount if r else 0.0, "balance_eur": self.balance()}

    # ---------- proactive alerts (F4) ----------
    def salary_nudge(self) -> dict:
        """What the "pay yourself first" message quotes (read only)."""
        cut = self.simulate_cut("Restaurants", 20)["saving_per_month_eur"]
        return {"salary_eur": self.next_payday()["expected_eur"], "suggest_save_eur": float(round(cut)),
                "balance_eur": self.balance(),
                "goals": [{"id": g["goal"]["id"], "name": g["goal"]["name"]} for g in self.goals()]}

    def alert(self, kind: str) -> dict:
        if kind == "salary":
            self.receive_salary()
            return {"kind": kind, **self.salary_nudge()}
        if kind == "unusual":
            t = self.tx
            s = t[(t["date"] >= self.today - timedelta(days=30)) & (t["tx_amount_eur"] < 0)
                  & t["category"].isin(BASKETS["everyday"]["categories"])]
            if s.empty:
                return {"kind": kind, "found": False}
            spent = self.base[self.base["tx_amount_eur"] < 0]
            usual = {c: float(-spent[spent["category"] == c]["tx_amount_eur"].median()) for c in s["category"].unique()}
            ratio = s.apply(lambda r: -r["tx_amount_eur"] / usual[r["category"]], axis=1)
            row = s.loc[ratio.idxmax()]
            u = usual[row["category"]]
            return {"kind": kind, "found": bool(ratio.max() >= 1.5), "category": row["category"],
                    "amount_eur": eur(-row["tx_amount_eur"]), "date": row["date"].isoformat(),
                    "where": merchant_name(row["merchant_id"]), "usual_eur": eur(u),
                    "times_usual": float(round(-row["tx_amount_eur"] / u, 1))}
        if kind == "bills":
            # A regular payment that went up, else a utility bill well above the usual range, else the next one due.
            outs = [x for x in self.commitments()["regular_payments"]
                    if x["direction"] == "out" and x["status"] == "active"]
            up = next((x for x in outs if (x.get("trend") or {}).get("direction") == "up"), None)
            if up:
                return {"kind": kind, "found": True, "type": "increase", **up}
            odd = next((x for x in outs if x.get("unusual_bill")), None)
            if odd:
                return {"kind": kind, "found": True, "type": "unusual_bill", **odd}
            due = [x for x in outs if x["days_to_next"] is not None]
            if due:
                return {"kind": kind, "found": True, "type": "upcoming", **min(due, key=lambda x: x["days_to_next"])}
            return {"kind": kind, "found": False}
        raise ValueError("kind must be salary, unusual or bills")

    # ---------- first home (data/mortgage_benchmark.json) ----------
    REASON_LABELS = {
        "insufficient_collateral": "Collateral: the bank values the home below its price, so a big loan often needs "
                                   "extra collateral (another flat or a guarantee)",
        "insufficient_income": "Income: enough steady monthly income to carry the loan",
        "insufficient_equity": "Own money (equity): savings that pay part of the price",
        "existing_debt": "Existing debts: other loans you are paying",
    }

    def _home_costs(self, price: float) -> dict:
        """Deposit (median equity share of approved applications) plus typical one-off costs at this price."""
        m = self.mortgage
        share = m["approved_applications"]["equity_share_pct"]["median"] / 100
        ex = m["expenses"]
        costs = {t: eur(price * ex[t]["median_share_of_price_pct"] / 100) if t == "transfer_tax"
                 else float(ex[t]["median_eur"]) for t in m["one_off_cost_types"]}
        equity = price * share
        return {"price_eur": eur(price), "equity_share_pct": eur(share * 100), "equity_eur": eur(equity),
                "loan_eur": eur(price - equity),
                "one_off_costs_eur": {f"{t}_eur": v for t, v in costs.items()},
                "total_needed_eur": eur(equity + sum(costs.values())),
                "not_counted": {"renovation_median_eur": float(ex["renovation"]["median_eur"]),
                                "renovation_applications": ex["renovation"]["applications"],
                                "maintenance_fee_median_eur_per_month": float(ex["maintenance_fee"]["median_eur"])}}

    def home_readiness(self, price: float | None = None) -> dict:
        """'Ready for my first home?': an educational estimate from the challenge's mortgage data. Never a loan offer."""
        m = self.mortgage
        if not m:
            raise ValueError("mortgage benchmark not available (run scripts/build_mortgage_benchmark.py)")
        if price is not None and not 10_000 <= price <= 5_000_000:
            raise ValueError("price must be between 10,000 and 5,000,000 EUR")
        sal = self._rec("salary")
        if not sal:
            return {"label": "Educational estimate from S-Pankki's challenge data. Not a loan offer.",
                    "not_enough_data": "No regular monthly salary in the last 6 full months, and the estimate is "
                                       "built on it. Say the loan range cannot be estimated yet; never guess an income.",
                    "your_money": {"balance_eur": self.balance()},
                    "asp": {"guide_topic": "asp", "note": "get_money_guide('asp') has the ASP rules."},
                    "source": m["source"]}
        income, ratio = sal.avg_amount, m["required_income_per_loan_eur"]
        appr = m["approved_applications"]
        loan = income / ratio
        prices = {k: eur(loan / (1 - appr["equity_share_pct"][p] / 100))
                  for k, p in (("with_small_deposit", "p25"), ("typical", "median"), ("with_big_deposit", "p75"))}
        need = self._home_costs(float(price) if price is not None else prices["typical"])
        saved = sum(g["saved"] for g in self.state.get("goals", {}).values())
        have = self.balance() + saved
        gap = max(0.0, need["total_needed_eur"] - have)
        need.update({"you_have_eur": eur(have), "still_to_save_eur": eur(gap),
                     "covered_pct": max(0, min(100, round(have / need["total_needed_eur"] * 100)))})
        port = self.portfolio()
        paces = [("Cut restaurants by 20%", self.simulate_cut("Restaurants", 20)["saving_per_month_eur"]),
                 ("Save 50 € a month", 50.0), ("Save 100 € a month", 100.0)]
        if port["monthly_plan_in_data"]:
            paces.append(("Put your monthly investing into the deposit instead",
                          port["monthly_plan_in_data"]["amount_eur"]))
        path = []
        for how, pace in paces:
            if pace <= 0:  # e.g. no restaurant spending to cut
                continue
            n = int(-(-gap // pace)) if gap else 0
            path.append({"how": how, "monthly_eur": eur(pace), "months": n, "years": round(n / 12, 1),
                         "reached_by": self._month_after(n)})
        out = {
            "label": "Educational estimate from S-Pankki's challenge data (200 mortgage applications, synthetic but "
                     "modelled on real S-Bank data). Not a loan offer and not a loan decision.",
            "your_money": {"monthly_salary_eur": income, "balance_eur": self.balance(), "saved_in_goals_eur": eur(saved),
                           "invested_so_far_eur": port["net_put_in_eur"],
                           "invested_note": "Not counted in what you have: investments can be sold for a deposit, "
                                            "but their value goes up and down."},
            "at_your_income": {
                "loan_eur": eur(loan),
                "rule": f"In the data every application needed a monthly income of {ratio} x the loan, "
                        f"so a loan of about {round(1 / ratio, 1)} x monthly income",
                "price_range_eur": prices,
                "approved_homes_price_eur": appr["property_price_eur"],
                "cheaper_than_most_approved_homes": bool(prices["typical"] < appr["property_price_eur"]["p25"]),
            },
            "deposit": need,
            "saving_path": path,
            "asp": {"guide_topic": "asp", "note": "ASP is the state-backed savings account for a first home; "
                                                  "get_money_guide('asp') has its rules and the official source."},
            "what_banks_look_at": [{"reason": r["reason"], "what": self.REASON_LABELS.get(r["reason"], r["reason"]),
                                    "rejections": r["count"], "share_of_rejections_pct": r["share_of_rejections_pct"]}
                                   for r in m["rejection_reasons"]],
            "collateral_fact": {
                "home_valued_as_collateral_pct_of_price": appr["collateral_value_share_of_price_pct"]["median"],
                "typical_loan_pct_of_price": appr["loan_to_value_pct"]["median"],
                "applications_asked_for_more_collateral_pct": m["additional_collateral_requested_pct"]},
            "data": {"applications": m["applications"], "approval_rate_pct": m["approval_rate_pct"],
                     "approved_applicants_age": appr["age"], "property_types": m["property_types"]},
            "suggested_goal": {"name": "First home deposit", "target_eur": need["total_needed_eur"]},
            "source": m["source"],
        }
        age = self.age
        if age and age < appr["age"]["min"]:
            out["age_note"] = (f"Approved applicants in the data were aged {appr['age']['min']}-{appr['age']['max']}; "
                               f"nobody aged {age} is in it, so compare loosely.")
        if price is not None:
            income_needed = need["loan_eur"] * ratio
            out["price_check"] = {
                "price_eur": eur(price), "loan_needed_eur": need["loan_eur"],
                "monthly_income_needed_eur": eur(income_needed), "your_salary_eur": income,
                "income_enough": bool(income >= income_needed),
                "income_short_by_eur": eur(max(0.0, income_needed - income)),
                "max_price_at_your_income_eur": prices["typical"]}
        return out

    # ---------- people like you (data/peer_benchmark.json, app/peers.py) ----------
    def new_life_budget(self, area: str | None = None, age: int | None = None, starting_out: bool = False) -> dict:
        """'How much does someone like me need a month here?': the typical month of S-Pankki customers of the same
        area and age (groups of at least 30 only), and where this person's own month sits in it."""
        from .peers import new_life_budget
        return new_life_budget(self, area, age, starting_out)

    peers = new_life_budget
