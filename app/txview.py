"""Transactions as S-mobiili shows them: grouped by day, a readable name for each place, a round icon tinted with its
basket, and everything the detail sheet needs (time, merchant type, basket and what it means, the receipt link).

Display layer only. The engine's rows, names and numbers are not changed: the dataset's anonymous merchant ids
(MRC-RES-0024) are shown by their kind of place, from the merchant code (5814 -> "Café or fast food"), and the id's
number stays as a quiet "#24" so two grocery stores still tell apart."""
from __future__ import annotations

from datetime import date, datetime

from .engine import BASKETS, CATEGORY_BASKET, Engine, eur

# Merchant code -> (English, Finnish) name of the kind of place, as a person would say it.
MCC_NAME = {
    "4111": ("Public transport", "Joukkoliikenne"), "4121": ("Taxi", "Taksi"),
    "4722": ("Travel agency", "Matkatoimisto"), "5200": ("Home supply store", "Rautakauppa"),
    "5411": ("Grocery store", "Ruokakauppa"), "5422": ("Butcher", "Lihakauppa"),
    "5451": ("Dairy shop", "Maitokauppa"), "5499": ("Food shop", "Lähikauppa"),
    "5541": ("Fuel station", "Huoltoasema"), "5542": ("Fuel station", "Huoltoasema"),
    "5712": ("Furniture store", "Huonekaluliike"), "5732": ("Electronics store", "Elektroniikkaliike"),
    "5812": ("Restaurant", "Ravintola"), "5813": ("Bar or pub", "Baari"),
    "5814": ("Café or fast food", "Kahvila"), "5912": ("Pharmacy", "Apteekki"),
    "5941": ("Sports shop", "Urheiluliike"), "5942": ("Bookshop", "Kirjakauppa"),
    "5945": ("Toy shop", "Lelukauppa"), "5999": ("Shop", "Kauppa"), "7011": ("Hotel", "Hotelli"),
    "7523": ("Parking", "Pysäköinti"), "7832": ("Cinema", "Elokuvateatteri"),
    "7922": ("Event tickets", "Tapahtumaliput"), "7991": ("Attraction", "Nähtävyys"),
    "7996": ("Amusement park", "Huvipuisto"), "7999": ("Recreation", "Vapaa-aika"),
    "8011": ("Doctor", "Lääkäri"), "8021": ("Dentist", "Hammaslääkäri"), "8099": ("Health service", "Terveyspalvelu"),
}
# When a row has no merchant code: the merchant id's kind (MRC-RES-...).
KIND_NAME = {"RES": ("Restaurant", "Ravintola"), "GRO": ("Grocery store", "Ruokakauppa"),
             "ENT": ("Entertainment", "Viihde"), "TRN": ("Transport", "Liikenne"),
             "HMT": ("Home & tech store", "Koti ja tekniikka"), "HEA": ("Health", "Terveys"),
             "LIF": ("Shop", "Kauppa"), "TRV": ("Travel", "Matkat")}
CATEGORY_NAME = {"Restaurants": ("Restaurants", "Ravintolat"), "Transportation": ("Transport", "Liikenne"),
                 "Groceries": ("Groceries", "Ruokaostokset"), "Utilities": ("Utility bills", "Asumisen laskut"),
                 "Entertainment": ("Entertainment", "Viihde"), "Health": ("Health", "Terveys"),
                 "Housing": ("Rent", "Vuokra"), "Salary": ("Salary", "Palkka"),
                 "Home & Tech": ("Home & tech", "Koti ja tekniikka"), "Invest Deposit": ("Monthly investing", "Kuukausisijoitus"),
                 "Lifestyle": ("Shopping", "Ostokset"), "Travel": ("Travel", "Matkat"),
                 "Other Income": ("Other income", "Muut tulot"), "Invest Withdraw": ("Investment withdrawal", "Sijoituksen nosto"),
                 "Savings": ("Savings", "Säästöt"), "Other": ("Other payment", "Muu maksu"),
                 "Credit card": ("Credit card", "Luottokortti")}
NOTE_FI = {"Rent": "Vuokra", "Monthly investment": "Kuukausisijoitus", "Utility bills": "Asumisen laskut",
           "Monthly investing": "Kuukausisijoitus", "Salary": "Palkka"}
