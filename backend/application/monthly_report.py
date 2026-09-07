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


def monthly_snapshot(matrix: dict[str, Any], month: int, organization: str) -> dict[str, Any]:
    year = matrix["year"]
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("Выберите месяц от 1 до 12")
    if not isinstance(year, int):
        raise ValueError("Год печатного отчёта не задан")
    period = f"{year:04d}-{month:02d}"
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
