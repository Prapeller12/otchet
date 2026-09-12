"""Monthly print snapshot derived from backend-calculated matrix values."""

from __future__ import annotations

import calendar
import hashlib
import json
from decimal import Decimal
from typing import Any


def aggregate(cells: list[dict[str, Any]], calculated: bool) -> str:
    if not cells:
        return ""
    if calculated:
        value = cells[-1]["value"]
        return str(value["quantity"]) if value["kind"] == "QUANTITY" else ""
    quantities = [
        Decimal(c["value"]["quantity"]) for c in cells if c["value"]["kind"] == "QUANTITY"
    ]
    return format(sum(quantities, Decimal(0)), "f") if quantities else ""


def monthly_snapshot(
    matrix: dict[str, Any], month: int, organization: str, week_start: object = None
) -> dict[str, Any]:
    year = matrix["year"]
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("Выберите месяц от 1 до 12")
    if not isinstance(year, int):
        raise ValueError("Год печатного отчёта не задан")
    period = f"{year:04d}-{month:02d}"
    if matrix.get("subsidiary"):
        indices = [i for i, c in enumerate(matrix["time_columns"]) if c["group_label"] == period]
        weeks = [
            c["id"]
            for c in matrix["time_columns"]
            if c["group_label"] == period and c.get("kind") == "USED"
        ]
        selected_week = ""
        as_of = f"{period}-{calendar.monthrange(year, month)[1]:02d}"
        if not matrix.get("head_site"):
            selected_week = weeks[-1] if week_start is None else str(week_start)
            if not isinstance(selected_week, str) or selected_week not in weeks:
                raise ValueError("Неделя остатка не принадлежит выбранному месяцу")
            from backend.application.report_calendar import reporting_weeks

            as_of = next(
                w.end.isoformat()
                for w in reporting_weeks(year, month)
                if w.start.isoformat() == selected_week
            )
        return {
            "as_of": as_of,
            "schema": 2,
            "report_type": matrix["report_type"],
            "subsidiary": True,
            "head_site": matrix.get("head_site", False),
            "organization_id": matrix["organization_id"],
            "organization": organization,
            "title": matrix["title"],
            "period": period,
            "revision": matrix["matrix_revision"],
            "plan": matrix["presentation"].get("plans", {}).get(period, ""),
            "actual": matrix["presentation"].get("actuals", {}).get(period, ""),
            "completion": matrix["presentation"].get("completion", {}).get(period, ""),
            "left_columns": matrix["left_columns"],
            "columns": [matrix["time_columns"][i] for i in indices],
            "rows": [
                {
                    "group_id": row["group_id"],
                    "left_values": row["left_values"],
                    "image": row.get("image", ""),
                    "values": [
                        str(row["stock_by_week"][selected_week].get("quantity", ""))
                        if matrix["time_columns"][i].get("kind") == "STOCK"
                        and selected_week in row.get("stock_by_week", {})
                        else aggregate([row["cells"][i]], True)
                        for i in indices
                    ],
                    "errors": [
                        row["cells"][i].get("issue", {}).get("message", "Ошибка")
                        for i in indices
                        if row["cells"][i]["state"].get("persistence") == "error"
                        or row["cells"][i].get("issue", {}).get("code") == "PRODUCTION_MISMATCH"
                    ],
                }
                for row in matrix["rows"]
            ],
        }
    days = [f"{period}-{day:02d}" for day in range(1, calendar.monthrange(year, month)[1] + 1)]
    prior = [f"{year:04d}-{m:02d}" for m in range(1, month)]
    rows = []
    for row in matrix["rows"]:
        cells = [c for c in row["cells"] if c["column_id"][:7] <= period]
        calculated = any(c["state"]["access"] == "calculated" for c in cells)
        by_id = {c["column_id"]: c for c in cells}
        left = row["left_values"]
        identifiers = [left.get(c["id"], "") for c in matrix["left_columns"]]
        rows.append(
            {
                "group_id": row["group_id"],
                "position": row["group_label"],
                "party": identifiers[0],
                "label": identifiers[-1],
                "metric_code": row.get("metric_code", ""),
                "calculated": calculated,
                "summary": aggregate(cells, calculated),
                "prior": [
                    aggregate([c for c in cells if c["column_id"].startswith(p)], calculated)
                    for p in prior
                ],
                "days": [aggregate([by_id[d]], calculated) if d in by_id else "" for d in days],
                "monthly": [
                    aggregate(
                        [c for c in cells if c["column_id"].startswith(f"{year:04d}-{m:02d}")],
                        calculated,
                    )
                    for m in range(1, 13)
                ],
                "errors": [
                    str(c.get("error", c.get("lock_reason", "Ошибка расчёта")))
                    for c in cells
                    if c["state"].get("access") == "error"
                    or c.get("state", {}).get("persistence") == "error"
                ],
            }
        )
    return {
        "schema": 1,
        "report_type": matrix["report_type"],
        "organization_id": matrix["organization_id"],
        "organization": organization,
        "title": matrix["title"],
        "period": period,
        "revision": matrix["matrix_revision"],
        "prior": prior,
        "days": days,
        "rows": rows,
    }


def snapshot_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(
        {k: v for k, v in snapshot.items() if k != "_stamp"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(snapshot_json(snapshot).encode("utf-8")).hexdigest()
