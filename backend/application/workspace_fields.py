"""Validate and evaluate user-owned worksheet field configuration."""

from __future__ import annotations

import ast
import base64
import binascii
import io
import json
import re
import warnings
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from PIL import Image

from backend.domain.worksheet_formulas import FormulaError, evaluate_formula, parse_formula

CATEGORIES = {"UNSPECIFIED", "PKI", "DSE", "PART", "PRODUCT", "ASSEMBLY"}


def validate_configuration(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Настройки позиции должны быть объектом")
    allowed = {
        "category",
        "image",
        "norm",
        "opening",
        "opening_date",
        "indicators",
        "subsidiary",
        "head_links",
    }
    if raw.keys() - allowed:
        raise ValueError("Неизвестные настройки позиции")
    category = raw.get("category", "UNSPECIFIED")
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ValueError("Выберите ПКИ, ДСЕ, составную часть, изделие или комплект")
    result: dict[str, Any] = {"category": category}
    if "head_links" in raw:
        links = raw["head_links"]
        if (
            not isinstance(links, list)
            or len(links) > 50
            or any(not isinstance(v, str) or not v.isdigit() for v in links)
            or len(set(links)) != len(links)
        ):
            raise ValueError("Выберите разные отчёты дочерних обществ")
        result["head_links"] = links
    if "subsidiary" in raw:
        from backend.application.subsidiary_report import validate_detail

        result["subsidiary"] = validate_detail(raw["subsidiary"])
    if "opening_date" in raw:
        opening_date = raw["opening_date"]
        if not isinstance(opening_date, str) or (
            opening_date and date.fromisoformat(opening_date).isoformat() != opening_date
        ):
            raise ValueError("Дата начального остатка должна быть в формате ГГГГ-ММ-ДД")
        result["opening_date"] = opening_date
    for key in ("norm", "opening"):
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError("Норма и начальный остаток должны быть десятичными строками")
        if value:
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("Неверное числовое значение настройки") from exc
            if (
                not number.is_finite()
                or number.copy_abs() > Decimal("1e50")
                or (number != 0 and number.copy_abs() < Decimal("1e-50"))
            ):
                raise ValueError("Числовая настройка вне допустимого диапазона")
            if key == "norm" and number <= 0:
                raise ValueError("Норма входимости должна быть больше нуля")
            if number == 0:
                value = "0"
        result[key] = value
    image = raw.get("image", "")
    if not isinstance(image, str) or len(image) > 2_800_000:
        raise ValueError("Изображение должно быть PNG/JPEG размером не более 2 МБ")
    if image:
        try:
            header, encoded = image.split(",", 1)
            if header not in {"data:image/png;base64", "data:image/jpeg;base64"}:
                raise ValueError("Поддерживаются PNG и JPEG")
            data = base64.b64decode(encoded, validate=True)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Изображение больше 2 МБ")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.width * picture.height > 16_000_000:
                        raise ValueError("Изображение больше 16 мегапикселей")
                    expected = "PNG" if "png" in header else "JPEG"
                    if picture.format != expected:
                        raise ValueError("Содержимое изображения не соответствует формату")
                    picture.verify()
                with Image.open(io.BytesIO(data)) as picture:
                    picture.load()
        except (
            OSError,
            binascii.Error,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise ValueError("Изображение повреждено или слишком велико") from exc
    result["image"] = image
    indicators = raw.get("indicators", [])
    if not isinstance(indicators, list) or len(indicators) > 40:
        raise ValueError("Разрешено не более 40 показателей позиции")
    codes: set[str] = set()
    normalized = []
    for item in indicators:
        if not isinstance(item, dict) or item.keys() - {"code", "label", "formula"}:
            raise ValueError("Неверный показатель")
        code, label, formula = (item.get(key, "") for key in ("code", "label", "formula"))
        if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) is None:
            raise ValueError("Код показателя: латинские заглавные буквы, цифры и _")
        if code in codes or code in {
            "NORM",
            "OPENING",
            "SUM",
            "MIN",
            "MAX",
            "IF",
            "CUM",
            "CUMSUM",
            "BALANCE",
            "ROUNDDOWN",
        }:
            raise ValueError("Код показателя повторяется или зарезервирован")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ValueError("Укажите название показателя (до 200 символов)")
        if not isinstance(formula, str):
            raise ValueError("Формула должна быть строкой")
        if code == "READY_SETS" and not formula.strip():
            raise ValueError("READY_SETS — расчётный показатель; укажите формулу")
        if formula.strip():
            parse_formula(formula)
        normalized.append({"code": code, "label": label.strip(), "formula": formula.strip()})
        codes.add(code)
    dependencies: dict[str, set[str]] = {}
    for item in normalized:
        if not item["formula"]:
            dependencies[item["code"]] = set()
            continue
        tree = parse_formula(item["formula"])
        functions = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and id(node) not in functions
        }
        unknown = names - codes - {"NORM", "OPENING"}
        if unknown:
            raise ValueError("Неизвестные показатели в формуле: " + ", ".join(sorted(unknown)))
        dependencies[item["code"]] = names & codes

    complete: set[str] = set()

    def check(code: str, path: set[str]) -> None:
        if code in path:
            raise ValueError("Циклическая ссылка в формуле: " + code)
        if code in complete:
            return
        for dependency in dependencies[code]:
            check(dependency, path | {code})
        complete.add(code)

    for code in codes:
        check(code, set())
    result["indicators"] = normalized
    return result


