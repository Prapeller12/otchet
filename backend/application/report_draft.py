"""One report/month draft saved atomically through persistence ports.

Authentication, complete backups and post-commit signing remain in the secure
desktop facade. This service never stores a personal authorization code.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast

from backend.application.report_cells import (
    IdempotencyConflictError,
    ReportCellChange,
    ReportCellError,
    ReportCellValidationError,
    RevisionConflictError,
)
from backend.application.report_header import apply_header_patch, effective_production_presentation
from backend.application.subsidiary_report import quantity
from backend.repositories.report_facts import ReportCellUnitOfWork

DRAFT_COMMAND = "SAVE_REPORT_DRAFT_V1"


class ReportDraftOutcomeUnknown(ReportCellError):
    code = "SAVE_OUTCOME_UNKNOWN"


PRESENTATION_FIELDS = frozenset(
    {
        "title",
        "widths",
        "plans",
        "actuals",
        "header",
        "production_code_plans",
        "production_code_actuals",
        "confirm_production_totals",
    }
)


class DraftUnitOfWork(ReportCellUnitOfWork, Protocol):
    def revision(self, report_type: str, organization_id: int) -> str: ...

    def presentation(self, report_type: str, organization_id: int) -> dict[str, object]: ...

    def save_presentation(
        self, report_type: str, organization_id: int, patch: Mapping[str, object], actor_ref: str
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ReportDraft:
    report_type: str
    organization_id: int
    year: int
    month: int
    week_start: str | None
    base_revision: str
    idempotency_key: str
    actor_ref: str
    changes: Sequence[ReportCellChange]
    presentation: Mapping[str, object]
    allowed_coordinates: frozenset[str]

    def context(self) -> dict[str, object]:
        return {
            "report_type": self.report_type,
            "organization_id": str(self.organization_id),
            "year": self.year,
            "month": self.month,
            "week_start": self.week_start,
        }


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_presentation(raw: Mapping[str, object], month_key: str) -> dict[str, object]:
    """Validate request-only fields before calculating its stable fingerprint."""
    if raw.keys() - PRESENTATION_FIELDS:
        raise ReportCellValidationError("Пакет не изменяет структуру формы или коды выпуска")
    result = dict(raw)
    for field in ("plans", "actuals"):
        if field in result:
            result[field] = _month_values(result[field], month_key)
    for field in ("production_code_plans", "production_code_actuals"):
        if field in result:
            codes = result[field]
            if not isinstance(codes, Mapping) or not codes or len(codes) > 50:
                raise ReportCellValidationError("Неверные значения выпуска по кодам")
            result[field] = {
                identity: _month_values(values, month_key) for identity, values in codes.items()
            }
    return result


def _month_values(raw: object, month_key: str) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) != {month_key}:
        raise ReportCellValidationError("Пакет содержит значения другого месяца")
    return {month_key: quantity(raw[month_key])}


def presentation_patch(
    current: Mapping[str, object], request: Mapping[str, object], report_type: str
) -> dict[str, object]:
    patch = apply_header_patch(current, request, report_type)
    for field in ("plans", "actuals"):
        if field in request:
            if report_type not in {"SUBSIDIARY", "HEAD_SITE"}:
                raise ReportCellValidationError("План и выпуск доступны в месячных отчётах")
            patch[field] = {
                **cast(dict[str, object], current.get(field, {})),
                **cast(dict[str, object], request[field]),
            }
    if "title" in request:
        title = request["title"]
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ReportCellValidationError("Название отчёта: от 1 до 200 символов")
        patch["title"] = title.strip()
    if "widths" in request:
        widths = request["widths"]
        if (
            not isinstance(widths, Mapping)
            or len(widths) > 400
            or any(
                not isinstance(key, str)
                or len(key) > 80
                or type(width) is not int
                or not 48 <= width <= 600
                for key, width in widths.items()
            )
        ):
            raise ReportCellValidationError("Ширина столбца: от 48 до 600 пикселей")
        patch["widths"] = dict(widths)
    return patch


class ReportDraftService:
    def __init__(self, factory: Callable[[], DraftUnitOfWork]) -> None:
        self._factory = factory

    def save(self, draft: ReportDraft) -> dict[str, Any]:
        if type(draft.year) is not int or not 1900 <= draft.year <= 2100:
            raise ReportCellValidationError("Неверный год черновика")
        if type(draft.month) is not int or not 1 <= draft.month <= 12:
            raise ReportCellValidationError("Неверный месяц черновика")
        if not draft.idempotency_key.strip() or not draft.actor_ref.strip():
            raise ReportCellValidationError("Укажите идентификатор сохранения и автора")
        presentation = normalize_presentation(
            draft.presentation, f"{draft.year:04d}-{draft.month:02d}"
        )
        if not draft.changes and not draft.presentation:
            raise ReportCellValidationError("Черновик не содержит изменений")
        for change in draft.changes:
            coordinate = change.coordinate
            period = date.fromisoformat(str(coordinate.operation_date or coordinate.period_start))
            if (
                coordinate.report_type != draft.report_type
                or coordinate.organization_id != str(draft.organization_id)
                or (period.year, period.month) != (draft.year, draft.month)
            ):
                raise ReportCellValidationError("Ячейка не принадлежит отчёту и месяцу черновика")
        keys = [change.coordinate.key for change in draft.changes]
        if len(keys) != len(set(keys)):
            raise ReportCellValidationError("Пакет содержит повторные ячейки")
        payload = {
            "context": draft.context(),
            "actor_ref": draft.actor_ref,
            "base_revision": draft.base_revision,
            "presentation": presentation,
            "changes": sorted(
                [
                    {"coordinate": change.coordinate.to_dict(), "value": change.value.to_dict()}
                    for change in draft.changes
                ],
                key=canonical,
            ),
        }
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with self._factory() as transaction:
            previous = transaction.idempotency.get(DRAFT_COMMAND, draft.idempotency_key)
            if previous is not None:
                if previous.request_sha256 != digest:
                    raise IdempotencyConflictError("Ключ сохранения уже относится к другому пакету")
                envelope = json.loads(previous.response_json)
                return result_for_current(
                    envelope,
                    transaction.revision(draft.report_type, draft.organization_id),
                    replay=True,
                )
            if (
                transaction.revision(draft.report_type, draft.organization_id)
                != draft.base_revision
            ):
                raise RevisionConflictError("Форма изменилась. Пакет не записан; обновите данные")
            if any(
                canonical(change.coordinate.to_dict()) not in draft.allowed_coordinates
                for change in draft.changes
            ):
                raise ReportCellValidationError("Ячейка не разрешена для ввода")
            current_presentation = transaction.presentation(
                draft.report_type, draft.organization_id
            )
            patch = presentation_patch(current_presentation, presentation, draft.report_type)
            cells: list[dict[str, object]] = []
            for change in draft.changes:
                previous_fact = transaction.facts.get_current(change.coordinate.to_record())
                added = transaction.facts.add_revision(
                    coordinate=change.coordinate.to_record(),
                    value_kind=change.value.kind,
                    quantity=change.value.quantity,
                    revision=1 if previous_fact is None else previous_fact.revision + 1,
                    previous_revision_id=None if previous_fact is None else previous_fact.id,
                )
                cell: dict[str, object] = {
                    "coordinate": change.coordinate.to_dict(),
                    "value": change.value.to_dict(),
                    "state": {"access": "editable", "persistence": "saved"},
                }
                cells.append(cell)
                transaction.audit.add(
                    actor_ref=draft.actor_ref,
                    entity_type="REPORT_FACT",
                    entity_id=str(added.id),
                    action="DECLARE",
                    before_json=None
                    if previous_fact is None
                    else canonical(
                        {
                            "kind": previous_fact.value_kind,
                            "quantity": previous_fact.quantity,
                            "revision": previous_fact.revision,
                        }
                    ),
                    after_json=canonical({**cell, "revision": added.revision}),
                )
            presentation = current_presentation
            if patch:
                presentation = transaction.save_presentation(
                    draft.report_type, draft.organization_id, patch, draft.actor_ref
                )
            revision = transaction.revision(draft.report_type, draft.organization_id)
            result = {
                "data_state": "committed",
                "operation_revision": revision,
                "current_revision": revision,
                "matrix_revision": revision,
                "idempotent_replay": False,
                "cells": cells,
                "presentation": effective_production_presentation(presentation, draft.year),
                "verification_state": "pending",
                "backup_state": "pending",
            }
            transaction.idempotency.add(
                command_name=DRAFT_COMMAND,
                idempotency_key=draft.idempotency_key,
                request_sha256=digest,
                response_json=canonical(
                    {
                        "actor_ref": draft.actor_ref,
                        "context": draft.context(),
                        "result": result,
                    }
                ),
            )
        return result


def result_for_current(
    envelope: Mapping[str, Any], current_revision: str, *, replay: bool
) -> dict[str, Any]:
    result = {
        **envelope["result"],
        "current_revision": current_revision,
        "matrix_revision": current_revision,
        "idempotent_replay": replay,
    }
    if result["operation_revision"] != current_revision:
        result["verification_state"] = "stale"
    return result
