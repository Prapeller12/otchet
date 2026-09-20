"""User-owned report headings and monthly finished-product code breakdowns.

Legacy aggregate plans/actuals stay persisted. Code totals only supersede months
explicitly present in the breakdown; missing code values never become zero.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from backend.application.subsidiary_report import quantity
from backend.application.workspace_fields import validate_configuration
from backend.domain.calculations import sum_quantities

HEADER_FIELDS = {"product_designation", "product_name", "factory_name", "product_image"}


def validate_header(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict) or raw.keys() - HEADER_FIELDS:
        raise ValueError("Неверные поля шапки отчёта")
    result = {}
    for field in sorted(HEADER_FIELDS - {"product_image"}):
        value = raw.get(field, "")
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError("Шифр, наименование и завод: до 200 символов")
        result[field] = value.strip()
    result["product_image"] = validate_configuration({"image": raw.get("product_image", "")})[
        "image"
    ]
    return result


def validate_production_codes(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 50:
        raise ValueError("Разрешено до 50 кодов выпуска")
    result = []
    identities: set[str] = set()
    labels: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or item.keys() - {"id", "label", "plans", "actuals"}:
            raise ValueError("Неверная структура кода выпуска")
        identity, label = item.get("id"), item.get("label")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[A-Z0-9]{1,32}", identity)
            or identity in identities
        ):
            raise ValueError("Идентификатор кода выпуска неверен или повторяется")
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label) > 200
            or label.strip().casefold() in labels
        ):
            raise ValueError("Укажите разные названия кодов выпуска (до 200 символов)")
        identities.add(identity)
        labels.add(label.strip().casefold())
        normalized: dict[str, Any] = {"id": identity, "label": label.strip()}
        for field in ("plans", "actuals"):
            values = item.get(field, {})
            if (
                not isinstance(values, dict)
                or len(values) > 1200
                or any(
                    not isinstance(key, str)
                    or re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", key) is None
                    for key in values
                )
            ):
                raise ValueError("Неверные месяцы выпуска по кодам")
            normalized[field] = {key: quantity(value) for key, value in values.items()}
        result.append(normalized)
    return result


def _code_totals(codes: list[dict[str, Any]], field: str) -> dict[str, str]:
    months = {month for code in codes for month in code[field]}
    totals = {}
    for month in sorted(months):
        values = [code[field].get(month, "") for code in codes]
        totals[month] = (
            format(sum_quantities(Decimal(value) for value in values), "f")
            if all(value != "" for value in values)
            else ""
        )
    return totals


def effective_production_presentation(
    presentation: Mapping[str, Any], year: int | None = None
) -> dict[str, Any]:
    """Read model; never persist this over the legacy aggregate source values."""
    result = dict(presentation)
    codes = validate_production_codes(presentation.get("production_codes", []))
    for field in ("plans", "actuals"):
        result[field] = {**presentation.get(field, {}), **_code_totals(codes, field)}
    if year is not None:
        annual: dict[str, Any] = {}
        for field, target in (("plans", "plan"), ("actuals", "actual")):
            values = [result[field].get(f"{year:04d}-{month:02d}", "") for month in range(1, 13)]
            known = [Decimal(value) for value in values if value != ""]
            annual[target] = format(sum_quantities(known), "f") if known else ""
            annual[target + "_months"] = len(known)
        plan, actual = annual["plan"], annual["actual"]
        annual["completion"] = (
            format(Decimal(actual) / Decimal(plan) * 100, ".2f")
            if plan and Decimal(plan) > 0 and actual != ""
            else ""
        )
        result["annual"] = annual
        code_annual = {}
        for code in codes:
            values = [
                Decimal(value)
                for month, value in code["actuals"].items()
                if month.startswith(f"{year:04d}-") and value != ""
            ]
            code_annual[code["id"]] = format(sum_quantities(values), "f") if values else ""
        result["production_code_annual"] = code_annual
    return result


def apply_header_patch(
    current: Mapping[str, Any], request: Mapping[str, Any], report_type: str
) -> dict[str, Any]:
    """Validate an additive presentation patch, retaining other years and sources."""
    patch: dict[str, Any] = {}
    if "header" in request:
        raw = request["header"]
        if not isinstance(raw, dict):
            raise ValueError("Неверные поля шапки отчёта")
        patch["header"] = validate_header({**current.get("header", {}), **raw})
    if "production_code_actuals" in request:
        if "production_codes" in request:
            raise ValueError("Сохраните настройку кодов и фактический выпуск отдельными действиями")
        actuals = request["production_code_actuals"]
        existing = validate_production_codes(current.get("production_codes", []))
        known = {code["id"] for code in existing}
        if not isinstance(actuals, dict) or not actuals or actuals.keys() - known:
            raise ValueError("Выберите существующие коды выпуска; новые коды задаёт администратор")
        for code in existing:
            if code["id"] not in actuals:
                continue
            values = actuals[code["id"]]
            if not isinstance(values, dict):
                raise ValueError("Неверные месяцы фактического выпуска по кодам")
            code["actuals"] = {**code["actuals"], **values}
        # Reuse all numeric/month/legacy-total checks without accepting plan,
        # label or identifier changes through this reviewer-only command.
        request = {**request, "production_codes": existing}
    if "production_codes" not in request:
        return patch
    if report_type != "HEAD_SITE":
        raise ValueError("Выпуск по кодам доступен в отчёте головной площадки")
    codes = validate_production_codes(request["production_codes"])
    previous = validate_production_codes(current.get("production_codes", []))
    by_id = {code["id"]: code for code in codes}
    for old in previous:
        replacement = by_id.get(old["id"])
        if replacement is None:
            if any(value != "" for field in ("plans", "actuals") for value in old[field].values()):
                raise ValueError("Код содержит данные выпуска; сначала явно очистите его значения")
            continue
        for field in ("plans", "actuals"):
            replacement[field] = {**old[field], **replacement[field]}
    acknowledged = request.get("confirm_production_totals", False)
    if not isinstance(acknowledged, bool):
        raise ValueError("Неверное подтверждение итогов выпуска")
    structure_changed = {code["id"] for code in previous} != {code["id"] for code in codes}
    for field in ("plans", "actuals"):
        old_totals = _code_totals(previous, field)
        new_totals = _code_totals(codes, field)
        for month, value in new_totals.items():
            legacy = old_totals.get(month, current.get(field, {}).get(month, ""))
            if (month not in old_totals or structure_changed) and legacy != "":
                differs = value == "" or Decimal(value) != Decimal(legacy)
                if differs and not acknowledged:
                    raise ValueError(
                        "Суммы по кодам отличаются от сохранённого общего выпуска "
                        f"за {month}. Подтвердите замену расчётных итогов; "
                        "прежние значения сохранятся."
                    )
    patch["production_codes"] = validate_production_codes(codes)
    return patch
