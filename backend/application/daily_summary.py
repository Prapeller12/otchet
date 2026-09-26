"""Read-only daily worksheet totals shared by the working field and exports."""

from __future__ import annotations

from typing import Any

from backend.application.monthly_report import aggregate

_COMPONENT_METRICS = ("WRK_DAILY_RECEIVED", "WRK_DAILY_USED", "WRK_DAILY_BALANCE")


def summarize_daily_rows(
    rows: list[dict[str, Any]], year: int, left_columns: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarize exact backend values, preserving empty cells and configured order.

    Input flows sum recorded quantities; calculated stock/cumulative rows retain the
    period-end value, as in the monthly publication snapshot. No browser arithmetic.
    """
    periods = [f"{year:04d}-{month:02d}" for month in range(1, 13)]
    summaries: list[dict[str, Any]] = []
    by_group: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for row in rows:
        cells = [
            cell
            for cell in row["cells"]
            if str(cell["coordinate"].get("operation_date", "")).startswith(f"{year:04d}-")
        ]
        calculated = any(cell["state"]["access"] == "calculated" for cell in cells)

        def total(selected: list[dict[str, Any]], calculated: bool = calculated) -> str:
            if any(cell["state"].get("persistence") == "error" for cell in selected):
                return ""
            return aggregate(selected, calculated)

        item = {
            "row_id": row["id"],
            "annual": total(cells),
            "monthly": [
                total(
                    [
                        cell
                        for cell in cells
                        if str(cell["coordinate"].get("operation_date", "")).startswith(period)
                    ]
                )
                for period in periods
            ],
            "through_month": [
                total(
                    [
                        cell
                        for cell in cells
                        if str(cell["coordinate"].get("operation_date", ""))[:7] <= period
                    ]
                )
                for period in periods
            ],
        }
        summaries.append(item)
        by_group.setdefault(row["group_id"], []).append((row, item))
    components = []
    for group_id, group in by_group.items():
        first = group[0][0]
        if first.get("category") != "PART":
            continue
        by_code = {row.get("metric_code"): (row, item) for row, item in group}
        if not all(code in by_code for code in _COMPONENT_METRICS):
            continue
        components.append(
            {
                "group_id": group_id,
                "party": first["left_values"].get(left_columns[0]["id"], "")
                if left_columns
                else "",
                "position": first["group_label"],
                "rows": [{"metric_code": code, **by_code[code][1]} for code in _COMPONENT_METRICS],
            }
        )
    return {"year": year, "periods": periods, "rows": summaries, "components": components}
