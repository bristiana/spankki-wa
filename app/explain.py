"""'How I worked this out': each tool call as one plain-words step, its key figures and where the data came from.
Every figure is read straight from the tool result, so the panel shows exactly what the reply was built on."""
from __future__ import annotations

TX = "your S-Pankki transactions"
STATS_FI = "Statistics Finland 2022 table 14q4"
PRICES = "Yahoo Finance monthly closes"
MORTGAGES = "S-Pankki mortgage applications (challenge data)"

PERIOD_WORDS = {"this_month": "this month", "last_month": "last month", "last_7_days": "the last 7 days",
                "last_30_days": "the last 30 days", "last_3_months": "the last 3 months",
                "last_12_months": "the last 12 months", "all": "all your history"}


def eur(x) -> str:
    return f"{x:,.2f} €".replace(",", " ")


def pct(x) -> str:
    return f"{x:g}%"


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


def _period(a: dict) -> str:
    if a.get("start") or a.get("end"):
        return f"{a.get('start') or 'the start'} to {a.get('end') or 'today'}"
    return PERIOD_WORDS.get(a.get("period"), "last month")


def safe_to_spend(a, o):
    bills = o.get("bills_before_payday") or []
    bill_label = f"{bills[0]['name']} due" if len(bills) == 1 else "Bills before payday"
    needs = (o.get("utilities_estimate_eur") or 0) + (o.get("groceries_and_transport_estimate_eur") or 0)
    figs = _figs(o, [("Balance", "balance_eur", eur), (bill_label, "bills_total_eur", eur)])
    figs.append({"label": "Usual needs until payday", "value": eur(round(needs, 2))})
    figs += _figs(o, [("Buffer for surprises", "surprise_buffer_eur", eur),
                      ("Safe until payday", "safe_to_spend_until_payday_eur", eur)])
    if o.get("reserve_eur"):
        figs += _figs(o, [("Built up beyond the pay cycle", "reserve_eur", eur)])
    return "Looked up what's safe to spend until payday", figs, [TX]


def spending(a, o):
    what = a.get("merchant_type") or a.get("category") or "all your"
    figs = _figs(o, [("Total", "total_eur", eur), ("Payments", "transactions", str),
                     ("Average payment", "average_per_transaction_eur", eur),
                     ("Period before", "previous_period.total_eur", eur)])
    if o.get("period_used"):  # 'this month' had no payments yet, so the engine answered with the last full month
        a = {**a, "period": o["period_used"]}
    return f"Added up {what} spending for {_period(a)}", figs, [TX]


def baskets(a, o):
    figs = [{"label": f"{b['label']} per month", "value": eur(b["per_month_eur"])} for b in o.get("baskets", [])]
    figs += _figs(o, [("Income", "income_eur", eur), ("Left over", "left_over_eur", eur)])
    return f"Split your spending into the three baskets for {_period(a)}", figs, [TX]


def simulate_cut(a, o):
    figs = _figs(o, [("Now per month", "current_per_month_eur", eur), ("After the cut", "new_per_month_eur", eur),
                     ("Saved per month", "saving_per_month_eur", eur), ("Saved per year", "saving_per_year_eur", eur)])
    src = [TX] + ([PRICES] if o.get("real_past_check") else [])
    return f"Worked out what cutting {a.get('category', 'it')} by {pct(a.get('cut_pct', 0))} saves", figs, src


def afford(a, o):
    item = str(a.get("what") or "").strip()[:60]
    price = eur(float(a.get("amount_eur") or 0)).replace(".00 €", " €")
    figs = _figs(o, [("Price", "price_eur", eur), ("Safe until payday", "safe_to_spend_until_payday_eur", eur),
                     ("Safe left after it", "safe_left_after_eur", eur),
                     ("Flexible budget left this month", "flexible_left_this_month_eur", eur)])
    return f"Checked whether {f'the {item} for ' if item else ''}{price} fits your budget", figs, [TX]


