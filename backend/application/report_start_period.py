"""Beginning of a user-created report profile, independent of period closure."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from backend.application.report_cells import ReportCellValidationError


def require_period_at_or_after(period: str, start_period: str | None) -> None:
    if start_period is None:
        return
    selected = date.fromisoformat(period + "-01" if len(period) == 7 else period)
    beginning = date.fromisoformat(start_period)
    if selected < beginning:
        raise ReportCellValidationError(
            "Период раньше начала заготовки " + beginning.strftime("%m.%Y") + ". Данные не записаны"
        )


def require_presentation_at_or_after(
    presentation: Mapping[str, object], start_period: str | None
) -> None:
    if start_period is None:
        return
    for field in ("plans", "actuals"):
        values = presentation.get(field, {})
        if isinstance(values, Mapping):
            for period in values:
                require_period_at_or_after(str(period), start_period)
    for field in ("production_code_plans", "production_code_actuals"):
        values_by_code = presentation.get(field, {})
        if isinstance(values_by_code, Mapping):
            for values in values_by_code.values():
                if isinstance(values, Mapping):
                    for period in values:
                        require_period_at_or_after(str(period), start_period)
    codes = presentation.get("production_codes", [])
    if isinstance(codes, list):
        for code in codes:
            if isinstance(code, Mapping):
                require_presentation_at_or_after(code, start_period)
