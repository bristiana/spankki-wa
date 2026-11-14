"""The S-mobiili screens' sample data matches the API contracts in
docs/superpowers/specs/2026-11-14-oiva-wealth-and-voice-design.md. The same checkers can run on the live API."""
import json
from pathlib import Path

import pytest

from tests.contracts import check_budget, check_compare, check_recurring, check_spend

FIX = Path(__file__).resolve().parents[1] / "app" / "static" / "fixtures"
CHECKS = {"spend": check_spend, "budget": check_budget, "compare": check_compare, "recurring": check_recurring}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_fixtures_match_contracts(name):
    data = json.loads((FIX / f"{name}.json").read_text())
    CHECKS[name](data)
    assert data.get("fixture") is True  # the screens label sample data


def test_compare_has_the_five_figures():
    data = json.loads((FIX / "compare.json").read_text())
    ids = {f["id"] for f in data["figures"]}
    assert ids == {"income", "rent_share", "savings_rate", "restaurants", "shopping", "buffer_months"}
    for f in data["figures"]:
        assert {"you", "median", "p25", "p75", "percentile", "unit", "better", "sentence"} <= set(f)


def test_samples_cover_the_awkward_cases():
    compare = json.loads((FIX / "compare.json").read_text())
    assert next(f for f in compare["figures"] if f["id"] == "savings_rate")["you"] < 0
    budget = json.loads((FIX / "budget.json").read_text())
    assert any(i["limit_eur"] is None for i in budget["items"])


def test_checker_rejects_missing_field():
    with pytest.raises(AssertionError, match="month"):
        check_spend({})
