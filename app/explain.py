"""'How I worked this out': each tool call as one plain-words step, its key figures and where the data came from.
Every figure is read straight from the tool result, so the panel shows exactly what the reply was built on."""
from __future__ import annotations

import re

from .charts import BASKET_FI, MCC_FI, MONTH_FI
from .engine import CATEGORY_FI, CATEGORY_WORDS

TX = "your S-Pankki transactions"
STATS_FI = "Statistics Finland 2022 table 14q4"
PRICES = "Yahoo Finance monthly closes"
MORTGAGES = "S-Pankki mortgage applications (challenge data)"

TX_FI = "S-Pankki-tapahtumasi"
STATS_FI_FI = "Tilastokeskus 2022, taulukko 14q4"
PRICES_FI = "Yahoo Finance, kuukauden päätöskurssit"
MORTGAGES_FI = "S-Pankin asuntolainahakemukset (haastedata)"
GUIDE_FI = "virallinen opas: "
PERIOD_FI = {"this_month": "tältä kuulta", "last_month": "viime kuulta", "last_7_days": "viimeisen 7 päivän ajalta",
             "last_30_days": "viimeisen 30 päivän ajalta", "last_3_months": "viimeisen 3 kuukauden ajalta",
             "last_12_months": "viimeisen 12 kuukauden ajalta", "all": "koko historiasi ajalta"}
TOPIC_FI = {"emergency-fund": "puskurirahasto", "housing-allowance": "asumistuki", "students": "opiskelijan raha-asiat",
            "asp": "asp-säästäminen", "equity-savings-account": "osakesäästötili", "capital-income-tax": "pääomatulovero",
            "deposit-guarantee": "talletussuoja", "consumer-credit": "kulutusluotto", "s-bank": "S-Pankki"}
WEEKDAY_FI = {"Mon": "ma", "Tue": "ti", "Wed": "ke", "Thu": "to", "Fri": "pe", "Sat": "la", "Sun": "su",
              "Monday": "maanantai", "Tuesday": "tiistai", "Wednesday": "keskiviikko", "Thursday": "torstai",
              "Friday": "perjantai", "Saturday": "lauantai", "Sunday": "sunnuntai"}
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
GROUP_BY_FI = {"month": "kuukausittain", "week": "viikoittain", "weekday": "viikonpäivittäin",
               "category": "kategorioittain", "basket": "menoryhmittäin", "merchant": "kauppiaittain",
               "merchant_type": "kauppatyypeittäin"}
PERIOD_WORDS = {"this_month": "this month", "last_month": "last month", "last_7_days": "the last 7 days",
                "last_30_days": "the last 30 days", "last_3_months": "the last 3 months",
                "last_12_months": "the last 12 months", "all": "all your history"}


def eur(x, fi: bool = False) -> str:
    s = f"{x:,.2f} €".replace(",", " ")
    return s.replace(".", ",") if fi else s


def pct(x, fi: bool = False) -> str:
    return f"{x:g}".replace(".", ",") + " %" if fi else f"{x:g}%"


def num(x, fi: bool = False) -> str:
    return f"{x:g}".replace(".", ",") if fi else f"{x:g}"


def _fmt(fi: bool):
    """The money, percent and plain-number formats of the language."""
    return (lambda x: eur(x, fi)), (lambda x: pct(x, fi)), (lambda x: num(x, fi))


def _up(s: str) -> str:
    return s[:1].upper() + s[1:]


def cat_fi(c) -> str:
    """A spending category, merchant kind or basket in Finnish (as it is written in the middle of a sentence)."""
    c = str(c)
    if c in CATEGORY_FI:
        return CATEGORY_FI[c]
    if c in MCC_FI:
        return MCC_FI[c].lower()
    return c


def _label_fi(c) -> str:
    """A row label from the explorer: a category, merchant kind, basket, month or weekday, in Finnish."""
    c = str(c)
    if c in BASKET_FI:
        return BASKET_FI[c]
    if c in WEEKDAY_FI:
        return WEEKDAY_FI[c]
    m = re.fullmatch(r"([A-Z][a-z]{2}) (\d{4})", c)
    if m and m.group(1) in _MON:
        return f"{MONTH_FI[_MON.index(m.group(1))]} {m.group(2)}"
    return _up(cat_fi(c)) if (c in CATEGORY_FI or c in MCC_FI) else c


