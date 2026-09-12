"""Validate explicit source-to-workspace mappings without guessing business meaning."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.application.report_cells import ReportCellCoordinate, ReportCellValue


def numeric_sources(document: dict[str, Any]) -> dict[str, dict[str, str]]:
    result = {}
    for index, sheet in enumerate(document["sheets"]):
        for address, cell in sheet["cells"].items():
            letters = address.rstrip("0123456789")
            row = int(address[len(letters) :])
            col = 0
            for letter in letters:
                col = col * 26 + ord(letter) - 64
            if row in (1, 7, 8) or letters == "B" or (row >= 9 and col < 5):
                continue
            raw = str(cell.get("display", cell.get("value", "")))
            try:
                number = Decimal(raw)
                numeric = number.is_finite()
            except InvalidOperation:
                numeric = False
            quantity_region = row >= 9 and (col == 5 or col >= 7)
            if not numeric and not quantity_region:
                continue
            if not raw.strip():
                continue
            result[f"{index}:{address}"] = {
                "sheet": sheet["name"],
                "address": address,
                "value": format(number, "f") if numeric else raw,
                "formula": str(cell["value"]) if cell["kind"] == "f" else "",
            }
    return result


def validate_transfer(
    document: dict[str, Any], matrix: dict[str, Any], mappings: object
) -> dict[str, Any]:
    sources = numeric_sources(document)
    issues: list[dict[str, str]] = []
    changes = []
    seen: set[str] = set()
    targets: set[str] = set()
    allowed = {
        json.dumps(cell["coordinate"], sort_keys=True): cell
        for row in matrix["rows"]
        for cell in row["cells"]
        if cell["state"]["access"] == "editable"
    }
    if not isinstance(mappings, list) or len(mappings) > 10000:
        raise ValueError("Сопоставления должны быть списком не более 10 000 ячеек")
    if document["report_type"] != matrix["report_type"]:
        raise ValueError("Тип исходного отчёта не совпадает с выбранной рабочей вкладкой")
    for entry in mappings:
        if not isinstance(entry, dict):
            raise ValueError("Некорректное сопоставление")
        key = str(entry.get("source", ""))
        message = ""
        if key not in sources or key in seen:
            message = "Исходная ячейка отсутствует или указана повторно"
        elif entry.get("skip_reason"):
            reason = entry["skip_reason"]
            if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
                message = "Укажите причину исключения ячейки (3–500 символов)"
        else:
            try:
                if not isinstance(entry.get("coordinate"), dict):
                    raise ValueError("Выберите рабочую ячейку")
                coordinate = ReportCellCoordinate.from_mapping(entry["coordinate"])
                target = json.dumps(coordinate.to_dict(), sort_keys=True)
                if target not in allowed:
                    raise ValueError(
                        "Выберите вводимый показатель и период действующей рабочей формы"
                    )
                if target in targets:
                    raise ValueError(
                        "В одну рабочую ячейку назначено несколько значений; уточните сопоставление"
                    )
                raw = entry.get("quantity")
                if not isinstance(raw, str) or len(raw) > 100:
                    raise ValueError("Укажите числовое значение")
                number = Decimal(raw.strip().replace(",", "."))
                if (
                    not number.is_finite()
                    or abs(number) > Decimal("1e50")
                    or (number != 0 and abs(number) < Decimal("1e-50"))
                ):
                    raise ValueError("Значение вне допустимого диапазона")
                value = ReportCellValue.from_mapping(
                    {"kind": "QUANTITY", "quantity": format(number, "f")}
                )
                if entry.get("confirmed") is not True:
                    raise ValueError(
                        "Подтвердите соответствие значения выбранному показателю и периоду"
                    )
                targets.add(target)
                changes.append(
                    {
                        "source_cell": key,
                        "coordinate": coordinate.to_dict(),
                        "value": value.to_dict(),
                    }
                )
            except (ValueError, InvalidOperation, TypeError) as exc:
                message = str(exc)
        seen.add(key)
        if message:
            issues.append({"source_cell": key, "code": "MAPPING_REQUIRED", "message": message})
    for key in sources.keys() - seen:
        issues.append(
            {
                "source_cell": key,
                "code": "MAPPING_REQUIRED",
                "message": "Выберите рабочую ячейку или явно укажите причину исключения",
            }
        )
    if not changes:
        issues.append(
            {
                "source_cell": "",
                "code": "EMPTY_TRANSFER",
                "message": "Не выбрана ни одна ячейка для переноса",
            }
        )
    return {"changes": changes, "issues": issues, "sources": sources}
