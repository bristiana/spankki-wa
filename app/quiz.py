"""The basket game: 5 of your own payments, you pick the basket. No AI, numbers from the engine.

The challenge's teaching model: every euro lands in one of three baskets (engine.BASKETS). The game picks five of
the person's recent payments, spread across the baskets, asks which basket each one is in, explains the answer with
their own numbers, and ends with their real split for last month and what -20% on the third basket frees in a year.
Voice: a right answer gets "Right.", a wrong one a kind "Easy to mix up" and the same explanation, never a grade on the person.
"stop" pauses; any other question pauses too, the coach answers, and *game* picks it up again.
The payments are ones a 20-year-old sorts at a glance (rent, a supermarket, a dentist, a café, cinema tickets),
named by the kind of place (SORTABLE), not "Shopping payment" or an anonymised "Restaurant #24"; a basket with none
of those in the history falls back to any of its payments. The web chat plays it in Finnish too (lang "fi").
State: engine.state["quiz"][user] = {"game", "round", "picks", "right", "paused", "note", "done", "lang"}.
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta

from .engine import BASKETS, CATEGORY_BASKET, MCC_LABELS, SPEND_CATEGORIES, Engine, merchant_name
from .onboarding import Turn, _money

ROUNDS = 5
QUOTA = {"regular": 2, "irregular": 1, "everyday": 2}  # filled from the other baskets when one has too few kinds
WINDOW_DAYS = 90
IRREGULAR_WINDOW_DAYS = 365  # one-offs are rare: look back a year before giving up on the middle basket
AMBIGUOUS_MCC = {"4121", "7523"}  # taxis and parking: the engine files them as transport (regular), people may not
CUT_PCT = 20
ANSWER = {k: b["label"] for k, b in BASKETS.items()}  # the button titles: Bills & basics, Occasional costs, Fun & extras
ANSWER_FI = {"regular": "Laskut ja perusmenot", "irregular": "Satunnaiset menot", "everyday": "Valinnaiset menot"}
# Payments anyone can place in a basket at a glance, by (category, merchant code or None), in the order a first game
# picks them: (English, Finnish), every name used once. Left out on purpose: travel, furniture, electronics, taxis and
# petrol, which people file under different baskets, and "other shops", which says nothing.
SORTABLE = {
    "regular": {("Housing", None): ("Rent", "Vuokra"),
                ("Groceries", "5411"): ("Supermarket", "Ruokakauppa"),
                ("Transportation", "4111"): ("Bus or metro ticket", "Bussi- tai metrolippu"),
                ("Utilities", None): ("Electricity and water bill", "Sähkö- ja vesilasku"),
                ("Groceries", "5499"): ("Food shop", "Lähikauppa")},
    "irregular": {("Health", "8021"): ("Dentist", "Hammaslääkäri"),
                  ("Health", "8011"): ("Doctor's visit", "Lääkärikäynti"),
                  ("Health", "5912"): ("Pharmacy", "Apteekki")},
    "everyday": {("Restaurants", "5814"): ("Café or fast food", "Kahvila tai pikaruoka"),
                 ("Entertainment", "7832"): ("Cinema tickets", "Elokuvaliput"),
                 ("Entertainment", "7922"): ("Concert or event tickets", "Keikka- tai tapahtumaliput"),
                 ("Restaurants", "5812"): ("Restaurant", "Ravintola"),
                 ("Lifestyle", "5651"): ("Clothes shop", "Vaatekauppa"),
                 ("Lifestyle", "5941"): ("Sports shop", "Urheilukauppa"),
                 ("Restaurants", "5813"): ("Bar", "Baari"),
                 ("Entertainment", "7996"): ("Amusement park", "Huvipuisto"),
                 ("Lifestyle", "5945"): ("Toy and game shop", "Lelu- ja pelikauppa")},
}
DAY_FI = ("ma", "ti", "ke", "to", "pe", "la", "su")
MONTH_IN_FI = ("tammikuussa", "helmikuussa", "maaliskuussa", "huhtikuussa", "toukokuussa", "kesäkuussa", "heinäkuussa",
               "elokuussa", "syyskuussa", "lokakuussa", "marraskuussa", "joulukuussa")
KIND = {"Housing": "Housing", "Utilities": "Utility bills", "Transportation": "Transport", "Health": "Health",
        "Home & Tech": "Home & tech", "Travel": "Travel", "Groceries": "Groceries", "Restaurants": "Eating out",
        "Lifestyle": "Shopping", "Entertainment": "Entertainment"}
NAME = {"Housing": "Housing payment", "Utilities": "Utility bill"}  # rows with no merchant
WHY = {
    "regular": "{kind}: needed, and it comes back on a schedule, so you can plan for it. {amt} of yours {period}.",
    "irregular": "{kind}: needed, but you can't know when, so a small buffer helps. {amt} of yours {period}.",
    "everyday": "{kind}: your own choice, small things that add up. {amt} of yours {period}.",
}
WHY_FI = {
    "regular": "{kind}: välttämätön, ja se toistuu aikataulussa, joten sen voi suunnitella. Sinulta meni tähän {amt} {period}.",
    "irregular": "{kind}: välttämätön, mutta et tiedä milloin, joten pieni puskuri auttaa. Sinulta meni tähän {amt} {period}.",
    "everyday": "{kind}: oma valintasi, pienet summat kertyvät. Sinulta meni tähän {amt} {period}.",
}
PERIOD_FI = {"last month": "viime kuussa", "over the last 3 months": "viimeisen 3 kuukauden aikana"}

START = re.compile(r"^\W*(quiz|game|basket game|the basket game|play|play the basket game|play the game|"
                   r"play a game|start the game|resume the game|continue the game|peli|koripeli|pelaa|"
                   r"pelataan|pelaa koripeliä|jatka peliä)\W*$", re.I)
EXIT = re.compile(r"^\W*(stop|stop the game|stop game|quit|exit|end|enough|pause|later|not now|bye|no more|"
                  r"lopeta)\W*$", re.I)
TYPED = [("irregular", re.compile(r"^\W*(2|occasional( costs?)?|irregular( must-?pay)?|satunnaiset( menot)?)\W*$", re.I)),
         ("regular", re.compile(r"^\W*(1|bills?( (&|and) basics)?|basics|regular( must-?pay)?|laskut( ja perusmenot)?|"
                                r"perusmenot)\W*$", re.I)),
         ("everyday", re.compile(r"^\W*(3|fun( (&|and) extras)?|extras|slips?( through)?( your fingers)?|everyday|"
                                 r"fingers|valinnaiset( menot)?)\W*$", re.I))]


def _rec(engine: Engine, user: str) -> dict | None:
    return engine.state.setdefault("quiz", {}).get(user)


def active(engine: Engine, user: str) -> bool:
    r = _rec(engine, user)
    return bool(r) and not r["done"] and not r["paused"]


def forget(engine: Engine, user: str):
    engine.state.setdefault("quiz", {}).pop(user, None)
    engine._save()


def _kind(category: str, mcc) -> tuple[str, str | None]:
    """('Fast food and cafés', '5814'), or the category's plain name when the code has no label."""
    if isinstance(mcc, str) and mcc in MCC_LABELS:
        return MCC_LABELS[mcc], mcc
    return KIND.get(category, category), None