def _date_fi(d: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", d or "")
    return f"{int(m.group(3))}.{int(m.group(2))}.{m.group(1)}" if m else d


def group_fi(g: str) -> str:
    """A peer group ("S-Pankki customers aged 25-29", "customers aged 30-39 in the Espoo area") in Finnish."""
    g = str(g or "")
    age = re.search(r"aged (\d+)-(\d+)", g)
    out = "S-Pankin asiakkaat" + (f", {age.group(1)}–{age.group(2)}-vuotiaat" if age else "")
    if "starting out" in g:
        out = "S-Pankin asiakkaat, jotka aloittavat omillaan (18–29-vuotiaat, säännöllinen palkka)"
    for en, fin in (("Helsinki", "Helsingissä"), ("Vantaa", "Vantaan alueella"), ("Espoo", "Espoon alueella"),
                    ("capital region", "pääkaupunkiseudulla")):
        if en in g:
            return f"{out}, {fin}"
    return out


def _get(out, path: str):
    for k in path.split("."):
        out = out.get(k) if isinstance(out, dict) else None
    return out


def _figs(out: dict, rows) -> list[dict]:
    """[(label, path, format)] -> [{label, value}], skipping what the result does not have."""
    figs = []
    for label, path, f in rows:
        v = _get(out, path)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            figs.append({"label": label, "value": f(v)})
    return figs


def _period(a: dict, fi: bool = False) -> str:
    if a.get("start") or a.get("end"):
        if fi:
            return f"{_date_fi(a.get('start')) or 'alusta'} – {_date_fi(a.get('end')) or 'tähän päivään'} väliseltä ajalta"
        return f"{a.get('start') or 'the start'} to {a.get('end') or 'today'}"
    return (PERIOD_FI if fi else PERIOD_WORDS).get(a.get("period"), "viime kuulta" if fi else "last month")


def safe_to_spend(a, o, fi=False):
    E, P, N = _fmt(fi)
    bills = o.get("bills_before_payday") or []
    bill_label = (f"{bills[0]['name']} erääntyy" if fi else f"{bills[0]['name']} due") if len(bills) == 1 else (
        "Laskut ennen palkkapäivää" if fi else "Bills before payday")
    needs = (o.get("utilities_estimate_eur") or 0) + (o.get("groceries_and_transport_estimate_eur") or 0)
    figs = _figs(o, [("Saldo" if fi else "Balance", "balance_eur", E), (bill_label, "bills_total_eur", E)])
    figs.append({"label": "Tavalliset menot palkkapäivään asti" if fi else "Usual needs until payday",
                 "value": E(round(needs, 2))})
    figs += _figs(o, [("Puskuri yllätyksille" if fi else "Buffer for surprises", "surprise_buffer_eur", E),
                      ("Turvallisesti käytettävissä palkkapäivään" if fi else "Safe until payday",
                       "safe_to_spend_until_payday_eur", E)])
    if o.get("reserve_eur"):
        figs += _figs(o, [("Maksukauden ylittävä kertymä" if fi else "Built up beyond the pay cycle", "reserve_eur", E)])
    return ("Katsoin, paljonko voit käyttää palkkapäivään asti" if fi else
            "Looked up what's safe to spend until payday"), figs, [TX_FI if fi else TX]


def spending(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Yhteensä" if fi else "Total", "total_eur", E), ("Maksuja" if fi else "Payments", "transactions", str),
                     ("Keskimääräinen maksu" if fi else "Average payment", "average_per_transaction_eur", E),
                     ("Edellinen jakso" if fi else "Period before", "previous_period.total_eur", E)])
    if o.get("period_used"):  # 'this month' had no payments yet, so the engine answered with the last full month
        a = {**a, "period": o["period_used"]}
    if fi:
        what = a.get("merchant_type") or a.get("category")
        step = (f"Laskin yhteen menosi ({cat_fi(what)}) {_period(a, True)}" if what
                else f"Laskin yhteen kaikki menosi {_period(a, True)}")
        return step, figs, [TX_FI]
    what = a.get("merchant_type") or a.get("category") or "all your"
    return f"Added up {what} spending for {_period(a)}", figs, [TX]


