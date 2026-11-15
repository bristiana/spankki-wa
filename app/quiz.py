"""The basket game: 5 of your own payments, you pick the basket. No AI, numbers from the engine.

The challenge's teaching model: every euro lands in one of three baskets (engine.BASKETS). The game picks five of
the person's recent payments, spread across the baskets, asks which basket each one is in, explains the answer with
their own numbers, and ends with their real split for last month and what -20% on the third basket frees in a year.
Voice: a right answer gets "Right.", a wrong one a kind "Easy to mix up" and the same explanation, never a grade on the person.
"stop" pauses; any other question pauses too, the coach answers, and *game* picks it up again.
State: engine.state["quiz"][user] = {"game", "round", "picks", "right", "paused", "note", "done"}.
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta

from .engine import BASKETS, CATEGORY_BASKET, CATEGORY_FI, MCC_LABELS, SPEND_CATEGORIES, Engine, merchant_name
from .onboarding import Turn, _money

ROUNDS = 5
QUOTA = {"regular": 2, "irregular": 1, "everyday": 2}  # filled from the other baskets when one has too few kinds
WINDOW_DAYS = 90
IRREGULAR_WINDOW_DAYS = 365  # one-offs are rare: look back a year before giving up on the middle basket
AMBIGUOUS_MCC = {"4121", "7523"}  # taxis and parking: the engine files them as transport (regular), people may not
CUT_PCT = 20
ANSWER = {k: b["label"] for k, b in BASKETS.items()}  # the button titles: Bills & basics, Occasional costs, Fun & extras
KIND = {"Housing": "Housing", "Utilities": "Utility bills", "Transportation": "Transport", "Health": "Health",
        "Home & Tech": "Home & tech", "Travel": "Travel", "Groceries": "Groceries", "Restaurants": "Eating out",
        "Lifestyle": "Shopping", "Entertainment": "Entertainment"}
NAME = {"Housing": "Housing payment", "Utilities": "Utility bill"}  # rows with no merchant
ANSWER_FI = {"regular": "Laskut ja perusmenot", "irregular": "Satunnaiset menot", "everyday": "Valinnaiset menot"}  # = main.BASKET_FI
NAME_FI = {"Housing": "Vuokranmaksu", "Utilities": "Sähkölasku"}
MCC_FI = {"4111": "Joukkoliikenne", "4121": "Taksit", "4722": "Matkatoimistot", "5200": "Rautakaupat",
          "5411": "Supermarketit", "5422": "Lihakaupat", "5451": "Maitokaupat", "5499": "Ruokakaupat",
          "5541": "Huoltoasemat", "5542": "Polttoaine (itsepalvelu)", "5712": "Kalustekaupat",
          "5732": "Elektroniikkakaupat", "5812": "Ravintolat", "5813": "Baarit ja pubit", "5814": "Pikaruoka ja kahvilat",
          "5912": "Apteekit", "5941": "Urheilukaupat", "5942": "Kirjakaupat", "5945": "Lelukaupat",
          "5999": "Muut kaupat", "7011": "Hotellit", "7523": "Pysäköinti", "7832": "Elokuvat",
          "7922": "Teatteri- ja tapahtumaliput", "7991": "Nähtävyydet", "7996": "Huvipuistot", "7999": "Vapaa-aika",
          "8011": "Lääkärit", "8021": "Hammaslääkärit", "8099": "Terveyspalvelut"}
WD_FI = ["ma", "ti", "ke", "to", "pe", "la", "su"]
MONTH_FI = ["tammikuussa", "helmikuussa", "maaliskuussa", "huhtikuussa", "toukokuussa", "kesäkuussa", "heinäkuussa",
            "elokuussa", "syyskuussa", "lokakuussa", "marraskuussa", "joulukuussa"]
WHY_FI = {
    "regular": "{kind}: välttämätön ja toistuu aikataulussa, joten sen voi ennakoida. Sinulla {amt} {period}.",
    "irregular": "{kind}: välttämätön, mutta et tiedä milloin, joten pieni puskuri auttaa. Sinulla {amt} {period}.",
    "everyday": "{kind}: oma valintasi, pieniä asioita jotka kasautuvat. Sinulla {amt} {period}.",
}
PERIOD_FI = {"last month": "viime kuussa", "over the last 3 months": "kolmen viime kuukauden aikana"}
WHY = {
    "regular": "{kind}: needed, and it comes back on a schedule, so you can plan for it. {amt} of yours {period}.",
    "irregular": "{kind}: needed, but you can't know when, so a small buffer helps. {amt} of yours {period}.",
    "everyday": "{kind}: your own choice, small things that add up. {amt} of yours {period}.",
}

START_FI = re.compile(r"^\W*(koripeli|koripeliä|peli|peliä|pelaa|pelataan|pelataan koripeliä|pelaa koripeliä|"
                      r"pelataan peliä|aloita peli|aloita koripeli|pelataan peli|jatka peliä|jatketaan peliä|"
                      r"haluan pelata|pelataanko)\W*$", re.I)
START = re.compile(r"^\W*(quiz|game|basket game|the basket game|play|play the basket game|play the game|"
                   r"play a game|start the game|resume the game|continue the game)\W*$", re.I)
EXIT = re.compile(r"^\W*(stop|stop the game|stop game|quit|exit|end|enough|pause|later|not now|bye|no more|"
                  r"lopeta|tauko|keskeytä|riittää|ei enää|myöhemmin)\W*$", re.I)
TYPED = [("irregular", re.compile(r"^\W*(2|occasional( costs?)?|irregular( must-?pay)?|satunnaiset( menot)?)\W*$", re.I)),
         ("regular", re.compile(r"^\W*(1|bills?( (&|and) basics)?|basics|regular( must-?pay)?|laskut( ja perusmenot)?|"
                                r"perusmenot)\W*$", re.I)),
         ("everyday", re.compile(r"^\W*(3|fun( (&|and) extras)?|extras|slips?( through)?( your fingers)?|everyday|"
                                 r"fingers|valinnaiset( menot)?)\W*$", re.I))]


def _fi(r) -> bool:
    return (r or {}).get("lang") == "fi"


def _fmoney(x: float, fi: bool) -> str:
    s = _money(x)
    return s.replace(".", ",") if fi else s


def _answers(fi: bool) -> dict:
    return ANSWER_FI if fi else ANSWER


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
        if only and kinds[only]:
            break
        for i, r in tx[tx["date"] >= engine.today - timedelta(days=days)].iterrows():
            b = CATEGORY_BASKET[r["category"]]
            if only in (None, b):
                kinds[b].setdefault((r["category"],) + _kind(r["category"], r["mcc_code"]), []).append(i)
    # The first game starts with where most of the money went; a replay mixes the kinds.
    spent = {k: -tx.loc[ix, "tx_amount_eur"].sum() for ks in kinds.values() for k, ix in ks.items()}
    chosen = {b: sorted(ks, key=lambda k: (-spent[k], str(k))) for b, ks in kinds.items()}
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
        place = merchant_name(r["merchant_id"])
        out.append({"date": r["date"].isoformat(), "name": place or NAME.get(category, f"{label} payment"), "place": bool(place),
                    "kind": label, "category": category, "amount_eur": round(-float(r["tx_amount_eur"]), 2),
                    "basket": CATEGORY_BASKET[category], **_teach(engine, category, mcc)})
    return out


def _buttons(r: dict) -> list:
    return [(f"quiz:{r['game']}:{r['round']}:{b}", label) for b, label in _answers(_fi(r)).items()]


def _kind_fi(p: dict) -> str:
    mcc = next((c for c, l in MCC_LABELS.items() if l == p["kind"]), None)
    return MCC_FI[mcc] if mcc else CATEGORY_FI.get(p["category"], p["kind"]).capitalize()


def _name_fi(p: dict) -> str:
    if p["place"]:
        return p["name"]
    return NAME_FI.get(p["category"]) or f"{_kind_fi(p)} (maksu)"


def _question(r: dict) -> Turn:
    p = r["picks"][r["round"]]
    d = date.fromisoformat(p["date"])
    fi = _fi(r)
    if fi:
        kind, name = _kind_fi(p), _name_fi(p)
        what = f"{name} · {kind.lower()}" if p["place"] else name
        return Turn(f"*{r['round'] + 1}/{len(r['picks'])}* {what} · {_fmoney(p['amount_eur'], True)} · "
                    f"{WD_FI[d.weekday()]} {d.day}.{d.month}. Mikä kori?", _buttons(r))
    what = f"{p['name']} · {p['kind'].lower()}" if p["place"] else p["name"]
    return Turn(f"*{r['round'] + 1}/{len(r['picks'])}* {what} · {_money(p['amount_eur'])} · "
                f"{d.strftime('%a')} {d.day} {d.strftime('%b')}. Which basket?", _buttons(r))


def start(engine: Engine, user: str, lang: str = "en") -> Turn:
    """A new game (the next one if they played before: other payments, same rules)."""
    games = engine.state.setdefault("quiz", {})
    game = games[user]["game"] + 1 if user in games else 0
    picks = pick(engine, game)
    if not picks:
        if lang == "fi":
            return Turn("Tarvitsen muutaman korttimaksusi koripeliin, enkä löydä tuoreita vielä.")
        return Turn("I need a few of your card payments to play the basket game, and I can't find recent ones yet.")
    r = games[user] = {"game": game, "round": 0, "picks": picks, "right": 0, "paused": False, "note": False,
                       "done": False, "lang": lang}
    engine._save()
    t = _question(r)
    if lang == "fi":
        t.lead = [f"*Koripeli.* Jokainen käyttämäsi euro päätyy yhteen kolmesta korista. Näytän {len(picks)} omaa "
                  "maksuasi, ja sinä valitset korin.\n"
                  "• Laskut ja perusmenot: tarvitaan joka kuukausi, kuten vuokra ja ruokaostokset\n"
                  "• Satunnaiset menot: tarvitaan silloin tällöin, kuten lääkäri tai korjaus\n"
                  "• Valinnaiset menot: oma valintasi, kuten ravintolat ja shoppailu"]
        return t
    t.lead = [f"*The basket game.* Every euro you spend lands in one of three baskets. I'll show you "
              f"{len(picks)} of your own payments, you pick the basket.\n"
              "• Bills & basics: needed every month, like rent and groceries\n"
              "• Occasional costs: needed now and then, like a doctor or a repair\n"
              "• Fun & extras: your choice, like eating out and shopping"]
    return t


def _feedback(p: dict, guess: str, fi: bool = False) -> str:
    if fi:
        why = WHY_FI[p["basket"]].format(kind=_kind_fi(p), amt=_fmoney(p["total_eur"], True),
                                         period=PERIOD_FI.get(p["period"], p["period"]))
        if guess == p["basket"]:
            return f"Oikein. {why}"
        return f"Helppo sekoittaa: tämä kuuluu koriin *{ANSWER_FI[p['basket']]}*. {why}"
    why = WHY[p["basket"]].format(kind=p["kind"], amt=_money(p["total_eur"]), period=p["period"])
    if guess == p["basket"]:
        return f"Right. {why}"
    return f"Easy to mix up: this one is *{ANSWER[p['basket']]}*. {why}"


def _summary_fi(engine: Engine, r: dict, b: dict, n: int, right: int) -> Turn:
    month = MONTH_FI[date.fromisoformat(b["from"]).month - 1]
    head = ("Hyvin tiedät korisi." if right == n else "Suurin osa oikein." if right * 2 > n
            else "Nyt tiedät mistä katsoa.")
    lines = [f"*{right}/{n}*. {head}", "", f"Todellinen jakosi {month}:"]
    lines += [f"• {ANSWER_FI[k['basket']]}: {_fmoney(k['total_eur'], True)} ({k['share_pct']}%)"
              for k in b["baskets"]]
    cuts = [engine.simulate_cut(c, CUT_PCT) for c in BASKETS["everyday"]["categories"]]
    per_month = round(sum(c["current_per_month_eur"] for c in cuts), 2)
    per_year = round(sum(c["saving_per_year_eur"] for c in cuts), 2)
    if per_year:
        lines += ["", f"Valinnaisiin menoihin meni keskimäärin {_fmoney(per_month, True)} kuukaudessa kolmen viime "
                      f"kuukauden aikana. −{CUT_PCT} % vapauttaisi noin *{_fmoney(per_year, True)} vuodessa*."]
    return Turn("\n".join(lines), [("ask:everyday", "Mihin se menee?"), ("quiz:again", "Pelaa uudestaan"),
                                   ("quiz:money", "Avaa S-mobiili")], done=True)


def summary(engine: Engine, r: dict) -> Turn:
    """Score, the real basket split for last month, and the third basket over a year."""
    b = engine.baskets("last_month")
    month = date.fromisoformat(b["from"]).strftime("%B")
    n, right = len(r["picks"]), r["right"]
    if _fi(r):
        return _summary_fi(engine, r, b, n, right)
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
    """One game step. None = not a game turn (the coach or the interview answers)."""
    r = _rec(engine, user)
    text = (text or "").strip()
    typed_fi = not button_id and bool(START_FI.match(text))
    if button_id in ("quiz:start", "quiz:again", "quiz:resume") or (typed_fi or (not button_id and START.match(text))):
        # "game" while paused, or the "Keep playing" of a follow-up the day after: back where they left
        if r and not r["done"] and (r["paused"] or button_id == "quiz:resume") and button_id != "quiz:again":
            r["paused"] = r["note"] = False
            engine._save()
            t = _question(r)
            t.lead = ["Takaisin peliin." if _fi(r) else "Back to the game."]
            return t
        return start(engine, user, "fi" if typed_fi or lang == "fi" else "en")
    if button_id.startswith("quiz:") and button_id.count(":") == 3:
        _, game, rnd, guess = button_id.split(":")
        if not r or r["done"] or int(game) != r["game"]:
            if lang == "fi" or _fi(r):
                return Turn("Peli on päättynyt. Pelataanko uusi kierros?", [("quiz:again", "Pelaa uudestaan")])
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
            return Turn(f"Peli on tauolla kohdassa {r['round'] + 1}/{len(r['picks'])}. Kirjoita *peli*, kun haluat jatkaa.")
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