def safe_to_save(a, o):
    figs = _figs(o, [("Could save now", "now_max_eur", eur), ("Built up beyond the pay cycle", "reserve_eur", eur),
                     ("Monthly surplus", "monthly.surplus_per_month_eur", eur),
                     ("Steady saving per month", "monthly.sustainable_saving_per_month_eur", eur)])
    return "Worked out how much you can safely save", figs, [TX]


def home_readiness(a, o):
    figs = _figs(o, [("Monthly salary", "your_money.monthly_salary_eur", eur),
                     ("Loan people with your income carried", "at_your_income.loan_eur", eur),
                     ("Home price", "deposit.price_eur", eur), ("Deposit and costs needed", "deposit.total_needed_eur", eur),
                     ("You have", "deposit.you_have_eur", eur), ("Still to save", "deposit.still_to_save_eur", eur)])
    guides = [f"official guide: {u}" for u in _get(o, "asp.sources") or []]
    price = a.get("price_eur")
    step = f"Checked a {eur(price)} first home against your income" if price else "Estimated how ready you are for a first home"
    return step, figs, [TX, MORTGAGES] + guides


def explore(a, o):
    unit = eur if o.get("unit") == "€" else (lambda v: f"{v:g}")
    figs = _figs(o, [("Total", "totals.value", unit), ("Payments", "totals.transactions", str)])
    top = _get(o, "totals.highest")
    if isinstance(top, dict) and isinstance(top.get("value"), (int, float)):
        figs.append({"label": f"Highest: {top.get('label')}", "value": unit(top["value"])})
    title = o.get("title") or "your data"
    return f"Charted {title[0].lower() + title[1:]} for {_period(a)}", figs, [TX]


def spender_profile(a, o):
    figs = _figs(o, [("Share that goes to fun & extras", "everyday_share_pct", pct),
                     ("Savings rate", "savings_rate_pct", pct)])
    for d in (_get(o, "peers.biggest_differences") or [])[:2]:
        figs.append({"label": f"{d['category']}: you vs peers", "value": f"{eur(d['you_per_month_eur'])} vs "
                                                                          f"{eur(d['peers_median_eur'])}"})
    fi = o.get("finland_official") or {}
    for c in (fi.get("comparison") or [])[:1]:
        figs.append({"label": f"{c['category']}: you vs Finnish households", "value": f"{eur(c['you_per_month_eur'])} vs "
                                                                                     f"{eur(c['finnish_households_eur'])}"})
    peers = _get(o, "peers.group")
    src = [TX] + ([f"{peers} (challenge data)"] if peers else []) + ([STATS_FI] if fi else [])
    return "Compared your spending with people your age", figs, src


def regular_payments(a, o):
    figs = [{"label": x["name"], "value": f"{eur(x['per_month_eur'])} a month"}
            for x in o.get("regular_payments", []) if x.get("status") == "active"][:4]
    figs += _figs(o, [("Going out per year", "outgoing_per_year_eur", eur), ("Subscriptions found", "subscriptions_found", str)])
    return "Found the payments that come back every month", figs, [TX]


def full_picture(a, o):
    figs = _figs(o, [("All balances", "total_balances_eur", eur), ("Card debt", "card_debt_eur", eur),
                     ("Net position", "net_position_eur", eur)])
    src = [TX] + (["your linked Demo Bank (PSD2)"] if o.get("linked") else [])
    return "Added up your accounts across banks", figs, src


def money_guide(a, o):
    topic = (o.get("topic") or a.get("topic") or "").replace("-", " ")
    return f"Looked up the guide on {topic}", [], [f"official guide: {u}" for u in o.get("sources", [])]


def portfolio(a, o):
    figs = _figs(o, [("Put in so far", "invested_total_eur", eur), ("Taken out", "withdrawn_total_eur", eur),
                     ("Net put in", "net_put_in_eur", eur), ("Monthly plan", "monthly_plan_in_data.amount_eur", eur)])
    src = [TX] + ([PRICES] if o.get("if_your_deposits_were_in_each_fund") else [])
    return "Added up what you have invested", figs, src


def upcoming_bills(a, o):
    figs = [{"label": b["name"], "value": eur(b["amount_eur"])} for b in o[:5] if isinstance(b.get("amount_eur"), (int, float))]
    return f"Checked the bills due in the next {a.get('days', 14)} days", figs, [TX]