def baskets(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = [{"label": f"{BASKET_FI.get(b.get('basket'), b['label'])} kuukaudessa" if fi else f"{b['label']} per month",
             "value": E(b["per_month_eur"])} for b in o.get("baskets", [])]
    figs += _figs(o, [("Tulot" if fi else "Income", "income_eur", E), ("Jäi yli" if fi else "Left over", "left_over_eur", E)])
    if fi:
        return f"Jaoin menosi kolmeen ryhmään {_period(a, True)}", figs, [TX_FI]
    return f"Split your spending into the three baskets for {_period(a)}", figs, [TX]


def simulate_cut(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Nyt kuukaudessa" if fi else "Now per month", "current_per_month_eur", E),
                     ("Leikkauksen jälkeen" if fi else "After the cut", "new_per_month_eur", E),
                     ("Säästö kuukaudessa" if fi else "Saved per month", "saving_per_month_eur", E),
                     ("Säästö vuodessa" if fi else "Saved per year", "saving_per_year_eur", E)])
    if fi:
        src = [TX_FI] + ([PRICES_FI] if o.get("real_past_check") else [])
        cat = a.get("category")
        what = f"menoja ({cat_fi(cat)})" if cat else "menoja"
        return f"Laskin, paljonko säästät leikkaamalla {what} {P(a.get('cut_pct', 0))}:lla", figs, src
    src = [TX] + ([PRICES] if o.get("real_past_check") else [])
    return f"Worked out what cutting {a.get('category', 'it')} by {pct(a.get('cut_pct', 0))} saves", figs, src


def afford(a, o, fi=False):
    E, P, N = _fmt(fi)
    item = str(a.get("what") or "").strip()[:60]
    price = E(float(a.get("amount_eur") or 0)).replace(",00 €" if fi else ".00 €", " €")
    figs = _figs(o, [("Hinta" if fi else "Price", "price_eur", E),
                     ("Turvallisesti käytettävissä palkkapäivään" if fi else "Safe until payday",
                      "safe_to_spend_until_payday_eur", E),
                     ("Turvallisesti jäljellä oston jälkeen" if fi else "Safe left after it", "safe_left_after_eur", E),
                     ("Joustavaa budjettia jäljellä tässä kuussa" if fi else "Flexible budget left this month",
                      "flexible_left_this_month_eur", E)])
    if fi:
        return f"Tarkistin, mahtuuko ostos ({item + ', ' if item else ''}{price}) budjettiisi", figs, [TX_FI]
    return f"Checked whether {f'the {item} for ' if item else ''}{price} fits your budget", figs, [TX]


def safe_to_save(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Voisit säästää nyt" if fi else "Could save now", "now_max_eur", E),
                     ("Maksukauden ylittävä kertymä" if fi else "Built up beyond the pay cycle", "reserve_eur", E),
                     ("Kuukausittainen ylijäämä" if fi else "Monthly surplus", "monthly.surplus_per_month_eur", E),
                     ("Tasainen säästö kuukaudessa" if fi else "Steady saving per month",
                      "monthly.sustainable_saving_per_month_eur", E)])
    return ("Laskin, paljonko voit turvallisesti säästää" if fi else "Worked out how much you can safely save"), \
        figs, [TX_FI if fi else TX]