def _teach(engine: Engine, category: str, mcc: str | None) -> dict:
    """How much of theirs went to this kind of place: last month, or the last 3 months if last month had none."""
    for period, words in (("last_month", "last month"), ("last_3_months", "over the last 3 months")):
        s = engine.spending(merchant_type=mcc, period=period) if mcc else engine.spending(category, period=period)
        if s["total_eur"]:
            return {"total_eur": s["total_eur"], "period": words}
    return {"total_eur": 0.0, "period": "last month"}


def pick(engine: Engine, game: int = 0) -> list[dict]:
    """Five recent payments, different kinds of place, spread over the three baskets. Same date + game = same picks."""
    rng = random.Random(f"{engine.customer_id}:{engine.today.isoformat()}:{game}")
    tx = engine.tx
    tx = tx[(tx["tx_amount_eur"] < 0) & tx["category"].isin(SPEND_CATEGORIES) & ~tx["mcc_code"].isin(AMBIGUOUS_MCC)]
    kinds: dict[str, dict[tuple, list]] = {k: {} for k in BASKETS}
    for days, only in ((WINDOW_DAYS, None), (IRREGULAR_WINDOW_DAYS, "irregular")):
        if only and _sortable(kinds[only], only):
            break
        for i, r in tx[tx["date"] >= engine.today - timedelta(days=days)].iterrows():
            b = CATEGORY_BASKET[r["category"]]
            if only in (None, b):
                kinds[b].setdefault((r["category"],) + _kind(r["category"], r["mcc_code"]), []).append(i)
    # Payments anyone can sort, when the basket has some: the first game in SORTABLE's order, a replay mixed.
    kinds = {b: _sortable(ks, b) or ks for b, ks in kinds.items()}
    spent = {k: -tx.loc[ix, "tx_amount_eur"].sum() for ks in kinds.values() for k, ix in ks.items()}
    order = {b: list(SORTABLE[b]) for b in BASKETS}
    rank = lambda b, k: order[b].index(_skey(k)) if _skey(k) in order[b] else len(order[b])
    chosen = {b: sorted(ks, key=lambda k: (rank(b, k), -spent[k], str(k))) for b, ks in kinds.items()}
    if game:
        chosen = {b: rng.sample(ks, len(ks)) for b, ks in chosen.items()}
    take = {b: chosen[b][:QUOTA[b]] for b in BASKETS}
    spare = [k for b in BASKETS for k in chosen[b][QUOTA[b]:]]
    rng.shuffle(spare)
    rows = [k for b in BASKETS for k in take[b]] + spare
    rows = rows[:ROUNDS]
    rng.shuffle(rows)
    out = []
    for key in rows:
        category, label, mcc = key
        rows_of = kinds[CATEGORY_BASKET[category]][key]
        r = tx.loc[rng.choice(rows_of) if game else rows_of[-1]]  # the first game: the latest one
        b = CATEGORY_BASKET[category]
        en, fi = SORTABLE[b].get(_skey(key)) or (None, None)
        place = None if en else merchant_name(r["merchant_id"])
        out.append({"date": r["date"].isoformat(), "name": en or place or NAME.get(category, f"{label} payment"),
                    "name_fi": fi, "place": bool(place), "kind": en or label, "kind_fi": fi, "category": category,
                    "amount_eur": round(-float(r["tx_amount_eur"]), 2), "basket": b, **_teach(engine, category, mcc)})
    return out


