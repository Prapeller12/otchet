"""Calendar reporting weeks, with short month-boundary fragments merged inward."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ReportingWeek:
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.start.day:02d}–{self.end.day:02d}"


def reporting_weeks(year: int, month: int) -> tuple[ReportingWeek, ...]:
    """Use Monday–Sunday weeks; retain boundary fragments only at >= 3 days."""
    last = date(year, month, calendar.monthrange(year, month)[1])
    current = date(year, month, 1)
    weeks = []
    while current <= last:
        end = min(current + timedelta(days=6 - current.weekday()), last)
        weeks.append(ReportingWeek(current, end))
        current = end + timedelta(days=1)
    if (weeks[0].end - weeks[0].start).days + 1 < 3:
        weeks[:2] = [ReportingWeek(weeks[0].start, weeks[1].end)]
    if (weeks[-1].end - weeks[-1].start).days + 1 < 3:
        weeks[-2:] = [ReportingWeek(weeks[-2].start, weeks[-1].end)]
    return tuple(weeks)