def home_readiness(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Kuukausipalkka" if fi else "Monthly salary", "your_money.monthly_salary_eur", E),
                     ("Samalla tulotasolla saatu laina" if fi else "Loan people with your income carried",
                      "at_your_income.loan_eur", E),
                     ("Asunnon hinta" if fi else "Home price", "deposit.price_eur", E),
                     ("Tarvittava omarahoitus ja kulut" if fi else "Deposit and costs needed", "deposit.total_needed_eur", E),
                     ("Sinulla on" if fi else "You have", "deposit.you_have_eur", E),
                     ("Vielä säästettävä" if fi else "Still to save", "deposit.still_to_save_eur", E)])
    guides = [f"{GUIDE_FI if fi else 'official guide: '}{u}" for u in _get(o, "asp.sources") or []]
    price = a.get("price_eur")
    if fi:
        step = (f"Vertasin {E(price)} ensiasuntoa tuloihisi" if price else "Arvioin, kuinka valmis olet ensiasuntoon")
        return step, figs, [TX_FI, MORTGAGES_FI] + guides
    step = f"Checked a {eur(price)} first home against your income" if price else "Estimated how ready you are for a first home"
    return step, figs, [TX, MORTGAGES] + guides


def explore(a, o, fi=False):
    E, P, N = _fmt(fi)
    unit = E if o.get("unit") == "€" else N
    figs = _figs(o, [("Yhteensä" if fi else "Total", "totals.value", unit),
                     ("Maksuja" if fi else "Payments", "totals.transactions", str)])
    top = _get(o, "totals.highest")
    if isinstance(top, dict) and isinstance(top.get("value"), (int, float)):
        figs.append({"label": f"Suurin: {_label_fi(top.get('label'))}" if fi else f"Highest: {top.get('label')}",
                     "value": unit(top["value"])})
    if fi:
        q = o.get("query") or {}
        subject = "tulot" if q.get("what") == "income" else "menot"
        extra = q.get("category") or q.get("merchant_type")
        if extra:
            subject += f" ({cat_fi(extra)})"
        elif q.get("basket"):
            subject += f" ({BASKET_FI.get(q['basket'], q['basket'])})"
        lead = {"count": "maksujen määrä, ", "average": "keskimääräinen maksu, "}.get(q.get("metric"), "")
        by = GROUP_BY_FI.get(q.get("group_by"), "")
        return f"Piirsin kaavion: {lead}{subject}{' ' + by if by else ''}, {_period(a, True)}", figs, [TX_FI]
    title = o.get("title") or "your data"
    return f"Charted {title[0].lower() + title[1:]} for {_period(a)}", figs, [TX]


def spender_profile(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Hauskaan ja extriin menevä osuus" if fi else "Share that goes to fun & extras", "everyday_share_pct", P),
                     ("Säästöaste" if fi else "Savings rate", "savings_rate_pct", P)])
    for d in (_get(o, "peers.biggest_differences") or [])[:2]:
        figs.append({"label": f"{_up(cat_fi(d['category']))}: sinä vs vertaiset" if fi else f"{d['category']}: you vs peers",
                     "value": f"{E(d['you_per_month_eur'])} vs {E(d['peers_median_eur'])}"})
    f_ = o.get("finland_official") or {}
    for c in (f_.get("comparison") or [])[:1]:
        figs.append({"label": (f"{_up(cat_fi(c['category']))}: sinä vs suomalaiset kotitaloudet" if fi
                               else f"{c['category']}: you vs Finnish households"),
                     "value": f"{E(c['you_per_month_eur'])} vs {E(c['finnish_households_eur'])}"})
    peers = _get(o, "peers.group")
    if fi:
        src = [TX_FI] + ([f"{group_fi(peers)} (haastedata)"] if peers else []) + ([STATS_FI_FI] if f_ else [])
        return "Vertasin menojasi samanikäisiin", figs, src
    src = [TX] + ([f"{peers} (challenge data)"] if peers else []) + ([STATS_FI] if f_ else [])
    return "Compared your spending with people your age", figs, src


NAME_FI = {"Rent": "Vuokra", "Utility bills": "Asumisen laskut", "Utility bill": "Asumisen lasku",
           "Monthly investment": "Kuukausisijoitus", "Monthly investing": "Kuukausisijoitus", "Salary": "Palkka"}
MONTH_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
            "November", "December"]
PAID_KEYS = [("rent", "Rent", "Vuokra"), ("utilities", "Utility bills", "Asumisen laskut"),
             ("investing", "Investing", "Sijoitukset")]


