import calendar
from datetime import date, timedelta

from backend.application.report_calendar import reporting_weeks


def test_three_day_boundary_is_separate_and_two_days_merge() -> None:
    assert [w.label for w in reporting_weeks(2026, 6)] == ["01–07", "08–14", "15–21", "22–30"]
    assert [w.label for w in reporting_weeks(2026, 9)] == [
        "01–06",
        "07–13",
        "14–20",
        "21–27",
        "28–30",
    ]
    assert [w.label for w in reporting_weeks(2026, 5)] == [
        "01–03",
        "04–10",
        "11–17",
        "18–24",
        "25–31",
    ]
    assert [w.label for w in reporting_weeks(2026, 8)] == ["01–09", "10–16", "17–23", "24–31"]


def test_calendar_partitions_every_month_without_loss_or_overlap() -> None:
    for year in range(2000, 2400):
        for month in range(1, 13):
            weeks = reporting_weeks(year, month)
            assert len(weeks) in (4, 5)
            assert weeks[0].start == date(year, month, 1)
            assert weeks[-1].end.day == calendar.monthrange(year, month)[1]
            for index, week in enumerate(weeks):
                assert (week.end - week.start).days + 1 >= 3
                if index:
                    assert week.start == weeks[index - 1].end + timedelta(days=1)
                    assert week.start.weekday() == 0
                if index < len(weeks) - 1:
                    assert week.end.weekday() == 6
