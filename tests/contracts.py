"""Shape checks for the Oiva API contracts (docs/superpowers/specs/2026-11-14-oiva-wealth-and-voice-design.md).

Used on the S-mobiili sample fixtures now, and on the live /api/spend, /api/budget, /api/compare and /api/recurring
responses once they exist: check_spend(client.get("/api/spend").json()).
Each check raises AssertionError naming the first missing key.
"""

FIGURES = {"income", "rent_share", "savings_rate", "restaurants", "shopping", "buffer_months"}


def has(d, keys, where):
    assert isinstance(d, dict), f"{where}: expected an object"
    for k in keys:
        assert k in d, f"{where}: missing '{k}'"


def check_spend(d):
    has(d, ["month", "total_eur", "prev_total_eur", "income_eur", "budget_total_eur", "pace", "categories", "daily",
            "bills", "as_of", "source"], "spend")
    has(d["pace"], ["expected_by_today_eur", "spent_by_today_eur", "status"], "spend.pace")
    assert d["pace"]["status"] in ("on_track", "over", "under"), "spend.pace.status"
    for c in d["categories"]:
        has(c, ["category", "label", "basket", "spent_eur", "prev_eur", "budget_eur", "pct_of_budget"], "spend.categories[]")
    for x in d["daily"]:
        has(x, ["day", "cumulative_eur"], "spend.daily[]")
    for b in d["bills"]:
        has(b, ["name", "date", "amount_eur"], "spend.bills[]")


def check_budget(d):
    has(d, ["month", "items", "presets", "as_of", "source"], "budget")
    for i in d["items"]:
        has(i, ["category", "limit_eur", "source", "spent_eur", "left_eur", "left_per_day_eur", "status"], "budget.items[]")
    has(d["presets"], ["peers", "helsinki", "finland"], "budget.presets")
    has(d["presets"]["peers"], ["cohort", "per_category"], "budget.presets.peers")
    has(d["presets"]["peers"]["cohort"], ["age_band", "city", "n"], "budget.presets.peers.cohort")
    has(d["presets"]["helsinki"], ["n", "per_category"], "budget.presets.helsinki")
    has(d["presets"]["finland"], ["year", "per_person", "per_category"], "budget.presets.finland")


def check_compare(d):
    has(d, ["cohort", "figures", "as_of", "source"], "compare")
    has(d["cohort"], ["age_band", "city", "n", "from", "to"], "compare.cohort")
    for f in d["figures"]:
        has(f, ["id", "label", "unit", "you", "median", "p25", "p75", "percentile", "better", "sentence"], "compare.figures[]")
        assert f["id"] in FIGURES, f"compare.figures[]: unknown id '{f['id']}'"
        assert f["unit"] in ("eur", "pct", "months"), f"compare.figures[]: unit '{f['unit']}'"


def check_recurring(d):
    """Engine.commitments() as returned by /api/recurring."""
    has(d, ["regular_payments", "outgoing_per_month_eur", "bills_per_month_eur", "income_per_month_eur",
            "subscriptions_found"], "recurring")
    for p in d["regular_payments"]:
        has(p, ["id", "name", "direction", "status", "per_month_eur", "next_expected", "trend"], "recurring.regular_payments[]")
