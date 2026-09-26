"""Server-owned semantic mapping; ambiguity never becomes a guessed quantity/date."""

from __future__ import annotations

import calendar
import copy
import json
from datetime import date
from typing import Any

from backend.application.import_recognition import decimal_text, normalize, recognize_document
from backend.application.report_cells import ReportCellCoordinate, ReportCellValue


def numeric_sources(document: dict[str, Any]) -> dict[str, Any]:
    """Compatibility name; classification comes from semantic recognition, not coordinates."""
    return dict(recognize_document(document)["sources"])


def _target_metric(source: dict[str, Any], row: dict[str, Any], kind: str) -> str | None:
    metric = str(source.get("metric", ""))
    supplier = row.get("supplier_id", "PRIMARY")
    prefix = "HEAD" if kind == "HEAD_SITE" else "SUB"
    if metric == "OPENING":
        return prefix + "_OPENING"
    if kind == "HEAD_SITE":
        return {
            "PLAN": f"HEAD_PLAN_{supplier}",
            "PRODUCED": f"HEAD_FACT_{supplier}",
            "SUPPLIED": f"HEAD_FACT_{supplier}",
        }.get(metric)
    if metric in {"SUPPLIED", "RECEIVED"}:
        period = source.get("period") or {}
        return f"SUB_{'RECEIVED' if period.get('calendar') == 'MONTH' else 'SUPPLIED'}_{supplier}"
    if metric == "USED":
        return f"SUB_USED_{supplier}"
    return None


def _auto_coordinate(source: dict[str, Any], matrix: dict[str, Any]) -> dict[str, str] | None:
    position = source.get("position", {})
    period = source.get("period")
    if not position.get("code") or not period:
        return None
    candidates = []
    for row in matrix["rows"]:
        left = row.get("left_values", {})
        if str(left.get("designation", "")) != position["code"]:
            continue
        if normalize(left.get("party")) != normalize(position.get("manufacturer")):
            continue
        if position.get("name") and normalize(left.get("position")) != normalize(position["name"]):
            continue
        if position.get("parent_code") and left.get("parent_code") != position["parent_code"]:
            continue
        if (
            source.get("product_context")
            and left.get("product_context") != source["product_context"]
        ):
            continue
        metric = _target_metric(source, row, matrix["report_type"])
        for cell in row["cells"]:
            coordinate = cell["coordinate"]
            if cell["state"]["access"] != "editable" or coordinate.get("metric_code") != metric:
                continue
            if coordinate.get("period_start", coordinate.get("operation_date")) != period["start"]:
                continue
            # Verify end boundary too; matching ordinal/start alone is insufficient.
            if period["calendar"] != "MONTH":
                column = next(
                    (
                        c
                        for c in matrix.get("time_columns", [])
                        if c["id"] == cell.get("column_id", cell.get("time_column_id"))
                    ),
                    None,
                )
                if column is None:
                    column = next(
                        (c for c in matrix.get("time_columns", []) if c["id"] == period["start"]),
                        None,
                    )
                if not column:
                    continue
                expected_end = column.get("period_end") or column.get("end")
                if not expected_end:
                    from backend.application.report_calendar import reporting_weeks

                    start = date.fromisoformat(period["start"])
                    expected_end = next(
                        (
                            w.end.isoformat()
                            for w in reporting_weeks(start.year, start.month)
                            if w.start.isoformat() == period["start"]
                        ),
                        None,
                    )
                if expected_end != period["end"]:
                    continue
            candidates.append(coordinate)
    return candidates[0] if len(candidates) == 1 else None


