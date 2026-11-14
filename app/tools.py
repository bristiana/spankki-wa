"""Claude tool definitions, each one a thin wrapper over the deterministic engine."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .engine import FUNDS, PERIODS, SPEND_CATEGORIES, Engine

GUIDE_PATH = Path(__file__).resolve().parent.parent / "content" / "finland-money-guide.md"


def load_guide(path: Path = GUIDE_PATH) -> dict[str, dict]:
    """content/finland-money-guide.md -> {topic: {text, sources, checked}}, one entry per '## topic' section."""
    guide = {}
    md = path.read_text(encoding="utf-8") if path.exists() else ""
    for part in re.split(r"^## ", md, flags=re.M)[1:]:
        topic, _, body = part.partition("\n")
        src = re.search(r"^Source: (.+)$", body, re.M)
        checked = re.search(r"^Checked: (.+)$", body, re.M)
        text = re.sub(r"^(Source|Checked): .*$", "", body, flags=re.M).strip()
        guide[topic.strip()] = {"text": text, "sources": src.group(1).split(" · ") if src else [],
                                "checked": checked.group(1).strip() if checked else None}
    return guide


GUIDE = load_guide()


def money_guide(topic: str) -> dict:
    """The guide section for a topic id (or a word in it, like 'asp' or 'credit')."""
    key = (topic or "").strip().lower().replace(" ", "-")
    hit = key if key in GUIDE else next((t for t in GUIDE if key and (key in t or t in key)), None)
    if not hit:
        raise ValueError(f"no guide topic {topic!r}; topics: {list(GUIDE)}")
    return {"topic": hit, **GUIDE[hit], "note": "General information, not personal advice. Cite the source link."}

PERIOD_PROP = {"type": "string", "enum": PERIODS,
               "description": "Time window. last_month = previous calendar month. last_3_months = the 3 full months before this one."}
DATE_PROP = {"type": ["string", "null"], "description": "Optional ISO date (YYYY-MM-DD) that overrides period."}

TOOLS = [
    {
        "name": "get_spending",
        "description": "Total spent in a category, a merchant type (taxis, fast food, parking...) or all spending over a "
                       "period, with number of payments, top places, a split by merchant type and the previous period for "
                       "comparison. Use for any 'how much did I spend on X' question. If found is false, say there were "
                       "no such payments and use category_by_merchant_type to say where that money went instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": ["string", "null"], "enum": SPEND_CATEGORIES + [None],
                             "description": "Category, or null for all spending. Shopping is Lifestyle. Ignored when "
                                            "merchant_type is set."},
                "merchant_type": {"type": ["string", "null"],
                                  "description": "A narrower kind of place, from the card's merchant code: e.g. taxi "
                                                 "(also uber, bolt), public transport, fuel, parking, fast food, coffee, "
                                                 "restaurant, bar, supermarket, cinema, events, recreation, pharmacy, "
                                                 "doctor, dentist, hotel, electronics, furniture. Null for a whole category."},
                "period": PERIOD_PROP,
                "start": DATE_PROP,
                "end": DATE_PROP,
            },
            "required": ["category", "period"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_baskets",
        "description": "Splits spending into the three baskets (mandatory & regular, mandatory & irregular, slips through "
                       "your fingers), with income, money invested and what was left over.",
        "input_schema": {"type": "object", "properties": {"period": PERIOD_PROP}, "required": ["period"],
                         "additionalProperties": False},
    },
    {
        "name": "get_upcoming_bills",
        "description": "Recurring payments coming up (rent, monthly investment) with bill_id, amount and due date, plus the "
                       "average utilities per month.",
        "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "description": "Look-ahead window in days, e.g. 14."}},
                         "required": ["days"], "additionalProperties": False},
    },
    {
        "name": "get_safe_to_spend",
        "description": "How much is safe to spend until next payday, per day and this week, after bills, usual needs and a "
                       "buffer for surprises. Also returns the current balance.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_safe_to_save",
        "description": "How much can go to savings now without touching bills before payday or the buffer for irregular "
                       "costs, and a sustainable monthly amount from the average monthly surplus. When there is no "
                       "surplus, where a cut could come from. Includes goal ids for create_saving_link.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_full_picture",
        "description": "All the customer's accounts across banks (S-Pankki plus any bank linked through PSD2): total "
                       "balances, credit card debt, net position, the next card bill and how many months savings could "
                       "cover the irregular basket. Tells you when no other bank is linked.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "can_i_afford",
        "description": "Whether a purchase fits this month's flexible budget and what is safe to spend until payday: "
                       "what is left after it, per day until payday, and how many days it pushes back each savings goal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number", "description": "Price of the purchase in euros."},
                "what": {"type": "string", "description": "What they want to buy, e.g. 'jacket'. Empty if not said."},
            },
            "required": ["amount_eur", "what"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simulate_cut",
        "description": "What-if: how much the user saves per month and year by cutting a category by a percentage, and what "
                       "that saving could grow to in each demo fund (a scenario, not a promise).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": SPEND_CATEGORIES},
                "cut_pct": {"type": "number", "description": "Percent to cut, 1-100."},
                "years": {"type": "integer", "description": "Years for the investment scenario, default 5."},
            },
            "required": ["category", "cut_pct", "years"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_spender_profile",
        "description": "What kind of spender the user is: traits, savings rate, and how they compare with other S-Pankki "
                       "customers of the same age group.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_funds",
        "description": "The demo investment funds with risk level and what they hold. Use before talking about investing.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_payment_link",
        "description": "Prepare paying an upcoming bill. Does NOT pay: the user gets a button and confirms in the bank. "
                       "Only call when the user asked to pay.",
        "input_schema": {"type": "object", "properties": {"bill_id": {"type": "string"}}, "required": ["bill_id"],
                         "additionalProperties": False},
    },
    {
        "name": "create_investment_link",
        "description": "Prepare an investment into a fund the USER chose. Does NOT invest: the user gets a button and "
                       "confirms in the bank. Only call after the user picked the fund and the amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fund_id": {"type": "string", "enum": [f["id"] for f in FUNDS]},
                "amount_eur": {"type": "number"},
                "monthly": {"type": "boolean", "description": "true = repeat every payday, false = one-off."},
            },
            "required": ["fund_id", "amount_eur", "monthly"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_subscriptions",
        "description": "Card subscriptions found from repeating charges: active ones (with next charge and monthly/yearly "
                       "cost), inactive ones (stopped charging) and price increases.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_stop_subscription_link",
        "description": "Prepare stopping future card payments to an active subscription. Does NOT stop it: the user "
                       "confirms with PIN. Only call when the user asked to stop or cancel it.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Subscription name"}},
                         "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "create_goal",
        "description": "Create a savings goal (e.g. a trip, a laptop, an emergency fund) and get a plan: monthly amounts, "
                       "how long each takes, and what a deadline needs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target_eur": {"type": "number"},
                "deadline": {"type": ["string", "null"], "description": "Optional ISO date YYYY-MM-DD"},
            },
            "required": ["name", "target_eur", "deadline"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_goals",
        "description": "The user's savings goals with progress and plans.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_saving_link",
        "description": "Prepare moving money from the current account into the savings pocket of a goal. Does NOT move "
                       "it: the user confirms with PIN. Only call when the user asked to save an amount.",
        "input_schema": {
            "type": "object",
            "properties": {"goal_id": {"type": "string"}, "amount_eur": {"type": "number"}},
            "required": ["goal_id", "amount_eur"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_portfolio",
        "description": "What the user has invested so far (from their account history), their monthly investing plan, "
                       "linked investment platforms and orders placed through the coach.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_money_guide",
        "description": "Checked facts about money in Finland, with the official source link: emergency fund, Kela housing "
                       "allowance, student aid, ASP first-home saving, equity savings account (osakesäästötili), capital "
                       "income tax, deposit guarantee, consumer credit and pay-later, S-Bank basics.",
        "input_schema": {"type": "object", "properties": {"topic": {"type": "string", "enum": list(GUIDE)}},
                         "required": ["topic"], "additionalProperties": False},
    },
    {
        "name": "get_home_readiness",
        "description": "'Ready for my first home?': from the user's salary and S-Pankki's mortgage application data, the "
                       "loan and price range people with that income carried, the deposit and one-off costs typically "
                       "needed, what they have, months to the deposit at several saving paces, what banks look at "
                       "(rejection reasons), and the ASP pointer. Educational estimate, not a loan offer.",
        "input_schema": {"type": "object", "properties": {
            "price_eur": {"type": ["number", "null"], "description": "A specific home price to check, or null."}},
            "required": ["price_eur"], "additionalProperties": False},
    },
]


def public_url() -> str:
    return os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")


def run_tool(engine: Engine, name: str, args: dict, buttons: list) -> str:
    """Run one tool. Returns JSON text; appends a URL button when a money move was prepared."""
    if name == "get_spending":
        out = engine.spending(args.get("category"), args.get("period") or "last_month", args.get("start"), args.get("end"),
                              args.get("merchant_type"))
    elif name == "get_baskets":
        out = engine.baskets(args.get("period") or "last_3_months")
    elif name == "get_upcoming_bills":
        out = engine.upcoming_bills(int(args.get("days") or 14))
    elif name == "get_safe_to_spend":
        out = engine.safe_to_spend()
    elif name == "get_safe_to_save":
        out = engine.safe_to_save()
    elif name == "get_full_picture":
        out = engine.full_picture()
    elif name == "can_i_afford":
        out = engine.afford(float(args["amount_eur"]), args.get("what") or "")
    elif name == "simulate_cut":
        out = engine.simulate_cut(args["category"], float(args["cut_pct"]), years=int(args.get("years") or 5))
    elif name == "get_spender_profile":
        out = engine.spender_profile()
    elif name == "list_funds":
        out = [{k: f[k] for k in ("id", "name", "risk", "risk_level", "what")} | {"scenario_return_pct": f["scenario_return"] * 100}
               for f in FUNDS]
    elif name == "create_payment_link":
        p = engine.create_payment(args["bill_id"])
        buttons.append({"label": f"Pay {p['amount']:.2f} €", "url": f"{public_url()}/pay/{p['pending_id']}"})
        out = {**p, "note": "A 'Pay' button is attached to your message. Do not paste a link."}
    elif name == "create_investment_link":
        p = engine.create_investment(float(args["amount_eur"]), args["fund_id"], bool(args["monthly"]))
        label = f"Invest {p['amount']:.0f} €" + ("/month" if p["monthly"] else "")
        buttons.append({"label": label, "url": f"{public_url()}/invest/{p['pending_id']}"})
        out = {**p, "note": "An 'Invest' button is attached to your message. Do not paste a link."}
    elif name == "get_subscriptions":
        out = engine.subscriptions()
    elif name == "create_stop_subscription_link":
        p = engine.create_stop_subscription(args["name"])
        buttons.append({"label": f"Stop {p['name']}", "url": f"{public_url()}/stop/{p['pending_id']}"})
        out = {**p, "note": "A 'Stop' button is attached to your message. Do not paste a link."}
    elif name == "create_goal":
        out = engine.create_goal(args["name"], float(args["target_eur"]), args.get("deadline"))
    elif name == "get_goals":
        out = engine.goals() or {"goals": [], "note": "No goals yet."}
    elif name == "create_saving_link":
        p = engine.create_saving(args["goal_id"], float(args["amount_eur"]))
        buttons.append({"label": f"Save {p['amount']:.0f} €", "url": f"{public_url()}/save/{p['pending_id']}"})
        out = {**p, "note": "A 'Save' button is attached to your message. Do not paste a link."}
    elif name == "get_portfolio":
        out = engine.portfolio()
    elif name == "get_money_guide":
        out = money_guide(args["topic"])
    elif name == "get_home_readiness":
        price = args.get("price_eur")
        out = engine.home_readiness(float(price) if price else None)
        out["asp"]["sources"] = GUIDE.get("asp", {}).get("sources", [])
    else:
        raise ValueError(f"unknown tool {name}")
    return json.dumps(out, default=str)