def regular_payments(a, o, fi=False):
    E, P, N = _fmt(fi)
    paid = o.get("paid_in_month_shown")
    figs = []
    if isinstance(paid, dict):  # what each payment came to in the month shown, ahead of the usual monthly figures
        m = re.fullmatch(r"(\d{4})-(\d{2})", str(paid.get("month") or ""))
        if m:
            i = int(m.group(2)) - 1
            when = (MONTH_FI[i] + "ssa") if fi else f"in {MONTH_EN[i]}"
            for key, en, fin in PAID_KEYS:
                x = paid.get(key) or {}
                if x.get("payments") and isinstance(x.get("total_eur"), (int, float)):
                    figs.append({"label": f"Maksettu {when}: {fin}" if fi else f"Paid {when}: {en}",
                                 "value": E(x["total_eur"])})
    usual = ", tavallisesti kuussa" if fi else ", usually a month"
    for x in [x for x in o.get("regular_payments", []) if x.get("status") == "active"][:4]:
        if paid:
            figs.append({"label": (NAME_FI.get(x["name"], x["name"]) if fi else x["name"]) + usual,
                         "value": E(x["per_month_eur"])})
        else:
            figs.append({"label": x["name"], "value": f"{E(x['per_month_eur'])} kuukaudessa" if fi
                         else f"{E(x['per_month_eur'])} a month"})
    figs += _figs(o, [("Menee vuodessa" if fi else "Going out per year", "outgoing_per_year_eur", E),
                      ("Löydettyjä tilauksia" if fi else "Subscriptions found", "subscriptions_found", str)])
    return ("Etsin maksut, jotka toistuvat joka kuukausi" if fi else "Found the payments that come back every month"), \
        figs, [TX_FI if fi else TX]


CITY_FI = {"Helsinki": "Helsingissä", "Vantaa": "Vantaan alueella", "Espoo": "Espoon alueella"}


def where_you_sit(a, o, fi=False):
    E, P, N = _fmt(fi)
    c = o.get("cohort") or {}
    band, city = c.get("age_band") or "", str(c.get("city") or "")
    band_fi = band.replace("-", "–")
    city_fi = next((v for k, v in CITY_FI.items() if k in city), "pääkaupunkiseudulla")
    inc = next((f for f in o.get("figures") or [] if f.get("id") == "income"), None)
    figs = []
    if inc:
        figs += [{"label": "Tulosi, 12 kuukauden keskiarvo" if fi else "Your income, 12-month average", "value": E(inc["you"])},
                 {"label": (f"Tyypillinen: {band_fi}-vuotiaat, {city_fi}" if fi
                            else f"Typical for people aged {band} in {city}"), "value": E(inc["median"])}]
    figs += _figs(o, [("Vertailuryhmän koko" if fi else "People in the group", "cohort.n", str)])
    if fi:
        return ("Vertasin tulojasi ja menojasi alueesi samanikäisiin", figs,
                [TX_FI, f"S-Pankin {band_fi}-vuotiaiden asiakkaiden keskiarvot, {city_fi} (haastedata)"])
    return ("Compared your income and spending with people your age in your area", figs,
            [TX, f"averages of S-Pankki customers aged {band} in {city} (challenge data)"])


def full_picture(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Kaikki saldot" if fi else "All balances", "total_balances_eur", E),
                     ("Korttivelka" if fi else "Card debt", "card_debt_eur", E),
                     ("Nettoasema" if fi else "Net position", "net_position_eur", E)])
    if fi:
        return "Laskin yhteen tilisi eri pankeissa", figs, [TX_FI] + (["yhdistetty Demo Bank (PSD2)"] if o.get("linked") else [])
    src = [TX] + (["your linked Demo Bank (PSD2)"] if o.get("linked") else [])
    return "Added up your accounts across banks", figs, src