BASKET_TEXT = {  # (label, one line on what the basket means) per language; the labels are the app's own
    "regular": {"en": (BASKETS["regular"]["label"], "Needed every month: rent, bills, groceries and getting around."),
                "fi": ("Laskut ja perusmenot", "Joka kuukausi tarvittavat: vuokra, laskut, ruoka ja liikkuminen.")},
    "irregular": {"en": (BASKETS["irregular"]["label"], "Needed now and then, but you can't plan when: a doctor, a broken laptop."),
                  "fi": ("Satunnaiset menot", "Tarpeellisia, mutta et tiedä milloin: lääkäri, rikkoutunut kone.")},
    "everyday": {"en": (BASKETS["everyday"]["label"], "Small choices that add up. The easiest place to save."),
                 "fi": ("Valinnaiset menot", "Pienet valinnat, jotka kertyvät. Tästä on helpointa säästää.")},
}
# A row's sub-line is narrow at 360 px: the Finnish basket in one word there (the sheet keeps the full name).
BASKET_SHORT_FI = {"regular": "Perusmenot", "irregular": "Satunnaiset", "everyday": "Valinnaiset"}
# Category -> icon drawn in smobiili_tx.html; a merchant code can be more exact (a café, a pharmacy, fuel).
CATEGORY_ICON = {"Groceries": "cart", "Restaurants": "food", "Transportation": "bus", "Health": "health",
                 "Housing": "house", "Utilities": "bolt", "Entertainment": "ticket", "Lifestyle": "bag",
                 "Travel": "plane", "Home & Tech": "device", "Salary": "in", "Other Income": "in",
                 "Invest Withdraw": "in", "Invest Deposit": "growth", "Savings": "pocket"}
MCC_ICON = {"5814": "coffee", "5813": "coffee", "5912": "health", "8011": "health", "8021": "health",
            "5541": "fuel", "5542": "fuel", "4121": "car", "7523": "car", "7832": "ticket", "7922": "ticket",
            "7011": "plane", "5732": "device"}