EXPLAIN = {"get_safe_to_spend": safe_to_spend, "get_spending": spending, "get_baskets": baskets,
           "simulate_cut": simulate_cut, "can_i_afford": afford, "get_safe_to_save": safe_to_save,
           "get_home_readiness": home_readiness, "explore_my_data": explore, "get_spender_profile": spender_profile,
           "get_regular_payments": regular_payments, "get_full_picture": full_picture, "get_money_guide": money_guide,
           "get_portfolio": portfolio, "get_upcoming_bills": upcoming_bills}

def people_like_me(a, o):
    g = o.get("group") or {}
    figs = _figs(o, [("Typical month", "typical_month.total.median_eur", eur)])
    you = next((r for r in (_get(o, "you.comparison") or []) if r.get("key") == "total"), None)
    if you:
        figs.append({"label": "Your month", "value": eur(you["you_per_month_eur"])})
    figs += _figs(o, [("Customers in the group", "group.customers", str)])
    src = ([TX] if you else []) + ["averages of at least 30 S-Pankki customers (challenge data)"]
    return f"Looked up how {g.get('who', 'people like you')} live", figs, src


EXPLAIN["get_people_like_me"] = people_like_me


def save_more(a, o):
    figs = []
    for i in o.get("ideas") or []:
        if i.get("idea") == "biggest_lever":
            figs.append({"label": f"20% less on {i['in_words']}, a year", "value": eur(i["saving_per_year_eur"])})
        elif i.get("idea") == "move_what_built_up":
            figs.append({"label": "Built up beyond the pay cycle", "value": eur(i["amount_eur"])})
        elif i.get("idea") == "pay_yourself_first":
            figs.append({"label": "Steady surplus a month", "value": eur(i["per_month_eur"])})
        elif i.get("idea") == "review_payment":
            k = i.get("number_to_say", "per_month_eur")
            figs.append({"label": f"{i['name']}, {'a year' if k == 'per_year_eur' else 'a month'}", "value": eur(i[k])})
    return "Looked for where you could save more", figs, [TX]


EXPLAIN["get_save_more_ideas"] = save_more

STEPS = {"list_funds": "Looked up the demo funds", "get_goals": "Looked up your savings goals",
         "create_goal": "Made a savings plan", "create_payment_link": "Prepared a payment for your PIN",
         "create_investment_link": "Prepared an investment for your PIN",
         "create_saving_link": "Prepared a saving for your PIN",
         "create_pause_investing_link": "Prepared pausing your monthly investing for your PIN"}


def generic(name: str, a: dict, o) -> tuple[str, list, list]:
    """Any other tool: its top-level numbers, labelled from their field names."""
    figs = []
    for k, v in (o.items() if isinstance(o, dict) else []):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and len(figs) < 5:
            label = k.removesuffix("_eur").removesuffix("_pct").replace("_", " ").capitalize()
            figs.append({"label": label, "value": eur(v) if k.endswith("_eur") else pct(v) if k.endswith("_pct") else f"{v:g}"})
    src = ["S-Pankki demo funds"] if name == "list_funds" else [TX]
    return STEPS.get(name, "Ran " + name.replace("_", " ")), figs, src


def explain(name: str, args: dict, out) -> dict:
    """One trace step: {tool, args, step, figures: [{label, value}], sources}. Never fails a reply."""
    args = args or {}
    try:
        fn = EXPLAIN.get(name)
        step, figs, src = fn(args, out) if fn else generic(name, args, out)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        step, figs, src = generic(name, args, out)
    return {"tool": name, "args": args, "step": step, "figures": figs, "sources": src}


def check_line(checked: int, unmatched: int, rewritten: bool) -> str:
    """The number guard's verdict in plain words."""
    if not checked:
        line = "No figures to check in this answer"
    elif unmatched:
        line = f"Only {checked - unmatched} of {checked} figures matched the calculations"
    else:
        line = f"{checked} of {checked} figures checked against the calculations"
    return line + (" (a first draft was rewritten to fix a figure)" if rewritten else "")