def _skey(key: tuple) -> tuple:
    """(category, label, mcc) -> SORTABLE's (category, mcc)."""
    return key[0], key[2]


def _sortable(ks: dict, basket: str) -> dict:
    return {k: v for k, v in ks.items() if _skey(k) in SORTABLE[basket]}


def _fi(r: dict) -> bool:
    return r.get("lang") == "fi"


def _eur(x: float, fi: bool) -> str:
    return _money(x).replace(".", ",") if fi else _money(x)


def _buttons(r: dict) -> list:
    return [(f"quiz:{r['game']}:{r['round']}:{b}", label) for b, label in (ANSWER_FI if _fi(r) else ANSWER).items()]


def _question(r: dict) -> Turn:
    p = r["picks"][r["round"]]
    d = date.fromisoformat(p["date"])
    fi = _fi(r)
    name = (p.get("name_fi") if fi else None) or p["name"]
    what = f"{name} · {p['kind'].lower()}" if p["place"] else name
    when = f"{DAY_FI[d.weekday()]} {d.day}.{d.month}." if fi else f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    ask = " Mihin koriin?" if fi else ". Which basket?"  # a Finnish date already ends in a full stop
    return Turn(f"*{r['round'] + 1}/{len(r['picks'])}* {what} · {_eur(p['amount_eur'], fi)} · {when}{ask}", _buttons(r))


