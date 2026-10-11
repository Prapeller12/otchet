from __future__ import annotations

import pytest

from backend.application.report_start_period import (
    require_period_at_or_after,
    require_presentation_at_or_after,
)


@pytest.mark.parametrize("period", ["2027-09", "2027-09-01", "2027-09-30", "2028-01"])
def test_start_month_and_later_periods_are_allowed(period: str) -> None:
    require_period_at_or_after(period, "2027-09-01")


@pytest.mark.parametrize("period", ["2027-08", "2027-08-31", "2026-12"])
def test_earlier_periods_are_rejected(period: str) -> None:
    with pytest.raises(ValueError, match="раньше начала"):
        require_period_at_or_after(period, "2027-09-01")


def test_legacy_profiles_remain_unchanged() -> None:
    require_period_at_or_after("2020-01", None)
    require_presentation_at_or_after({"plans": {"2020-01": "1"}}, None)


@pytest.mark.parametrize(
    "patch",
    [
        {"plans": {"2027-08": "0"}},
        {"actuals": {"2027-08": ""}},
        {"production_code_actuals": {"A": {"2027-08": "1"}}},
        {"production_code_plans": {"A": {"2027-08": "1"}}},
        {"production_codes": [{"id": "A", "plans": {"2027-08": "1"}}]},
    ],
)
def test_all_numeric_header_paths_obey_the_initial_period(patch: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="раньше начала"):
        require_presentation_at_or_after(patch, "2027-09-01")


def test_text_and_current_month_changes_are_allowed() -> None:
    require_presentation_at_or_after(
        {
            "title": "Новая форма",
            "header": {"factory_name": "Завод"},
            "actuals": {"2027-09": "0"},
        },
        "2027-09-01",
    )
