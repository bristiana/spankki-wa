"""Kassakuitti, the digital receipt in S-mobiili's S-Etukortti section.

The challenge data has the purchase (date, amount, an anonymous grocery merchant) but no basket, so the item lines are
made up: deterministic per transaction, and they always add up to the real amount to the cent. The page says demo.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date

import pandas as pd

# The stores a receipt can come from (one co-op, so the header lines stay consistent). Demo: the dataset's merchants
# are anonymous ids, each one is mapped to one of these.
STORES = [
    {"name": "Prisma Mylly", "header": "MYLLYN PRISMA", "phone": "p.0107643000"},
    {"name": "Prisma Itäharju", "header": "ITÄHARJUN PRISMA", "phone": "p.0107643100"},
    {"name": "Prisma Länsikeskus", "header": "LÄNSIKESKUKSEN PRISMA", "phone": "p.0107643200"},
    {"name": "S-market Kupittaa", "header": "S-MARKET KUPITTAA", "phone": "p.0107643300"},
]
COOP = "Turun Osuuskauppa, Y-0142122-9"

# Fixed-price items (receipt name, cents).
ITEMS = [
    ("VÄHÄSUOL.VIHR.OLIIVI", 95), ("TOMAATTIMURSKA 2-PACK", 109), ("PARMIGIANO REGGIANO STICK", 349),
    ("KURKKU SNACK LUOMU", 189), ("PALVARIN LIHAPYÖRYKÄT", 259), ("VEHNÄPAAHTOLEIPÄ", 98), ("TACO-SALAATTI", 198),
    ("MOZZARELLA LUOMU", 169), ("LEGO EXTRA", 995), ("LÄHDEVESI 5.1L KAN", 155), ("GO ON SOIJALAM.VANILJA", 209),
    ("KIVIVESI VADELMA 1,5L", 77), ("MAITO 1L RASVATON", 105), ("KAURAJUOMA 1L", 159), ("RUISLEIPÄ 6KPL", 189),
    ("JUUSTO EMMENTAL 400G", 499), ("KANANMUNAT 10KPL", 279), ("JAUHELIHA 400G", 449), ("BROILERIN FILEE 500G", 589),
    ("LOHIFILEE 300G", 699), ("KAHVI 500G", 649), ("PASTA FUSILLI 500G", 119), ("RIISI 1KG", 189),
    ("JOGURTTI MANSIKKA", 89), ("RAEJUUSTO 200G", 135), ("VOI 500G", 469), ("APPELSIINITUORE 1L", 249),
    ("KAURAHIUTALE 1KG", 145), ("PAKASTEHERNE 500G", 159), ("TOMAATTI KIRSIKKA 250G", 199), ("PIZZA KINKKU", 329),
    ("SUKLAALEVY 200G", 299), ("WC-PAPERI 8RL", 549), ("ASTIANPESUAINE", 389), ("BANAANIJOGURTTI 1KG", 219),
]
# Priced per kilo (receipt name, cents per kg): the last line, its weight chosen so the total matches to the cent.
WEIGHED = [("KESÄKURPITSA VIHREÄ", 279), ("BANAANI", 199), ("PERUNA PESTY", 129), ("OMENA PINK LADY", 449),
           ("PORKKANA", 149), ("SIPULI KELTAINEN", 159), ("PAPRIKA PUNAINEN", 599), ("KURKKU", 349)]
BAG = ("MUOVIKASSI 40L HARMAA", 19)


def _cents(x: float) -> int:
    return int(round(abs(x) * 100))


def _eur(c: int) -> str:
    """Receipt style: 2.79, a point, no currency."""
    return f"{c // 100}.{c % 100:02d}"


def store_for(merchant_id) -> dict:
    h = int(hashlib.sha1(str(merchant_id).encode()).hexdigest(), 16) if isinstance(merchant_id, str) else 0
    return STORES[h % len(STORES)]


def grocery_rows(e) -> pd.DataFrame:
    """The persona's real grocery purchases up to today, newest first (index = row in the engine's data)."""
    b = e.base
    g = b[(b["category"] == "Groceries") & (b["tx_amount_eur"] < 0) & (b["date"] <= e.today)]
    return g.iloc[::-1]


def receipt_list(e, n: int = 12) -> list[dict]:
    return [{"id": int(i), "date": r["date"], "store": store_for(r["merchant_id"])["name"],
             "total_eur": round(abs(float(r["tx_amount_eur"])), 2)} for i, r in grocery_rows(e).head(n).iterrows()]


def _weighed_line(rng: random.Random, cents: int) -> dict:
    """One weighed item costing exactly `cents`: a price per kilo under 10 € steps the price by under a cent per
    gram, so some weight always rounds to the amount."""
    options = [w for w in WEIGHED if 0.15 <= cents / w[1] <= 2.5] or [min(WEIGHED, key=lambda w: abs(cents / w[1] - 1))]
    name, per_kg = rng.choice(options)
    grams = round(cents * 1000 / per_kg)
    for g in (grams, grams - 1, grams + 1, grams - 2, grams + 2):
        if g > 0 and round(g * per_kg / 1000) == cents:
            return {"name": name, "cents": cents, "sub": f"{g / 1000:.3f} KG  {_eur(per_kg)} EUR/KG"}
    return {"name": name, "cents": cents, "sub": None}  # not reached for per_kg < 1000; keeps the sum exact anyway


def build(e, row_id: int, customer: str = "") -> dict | None:
    """The receipt for one grocery purchase, or None when that row is not one. Lines sum to the real amount."""
    if row_id not in grocery_rows(e).index:
        return None
    r = e.base.loc[row_id]
    total = _cents(r["tx_amount_eur"])
    seed = hashlib.sha1(f"{customer or e.customer_id}|{row_id}|{r['date']}|{total}".encode()).hexdigest()
    rng = random.Random(int(seed, 16))
    lines: list[dict] = []
    left = total
    bags = rng.choice((1, 2, 2, 3))
    bag = None
    if left - bags * BAG[1] >= 150:
        bag = {"name": BAG[0], "cents": bags * BAG[1], "sub": f"{bags} KPL  {_eur(BAG[1])} EUR/KPL" if bags > 1 else None}
        left -= bags * BAG[1]
    weighed_target = rng.randint(120, 600)  # what the last, weighed line should roughly cost
    qty: dict[str, int] = {}  # a product bought again is one line with a count, as on a real receipt
    price = dict(ITEMS)
    while left > weighed_target + 60:
        fits = [it for it in ITEMS if it[1] <= left - weighed_target]
        if not fits:
            break
        fresh = [it for it in fits if it[0] not in qty]
        name, cents = rng.choice(fresh if fresh and rng.random() < .9 else fits)
        qty[name] = qty.get(name, 0) + 1
        left -= cents
    lines = [{"name": n, "cents": k * price[n], "sub": f"{k} KPL  {_eur(price[n])} EUR/KPL" if k > 1 else None}
             for n, k in qty.items()]
    if left > 0:
        lines.append(_weighed_line(rng, left))
    rng.shuffle(lines)
    lines += [bag] if bag else []
    assert sum(x["cents"] for x in lines) == total
    d: date = r["date"]
    hh, mm = rng.randint(9, 20), rng.randint(0, 59)
    store = store_for(r["merchant_id"])
    return {"id": int(row_id), "store": store, "coop": COOP, "date": d, "total_eur": total / 100,
            "time": f"{hh:02d}:{mm:02d}", "code": f"{rng.randint(1000, 9999)} {rng.randint(1, 30):03d} {rng.randint(100000, 999999)}",
            "lines": [{**x, "price": _eur(x["cents"])} for x in lines], "total": _eur(total),
            "count": len(lines)}
