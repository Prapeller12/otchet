"""Explain each source control and compare only proven equivalent calculated fields."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.application.import_recognition import decimal_text, normalize


def reconcile_controls(
    recognition: dict[str, Any], matrix: dict[str, Any], *, values_applied: bool = False
) -> list[dict[str, Any]]:
    """matrix must contain backend calculations after the reviewed imported changes.

    No rounding is invented: supported quantity comparisons use exact Decimal values.
    Undefined periods, totals and percentages are explicitly UNVERIFIED.
    """
    result = []
    for key, source in recognition.get("fields", {}).items():
        if source.get("role") != "CONTROL":
            continue
        control = {
            "source_cell": key,
            "sheet": source["sheet"],
            "address": source["address"],
            "source_value": source.get("value", ""),
            "program_value": None,
            "unit": source.get("unit", ""),
            "period": source.get("period"),
            "status": "UNVERIFIED",
            "reason": "",
            "rounding": "Без округления: точное сравнение десятичных количеств",
        }
        result.append(control)
        if source.get("formula_status") == "UNSUPPORTED" or any(
            e.get("code") == "FORMULA_ERROR" for e in source.get("errors", [])
        ):
            control["reason"] = "Исходная формула не вычислена; сверка невозможна"
            continue
        if source.get("unit") == "%":
            control["reason"] = (
                "Процент сохранён как контрольный показатель. "
                "Основание расчёта и период не сопоставлены с формулой программы"
            )
            control["rounding"] = "Не применялось: числовое сравнение не выполнялось"
            continue
        label = normalize(source.get("source_label"))
        target_kind = (
            "VARIANCE"
            if "дефицит" in label or "профицит" in label
            else "STOCK"
            if "в наличии" in label or "на складе" in label
            else None
        )
        if target_kind is None:
            control["reason"] = (
                "Контрольный итог сохранён. Его состав не подтверждён как "
                "эквивалент расчётного показателя программы; повторно не суммируется"
            )
            continue
        period = source.get("period")
        if not period or period.get("calendar") != "MONTH":
            control["reason"] = (
                "Не заданы точные месячные границы исходного контрольного "
                "показателя; текущий месяц не определяется по имени файла"
            )
            continue
        if not values_applied:
            control["reason"] = (
                "Сверка ожидает пересчёта программы после применения "
                "проверяемых значений к предварительной матрице"
            )
            continue
        position = source.get("position", {})
        rows = [
            row
            for row in matrix.get("rows", [])
            if position.get("code")
            and row.get("left_values", {}).get("designation") == position["code"]
            and (
                not position.get("name")
                or normalize(row.get("left_values", {}).get("position"))
                == normalize(position["name"])
            )
            and (
                not position.get("parent_code")
                or row.get("left_values", {}).get("parent_code") == position["parent_code"]
            )
            and (
                not source.get("product_context")
                or row.get("left_values", {}).get("product_context") == source["product_context"]
            )
        ]
        group_ids = {row.get("group_id", row.get("workspace_id", row.get("id"))) for row in rows}
        if len(group_ids) != 1:
            control["reason"] = "Позиция контрольного итога не найдена или неоднозначна"
            continue
        # Common stock/variance is stored once for the group, not once per supplier.
        column_id = period["start"][:7] + "-" + target_kind
        candidates = [
            cell
            for row in rows
            for cell in row.get("cells", [])
            if cell.get("column_id") == column_id
            and cell.get("state", {}).get("access") == "calculated"
            and cell.get("value", {}).get("kind") == "QUANTITY"
        ]
        if len(candidates) != 1:
            control["reason"] = (
                "Расчёт программы отсутствует либо неоднозначен: "
                "проверьте начальный остаток, поступления, расход, план и входимость"
            )
            continue
        try:
            source_number = Decimal(decimal_text(source.get("value", "")))
            program_number = Decimal(decimal_text(candidates[0]["value"]["quantity"]))
        except ValueError:
            control["reason"] = "Исходный контрольный показатель не является подтверждённым числом"
            continue
        control["program_value"] = format(program_number, "f")
        if source_number == program_number:
            control["status"] = "MATCH"
            control["reason"] = (
                "Совпали показатель, позиция, точный период и рассчитанное количество"
            )
        else:
            control["status"] = "MISMATCH"
            control["reason"] = (
                "Исходный контрольный итог отличается от расчёта программы "
                f"на {format(source_number - program_number, 'f')}. "
                "Итог не импортируется как первичное значение; "
                "проверьте составляющие и правила расчёта источника"
            )
    return result
