"""'Budget for a new life / how people like you live here': what a typical month costs for S-Pankki customers of
the same area and age, from data/peer_benchmark.json (built by scripts/build_peer_benchmark.py from the challenge
dataset).

Privacy: the benchmark holds only p25 / median / p75 over groups of at least MIN_GROUP customers. When the person's
own group is smaller, a broader group is used (narrow age band -> wider band; area -> the whole capital region ->
everyone) and the reason is returned. Never a single customer's data.

Comparison words are neutral keys ("in line", "a bit above", "below"); the tone is the prompt's job.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_PATH = ROOT / "data" / "peer_benchmark.json"
AREAS_PATH = ROOT / "data" / "persona_areas.json"
MIN_GROUP = 30
WINDOW_MONTHS = 12

# First two digits of a Finnish postal code in this dataset (all 5,100 codes start with 0).
AREAS = {
    "00": {"name": "Helsinki", "covers": "the city of Helsinki (postal codes 00xxx)"},
    "01": {"name": "Vantaa area", "covers": "Vantaa, with nearby parts of Sipoo and Nurmijärvi (postal codes 01xxx)"},
    "02": {"name": "Espoo area", "covers": "Espoo and Kauniainen, with nearby parts of Kirkkonummi and Siuntio "
                                           "(postal codes 02xxx)"},
}
REGION = "capital region"  # all three areas together = the whole dataset
CITY_WORDS = {"helsinki": "00", "helsingfors": "00", "stadi": "00", "vantaa": "01", "vanda": "01", "tikkurila": "01",
              "espoo": "02", "esbo": "02", "kauniainen": "02", "grankulla": "02", "tapiola": "02", "leppävaara": "02",
              "kirkkonummi": "02"}
AGE_BANDS = [(18, 24), (25, 29), (30, 39), (40, 49), (50, 64), (65, 200)]
WIDE_BANDS = [(18, 29), (30, 49), (50, 200)]
STARTING_OUT = (18, 29)  # "someone starting out": aged 18-29 with a regular salary
SALARY_MONTHS = 10  # a regular salary: salary credited in at least 10 of the 12 months

CATEGORIES = ["Housing", "Utilities", "Groceries", "Transportation", "Health", "Home & Tech", "Travel",
              "Restaurants", "Entertainment", "Lifestyle"]
MCC_METRICS = {"taxi": ["4121"], "fast_food": ["5814"]}  # taxis and ride-hailing; fast food and cafés
BASKET_KEYS = ["regular", "irregular", "everyday"]
METRICS = ["income", "salary", "total"] + BASKET_KEYS + CATEGORIES + list(MCC_METRICS)
METRIC_LABELS = {"income": "Net income (salary and other income)", "salary": "Net salary",
                 "total": "Spending in total", "regular": "Mandatory & regular", "irregular": "Mandatory & irregular",
                 "everyday": "Slips through your fingers", "taxi": "Taxis and ride-hailing",
                 "fast_food": "Fast food and cafés"} | {c: c for c in CATEGORIES}
# What the card and the tool show, in this order: the three baskets, then the main categories.
MAIN_CATEGORIES = ["Housing", "Groceries", "Transportation", "Utilities", "Restaurants", "Entertainment", "Lifestyle"]


def band_label(lo: int, hi: int) -> str:
    return f"{lo}+" if hi >= 200 else f"{lo}-{hi}"


def band_of(age: int, bands=AGE_BANDS) -> str | None:
    return next((band_label(lo, hi) for lo, hi in bands if lo <= age <= hi), None)


def group_key(area: str | None = None, band: str | None = None, starting_out: bool = False) -> str:
    parts = (["starting_out"] if starting_out else []) + ([f"area={area}"] if area else []) + \
            ([f"age={band}"] if band else [])
    return "|".join(parts) or "all"


def area_code(area) -> str | None:
    """'Espoo' / 'espoossa' / '02' / '02780' -> '02'. None for an empty value. Raises ValueError for a place
    outside the dataset (it only has the capital region)."""
    if area is None or not str(area).strip():
        return None
    a = str(area).strip().lower()
    if re.fullmatch(r"\d{2,5}", a):
        code = a.zfill(5)[:2] if len(a) == 5 else a[:2]
        if code in AREAS:
            return code
    for word, code in CITY_WORDS.items():
        if word in a or a.startswith(word[:5]):
            return code
    for code, info in AREAS.items():
        if a in info["name"].lower():
            return code
    raise ValueError(f"no S-Pankki data for {area!r}: the dataset covers only the {REGION} "
                     f"({', '.join(i['name'] for i in AREAS.values())})")


def area_name(code: str | None) -> str:
    return AREAS[code]["name"] if code in AREAS else f"the whole {REGION}"


def describe(g: dict) -> str:
    """Plain words for a group: 'customers aged 25-29 in the Espoo area'."""
    who = "customers starting out (aged 18-29, with a regular salary)" if g.get("starting_out") else "customers"
    if g.get("age_band") and not g.get("starting_out"):
        who += f" aged {g['age_band']}"
    where = f" in {'the ' if g.get('area') in ('01', '02') else ''}{area_name(g['area'])}" if g.get("area") else \
        f" across the {REGION}"
    return who + where


def load(path: Path = BENCH_PATH) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_areas(path: Path = AREAS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("customers", {}) if path.exists() else {}


def candidates(area: str | None, age: int | None, starting_out: bool = False) -> list[tuple[str, str | None]]:
    """Groups to try, most specific first, each with the reason to move past the one before it."""
    out: list[tuple[str, str | None]] = []
    if starting_out:
        if area:
            out.append((group_key(area, starting_out=True), None))
        out.append((group_key(starting_out=True), "area"))
    band = band_of(age) if age is not None else None
    wide = band_of(age, WIDE_BANDS) if age is not None else None
    if area and band:
        out.append((group_key(area, band), None))
    if area and wide and wide != band:
        out.append((group_key(area, wide), "age"))
    if band:
        out.append((group_key(None, band), "area"))
    if wide and wide != band:
        out.append((group_key(None, wide), "age"))
    if area:
        out.append((group_key(area), "age"))
    out.append(("all", "age"))
    return out


def pick_group(bench: dict, area: str | None, age: int | None, starting_out: bool = False) -> dict:
    """The most specific group with at least MIN_GROUP customers, and why a broader one was used (or None)."""
    groups = bench["groups"]
    tried = []
    for key, _ in candidates(area, age, starting_out):
        g = groups.get(key)
        if g and g["n"] >= MIN_GROUP:
            if tried:
                wanted = tried[0]
                reason = (f"Fewer than {MIN_GROUP} S-Pankki customers are {describe(wanted)}, so this uses "
                          f"{describe(g)} instead.")
            else:
                reason = None
            return {"key": key, **g, "fallback_reason": reason}
        tried.append(_parse(key))
    raise ValueError("peer benchmark has no group with enough customers")


def _parse(key: str) -> dict:
    g = {"area": None, "age_band": None, "starting_out": False}
    for p in key.split("|"):
        if p == "starting_out":
            g["starting_out"] = True
        elif p.startswith("area="):
            g["area"] = p[5:]
        elif p.startswith("age="):
            g["age_band"] = p[4:]
    return g


SLACK = 0.05  # a few euros past the edge of the middle half still reads as "in line"


def verdict(mine: float, q: dict) -> str:
    """Neutral key for where the person's amount sits against the group's middle half (p25-p75)."""
    if mine < q["p25"] * (1 - SLACK):
        return "below"
    if mine > q["p75"] * (1 + SLACK):
        return "a bit above"
    return "in line"


def my_month(engine) -> dict:
    """The person's own monthly averages over the same 12 full months, computed the way the benchmark is: net
    amounts per category (refunds count), divided by the months the history covers."""
    from .engine import BASKETS, eur  # local: engine.py calls into this module
    d0, d1 = engine._full_months(WINDOW_MONTHS)
    n = engine._covered(d0, WINDOW_MONTHS) or 1
    s = engine._slice(d0, d1)
    out = {c: eur(-s[s["category"] == c]["tx_amount_eur"].sum() / n) for c in CATEGORIES}
    for key, codes in MCC_METRICS.items():
        out[key] = eur(-s[s["mcc_code"].isin(codes)]["tx_amount_eur"].sum() / n)
    for b in BASKET_KEYS:
        out[b] = eur(sum(out[c] for c in BASKETS[b]["categories"]))
    out["total"] = eur(sum(out[c] for c in CATEGORIES))
    out["income"] = eur(s[s["transaction_type"].isin(["salary", "other_income"])]["tx_amount_eur"].sum() / n)
    out["salary"] = eur(s[s["transaction_type"] == "salary"]["tx_amount_eur"].sum() / n)
    return out


def new_life_budget(engine, area=None, age: int | None = None, starting_out: bool = False,
                    bench: dict | None = None) -> dict:
    """What a typical month costs for people like this person (or in another area / at another age)."""
    bench = bench or load()
    if not bench:
        raise ValueError("peer benchmark not available (run scripts/build_peer_benchmark.py)")
    own = load_areas().get(engine.customer_id, {})
    own_area = own.get("area")
    asked_area = area_code(area)  # ValueError for a city outside the dataset
    what_if = bool((asked_area and asked_area != own_area) or (age is not None and age != engine.age))
    a = asked_area or own_area
    yrs = int(age) if age is not None else engine.age
    if yrs is not None and not 18 <= yrs <= 110:
        raise ValueError("age must be between 18 and 110")
    g = pick_group(bench, a, yrs, starting_out)

    def row(k):
        q = g["metrics"][k]
        return {"key": k, "label": METRIC_LABELS[k], "p25_eur": q["p25"], "median_eur": q["median"],
                "p75_eur": q["p75"]}

    typical = {"total": row("total"), "income": row("income"),
               "baskets": [row(b) for b in BASKET_KEYS],
               "categories": [row(c) for c in MAIN_CATEGORIES],
               "also": [row(c) for c in ("Health", "Home & Tech", "Travel", "taxi", "fast_food")]}
    heaviest = max(CATEGORIES, key=lambda c: g["metrics"][c]["median"])
    out = {
        "question": "What a typical month costs for S-Pankki customers like you",
        "group": {"who": describe(g), "area": area_name(g.get("area")) if g.get("area") else None,
                  "age_band": g.get("age_band"), "starting_out": g.get("starting_out", False), "customers": g["n"]},
        "asked_for": {"area": area_name(a) if a else None, "age": yrs, "starting_out": starting_out,
                      "what_if": what_if},
        "fallback_reason": g["fallback_reason"],
        "typical_month": typical,
        "heaviest_cost_for_most": {"category": heaviest, "median_eur": g["metrics"][heaviest]["median"],
                                   "share_of_typical_spending_pct":
                                       round(g["metrics"][heaviest]["median"] / g["metrics"]["total"]["median"] * 100)
                                       if g["metrics"]["total"]["median"] else None},
        "privacy": f"Averages of at least {MIN_GROUP} S-Pankki customers (this group: {g['n']}); never one "
                   f"person's data. p25-p75 is the middle half of the group.",
        "period": bench["period"],
        "source": bench["source"],
    }
    if not what_if:
        mine = my_month(engine)
        rows = []
        for k in ["total"] + BASKET_KEYS + MAIN_CATEGORIES:
            q = g["metrics"][k]
            rows.append({"key": k, "label": METRIC_LABELS[k], "you_per_month_eur": mine[k],
                         "typical_median_eur": q["median"], "typical_range_eur": [q["p25"], q["p75"]],
                         "where_you_are": verdict(mine[k], q)})
        out["you"] = {"income_per_month_eur": mine["income"], "comparison": rows,
                      "in_line": [r["label"] for r in rows if r["where_you_are"] == "in line"],
                      "a_bit_above": [r["label"] for r in rows if r["where_you_are"] == "a bit above"],
                      "below": [r["label"] for r in rows if r["where_you_are"] == "below"],
                      "note": "Your own monthly averages over the same 12 full months."}
    else:
        out["you"] = None
        out["note"] = ("A what-if for another area or age: there is no personal comparison, only what a typical "
                       "month costs there.")
    return out


CARD_ROWS = ["total", "regular", "irregular", "everyday", "Housing", "Groceries", "Restaurants"]


def card(result: dict) -> dict:
    """The /app card: one horizontal range bar per row (p25-p75 band, median tick, the person's dot), each on its own
    0..max scale in a 300-wide SVG so a small category stays readable next to rent."""
    rows = []
    for r in (result.get("you") or {}).get("comparison", []):
        if r["key"] not in CARD_ROWS:
            continue
        lo, hi = r["typical_range_eur"]
        top = max(hi * 1.25, r["you_per_month_eur"] * 1.1, 1.0)

        def x(v, top=top):
            return round(max(0.0, min(v, top)) / top * 288 + 6, 1)
        rows.append({**r, "x0": x(lo), "w": round(x(hi) - x(lo), 1), "xm": x(r["typical_median_eur"]),
                     "xy": x(r["you_per_month_eur"]), "main": r["key"] == "total"})
    g = result["group"]
    band = "starting out" if g.get("starting_out") else g.get("age_band") or "all ages"
    p = result["period"]
    when = " to ".join(date.fromisoformat(m + "-01").strftime("%b %Y") for m in (p["from"], p["to"]))
    return {"title": f"{g['area'] if g.get('area') else REGION.capitalize()} · {band}", "who": g["who"],
            "n": g["customers"], "rows": rows, "fallback_reason": result.get("fallback_reason"),
            "period": when, "min_group": MIN_GROUP}