def money_guide(a, o, fi=False):
    raw = o.get("topic") or a.get("topic") or ""
    if fi:
        return (f"Hain oppaan aiheesta {TOPIC_FI.get(raw, raw.replace('-', ' '))}", [],
                [f"{GUIDE_FI}{u}" for u in o.get("sources", [])])
    topic = raw.replace("-", " ")
    return f"Looked up the guide on {topic}", [], [f"official guide: {u}" for u in o.get("sources", [])]


def portfolio(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = _figs(o, [("Sijoitettu tähän mennessä" if fi else "Put in so far", "invested_total_eur", E),
                     ("Nostettu" if fi else "Taken out", "withdrawn_total_eur", E),
                     ("Nettosijoitus" if fi else "Net put in", "net_put_in_eur", E),
                     ("Kuukausittainen sijoitussuunnitelma" if fi else "Monthly plan", "monthly_plan_in_data.amount_eur", E)])
    if fi:
        return "Laskin yhteen sijoituksesi", figs, [TX_FI] + ([PRICES_FI] if o.get("if_your_deposits_were_in_each_fund") else [])
    src = [TX] + ([PRICES] if o.get("if_your_deposits_were_in_each_fund") else [])
    return "Added up what you have invested", figs, src


def upcoming_bills(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = [{"label": b["name"], "value": E(b["amount_eur"])} for b in o[:5] if isinstance(b.get("amount_eur"), (int, float))]
    if fi:
        return f"Tarkistin seuraavien {a.get('days', 14)} päivän aikana erääntyvät laskut", figs, [TX_FI]
    return f"Checked the bills due in the next {a.get('days', 14)} days", figs, [TX]


EXPLAIN = {"get_safe_to_spend": safe_to_spend, "get_spending": spending, "get_baskets": baskets,
           "simulate_cut": simulate_cut, "can_i_afford": afford, "get_safe_to_save": safe_to_save,
           "get_home_readiness": home_readiness, "explore_my_data": explore, "get_spender_profile": spender_profile,
           "get_regular_payments": regular_payments, "get_full_picture": full_picture, "get_money_guide": money_guide,
           "get_portfolio": portfolio, "get_where_you_sit": where_you_sit, "get_upcoming_bills": upcoming_bills}


def people_like_me(a, o, fi=False):
    E, P, N = _fmt(fi)
    g = o.get("group") or {}
    figs = _figs(o, [("Tyypillinen kuukausi" if fi else "Typical month", "typical_month.total.median_eur", E)])
    you = next((r for r in (_get(o, "you.comparison") or []) if r.get("key") == "total"), None)
    if you:
        figs.append({"label": "Sinun kuukautesi" if fi else "Your month", "value": E(you["you_per_month_eur"])})
    figs += _figs(o, [("Asiakkaita ryhmässä" if fi else "Customers in the group", "group.customers", str)])
    if fi:
        src = ([TX_FI] if you else []) + ["vähintään 30 S-Pankin asiakkaan keskiarvot (haastedata)"]
        return f"Katsoin, miten sinun kaltaisesi ihmiset elävät ({group_fi(g.get('who'))})", figs, src
    src = ([TX] if you else []) + ["averages of at least 30 S-Pankki customers (challenge data)"]
    return f"Looked up how {g.get('who', 'people like you')} live", figs, src


EXPLAIN["get_people_like_me"] = people_like_me

_WORDS_FI = {w: CATEGORY_FI[c] for c, w in CATEGORY_WORDS.items()}


def save_more(a, o, fi=False):
    E, P, N = _fmt(fi)
    figs = []
    for i in o.get("ideas") or []:
        if i.get("idea") == "biggest_lever":
            figs.append({"label": (f"20 % vähemmän: {_WORDS_FI.get(i['in_words'], i['in_words'])}, vuodessa" if fi
                                   else f"20% less on {i['in_words']}, a year"), "value": E(i["saving_per_year_eur"])})
        elif i.get("idea") == "move_what_built_up":
            figs.append({"label": "Maksukauden ylittävä kertymä" if fi else "Built up beyond the pay cycle",
                         "value": E(i["amount_eur"])})
        elif i.get("idea") == "pay_yourself_first":
            figs.append({"label": "Tasainen ylijäämä kuukaudessa" if fi else "Steady surplus a month",
                         "value": E(i["per_month_eur"])})
        elif i.get("idea") == "review_payment":
            k = i.get("number_to_say", "per_month_eur")
            figs.append({"label": (f"{i['name']}, {'vuodessa' if k == 'per_year_eur' else 'kuukaudessa'}" if fi
                                   else f"{i['name']}, {'a year' if k == 'per_year_eur' else 'a month'}"), "value": E(i[k])})
    return ("Etsin, mistä voisit säästää enemmän" if fi else "Looked for where you could save more"), figs, \
        [TX_FI if fi else TX]


EXPLAIN["get_save_more_ideas"] = save_more

STEPS = {"list_funds": "Looked up the demo funds", "get_goals": "Looked up your savings goals",
         "create_goal": "Made a savings plan", "create_payment_link": "Prepared a payment for your PIN",
         "create_investment_link": "Prepared an investment for your PIN",
         "create_saving_link": "Prepared a saving for your PIN",
         "create_pause_investing_link": "Prepared pausing your monthly investing for your PIN"}
STEPS_FI = {"list_funds": "Katsoin demorahastot", "get_goals": "Katsoin säästötavoitteesi",
            "create_goal": "Tein säästösuunnitelman", "create_payment_link": "Valmistelin maksun PIN-koodiasi varten",
            "create_investment_link": "Valmistelin sijoituksen PIN-koodiasi varten",
            "create_saving_link": "Valmistelin säästön PIN-koodiasi varten",
            "create_pause_investing_link": "Valmistelin kuukausisijoituksen keskeytyksen PIN-koodiasi varten"}


def generic(name: str, a: dict, o, fi: bool = False) -> tuple[str, list, list]:
    """Any other tool: its top-level numbers, labelled from their field names (in Finnish: a plain 'Luku')."""
    E, P, N = _fmt(fi)
    figs = []
    for k, v in (o.items() if isinstance(o, dict) else []):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and len(figs) < 5:
            label = k.removesuffix("_eur").removesuffix("_pct").replace("_", " ").capitalize()
            if fi:
                label = "Summa" if k.endswith("_eur") else "Osuus" if k.endswith("_pct") else "Luku"
            figs.append({"label": label, "value": E(v) if k.endswith("_eur") else P(v) if k.endswith("_pct") else N(v)})
    if fi:
        return STEPS_FI.get(name, "Tarkistin: " + name.replace("_", " ")), figs, \
            ["S-Pankin demorahastot"] if name == "list_funds" else [TX_FI]
    src = ["S-Pankki demo funds"] if name == "list_funds" else [TX]
    return STEPS.get(name, "Ran " + name.replace("_", " ")), figs, src


def explain(name: str, args: dict, out, fi: bool = False) -> dict:
    """One trace step: {tool, args, step, figures: [{label, value}], sources}. Never fails a reply.
    fi: the whole step in Finnish (sentences, labels, sources, number format)."""
    args = args or {}
    try:
        fn = EXPLAIN.get(name)
        step, figs, src = fn(args, out, fi) if fn else generic(name, args, out, fi)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        step, figs, src = generic(name, args, out, fi)
    return {"tool": name, "args": args, "step": step, "figures": figs, "sources": src}


def check_line(checked: int, unmatched: int, rewritten: bool, fi: bool = False) -> str:
    """The number guard's verdict in plain words."""
    if fi:
        if not checked:
            line = "Vastauksessa ei ollut tarkistettavia lukuja"
        elif unmatched:
            line = f"Vain {checked - unmatched}/{checked} lukua täsmäsi laskelmiin"
        else:
            line = f"{checked}/{checked} lukua tarkistettu laskelmista"
        return line + (" (ensimmäinen luonnos korjattiin luvun vuoksi)" if rewritten else "")
    if not checked:
        line = "No figures to check in this answer"
    elif unmatched:
        line = f"Only {checked - unmatched} of {checked} figures matched the calculations"
    else:
        line = f"{checked} of {checked} figures checked against the calculations"
    return line + (" (a first draft was rewritten to fix a figure)" if rewritten else "")