def configuration_json(raw: object) -> str:
    return json.dumps(validate_configuration(raw), ensure_ascii=False, sort_keys=True)


def calculate_fields(
    config: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    """Evaluate columns using stable indicator codes; leave missing inputs missing."""
    by_code = {row["metric_code"]: row for row in rows}
    formulas = {
        item["code"]: item["formula"] for item in config.get("indicators", []) if item["formula"]
    }
    cache: dict[tuple[str, int], Decimal | None] = {}
    active: set[tuple[str, int]] = set()
    prefixes: dict[tuple[str, int], list[tuple[Decimal | None, bool]]] = {}
    month_starts: list[int] = []
    last_month = ""
    month_start = 0
    dates: list[str] = []
    for i, cell in enumerate(rows[0]["cells"] if rows else []):
        coordinate = cell["coordinate"]
        day = str(coordinate.get("operation_date", coordinate.get("period_start", "")))
        dates.append(day)
        month = day[:7]
        if month != last_month:
            month_start, last_month = i, month
        month_starts.append(month_start)
    opening_date = str(config.get("opening_date", ""))
    opening_start = next((i for i, day in enumerate(dates) if day >= opening_date), len(dates))

    def prefix(name: str, index: int, start: int) -> tuple[Decimal | None, bool]:
        entries = prefixes.setdefault((name, start), [])
        while len(entries) <= index - start:
            total, missing = entries[-1] if entries else (None, False)
            current = value(name, start + len(entries))
            entries.append(
                (
                    total if current is None else (total or Decimal(0)) + current,
                    missing or current is None,
                )
            )
        return entries[index - start]

    def value(code: str, index: int) -> Decimal | None:
        if code in {"NORM", "OPENING"}:
            number = config.get("norm" if code == "NORM" else "opening", "")
            return Decimal(number) if number else None
        key = (code, index)
        if key in cache:
            return cache[key]
        if key in active:
            raise FormulaError("Циклическая ссылка")
        if code not in by_code:
            raise FormulaError("Неизвестный показатель: " + code)
        active.add(key)
        try:
            if code in formulas:

                def cumulative(name: str) -> Decimal | None:
                    total, missing = prefix(name, index, month_starts[index])
                    return None if missing else total

                def recorded_sum(name: str) -> Decimal | None:
                    return prefix(name, index, 0)[0]

                def balance(received: str, used: str) -> Decimal | None:
                    if index < opening_start:
                        return None
                    incoming = prefix(received, index, opening_start)[0]
                    outgoing = prefix(used, index, opening_start)[0]
                    opening = value("OPENING", index)
                    if incoming is None and outgoing is None and opening is None:
                        return None
                    return (
                        (opening or Decimal(0))
                        + (incoming or Decimal(0))
                        - (outgoing or Decimal(0))
                    )

                result = evaluate_formula(
                    formulas[code],
                    lambda name: value(name, index),
                    cumulative,
                    recorded_sum,
                    balance,
                )
            else:
                cell_value = by_code[code]["cells"][index]["value"]
                result = (
                    Decimal(cell_value["quantity"]) if cell_value["kind"] == "QUANTITY" else None
                )
            cache[key] = result
            return result
        finally:
            active.remove(key)

    for code, row in by_code.items():
        if code not in formulas:
            continue
        for index, cell in enumerate(row["cells"]):
            cell["formula"] = formulas[code]
            cell.pop("lock_reason", None)
            try:
                calculated = value(code, index)
                cell["value"] = (
                    {"kind": "DATA_NOT_PROVIDED"}
                    if calculated is None
                    else {"kind": "QUANTITY", "quantity": format(calculated, "f")}
                )
            except FormulaError as exc:
                cell["value"] = {"kind": "DATA_NOT_PROVIDED"}
                cell["state"]["persistence"] = "error"
                cell["issue"] = {"code": "FORMULA_ERROR", "message": str(exc)}


def calculate_ready_sets(rows: list[dict[str, Any]]) -> None:
    """Take the source workbook's minimum over explicitly configured worksheet positions."""
    candidates = [row for row in rows if row.get("metric_code") == "READY_SETS"]
    for row in rows:
        if row.get("metric_code") != "WRK_DAILY_READY_SETS" or not candidates:
            continue
        for index, cell in enumerate(row["cells"]):
            sources = [item["cells"][index] for item in candidates]
            cell.pop("lock_reason", None)
            cell["formula"] = "MIN(READY_SETS по настроенным позициям)"
            issues = [item["issue"] for item in sources if item.get("issue")]
            if issues:
                cell["state"]["persistence"] = "error"
                cell["issue"] = {
                    "code": "FORMULA_ERROR",
                    "message": "Ошибка исходной формулы: " + issues[0]["message"],
                }
            values = [item["value"] for item in sources]
            cell["value"] = (
                {
                    "kind": "QUANTITY",
                    "quantity": format(min(Decimal(item["quantity"]) for item in values), "f"),
                }
                if all(item["kind"] == "QUANTITY" for item in values)
                else {"kind": "DATA_NOT_PROVIDED"}
            )
