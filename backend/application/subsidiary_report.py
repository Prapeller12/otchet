"""Supplier consumption worksheet. Business rules approved on 2026-09-11."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.application.report_calendar import reporting_weeks
from backend.domain.calculations import (
    calculate_required_quantity,
    subtract_quantities,
    sum_quantities,
)


def quantity(value: object) -> str:
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Количество должно быть числовой строкой")
    if value == "":
        return ""
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Некорректное количество") from exc
    if (
        not number.is_finite()
        or abs(number) > Decimal("1e50")
        or (number and abs(number) < Decimal("1e-50"))
    ):
        raise ValueError("Количество вне допустимого диапазона")
    if number < 0:
        raise ValueError("План и договорной объём не могут быть отрицательными")
    return format(number, "f")


def validate_detail(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.keys() - {
        "number",
        "designation",
        "suppliers",
        "weekly_supply",
    }:
        raise ValueError("Неверная структура детали")
    result: dict[str, Any] = {}
    for key in ("number", "designation"):
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError("Номер и обозначение: до 200 символов")
        result[key] = value.strip()
    suppliers = raw.get("suppliers", [])
    if not isinstance(suppliers, list) or len(suppliers) > 50:
        raise ValueError("Разрешено до 50 производителей одной детали")
    seen = set()
    normalized = []
    for supplier in suppliers:
        if not isinstance(supplier, dict) or supplier.keys() - {
            "id",
            "name",
            "contract",
            "archived",
        }:
            raise ValueError("Неверные настройки производителя")
        identity = supplier.get("id")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[A-Z0-9]{1,32}", identity)
            or identity in seen
        ):
            raise ValueError("Идентификатор производителя повторяется или неверен")
        seen.add(identity)
        name = supplier.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Укажите наименование производителя")
        archived = supplier.get("archived", False)
        if not isinstance(archived, bool):
            raise ValueError("Неверный признак архива")
        normalized.append(
            {
                "id": identity,
                "name": name.strip(),
                "contract": quantity(supplier.get("contract", "")),
                "archived": archived,
            }
        )
    result["suppliers"] = normalized
    if "weekly_supply" in raw:
        if not isinstance(raw["weekly_supply"], bool):
            raise ValueError("Неверный режим недельных поставок")
        result["weekly_supply"] = raw["weekly_supply"]
    return result


def default_detail(name: str) -> dict[str, Any]:
    return {
        "number": "",
        "designation": "",
        "suppliers": [{"id": "PRIMARY", "name": name, "contract": "", "archived": False}],
    }


def number(cell: dict[str, Any]) -> Decimal | None:
    value = cell["value"]
    return Decimal(value["quantity"]) if value["kind"] == "QUANTITY" else None


def result_value(value: Decimal | None) -> dict[str, str]:
    return (
        {"kind": "DATA_NOT_PROVIDED"}
        if value is None
        else {"kind": "QUANTITY", "quantity": format(value, "f")}
    )


def build_rows(
    groups: list[tuple[Any, dict[str, Any]]],
    year: int,
    read: Callable[[Any, str, str, str, bool], dict[str, Any]],
    plans: dict[str, str],
) -> dict[str, Any]:
    columns = []
    weekly_columns = any(c.get("subsidiary", {}).get("weekly_supply", False) for _, c in groups)
    for month in range(1, 13):
        period = f"{year:04d}-{month:02d}"
        for key, label in [
            ("OPENING", "Остаток на начало"),
            ("RECEIVED", "Поступило за месяц"),
            ("STOCK", "На складе"),
            ("VARIANCE", "Дефицит / профицит"),
        ]:
            columns.append(
                {
                    "id": f"{period}-{key}",
                    "label": label,
                    "group_label": period,
                    "width": 100,
                    "kind": key,
                }
            )
        for week in reporting_weeks(year, month):
            if weekly_columns:
                columns.append(
                    {
                        "id": week.start.isoformat() + "-SUPPLIED",
                        "label": "Поставлено " + week.label,
                        "group_label": period,
                        "width": 100,
                        "kind": "SUPPLIED",
                        "period_start": week.start.isoformat(),
                        "period_end": week.end.isoformat(),
                    }
                )
            columns.append(
                {
                    "id": week.start.isoformat(),
                    "label": ("Расход " if weekly_columns else "") + week.label,
                    "period_start": week.start.isoformat(),
                    "period_end": week.end.isoformat(),
                    "group_label": period,
                    "width": 64,
                    "kind": "USED",
                }
            )
    rows = []
    for group, config in groups:
        detail = config.get("subsidiary", default_detail(group.party_name))
        weekly_supply = detail.get("weekly_supply", False)
        suppliers = sorted(detail["suppliers"], key=lambda item: item["archived"])
        if not suppliers:
            continue
        supplier_rows = []
        for supplier in suppliers:
            sid = supplier["id"]
            supplier_rows.append(
                {
                    "id": f"subsidiary-{group.id}-{sid}",
                    "workspace_id": str(group.id),
                    "supplier_id": sid,
                    "group_id": f"workspace-group-{group.id}",
                    "group_label": group.position_name,
                    "metric_code": f"SUB_USED_{sid}",
                    "category": config["category"],
                    "image": config["image"],
                    "left_values": {
                        "number": detail["number"],
                        "designation": detail["designation"],
                        "position": group.position_name,
                        "norm": config["norm"],
                        "party": supplier["name"] + (" (архив)" if supplier["archived"] else ""),
                        "contract": supplier["contract"],
                    },
                    "cells": [],
                    "stock_by_week": {},
                    "archived": supplier["archived"],
                }
            )
        for month in range(1, 13):
            period = f"{year:04d}-{month:02d}"
            day = period + "-01"
            weeks = reporting_weeks(year, month)
            opening = read(group, "SUB_OPENING", day, period + "-OPENING", True)
            receipts = [
                read(
                    group,
                    "SUB_RECEIVED_" + s["id"],
                    day,
                    period + "-RECEIVED",
                    not s["archived"] and not weekly_supply,
                )
                for s in suppliers
            ]
            used = [
                [
                    read(
                        group,
                        "SUB_USED_" + s["id"],
                        w.start.isoformat(),
                        w.start.isoformat(),
                        not s["archived"],
                    )
                    for w in weeks
                ]
                for s in suppliers
            ]
            supplied = (
                [
                    [
                        read(
                            group,
                            "SUB_SUPPLIED_" + s["id"],
                            w.start.isoformat(),
                            w.start.isoformat() + "-SUPPLIED",
                            weekly_supply and not s["archived"],
                        )
                        for w in weeks
                    ]
                    for s in suppliers
                ]
                if weekly_columns
                else []
            )
            if weekly_supply:
                for i, receipt in enumerate(receipts):
                    values = [number(c) for c in supplied[i]]
                    receipt["value"] = result_value(
                        sum_quantities(v for v in values if v is not None)
                        if all(v is not None for v in values)
                        else None
                    )
                    receipt["formula"] = (
                        "Сумма подтверждённых недельных поставок; пустота не равна нулю"
                    )
            opening_value = number(opening)
            incoming = [
                number(c)
                for i, c in enumerate(receipts)
                if not suppliers[i]["archived"] or number(c) is not None
            ]
            # Missing receipts/initial balance are unknown, not confirmed zero.
            available = (
                None
                if opening_value is None or any(v is None for v in incoming)
                else sum_quantities((v for v in incoming if v is not None), opening_value)
            )
            missing = []
            if opening_value is None:
                missing.append("остаток на начало месяца")
            for i, receipt in enumerate(receipts):
                if not suppliers[i]["archived"] and number(receipt) is None:
                    missing.append("поступление: " + suppliers[i]["name"])
            plan = plans.get(period, "")
            norm = config.get("norm", "")
            variance = (
                None
                if available is None or not plan or not norm
                else subtract_quantities(
                    available, calculate_required_quantity(Decimal(plan), Decimal(norm), Decimal(0))
                )
            )
            accumulated = Decimal(0)
            balances = {}
            for index, week in enumerate(weeks):
                # As in the approved worksheet: total of recorded consumption.
                accumulated = sum_quantities(
                    (number(items[index]) or Decimal(0) for items in used), accumulated
                )
                week_available = available
                if weekly_supply:
                    upto = [
                        number(c)
                        for i, items in enumerate(supplied)
                        for c in items[: index + 1]
                        if not suppliers[i]["archived"] or number(c) is not None
                    ]
                    consumed = [
                        number(c)
                        for i, items in enumerate(used)
                        for c in items[: index + 1]
                        if not suppliers[i]["archived"] or number(c) is not None
                    ]
                    week_available = (
                        sum_quantities((v for v in upto if v is not None), opening_value)
                        if opening_value is not None
                        and all(v is not None for v in upto)
                        and all(v is not None for v in consumed)
                        else None
                    )
                balances[week.start.isoformat()] = result_value(
                    None
                    if week_available is None
                    else subtract_quantities(week_available, accumulated)
                )
            for index, row in enumerate(supplier_rows):
                common = index == next((i for i, s in enumerate(suppliers) if not s["archived"]), 0)
                opening_cell = (
                    opening
                    if common
                    else read(
                        group,
                        "SUB_UNUSED_OPENING_" + suppliers[index]["id"],
                        day,
                        period + "-OPENING",
                        False,
                    )
                )
                stock = read(
                    group, "SUB_STOCK_" + suppliers[index]["id"], day, period + "-STOCK", False
                )
                stock["value"] = (
                    balances[weeks[-1].start.isoformat()] if common else result_value(None)
                )
                stock["formula"] = (
                    "Начальный остаток + поступления всех производителей − внесённый расход месяца"
                )
                delta = read(
                    group,
                    "SUB_VARIANCE_" + suppliers[index]["id"],
                    day,
                    period + "-VARIANCE",
                    False,
                )
                delta["value"] = result_value(variance if common else None)
                delta["formula"] = (
                    "Начальный остаток + поступления всех производителей "
                    "− план выпуска × входимость"
                )
                delta["tone"] = (
                    "deficit" if common and variance is not None and variance < 0 else ""
                )
                if common:
                    missing_plan = [*missing]
                    if not plan:
                        missing_plan.append("план выпуска")
                    if not norm:
                        missing_plan.append("входимость детали")
                    for calculated, fields in [(stock, missing), (delta, missing_plan)]:
                        if fields:
                            calculated["issue"] = {
                                "code": "MISSING_INPUT",
                                "message": "Заполните: "
                                + "; ".join(fields)
                                + ". Если поступлений не было, введите 0.",
                            }
                week_cells = (
                    [
                        cell
                        for pair in zip(supplied[index], used[index], strict=True)
                        for cell in pair
                    ]
                    if weekly_columns
                    else used[index]
                )
                row["cells"].extend([opening_cell, receipts[index], stock, delta, *week_cells])
                if common:
                    row["stock_by_week"].update(balances)
        # Archived suppliers without facts in this year need no visible row.
        for row in supplier_rows:
            if not row["archived"] or any(
                number(c) is not None
                for c in row["cells"]
                if c["coordinate"]["metric_code"] == "SUB_OPENING"
                or "SUB_RECEIVED_" in c["coordinate"]["metric_code"]
                or "SUB_USED_" in c["coordinate"]["metric_code"]
                or "SUB_SUPPLIED_" in c["coordinate"]["metric_code"]
            ):
                rows.append(row)
    return {
        "rows": rows,
        "time_columns": columns,
        "left_columns": [
            {"id": key, "label": label, "width": width, "shared": shared}
            for key, label, width, shared in [
                ("number", "№ п/п", 64, True),
                ("designation", "Обозначение", 130, True),
                ("position", "Наименование", 170, True),
                ("norm", "Входимость, шт.", 84, True),
                ("party", "Производитель", 160, False),
                ("contract", "Объём по договору", 100, False),
            ]
        ],
    }