def validate_transfer(
    document: dict[str, Any],
    matrix: dict[str, Any],
    mappings: object,
    period_rules: object = None,
    sheet_decisions: object = None,
    recognition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recognition = (
        recognize_document(document) if recognition is None else copy.deepcopy(recognition)
    )
    if sheet_decisions is None:
        sheet_decisions = {}
    if not isinstance(sheet_decisions, dict):
        raise ValueError("Решения по листам должны быть объектом")
    excluded = set()
    for sheet_key, decision in sheet_decisions.items():
        if not isinstance(decision, dict) or not isinstance(decision.get("include"), bool):
            raise ValueError("Укажите решение о включении листа")
        if not str(decision.get("reason", "")).strip():
            raise ValueError("Укажите причину решения по листу")
        if not decision["include"]:
            excluded.add(str(sheet_key))
    sources = copy.deepcopy(
        {k: v for k, v in recognition["sources"].items() if k.split(":")[0] not in excluded}
    )
    recognition["structural_actions"] = [
        a for a in recognition["structural_actions"] if str(a["sheet_index"]) not in excluded
    ]
    recognition["issues"] = [
        issue
        for issue in recognition["issues"]
        if not (
            issue["source_cell"].split(":")[0] in excluded
            or (
                issue["source_cell"].split(":")[0] in sheet_decisions
                and "Скрытый" in issue["message"]
            )
        )
    ]
    issues: list[dict[str, str]] = list(recognition["issues"])
    changes: list[dict[str, Any]] = []
    targets: set[str] = set()
    auto_mappings = []
    if not isinstance(mappings, list) or len(mappings) > 20000:
        raise ValueError("Сопоставления должны быть списком не более 20 000 ячеек")
    included_kinds = {
        s.get("kind")
        for i, s in enumerate(document["sheets"])
        if str(i) not in excluded and s.get("kind")
    }
    if included_kinds - {matrix["report_type"]}:
        issues.append(
            {
                "source_cell": "",
                "code": "REPORT_TYPE_MISMATCH",
                "message": "Тип включённого листа не совпадает с рабочей вкладкой; "
                "выберите листы одного типа",
            }
        )
    overrides: dict[str, dict[str, Any]] = {}
    for entry in mappings:
        if not isinstance(entry, dict):
            raise ValueError("Некорректное сопоставление")
        key = str(entry.get("source", ""))
        if key not in sources or key in overrides:
            issues.append(
                {
                    "source_cell": key,
                    "code": "MAPPING_REQUIRED",
                    "message": "Исходная ячейка отсутствует или указана повторно",
                }
            )
        else:
            overrides[key] = entry
    if period_rules is None:
        period_rules = {}
    if not isinstance(period_rules, dict):
        raise ValueError("Правила периодов должны быть объектом")
    allowed = {
        json.dumps(cell["coordinate"], sort_keys=True): cell
        for row in matrix["rows"]
        for cell in row["cells"]
        if cell["state"]["access"] == "editable"
    }
    skipped = 0
    for key, source in sources.items():
        rule = period_rules.get(source["period_block"])
        if rule is not None:
            if not isinstance(rule, dict):
                raise ValueError("Некорректное правило периода")
            try:
                start, end = date.fromisoformat(rule["start"]), date.fromisoformat(rule["end"])
                if start > end or not str(rule.get("reason", "")).strip():
                    raise ValueError("Укажите даты и основание правила периода")
                source["period"] = {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "calendar": (
                        "MONTH"
                        if start.day == 1
                        and end.year == start.year
                        and end.month == start.month
                        and end.day == calendar.monthrange(start.year, start.month)[1]
                        else "USER_CONFIRMED"
                    ),
                }
                source["errors"] = [e for e in source["errors"] if e["code"] != "PERIOD_REQUIRED"]
            except (KeyError, TypeError, ValueError) as exc:
                issues.append({"source_cell": key, "code": "PERIOD_REQUIRED", "message": str(exc)})
                continue
        entry = overrides.get(key)
        automatic = _auto_coordinate(source, matrix)
        if automatic:
            source["coordinate"] = automatic
            proposed = {
                "source": key,
                "coordinate": automatic,
                "quantity": source["value"],
                "confirmed": True,
                "method": "profile",
            }
            auto_mappings.append(proposed)
            if entry is None:
                entry = proposed
        if entry is None:
            error = (
                source["errors"][0]
                if source["errors"]
                else {"code": "MAPPING_REQUIRED", "message": "Выберите позицию и показатель"}
            )
            issues.append({"source_cell": key, **error})
            continue
        if entry.get("skip_reason"):
            reason = entry["skip_reason"]
            if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
                issues.append(
                    {
                        "source_cell": key,
                        "code": "MAPPING_REQUIRED",
                        "message": "Укажите причину исключения ячейки (3–500 символов)",
                    }
                )
            else:
                skipped += 1
            continue
        coordinate_raw = entry.get("coordinate")
        if (
            not isinstance(coordinate_raw, dict)
            or not (coordinate_raw.get("component_id") or coordinate_raw.get("product_id"))
            or not (coordinate_raw.get("metric_code") or coordinate_raw.get("operation_type"))
        ):
            issues.append(
                {
                    "source_cell": key,
                    "code": "MAPPING_REQUIRED",
                    "message": "Выберите позицию и показатель",
                }
            )
            continue
        if not (coordinate_raw.get("period_start") or coordinate_raw.get("operation_date")):
            issues.append(
                {
                    "source_cell": key,
                    "code": "PERIOD_REQUIRED",
                    "message": "Выберите точный период переноса",
                }
            )
            continue
        try:
            coordinate = ReportCellCoordinate.from_mapping(coordinate_raw)
            target = json.dumps(coordinate.to_dict(), sort_keys=True)
            if target not in allowed:
                raise ValueError("Выберите вводимый показатель и период действующей рабочей формы")
            if target in targets:
                raise ValueError("В одну рабочую ячейку назначено несколько значений")
            metric = coordinate.metric_code or ""
            if source.get("metric") in {"SUPPLIED", "RECEIVED", "PRODUCED"} and metric.startswith(
                "SUB_USED_"
            ):
                raise ValueError(
                    "Поставку нельзя записывать в расход: выберите показатель поступления"
                )
            # Explicit manual mapping of unknown generic fixture fields remains possible;
            # recognized enterprise periods require a block-level decision.
            if source.get("metric") and source.get("period") is None:
                issues.append(
                    {
                        "source_cell": key,
                        "code": "PERIOD_REQUIRED",
                        "message": "Подтвердите точные границы исходного периода для блока",
                    }
                )
                continue
            if (
                source.get("period")
                and coordinate.to_dict().get("period_start", coordinate.operation_date)
                != source["period"]["start"]
            ):
                raise ValueError("Период назначения не совпадает с исходными датами")
            if source.get("period") and source["period"]["calendar"] != "MONTH":
                from backend.application.report_calendar import reporting_weeks

                pstart = date.fromisoformat(source["period"]["start"])
                pend = next(
                    (
                        w.end.isoformat()
                        for w in reporting_weeks(pstart.year, pstart.month)
                        if w.start == pstart
                    ),
                    None,
                )
                if pend != source["period"]["end"]:
                    issues.append(
                        {
                            "source_cell": key,
                            "code": "PERIOD_REQUIRED",
                            "message": "Границы периода несовместимы с календарём рабочей формы",
                        }
                    )
                    continue
            value = ReportCellValue.from_mapping(
                {"kind": "QUANTITY", "quantity": decimal_text(entry.get("quantity", ""))}
            )
            if source.get("formula_status") == "UNSUPPORTED":
                raise ValueError(
                    "Исходная формула не вычислена; исправьте формулу или исключите с причиной"
                )
            if entry.get("confirmed") is not True:
                raise ValueError("Подтвердите соответствие показателя и периода")
            targets.add(target)
            changes.append(
                {
                    "source_cell": key,
                    "coordinate": coordinate.to_dict(),
                    "value": value.to_dict(),
                    "provenance": {
                        "sheet": source["sheet"],
                        "address": source["address"],
                        "profile": recognition["profile_id"],
                        "profile_version": recognition["profile_version"],
                        "formula": source["formula"],
                        "period": source["period"],
                    },
                }
            )
        except (ValueError, TypeError) as exc:
            issues.append({"source_cell": key, "code": "MAPPING_REQUIRED", "message": str(exc)})
    if not changes and not issues and not recognition["structural_actions"]:
        issues.append(
            {"source_cell": "", "code": "EMPTY_TRANSFER", "message": "Нет значений для переноса"}
        )
    return {
        "changes": changes,
        "issues": issues,
        "sources": sources,
        "auto_mappings": auto_mappings,
        "structural_actions": recognition["structural_actions"],
        "recognition": recognition,
        "skipped_count": skipped,
        "position_count": sum(
            a["kind"] == "UPSERT_POSITION" for a in recognition["structural_actions"]
        ),
    }
