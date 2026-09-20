"""Coordinate save/print attestations inside the locked, authorized desktop facade.

Saving and signing are separate durable operations. Once a save succeeds, a failed
attestation must never be reported as a failed save: callers receive an explicit
verification_error alongside their saved data.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date
from typing import Any

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.monthly_report import snapshot_hash
from backend.application.report_cells import ReportCellError


def prepare_save_confirmation(
    application: WorkingReferenceApplicationBridge,
    method: str,
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate the selected signing period before any report data is written."""
    clean = dict(request)
    context = clean.pop("confirmation", None)
    if method == "save_report_presentation":
        clean.pop("year", None)
    if context is None and method == "save_report_presentation":
        changed = clean.keys() - {"report_type", "organization_id", "expected_revision"}
        if changed == {"widths"}:
            return clean, None
    if not isinstance(context, Mapping):
        raise ValueError("Укажите месяц отчёта для подтверждения сохранённых данных")
    if context.keys() - {"year", "month", "week_start"}:
        raise ValueError("Неизвестные параметры подтверждения отчёта")
    year = context.get("year", request.get("year"))
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2100:
        raise ValueError("Укажите год отчёта от 1900 до 2100")
    if "year" in request and request["year"] != year:
        raise ValueError("Год подтверждения не совпадает с годом отчёта")
    month = context.get("month")
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("Выберите месяц от 1 до 12")
    snapshot_request = {
        "report_type": request.get("report_type"),
        "organization_id": request.get("organization_id"),
        "year": year,
        "month": month,
    }
    if "week_start" in context:
        week = context["week_start"]
        if not isinstance(week, str):
            raise ValueError("Укажите дату недели в формате ГГГГ-ММ-ДД")
        selected = date.fromisoformat(week)
        if selected.isoformat() != week or selected.year != year or selected.month != month:
            raise ValueError("Неделя подтверждения не принадлежит выбранному месяцу")
        snapshot_request["week_start"] = week
    # Also validate that a selected week belongs to this report's month. Error
    # rows are allowed here: the requested edit may be correcting those rows.
    application._monthly_snapshot(snapshot_request)
    return clean, snapshot_request


def _confirm(
    application: WorkingReferenceApplicationBridge,
    context: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = application._monthly_snapshot(context)
    return application._verification.verify(
        snapshot,
        str(authorization["signer_id"]),
        str(authorization["pin"]),
        snapshot_hash(snapshot),
    )


def confirm_saved_report(
    application: WorkingReferenceApplicationBridge,
    result: dict[str, Any],
    context: Mapping[str, Any] | None,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Sign committed data, retaining a truthful successful-save response."""
    if not result.get("ok") or context is None:
        return result
    try:
        confirmation = {"verification": _confirm(application, context, authorization)}
    except (OSError, ValueError, KeyError, sqlite3.Error, ReportCellError) as error:
        confirmation = {"verification_error": {"code": "VERIFICATION_ERROR", "message": str(error)}}
    return {**result, "data": {**result["data"], **confirmation}}


def confirm_print_report(
    application: WorkingReferenceApplicationBridge,
    request: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Sign the expected saved revision before opening the native PDF dialog."""
    if request.keys() - {
        "report_type",
        "organization_id",
        "year",
        "month",
        "week_start",
        "expected_revision",
    }:
        raise ValueError("Неизвестные параметры печати отчёта")
    return _confirm(application, request, authorization)
