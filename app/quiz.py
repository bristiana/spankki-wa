"""The basket game: 5 of your own payments, you pick the basket. No AI, numbers from the engine.

The challenge's teaching model: every euro lands in one of three baskets (engine.BASKETS). The game picks five of
the person's recent payments, spread across the baskets, asks which basket each one is in, explains the answer with
their own numbers, and ends with their real split for last month and what -20% on the third basket frees in a year.
Voice: a right answer gets ✅, a wrong one a kind 🤔 and the same explanation, never a grade on the person.
"stop" pauses; any other question pauses too, the coach answers, and *game* picks it up again.
State: engine.state["quiz"][user] = {"game", "round", "picks", "right", "paused", "note", "done"}.
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
ANSWER = {"regular": "Regular must-pay", "irregular": "Irregular must-pay", "everyday": "Slips through"}
KIND = {"Housing": "Housing", "Utilities": "Utility bills", "Transportation": "Transport", "Health": "Health",
        "Home & Tech": "Home & tech", "Travel": "Travel", "Groceries": "Groceries", "Restaurants": "Eating out",
        "Lifestyle": "Shopping", "Entertainment": "Entertainment"}
NAME = {"Housing": "Housing payment", "Utilities": "Utility bill"}  # rows with no merchant
WHY = {
    "regular": "{kind}: a must-pay that comes back on a schedule, so you can plan for it. {amt} of yours {period}.",
    "irregular": "{kind}: needed, but you can't know when, so a small buffer helps. {amt} of yours {period}.",
    "everyday": "{kind}: the classic 'slips through your fingers', small choices that add up. {amt} of yours {period}.",
}

START = re.compile(r"^\W*(quiz|game|basket game|the basket game|play|play the basket game|play the game|"
                   r"play a game|start the game|resume the game|continue the game)\W*$", re.I)
EXIT = re.compile(r"^\W*(stop|stop the game|stop game|quit|exit|end|enough|pause|later|not now|bye|no more|"
                  r"lopeta)\W*$", re.I)
TYPED = [("irregular", re.compile(r"^\W*(2|irregular( must-?pay)?)\W*$", re.I)),
         ("regular", re.compile(r"^\W*(1|regular( must-?pay)?)\W*$", re.I)),
         ("everyday", re.compile(r"^\W*(3|slips?( through)?( your fingers)?|everyday|fingers)\W*$", re.I))]


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
    return [(f"quiz:{r['game']}:{r['round']}:{b}", label) for b, label in ANSWER.items()]


def _question(r: dict) -> Turn:
    p = r["picks"][r["round"]]
    d = date.fromisoformat(p["date"])
    what = f"{p['name']} · {p['kind'].lower()}" if p["place"] else p["name"]
    return Turn(f"*{r['round'] + 1}/{len(r['picks'])}* {what} · {_money(p['amount_eur'])} · "
                f"{d.strftime('%a')} {d.day} {d.strftime('%b')}. Which basket?", _buttons(r))


def start(engine: Engine, user: str) -> Turn:
    """A new game (the next one if they played before: other payments, same rules)."""
    games = engine.state.setdefault("quiz", {})
    game = games[user]["game"] + 1 if user in games else 0
    picks = pick(engine, game)
    if not picks:
        return Turn("I need a few of your card payments to play the basket game, and I can't find recent ones yet.")
    r = games[user] = {"game": game, "round": 0, "picks": picks, "right": 0, "paused": False, "note": False,
                       "done": False}
    engine._save()
    t = _question(r)
    t.lead = [f"🧺 *The basket game.* Every euro you spend lands in one of three baskets. I'll show you "
              f"{len(picks)} of your own payments, you pick the basket.\n"
              "• Regular must-pay: rent, bills, groceries\n"
              "• Irregular must-pay: needed, but not on a schedule\n"
              "• Slips through: small choices that add up"]
    return t


def _feedback(p: dict, guess: str) -> str:
    why = WHY[p["basket"]].format(kind=p["kind"], amt=_money(p["total_eur"]), period=p["period"])
    if guess == p["basket"]:
        return f"✅ Right. {why}"
    return f"🤔 Easy to mix up: this one is *{ANSWER[p['basket']]}*. {why}"


def summary(engine: Engine, r: dict) -> Turn:
    """Score, the real basket split for last month, and the third basket over a year."""
    b = engine.baskets("last_month")
    month = date.fromisoformat(b["from"]).strftime("%B")
    n, right = len(r["picks"]), r["right"]
    head = ("All right, you know your baskets 🌰" if right == n else "Nicely done 🌰" if right * 2 > n
            else "Now you know where to look 🌰")
    lines = [f"🏁 *{right}/{n}*. {head}", "", f"Your real split for {month}:"]
    lines += [f"• {x['label']}: {_money(x['total_eur'])} ({x['share_pct']}%)" for x in b["baskets"]]
    cuts = [engine.simulate_cut(c, CUT_PCT) for c in BASKETS["everyday"]["categories"]]
    per_month = round(sum(c["current_per_month_eur"] for c in cuts), 2)
    per_year = round(sum(c["saving_per_year_eur"] for c in cuts), 2)
    if per_year:
        lines += ["", f"Slips through your fingers averaged {_money(per_month)} a month over the last 3 months. "
                      f"−{CUT_PCT}% on it would free about *{_money(per_year)} a year*."]
    return Turn("\n".join(lines), [("ask:everyday", "Where does it go?"), ("quiz:again", "Play again"),
                                   ("quiz:money", "Open My money")], done=True)


def _answer(engine: Engine, r: dict, guess: str) -> Turn:
    p = r["picks"][r["round"]]
    p["guess"] = guess
    r["right"] += guess == p["basket"]
    r["round"] += 1
    fb = _feedback(p, guess)
    if r["round"] < len(r["picks"]):
        engine._save()
        t = _question(r)
    else:
        r["done"] = True
        engine._save()
        t = summary(engine, r)
    t.lead = [fb]
    return t


def turn(engine: Engine, user: str, text: str, button_id: str = "") -> Turn | None:
    """One game step. None = not a game turn (the coach or the interview answers)."""
    r = _rec(engine, user)
    text = (text or "").strip()
    if button_id in ("quiz:start", "quiz:again") or (not button_id and START.match(text)):
        if r and not r["done"] and r["paused"] and button_id != "quiz:again":
            r["paused"] = r["note"] = False  # "game" while paused: back where they left
            engine._save()
            t = _question(r)
            t.lead = ["🧺 Back to the game."]
            return t
        return start(engine, user)
    if button_id.startswith("quiz:") and button_id.count(":") == 3:
        _, game, rnd, guess = button_id.split(":")
        if not r or r["done"] or int(game) != r["game"]:
            return Turn("That game is over 🙂 Want another round?", [("quiz:again", "Play again")])
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
        return Turn(f"👍 Paused at {r['round'] + 1}/{len(r['picks'])}. Write *game* when you want to pick it up.")
    guess = next((b for b, rx in TYPED if rx.match(text)), None)
    if guess:
        return _answer(engine, r, guess)
    r["paused"] = r["note"] = True  # another question: the coach answers, the game waits
    engine._save()
    return None


def paused_note(engine: Engine, user: str) -> str | None:
    """Once, after the coach answered a side question mid-game: how to get back."""
    r = _rec(engine, user)
    if not r or not r.get("note"):
        return None
    r["note"] = False
    engine._save()
    return f"🧺 The basket game is paused at {r['round'] + 1}/{len(r['picks'])}. Write *game* to pick it up."