WEEKDAY = {"en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), "fi": ("ma", "ti", "ke", "to", "pe", "la", "su")}


def _lang(lang: str) -> str:
    return "en" if lang == "en" else "fi"


def money_words(lang: str):
    return (lambda en, fi: en) if lang == "en" else (lambda en, fi: fi)


def day_label(d: date, today: date, lang: str) -> str:
    """Today / Yesterday / Tue 29.9. (ti 29.9. in Finnish); another year adds the year."""
    w = money_words(lang)
    if d == today:
        return w("Today", "Tänään")
    if (today - d).days == 1:
        return w("Yesterday", "Eilen")
    s = f"{WEEKDAY[_lang(lang)][d.weekday()]} {d.day}.{d.month}."
    return s + (str(d.year) if d.year != today.year else "")


def long_date(d: date, lang: str) -> str:
    wd = {"en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
          "fi": ("maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai")}[_lang(lang)]
    return f"{wd[d.weekday()].capitalize()} {d.day}.{d.month}.{d.year}"


def _time(ts) -> str | None:
    """'04OCT24:14:47:54' -> '14:47'."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, "%d%b%y:%H:%M:%S").strftime("%H:%M")
    except ValueError:
        return None


def _code(v) -> str | None:
    return v if isinstance(v, str) and v else None


def place(merchant_id, mcc, category: str, lang: str) -> tuple[str, str | None]:
    """(name, quiet number): ('Café or fast food', '#24'), or the category's own name for a row with no merchant."""
    i = 0 if lang == "en" else 1
    mid = _code(merchant_id)
    num = None
    kind = None
    if mid and mid.startswith("MRC-") and mid.count("-") == 2:
        _, kind, n = mid.split("-")
        num = f"#{int(n)}" if n.isdigit() else None
    code = _code(mcc)
    if code in MCC_NAME:
        return MCC_NAME[code][i], num
    if kind in KIND_NAME:
        return KIND_NAME[kind][i], num
    return CATEGORY_NAME.get(category, (category, category))[i], None


def rows(e: Engine, lang: str, n: int | None = 40, since: date | None = None, notes: bool = True) -> list[dict]:
    """The latest transactions, newest first, each ready to draw (row and detail sheet). id = the engine's row."""
    lg = _lang(lang)
    w = money_words(lang)
    t = e.tx
    has_ts = "tx_timestamp" in t.columns
    if since is not None:
        t = t[t["date"] >= since]
    t = t.iloc[::-1] if n is None else t.tail(n).iloc[::-1]
    moves: dict = {}  # moves made in the app (a payment, a saving) carry their recipient in the action's note
    if notes:
        for a in e.state.get("actions", []):
            moves.setdefault((a["date"], eur(a["amount"])), []).append(a.get("note") or "")
    out = []
    for i, r in t.iterrows():
        d, cat, amt = r["date"], r["category"], eur(r["tx_amount_eur"])
        name, num = place(r["merchant_id"], r["mcc_code"], cat, lang)
        if not _code(r["merchant_id"]):
            left = moves.get((d.isoformat(), amt)) or []
            note = left.pop() if left else ""
            if note.startswith("Savings pocket"):
                name = w("To your savings account", "Siirto säästötilille")
            elif note:
                name = note if lang == "en" else NOTE_FI.get(note, note)
        k = CATEGORY_BASKET.get(cat)
        code = _code(r["mcc_code"])
        out.append({
            "id": int(i), "iso": d.isoformat(), "date": d, "time": _time(r["tx_timestamp"]) if has_ts else None,
            "name": name, "name_en": place(r["merchant_id"], r["mcc_code"], cat, "en")[0], "num": num, "amount_eur": amt, "income": amt > 0,
            "category": CATEGORY_NAME.get(cat, (cat, cat))[0 if lg == "en" else 1], "category_en": cat,
            "kind": MCC_NAME[code][0 if lg == "en" else 1] if code in MCC_NAME else None,
            "tone": {"regular": "b1", "irregular": "b2", "everyday": "b3"}.get(k, "in" if amt > 0 else "none"),
            "basket": k, "basket_label": BASKET_TEXT[k][lg][0] if k else None,
            "basket_line": BASKET_TEXT[k][lg][1] if k else None,
            "basket_short": (BASKET_TEXT[k]["en"][0] if lg == "en" else BASKET_SHORT_FI[k]) if k else None,
            "icon": MCC_ICON.get(code) or CATEGORY_ICON.get(cat) or ("in" if amt > 0 else "out"),
            "receipt": cat == "Groceries" and amt < 0 and _code(r["merchant_id"]) is not None,
            "card": r["transaction_type"] == "purchase",
            "long_date": long_date(d, lang),
        })
    return out


def by_day(items: list[dict], today: date, lang: str, keep: set | None = None) -> list[dict]:
    """Rows -> [{label, iso, total_eur, n_out, hidden, rows}], newest day first. total_eur is what was spent that day
    (money out only, negative) over the rows shown; n_out counts them (the header shows a total from two up).
    keep: the row ids a filter shows; the others are drawn hidden, so the list can widen again without a reload."""
    days: list[dict] = []
    for x in items:
        if not days or days[-1]["iso"] != x["iso"]:
            days.append({"iso": x["iso"], "label": day_label(x["date"], today, lang), "rows": [], "total_eur": 0.0,
                         "n_out": 0, "hidden": True})
        d = days[-1]
        x = {**x, "hidden": keep is not None and x["id"] not in keep}
        d["rows"].append(x)
        if not x["hidden"]:
            d["hidden"] = False
            if not x["income"]:
                d["n_out"] += 1
                d["total_eur"] = eur(d["total_eur"] + x["amount_eur"])
    return days


def haystack(x: dict) -> str:
    """What a search looks in: the shown name, its number, the kind of place, the category, the basket (either
    language) and the amount (47.73 and 47,73)."""
    k = x["basket"]
    a = f"{abs(x['amount_eur']):.2f}"
    return " ".join(filter(None, [x["name"], x.get("name_en"), x["num"], x["kind"], x["category"], x["category_en"],
                                 *((BASKET_TEXT[k][lg][0] for lg in ("en", "fi")) if k else ()),
                                 a, a.replace(".", ",")])).lower()


def matches(x: dict, q: str) -> bool:
    q = q.strip().lower()
    return all(part in haystack(x) for part in q.split())
