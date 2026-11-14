"""The three mentor questions on every customer in the S-Pankki dataset, offline (no model calls).

  "What kind of spender am I?"        Engine.spender_profile (with its peer group)
  "What can I safely spend this week?" Engine.safe_to_spend
  "How could I save more?"             Engine.save_more, Engine.safe_to_save, Engine.simulate_cut

Usage: python scripts/fleet_check_mentor.py [--data-dir DIR] [--sample N] [--jobs J] [--cache FILE] [--label NAME]
Reads the transactions CSV once (same loader as scripts/fleet_check.py), builds an Engine per customer on the demo
date, records exceptions and every answer that would be wrong or impossible to phrase honestly, and writes the
issue counts to docs/fleet-check-mentor.json under --label ("before" / "after"), keeping the other label's counts.
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.engine import BASKETS, Engine  # noqa: E402
from app.peers import MIN_GROUP  # noqa: E402
from scripts.fleet_check import TODAY, load, load_ages, numbers, signature  # noqa: E402

WEEK_MAX_EUR = 5000  # a "safe this week" above this is not a sensible answer for a personal current account
EVERYDAY = BASKETS["everyday"]["categories"]


def mentor_calls(e: Engine) -> list:
    """(name, function) in the order the agent would make them for the three questions."""
    def cut(e):
        cats = [c for c in EVERYDAY if e.monthly_avg(c, 3) > 0] or ["Restaurants"]
        return e.simulate_cut(max(cats, key=lambda c: e.monthly_avg(c, 3)), 20)
    return [("spender_profile", lambda e: e.spender_profile()), ("safe_to_spend", lambda e: e.safe_to_spend()),
            ("safe_to_save", lambda e: e.safe_to_save()), ("save_more", lambda e: e.save_more()),
            ("simulate_cut", cut)]


REQUIRED = {
    "spender_profile": ["everyday_share_pct", "traits", "peers", "baskets"],
    "safe_to_spend": ["balance_eur", "safe_to_spend_until_payday_eur", "safe_per_day_eur", "safe_this_week_eur",
                      "horizon_days", "next_payday", "basis"],
    "safe_to_save": ["now_max_eur", "now_keeping_usual_fun_eur", "monthly"],
    "save_more": ["ideas", "next_step", "monthly_picture"],
    "simulate_cut": ["current_per_month_eur", "saving_per_month_eur", "saving_per_year_eur"],
}


def swept(e: Engine) -> dict:
    """Salary sweeps (salary moved on to another account the day it lands), straight from the rows: per full month
    over the 6 months safe to spend uses and the 3 months the monthly flow uses."""
    out = {}
    for months in (3, 6):
        d0, d1 = e._full_months(months)
        b = e.base[(e.base["date"] >= d0) & (e.base["date"] <= d1)]
        out[months] = -b[b["transaction_type"] == "salary_sweep"]["tx_amount_eur"].sum() / months
    return out


def mentor_issues(out: dict, age: int | None = None, sweep: dict | None = None,
                  cycle_income: float | None = None) -> list[str]:
    """Each string is one issue class. An answer that is odd but carries a flag the agent can phrase honestly
    (e.g. no payday + not_enough_data) is not an issue."""
    bad = []
    sweep = sweep or {3: 0.0, 6: 0.0}
    for name, res in out.items():
        for path, v in numbers(res):
            if not math.isfinite(v):
                bad.append(f"{name}: NaN/inf")
                break
        for k in REQUIRED.get(name, []):
            if k not in res or res[k] is None:
                bad.append(f"missing: {name}.{k}")
    s, ss, sm, sp, cut = (out.get(k) for k in ("safe_to_spend", "safe_to_save", "save_more", "spender_profile",
                                               "simulate_cut"))
    if s:
        bal, free, week = s["balance_eur"], s["safe_to_spend_until_payday_eur"], s["safe_this_week_eur"]
        if free < 0 or s["safe_per_day_eur"] < 0 or week < 0:
            bad.append("safe_to_spend: negative amount presented as spendable")
        if week > WEEK_MAX_EUR:
            bad.append(f"safe_to_spend: safe this week above {WEEK_MAX_EUR} EUR")
        if free > max(0.0, bal) + 0.01:
            bad.append("safe_to_spend: safe to spend above the balance")
        if week > max(0.0, free) + 0.01:
            bad.append("safe_to_spend: this week above the whole horizon")
        if not s["next_payday"].get("date") and "not_enough_data" not in s:
            bad.append("safe_to_spend: no payday and no flag")
        if not s["next_payday"].get("date") and "next_7_days" not in s:
            bad.append("safe_to_spend: no payday and no 7-day estimate from balance minus bills due")
        if sweep[6] > 1 and cycle_income is not None and s["income_per_month_eur"] > cycle_income - sweep[6] + 1:
            bad.append("safe_to_spend: salary swept to another account counted as this cycle's income")
        if bal < 0 and (free > 0 or not s.get("overdrawn")):
            bad.append("safe_to_spend: overdrawn without the overdrawn flag")
        if s.get("status") == "nothing_free" and free > 0:
            bad.append("safe_to_spend: status says nothing free but the amount is positive")
    if ss:
        bal = (s or {}).get("balance_eur", 0.0)
        if ss["now_max_eur"] < 0 or ss["monthly"]["sustainable_saving_per_month_eur"] < 0:
            bad.append("safe_to_save: negative amount")
        if ss["now_max_eur"] > max(0.0, bal) + 0.01:
            bad.append("safe_to_save: more than the balance")
        if ss["now_keeping_usual_fun_eur"] > ss["now_max_eur"] + 0.01:
            bad.append("safe_to_save: keeping fun is more than the maximum")
        inc = ss["monthly"]["income_per_month_eur"]
        if ss["monthly"]["sustainable_saving_per_month_eur"] > max(0.0, inc) + 0.01:
            bad.append("safe_to_save: monthly saving above monthly income")
        m = ss["monthly"]
        if sweep[3] > 1 and m["surplus_per_month_eur"] > m["income_per_month_eur"] - m["spending_per_month_eur"]                 - m["investing_per_month_eur"] - sweep[3] + 1:
            bad.append("safe_to_save: salary swept to another account counted as monthly surplus")
    if sm:
        ideas = sm["ideas"]
        if not ideas:
            bad.append("save_more: no idea at all")
        lever = next((i for i in ideas if i["idea"] == "biggest_lever"), None)
        if not lever and not any(i["idea"] in ("no_lever", "nothing_to_cut") for i in ideas) \
                and "no_lever_reason" not in sm:
            bad.append("save_more: no biggest lever and no reason")
        inc_y = sm["monthly_picture"]["income_per_month_eur"] * 12
        for i in ideas:
            yearly = i.get("saving_per_year_eur") or (i.get("per_month_eur") or 0) * 12 \
                if i["idea"] in ("biggest_lever", "pay_yourself_first") else 0
            if inc_y > 0 and yearly > inc_y + 0.01:  # with no income at all, a cut is still a real saving
                bad.append(f"save_more: {i['idea']} yearly saving above yearly income")
            for k, v in i.items():
                if k.endswith("_eur") and isinstance(v, (int, float)) and v < 0:
                    bad.append(f"save_more: negative {i['idea']}.{k}")
        if (s or {}).get("balance_eur", 0) < 0 and any(i["idea"] == "pay_yourself_first" for i in ideas):
            bad.append("save_more: pay yourself first while overdrawn")
        if not lever and "20% lighter" in sm["next_step"]:
            bad.append("save_more: next step offers a cut although there is no lever")
        if ideas and len({i["idea"] for i in ideas}) < len(ideas):
            bad.append("save_more: the same idea twice")
    if cut:
        if cut["saving_per_month_eur"] < 0 or cut["saving_per_month_eur"] > cut["current_per_month_eur"] + 0.01:
            bad.append("simulate_cut: saving outside 0..current")
    if sp:
        p = sp["peers"]
        if p.get("group") and (p.get("customers") or 0) < MIN_GROUP:
            bad.append(f"spender_profile: peer group under {MIN_GROUP} customers used")
        if p.get("group") is None and "no_peers_reason" not in p:
            bad.append("spender_profile: no peer group and no reason")
        if age is not None and p.get("group") and p.get("age_band"):
            band = p["age_band"]
            lo, hi = (int(band[:-1]), 200) if band.endswith("+") else map(int, band.split("-"))
            if not lo <= age <= hi:
                bad.append("spender_profile: peers from another age band")
        if not sp["traits"]:
            bad.append("spender_profile: no trait, nothing to say what kind of spender")
        if sp["restaurant_visits_last_3_months"] < 4 and any(t.startswith("Weekend") for t in sp["traits"]):
            bad.append("spender_profile: weekend trait from fewer than 4 restaurant visits")
        rate = sp["savings_rate_pct"]
        if rate is not None and not -100 <= rate <= 100:
            bad.append("spender_profile: savings rate outside -100..100%")
        if not 0 <= sp["everyday_share_pct"] <= 100:
            bad.append("spender_profile: everyday share outside 0..100%")
    return bad


def facts(out: dict) -> dict:
    """Conditions that are not bugs but should be phrased differently (counted, not flagged)."""
    s, sm, sp = out.get("safe_to_spend") or {}, out.get("save_more") or {}, out.get("spender_profile") or {}
    return {
        "no_payday": bool(s and not s["next_payday"].get("date")),
        "overdrawn": bool(s and s["balance_eur"] < 0),
        "nothing_free": bool(s and s["safe_to_spend_until_payday_eur"] <= 0),
        "status": s.get("status"),
        "kind": (sp.get("kind") or {}).get("key"),
        "no_lever": bool(sm and not any(i["idea"] == "biggest_lever" for i in sm["ideas"])),
        "ideas": [i["idea"] for i in sm.get("ideas", [])],
        "safe_this_week_eur": s.get("safe_this_week_eur"),
        "peer_customers": (sp.get("peers") or {}).get("customers"),
    }


def check_customer(cid: str, rows, state_dir: str, age: int | None = None) -> dict:
    rec = {"customer_id": cid, "exceptions": [], "issues": []}
    try:
        e = Engine(today=TODAY, state_path=Path(state_dir) / f"{cid}.json", frame=rows, age=age)
    except Exception as exc:
        rec["exceptions"].append({"method": "init", "class": signature("init", exc),
                                  "trace": traceback.format_exc(limit=-3)[-600:]})
        return rec
    out = {}
    for name, fn in mentor_calls(e):
        try:
            out[name] = fn(e)
        except Exception as exc:
            rec["exceptions"].append({"method": name, "class": signature(name, exc),
                                      "trace": traceback.format_exc(limit=-3)[-600:]})
    try:
        rec["issues"] = sorted(set(mentor_issues(out, age, swept(e), e.monthly_income(6))))
    except Exception as exc:  # a field the check needs is missing: that is itself an issue
        rec["issues"] = [f"check crashed: {signature('check', exc)}"]
    try:
        rec["facts"] = facts(out)
    except Exception:
        rec["facts"] = {}
    return rec


def _work(args):
    chunk, state_dir = args
    return [check_customer(cid, rows, state_dir, age) for cid, rows, age in chunk]


def groups_for(data_dir: Path, cache: Path | None, sample: int, seed: int):
    if cache and cache.exists():
        groups = pickle.loads(cache.read_bytes())
    else:
        df = load(data_dir)
        ages = load_ages(data_dir)
        groups = [(str(cid), g.drop(columns="_ts").astype({c: object for c in ("customer_id", "category",
                                                                                 "transaction_type")}),
                   ages.get(str(cid))) for cid, g in df.groupby("customer_id", observed=True, sort=True)]
        if cache:
            cache.write_bytes(pickle.dumps(groups, protocol=pickle.HIGHEST_PROTOCOL))
    if sample:
        import random
        groups = random.Random(seed).sample(groups, min(sample, len(groups)))
    return groups


def summarise(recs: list[dict]) -> dict:
    classes = defaultdict(list)
    for r in recs:
        for x in r["exceptions"]:
            classes["exception: " + x["class"]].append(r["customer_id"])
        for i in r["issues"]:
            classes[i].append(r["customer_id"])
    f = [r.get("facts") or {} for r in recs]
    count = lambda k: sum(1 for x in f if x.get(k))  # noqa: E731
    statuses, kinds, ideas = defaultdict(int), defaultdict(int), defaultdict(int)
    for x in f:
        statuses[str(x.get("status"))] += 1
        kinds[str(x.get("kind"))] += 1
        for i in x.get("ideas", []):
            ideas[i] += 1
    weeks = sorted(x["safe_this_week_eur"] for x in f if x.get("safe_this_week_eur") is not None)
    peers = [x["peer_customers"] for x in f if x.get("peer_customers")]
    return {
        "customers": len(recs),
        "customers_with_exceptions": sum(1 for r in recs if r["exceptions"]),
        "customers_with_issues": sum(1 for r in recs if r["issues"] or r["exceptions"]),
        "issues": {k: {"customers": len(v), "examples": v[:5]}
                   for k, v in sorted(classes.items(), key=lambda kv: (-len(kv[1]), kv[0]))},
        "edge_cases_phrased_honestly": {"no_regular_payday": count("no_payday"), "overdrawn": count("overdrawn"),
                                        "nothing_free_to_spend": count("nothing_free"),
                                        "no_everyday_lever": count("no_lever")},
        "safe_to_spend_status": dict(sorted(statuses.items())),
        "spender_kind": dict(sorted(kinds.items())),
        "save_more_ideas": dict(sorted(ideas.items())),
        "safe_this_week_eur": {"median": weeks[len(weeks) // 2], "p95": weeks[int(len(weeks) * 0.95)],
                               "max": weeks[-1]} if weeks else None,
        "smallest_peer_group_used": min(peers) if peers else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT.parent / "ref" / "Junction-2026-S-pankki"))
    ap.add_argument("--sample", type=int, default=0, help="check N random customers (0 = all)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--cache", default="", help="pickle of the per-customer frames, to skip the CSV next time")
    ap.add_argument("--label", default="after", help="key in the output json (before / after)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "fleet-check-mentor.json"))
    ap.add_argument("--dump", default="", help="also write every customer's record here (not committed)")
    a = ap.parse_args()
    t0 = time.perf_counter()
    groups = groups_for(Path(a.data_dir), Path(a.cache) if a.cache else None, a.sample, a.seed)
    print(f"{len(groups):,} customers ready in {time.perf_counter() - t0:.0f}s", flush=True)
    with tempfile.TemporaryDirectory() as state_dir:
        chunks = [(groups[i::a.jobs * 8], state_dir) for i in range(a.jobs * 8)]
        recs = []
        if a.jobs > 1:
            with ProcessPoolExecutor(a.jobs) as pool:
                for part in pool.map(_work, chunks):
                    recs += part
        else:
            for c in chunks:
                recs += _work(c)
    recs.sort(key=lambda r: r["customer_id"])
    summary = {"as_of": TODAY.isoformat(), "sample": a.sample or None,
               "wall_seconds": round(time.perf_counter() - t0, 1), **summarise(recs)}
    out = Path(a.out)
    doc = json.loads(out.read_text()) if out.exists() else {}
    doc.setdefault("about", "The three mentor questions (spender_profile, safe_to_spend, save_more + safe_to_save + "
                            "simulate_cut) run on all customers by scripts/fleet_check_mentor.py. Counts are "
                            "customers per issue class.")
    doc[a.label] = summary
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    if a.dump:
        Path(a.dump).write_text(json.dumps(recs, default=str))
    print(json.dumps({k: summary[k] for k in ("customers", "customers_with_exceptions", "customers_with_issues")}))
    for k, v in summary["issues"].items():
        print(f"  {v['customers']:5d}  {k}   e.g. {', '.join(v['examples'][:3])}")
    for k in ("edge_cases_phrased_honestly", "safe_to_spend_status", "spender_kind", "save_more_ideas",
              "safe_this_week_eur", "smallest_peer_group_used"):
        print(f"{k}: {summary[k]}")
    print(f"wrote {out} [{a.label}] in {summary['wall_seconds']}s")


if __name__ == "__main__":
    main()