def start(engine: Engine, user: str, lang: str | None = None) -> Turn:
    """A new game (the next one if they played before: other payments, same rules)."""
    games = engine.state.setdefault("quiz", {})
    game = games[user]["game"] + 1 if user in games else 0
    picks = pick(engine, game)
    if not picks:
        return Turn("Tarvitsen koripeliin muutaman korttimaksusi, enkä löydä vielä tuoreita." if lang == "fi" else
                    "I need a few of your card payments to play the basket game, and I can't find recent ones yet.")
    r = games[user] = {"game": game, "round": 0, "picks": picks, "right": 0, "paused": False, "note": False,
                       "done": False, "lang": lang or "en"}
    engine._save()
    t = _question(r)
    t.lead = [f"*Koripeli.* Jokainen käyttämäsi euro päätyy yhteen kolmesta korista. Näytän {len(picks)} omaa "
              "maksuasi, sinä valitset korin.\n"
              "• Laskut ja perusmenot: tarvitaan joka kuukausi, kuten vuokra ja ruoka\n"
              "• Satunnaiset menot: tarvitaan silloin tällöin, kuten lääkäri tai korjaus\n"
              "• Valinnaiset menot: oma valintasi, kuten kahvila ja shoppailu"] if _fi(r) else [f"*The basket game.* Every euro you spend lands in one of three baskets. I'll show you "
              f"{len(picks)} of your own payments, you pick the basket.\n"
              "• Bills & basics: needed every month, like rent and groceries\n"
              "• Occasional costs: needed now and then, like a doctor or a repair\n"
              "• Fun & extras: your choice, like eating out and shopping"]
    return t


def _feedback(p: dict, guess: str, fi: bool = False) -> str:
    if fi:
        why = WHY_FI[p["basket"]].format(kind=p.get("kind_fi") or p["kind"], amt=_eur(p["total_eur"], True),
                                         period=PERIOD_FI.get(p["period"], p["period"]))
        return f"Oikein. {why}" if guess == p["basket"] else f"Helppo sekoittaa: tämä kuuluu koriin *{ANSWER_FI[p['basket']]}*. {why}"
    why = WHY[p["basket"]].format(kind=p["kind"], amt=_money(p["total_eur"]), period=p["period"])
    if guess == p["basket"]:
        return f"Right. {why}"
    return f"Easy to mix up: this one is *{ANSWER[p['basket']]}*. {why}"


def summary(engine: Engine, r: dict) -> Turn:
    """Score, the real basket split for last month, and the third basket over a year."""
    b = engine.baskets("last_month")
    if _fi(r):
        return _summary_fi(engine, r, b)
    month = date.fromisoformat(b["from"]).strftime("%B")
    n, right = len(r["picks"]), r["right"]
    head = ("All right, you know your baskets." if right == n else "Most of them right." if right * 2 > n
            else "Now you know where to look.")
    lines = [f"*{right}/{n}*. {head}", "", f"Your real split for {month}:"]
    lines += [f"• {x['label']}: {_money(x['total_eur'])} ({x['share_pct']}%)" for x in b["baskets"]]
    cuts = [engine.simulate_cut(c, CUT_PCT) for c in BASKETS["everyday"]["categories"]]
    per_month = round(sum(c["current_per_month_eur"] for c in cuts), 2)
    per_year = round(sum(c["saving_per_year_eur"] for c in cuts), 2)
    if per_year:
        lines += ["", f"Fun & extras averaged {_money(per_month)} a month over the last 3 months. "
                      f"−{CUT_PCT}% on it would free about *{_money(per_year)} a year*."]
    return Turn("\n".join(lines), [("ask:everyday", "Where does it go?"), ("quiz:again", "Play again"),
                                   ("quiz:money", "Open S-mobiili")], done=True)


def _summary_fi(engine: Engine, r: dict, b: dict) -> Turn:
    month = MONTH_IN_FI[date.fromisoformat(b["from"]).month - 1]
    n, right = len(r["picks"]), r["right"]
    head = ("Hienoa, tunnet korisi." if right == n else "Suurin osa oikein." if right * 2 > n
            else "Nyt tiedät, mihin katsoa.")
    lines = [f"*{right}/{n}*. {head}", "", f"Todellinen jakosi {month}:"]
    lines += [f"• {ANSWER_FI[x['basket']]}: {_eur(x['total_eur'], True)} ({x['share_pct']}\u00a0%)" for x in b["baskets"]]
    cuts = [engine.simulate_cut(c, CUT_PCT) for c in BASKETS["everyday"]["categories"]]
    per_month = round(sum(c["current_per_month_eur"] for c in cuts), 2)
    per_year = round(sum(c["saving_per_year_eur"] for c in cuts), 2)
    if per_year:
        lines += ["", f"Valinnaiset menot olivat keskimäärin {_eur(per_month, True)} kuukaudessa viimeisen 3 kuukauden "
                      f"aikana. {CUT_PCT}\u00a0% vähemmän vapauttaisi noin *{_eur(per_year, True)} vuodessa*."]
    return Turn("\n".join(lines), [("ask:everyday", "Mihin ne menevät?"), ("quiz:again", "Pelaa uudelleen"),
                                   ("quiz:money", "Avaa S-mobiili")], done=True)


