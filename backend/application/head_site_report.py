"""Monthly head-site plan/fact worksheet, with subsidiary production checks."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from backend.application.subsidiary_report import default_detail, number, result_value
from backend.domain.calculations import QuantityValue, calculate_component_consumption


def build_head_rows(
    groups: list[tuple[Any, dict[str, Any]]],
    year: int,
    read: Callable[[Any, str, str, str, bool], dict[str, Any]],
    presentation: dict[str, Any],
    subsidiaries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    columns = [
        {
            "id": f"{year:04d}-{month:02d}-{kind}",
            "label": label,
            "group_label": f"{year:04d}-{month:02d}",
            "width": 110,
            "kind": kind,
        }
        for month in range(1, 13)
        for kind, label in [
            ("OPENING", "Остаток на начало"),
            ("PLAN", "План"),
            ("FACT", "Факт"),
            ("USED", "Использовано"),
            ("STOCK", "На складе"),
            ("VARIANCE", "Дефицит / профицит"),
        ]
    ]
    rows = []
    for group, config in groups:
        detail = config.get("subsidiary", default_detail(group.party_name))
        suppliers = sorted(detail["suppliers"], key=lambda s: s["archived"])
        if not suppliers:
            continue
        common = next((i for i, s in enumerate(suppliers) if not s["archived"]), 0)
        group_rows = [
            {
                "id": f"head-{group.id}-{s['id']}",
                "workspace_id": str(group.id),
                "supplier_id": s["id"],
                "group_id": f"workspace-group-{group.id}",
                "group_label": group.position_name,
                "metric_code": f"HEAD_FACT_{s['id']}",
                "category": config["category"],
                "image": config["image"],
                "archived": s["archived"],
                "left_values": {
                    "number": detail["number"],
                    "designation": detail["designation"],
                    "position": group.position_name,
                    "norm": config["norm"],
                    "party": s["name"] + (" (архив)" if s["archived"] else ""),
                    "contract": s["contract"],
                },
                "cells": [],
            }
            for s in suppliers
        ]
        for month in range(1, 13):
            period = f"{year:04d}-{month:02d}"
            day = period + "-01"
            opening = read(group, "HEAD_OPENING", day, period + "-OPENING", True)
            plans = [
                read(group, "HEAD_PLAN_" + s["id"], day, period + "-PLAN", not s["archived"])
                for s in suppliers
            ]
            facts = [
                read(group, "HEAD_FACT_" + s["id"], day, period + "-FACT", not s["archived"])
                for s in suppliers
            ]
            norm = config.get("norm", "")
            actual = presentation.get("actuals", {}).get(period, "")
            used = (
                calculate_component_consumption(
                    QuantityValue(Decimal(actual) if actual else None), Decimal(norm)
                ).value
                if norm
                else None
            )
            values = [
                number(c)
                for i, c in enumerate(facts)
                if not suppliers[i]["archived"] or number(c) is not None
            ]
            total = (
                sum((v for v in values if v is not None), Decimal(0))
                if values and all(v is not None for v in values)
                else None
            )
            opening_value = number(opening)
            available = (
                opening_value + total if opening_value is not None and total is not None else None
            )
            targets = [
                number(c)
                for i, c in enumerate(plans)
                if not suppliers[i]["archived"] or number(c) is not None
            ]
            target = (
                sum((v for v in targets if v is not None), Decimal(0))
                if targets and all(v is not None for v in targets)
                else None
            )
            stock = available - used if available is not None and used is not None else None
            # Supplier monthly plans are component quantities; do not multiply them again.
            delta = available - target if available is not None and target is not None else None
            links = config.get("head_links", [])
            linked_values = [
                subsidiaries.get(link, {}).get("actuals", {}).get(period, "") for link in links
            ]
            issue = ""
            if links:
                sources = [
                    f"{subsidiaries.get(link, {}).get('name', 'Общество ' + link)}: "
                    + (str(value) + " шт." if value != "" else "выпуск не указан")
                    for link, value in zip(links, linked_values, strict=True)
                ]
                location = f"Составная часть «{group.position_name}», месяц {period}. "
                if any(v == "" for v in linked_values):
                    issue = (
                        location
                        + "Нельзя проверить факт головной площадки. "
                        + "; ".join(sources)
                        + ". Заполните поле «Выпущено, шт.» "
                        "в указанных дочерних отчётах за этот месяц."
                    )
                elif total is not None:
                    linked_total = sum((Decimal(v) for v in linked_values), Decimal(0))
                    if total > linked_total:
                        issue = (
                            location
                            + f"Факт головной площадки: {total} шт.; "
                            + "; ".join(sources)
                            + f"; всего у дочерних обществ: {linked_total} шт. "
                            + f"Превышение: {total - linked_total} шт. "
                            + "Проверьте столбец «Факт» этой части и поле «Выпущено, шт.» "
                            "связанных обществ за тот же месяц."
                        )
            for i, row in enumerate(group_rows):
                cells = [
                    opening
                    if i == common
                    else read(
                        group,
                        "HEAD_UNUSED_OPENING_" + suppliers[i]["id"],
                        day,
                        period + "-OPENING",
                        False,
                    ),
                    plans[i],
                    facts[i],
                ]
                if issue:
                    facts[i]["issue"] = {"code": "PRODUCTION_MISMATCH", "message": issue}
                    facts[i]["tone"] = "deficit"
                for kind, value in [("USED", used), ("STOCK", stock), ("VARIANCE", delta)]:
                    cell = read(
                        group,
                        "HEAD_" + kind + "_" + suppliers[i]["id"],
                        day,
                        period + "-" + kind,
                        False,
                    )
                    cell["value"] = result_value(value if i == common else None)
                    if i == common and value is None:
                        cell["issue"] = {
                            "code": "MISSING_INPUT",
                            "message": (
                                "Заполните начальный остаток, план и факт производителей, "
                                "выпуск готовых изделий и входимость."
                            ),
                        }
                    if kind == "VARIANCE" and value is not None and value < 0:
                        cell["tone"] = "deficit"
                    cells.append(cell)
                row["cells"].extend(cells)
        rows.extend(group_rows)
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
                ("contract", "Объём по договору", 110, False),
            ]
        ],
    }
