from typing import Any

from backend.application.daily_summary import summarize_daily_rows


def row(code: str, quantities: list[str | None], *, category: str = "PART") -> dict[str, Any]:
    return {
        "id": code,
        "group_id": category,
        "group_label": "Позиция",
        "category": category,
        "metric_code": code,
        "left_values": {"party": "Поставщик"},
        "cells": [
            {
                "coordinate": {"operation_date": day},
                "state": {"access": "calculated" if code.endswith("BALANCE") else "editable"},
                "value": {"kind": "QUANTITY", "quantity": value}
                if value is not None
                else {"kind": "DATA_NOT_PROVIDED"},
            }
            for day, value in zip(
                ["2026-01-31", "2026-02-28", "2026-12-31"], quantities, strict=True
            )
        ],
    }


def test_monthly_totals_preserve_zero_stock_and_exact_quantities() -> None:
    rows = [
        row("WRK_DAILY_RECEIVED", ["0.1", "0.2", None]),
        row("WRK_DAILY_USED", [None, "0", None]),
        row("WRK_DAILY_BALANCE", ["0.1", "0.3", "0.3"]),
        row("WRK_DAILY_RECEIVED", ["99", "99", None], category="PKI"),
    ]
    summary = summarize_daily_rows(rows, 2026, [{"id": "party"}])
    assert summary["rows"][0]["annual"] == "0.3"
    assert summary["rows"][0]["monthly"][:3] == ["0.1", "0.2", ""]
    assert summary["rows"][0]["through_month"][:3] == ["0.1", "0.3", "0.3"]
    assert summary["rows"][1]["monthly"][:2] == ["", "0"]
    assert summary["rows"][2]["annual"] == "0.3"  # Stock is never summed.
    assert len(summary["components"]) == 1
    assert summary["components"][0]["rows"][0]["metric_code"] == "WRK_DAILY_RECEIVED"


def test_error_does_not_publish_an_incomplete_total() -> None:
    item = row("WRK_DAILY_BALANCE", ["3", None, None])
    item["cells"][1]["state"]["persistence"] = "error"
    result = summarize_daily_rows([item], 2026, [])
    assert result["rows"][0]["annual"] == ""
    assert result["rows"][0]["through_month"][0] == "3"
    assert result["rows"][0]["through_month"][1] == ""
    assert result["components"] == []  # An incomplete position is not a receipt/usage trio.
