from typing import Any

from backend.application.import_reconciliation import reconcile_controls


def test_controls_match_mismatch_and_missing_meaning_are_explicit() -> None:
    field: dict[str, Any] = {
        "role": "CONTROL",
        "sheet": "Отчёт",
        "address": "G9",
        "value": "115.00",
        "source_label": "В наличии на складе",
        "unit": "шт.",
        "period": {"start": "2026-09-01", "end": "2026-09-30", "calendar": "MONTH"},
        "position": {"code": "000.1", "name": "Деталь"},
    }
    rec = {"fields": {"0:G9": field}}
    matrix = {
        "rows": [
            {
                "group_id": "g1",
                "left_values": {"designation": "000.1", "position": "Деталь"},
                "cells": [
                    {
                        "column_id": "2026-09-STOCK",
                        "state": {"access": "calculated"},
                        "value": {"kind": "QUANTITY", "quantity": "115"},
                    }
                ],
            }
        ]
    }
    assert reconcile_controls(rec, matrix)[0]["status"] == "UNVERIFIED"
    assert reconcile_controls(rec, matrix, values_applied=True)[0]["status"] == "MATCH"
    field["value"] = "116"
    mismatch = reconcile_controls(rec, matrix, values_applied=True)[0]
    assert mismatch["status"] == "MISMATCH" and "на 1." in mismatch["reason"]
    field["period"] = None
    assert reconcile_controls(rec, matrix, values_applied=True)[0]["status"] == "UNVERIFIED"
    field["unit"] = "%"
    assert "Процент" in reconcile_controls(rec, matrix, values_applied=True)[0]["reason"]
