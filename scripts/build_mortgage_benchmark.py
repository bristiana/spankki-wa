"""Mortgage benchmark from the S-Pankki challenge dataset (200 mortgage applications, synthetic but modelled on
real S-Bank data). Writes the small data/mortgage_benchmark.json that Engine.home_readiness reads.

  python scripts/build_mortgage_benchmark.py [--data-dir DIR] [--skip-salaries]

--skip-salaries leaves out the check of required income against the applicants' real salary credits, which needs
a pass over the 346 MB transactions file.
"""
import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "mortgage_benchmark.json"
ONE_OFF = ["transfer_tax", "valuation_fee", "bank_fee"]  # paid once when buying; renovation is a choice
AGE_BANDS = [(30, 39), (40, 49), (50, 120)]


def q(s: pd.Series, digits: int = 0) -> dict:
    """p25 / median / p75 of a series."""
    return {k: round(float(s.quantile(p)), digits) for k, p in (("p25", .25), ("median", .5), ("p75", .75))}


def build(d: Path, salaries: bool = True) -> dict:
    final = pd.read_csv(d / "mtg_tx_customer_link_and_final_mtg_infio.csv", encoding="utf-8-sig")
    hist = pd.read_csv(d / "mtg_negotiation_history.csv", encoding="utf-8-sig")
    cust = pd.read_csv(d / "tx_customers_junction.csv", encoding="utf-8-sig")

    f = final.merge(cust[["customer_id", "customer_age"]], on="customer_id", how="left")
    f["equity_share"] = f.final_equity_amount_eur / f.final_property_price_eur
    f["income_per_loan"] = f.required_monthly_income_eur / f.final_requested_loan_eur
    f["collateral_share"] = f.final_collateral_value_eur / f.final_property_price_eur
    ok = f[f.final_decision == "approved"]
    rejected = f[f.final_decision == "rejected"]

    hist["ts"] = pd.to_datetime(hist.timestamp, format="%d%b%y:%H:%M:%S")
    decision = hist[hist.event_type == "loan_decision"].merge(
        final[["mortgage_customer_id", "final_decision"]], on="mortgage_customer_id")
    # Expenses: the latest amount of each type per application (some are recorded again when the price changes).
    exp = (hist[hist.event_type == "expense_recorded"].sort_values("ts")
           .groupby(["mortgage_customer_id", "expense_type"]).last().reset_index())
    exp["share"] = exp.expense_amount / exp.property_price_estimate
    expenses = {t: {"applications": int(len(g)), "median_eur": round(float(g.expense_amount.median())),
                    "median_share_of_price_pct": round(float(g.share.median()) * 100, 2)}
                for t, g in exp.groupby("expense_type")}

    reasons = rejected.rejection_reason.value_counts()
    out = {
        "source": "S-Pankki Junction 2026 challenge dataset: 200 mortgage applications and their negotiation history "
                  "(synthetic, modelled on real S-Bank data)",
        "applications": int(len(f)),
        "approved": int(len(ok)),
        "approval_rate_pct": round(len(ok) / len(f) * 100, 1),
        "approved_applications": {
            "property_price_eur": q(ok.final_property_price_eur),
            "loan_eur": q(ok.final_requested_loan_eur),
            "equity_eur": q(ok.final_equity_amount_eur),
            "equity_share_pct": q(ok.equity_share * 100, 1),
            "loan_to_value_pct": q(ok.lfinal_loan_to_value * 100, 1),
            "collateral_value_share_of_price_pct": q(ok.collateral_share * 100, 1),
            "required_monthly_income_eur": q(ok.required_monthly_income_eur),
            "age": {"min": int(ok.customer_age.min()), "median": int(ok.customer_age.median()),
                    "max": int(ok.customer_age.max())},
        },
        # The same for every application in the data: required monthly income = loan x this ratio.
        "required_income_per_loan_eur": round(float(f.income_per_loan.median()), 6),
        "required_income_per_loan_spread": round(float(f.income_per_loan.max() - f.income_per_loan.min()), 9),
        "rejection_reasons": [{"reason": r, "count": int(c), "share_of_rejections_pct": round(c / len(rejected) * 100)}
                              for r, c in reasons.items()],
        "additional_collateral_requested_pct": round(
            hist[hist.event_type == "additional_collateral_requested"].mortgage_customer_id.nunique() / len(f) * 100),
        "expenses": expenses,
        "one_off_cost_types": ONE_OFF,
        "property_types": {t: {"applications": int(len(g)),
                               "approval_rate_pct": round((g.final_decision == "approved").mean() * 100),
                               "median_price_eur": round(float(g.property_price_estimate.median()))}
                           for t, g in decision.groupby("property_type")},
        "by_age": {},
    }
    for lo, hi in AGE_BANDS:
        g = f[(f.customer_age >= lo) & (f.customer_age <= hi)]
        a = g[g.final_decision == "approved"]
        out["by_age"][f"{lo}-{hi}" if hi < 120 else f"{lo}+"] = {
            "applications": int(len(g)), "approval_rate_pct": round(len(a) / len(g) * 100) if len(g) else None,
            "median_price_eur": round(float(a.final_property_price_eur.median())) if len(a) else None,
            "median_equity_share_pct": round(float(a.equity_share.median()) * 100, 1) if len(a) else None}

    if salaries:  # does "required income" match what these people really earn? (salary credits in the transactions)
        ids, parts = set(f.customer_id), []
        for ch in pd.read_csv(d / "tx_junction_transactions.csv", encoding="utf-8-sig", chunksize=1_000_000,
                              usecols=["customer_id", "tx_date", "tx_amount_eur", "transaction_type"]):
            parts.append(ch[ch.customer_id.isin(ids) & (ch.transaction_type == "salary")])
        s = pd.concat(parts)
        sal = s.groupby([s.customer_id, s.tx_date.str[:7]]).tx_amount_eur.sum().groupby(level=0).median()
        a = ok.assign(salary=ok.customer_id.map(sal)).dropna(subset=["salary"])
        out["approved_vs_real_salary"] = {
            "customers": int(len(a)),
            "monthly_salary_eur": q(a.salary),
            "required_income_over_salary": q(a.required_monthly_income_eur / a.salary, 2),
            "loan_over_monthly_salary": q(a.final_requested_loan_eur / a.salary, 1),
            "note": "Median monthly salary credited to the account over the dataset period"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT.parent / "ref" / "Junction-2026-S-pankki"))
    ap.add_argument("--skip-salaries", action="store_true")
    a = ap.parse_args()
    out = build(Path(a.data_dir), not a.skip_salaries)
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