def _answer(engine: Engine, r: dict, guess: str) -> Turn:
    p = r["picks"][r["round"]]
    p["guess"] = guess
    r["right"] += guess == p["basket"]
    r["round"] += 1
    fb = _feedback(p, guess, _fi(r))
    if r["round"] < len(r["picks"]):
        engine._save()
        t = _question(r)
    else:
        r["done"] = True
        engine._save()
        t = summary(engine, r)
    t.lead = [fb]
    return t


def turn(engine: Engine, user: str, text: str, button_id: str = "", lang: str | None = None) -> Turn | None:
    """One game step. None = not a game turn (the coach or the interview answers). lang: the web chat's language
    ("fi" or "en"); WhatsApp leaves it out and the game keeps the language it started in."""
    r = _rec(engine, user)
    text = (text or "").strip()
    if r and lang and r.get("lang") != lang:  # the page's language switched mid-game
        r["lang"] = lang
    if button_id in ("quiz:start", "quiz:again", "quiz:resume") or (not button_id and START.match(text)):
        # "game" while paused, or the "Keep playing" of a follow-up the day after: back where they left
        if r and not r["done"] and (r["paused"] or button_id == "quiz:resume") and button_id != "quiz:again":
            r["paused"] = r["note"] = False
            engine._save()
            t = _question(r)
            t.lead = ["Takaisin peliin." if _fi(r) else "Back to the game."]
            return t
        return start(engine, user, lang)
    if button_id.startswith("quiz:") and button_id.count(":") == 3:
        _, game, rnd, guess = button_id.split(":")
        if not r or r["done"] or int(game) != r["game"]:
            if lang == "fi":
                return Turn("Se peli on jo ohi. Pelataanko uusi?", [("quiz:again", "Pelaa uudelleen")])
            return Turn("That game is over. Want another round?", [("quiz:again", "Play again")])
        r["paused"] = r["note"] = False
        if int(rnd) != r["round"] or guess not in BASKETS:
            engine._save()
            return _question(r)  # a button from an earlier round: the current question again
        return _answer(engine, r, guess)
    if button_id or not r or r["done"] or r["paused"]:
        return None
    if EXIT.match(text):
        r["paused"] = True
        engine._save()
        if _fi(r):
            return Turn(f"Tauolla kohdassa {r['round'] + 1}/{len(r['picks'])}. Kirjoita *peli*, kun haluat jatkaa.")
        return Turn(f"Paused at {r['round'] + 1}/{len(r['picks'])}. Write *game* when you want to pick it up.")
    guess = next((b for b, rx in TYPED if rx.match(text)), None)
    if guess:
        return _answer(engine, r, guess)
    r["paused"] = r["note"] = True  # another question: the coach answers, the game waits
    engine._save()
    return None


def waiting_question(engine: Engine, user: str) -> str | None:
    """The game question the person just stepped away from (the paused note is still to come), or None."""
    r = _rec(engine, user)
    if not r or not r.get("note") or r["done"]:
        return None
    return _question(r).text


def paused_note(engine: Engine, user: str) -> str | None:
    """Once, after the coach answered a side question mid-game: how to get back."""
    r = _rec(engine, user)
    if not r or not r.get("note"):
        return None
    r["note"] = False
    engine._save()
    if _fi(r):
        return f"Koripeli on tauolla kohdassa {r['round'] + 1}/{len(r['picks'])}. Kirjoita *peli* jatkaaksesi."
    return f"The basket game is paused at {r['round'] + 1}/{len(r['picks'])}. Write *game* to pick it up."
