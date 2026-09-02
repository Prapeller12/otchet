"""PyWebView application facade for the WORKING_REFERENCE matrix preview.

The facade is intentionally limited: it persists versioned matrix declarations,
but it does not post stock/product operations and it refuses Excel import/export
until the owner approves the form contracts and business bindings.
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from backend.api.bridge import DesktopBridge
from backend.application.report_cells import (
    ReportCellChange,
    ReportCellCoordinate,
    ReportCellError,
    ReportCellService,
    ReportCellValidationError,
    ReportCellValue,
)
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_cells import (
    SqliteReportCellUnitOfWorkFactory,
)
from backend.infrastructure.database.sqlite_report_workspace import (
    SqliteReportWorkspaceRepository,
)
from backend.repositories.report_workspace import (
    SubjectKind,
    WorkspaceGroup,
    WorkspaceGroupDraft,
    WorkspaceGroupTemplate,
)

_DEFINITION_FILES = {
    "DAILY_MOVEMENT": "daily-movement.working-reference.v0.1.0.json",
    "HEAD_SITE": "head-site.working-reference.v0.1.0.json",
    "SUBSIDIARY": "subsidiary.working-reference.v0.1.0.json",
}
_TITLES = {
    "DAILY_MOVEMENT": "Ежедневное движение и остатки",
    "HEAD_SITE": "Головная площадка",
    "SUBSIDIARY": "Дочерние общества",
}
_IMPORT_EXPORT_REASON = "Недоступно до утверждения версии формы и координатной карты."


class WorkingReferenceApplicationBridge:
    """Expose the matrix contract expected by the React PyWebView adapter."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        migrations_directory: str | Path,
        definitions_directory: str | Path,
    ) -> None:
        self._database_path = Path(database_path)
        self._definitions_directory = Path(definitions_directory)
        self._transport = DesktopBridge(
            self._database_path,
            migrations_directory=migrations_directory,
        )
        self._service = ReportCellService(SqliteReportCellUnitOfWorkFactory(self._database_path))
        self._workspace = SqliteReportWorkspaceRepository(str(self._database_path))
        self._default_organization = self._workspace.ensure_default_organization()

    def health(self) -> dict[str, object]:
        return self._transport.health()

    def bootstrap(self) -> dict[str, object]:
        return self._transport.bootstrap()

    def get_report_matrix(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"report_type", "organization_id"})
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            matrix = self._build_matrix(report_type, organization_id)
            return {"ok": True, "data": matrix, "request_id": request_id}
        except (OSError, ValueError, KeyError, json.JSONDecodeError, ReportCellError) as error:
            return _failure("WORKING_REFERENCE_ERROR", str(error), request_id)

    def save_report_cells(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request,
                {
                    "report_type",
                    "organization_id",
                    "base_revision",
                    "idempotency_key",
                    "changes",
                },
            )
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            expected_matrix_revision = _required_string(request, "base_revision")
            if expected_matrix_revision != self._matrix_revision(report_type, organization_id):
                return _failure(
                    "REVISION_CONFLICT",
                    "Матрица была изменена другим сохранением; перезагрузите форму.",
                    request_id,
                )
            raw_changes = request.get("changes")
            if not isinstance(raw_changes, Sequence) or isinstance(
                raw_changes, (str, bytes, bytearray)
            ):
                raise ReportCellValidationError("changes must be an array")

            allowed = self._editable_coordinate_keys(report_type, organization_id)
            current = {
                _coordinate_key(cell.coordinate): cell
                for cell in self._service.get_cells(
                    report_type=report_type,
                    organization_id=str(organization_id),
                )
            }
            changes: list[ReportCellChange] = []
            for raw_change in raw_changes:
                change = _mapping(raw_change, "change")
                _reject_unknown(change, {"coordinate", "value"})
                coordinate_raw = _mapping(change.get("coordinate"), "coordinate")
                value_raw = _mapping(change.get("value"), "value")
                coordinate = ReportCellCoordinate.from_mapping(coordinate_raw)
                coordinate_key = _coordinate_key(coordinate)
                if coordinate_key not in allowed:
                    raise ReportCellValidationError(
                        "cell is not editable in the WORKING_REFERENCE descriptor"
                    )
                value = ReportCellValue.from_mapping(value_raw)
                previous = current.get(coordinate_key)
                changes.append(
                    ReportCellChange(
                        coordinate=coordinate,
                        value=value,
                        expected_revision=None if previous is None else previous.revision,
                    )
                )

            saved = self._service.save_cells(
                changes,
                idempotency_key=_required_string(request, "idempotency_key"),
                actor_ref="local-working-reference",
            )
            cells = [
                {
                    "coordinate": cell.coordinate.to_dict(),
                    "value": cell.value.to_dict(),
                    "state": {"access": "editable", "persistence": "saved"},
                }
                for cell in saved
            ]
            return {
                "ok": True,
                "data": {
                    "matrix_revision": self._matrix_revision(report_type, organization_id),
                    "cells": cells,
                },
                "request_id": request_id,
            }
        except ReportCellError as error:
            return _failure(error.code, str(error), request_id)
        except sqlite3.IntegrityError:
            return _failure(
                "DATA_INTEGRITY_ERROR",
                "Изменение нарушает контракт локальной базы данных.",
                request_id,
            )
        except sqlite3.Error:
            return _failure(
                "DATABASE_ERROR",
                "Локальная база данных не завершила сохранение.",
                request_id,
            )

    def validate_import(self, _payload: object) -> dict[str, object]:
        return _failure(
            "TEMPLATE_CONTRACT_NOT_APPROVED",
            _IMPORT_EXPORT_REASON,
            uuid4().hex,
        )

    def export_report(self, _payload: object) -> dict[str, object]:
        return _failure(
            "TEMPLATE_CONTRACT_NOT_APPROVED",
            _IMPORT_EXPORT_REASON,
            uuid4().hex,
        )

    def list_organizations(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, set())
            return {
                "ok": True,
                "data": {"organizations": self._organizations_contract()},
                "request_id": request_id,
            }
        except (ValueError, sqlite3.Error, ReportCellError) as error:
            return _failure("WORKSPACE_SETTINGS_ERROR", str(error), request_id)

    def create_organization(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"name"})
            organization = self._workspace.create_organization(_required_string(request, "name"))
            return {
                "ok": True,
                "data": {
                    "organization": {
                        "id": str(organization.id),
                        "name": organization.name,
                        "kind": organization.kind,
                    }
                },
                "request_id": request_id,
            }
        except (ValueError, sqlite3.Error, ReportCellError) as error:
            return _failure("WORKSPACE_SETTINGS_ERROR", str(error), request_id)

    def rename_organization(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"organization_id", "name"})
            organization = self._workspace.rename_organization(
                _required_int_string(request, "organization_id"),
                _required_string(request, "name"),
            )
            return {
                "ok": True,
                "data": {
                    "organization": {
                        "id": str(organization.id),
                        "name": organization.name,
                        "kind": organization.kind,
                    }
                },
                "request_id": request_id,
            }
        except (ValueError, sqlite3.Error, ReportCellError) as error:
            return _failure("WORKSPACE_SETTINGS_ERROR", str(error), request_id)

    def archive_organization(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"organization_id"})
            self._workspace.archive_organization(_required_int_string(request, "organization_id"))
            return {
                "ok": True,
                "data": {"organizations": self._organizations_contract()},
                "request_id": request_id,
            }
        except (ValueError, sqlite3.Error, ReportCellError) as error:
            return _failure("WORKSPACE_SETTINGS_ERROR", str(error), request_id)

    def get_report_layout(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"report_type", "organization_id"})
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            templates = self._group_templates(report_type)
            groups = self._workspace.ensure_groups(organization_id, report_type, templates)
            return {
                "ok": True,
                "data": self._layout_contract(report_type, organization_id, templates, groups),
                "request_id": request_id,
            }
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            sqlite3.Error,
            ReportCellError,
        ) as error:
            return _failure("WORKSPACE_SETTINGS_ERROR", str(error), request_id)

    def save_report_layout(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"report_type", "organization_id", "rows"})
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            raw_rows = _sequence(request.get("rows"), "rows")
            drafts: list[WorkspaceGroupDraft] = []
            for raw_row in raw_rows:
                row = _mapping(raw_row, "row")
                _reject_unknown(row, {"id", "template_group_id", "party_name", "position_name"})
                raw_id = row.get("id")
                row_id = None if raw_id is None else _int_string(raw_id, "row.id")
                drafts.append(
                    WorkspaceGroupDraft(
                        id=row_id,
                        template_group_id=_required_string(row, "template_group_id"),
                        party_name=_required_string(row, "party_name"),
                        position_name=_required_string(row, "position_name"),
                    )
                )
            templates = self._group_templates(report_type)
            groups = self._workspace.save_groups(
                organization_id,
                report_type,
                templates,
                tuple(drafts),
            )
            return {
                "ok": True,
                "data": self._layout_contract(report_type, organization_id, templates, groups),
                "request_id": request_id,
            }
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            sqlite3.Error,
            ReportCellError,
        ) as error:
            return _failure("WORKSPACE_SETTINGS_ERROR", str(error), request_id)

    def _build_matrix(self, report_type: str, organization_id: int) -> dict[str, object]:
        definition = self._definition(report_type)
        periods = self._periods(report_type)
        rows: list[dict[str, object]] = []
        stored = {
            _coordinate_key(cell.coordinate): cell
            for cell in self._service.get_cells(
                report_type=report_type,
                organization_id=str(organization_id),
            )
        }
        layout = _mapping(definition.get("layout"), "layout")
        identifier_columns = _sequence(layout.get("identifier_columns"), "identifier_columns")
        left_columns = [
            {
                "id": _required_string(_mapping(column, "identifier column"), "column_id"),
                "label": _required_string(_mapping(column, "identifier column"), "label"),
                "width": 220 if index < 2 else 170,
            }
            for index, column in enumerate(identifier_columns)
        ]
        template_groups = {
            _required_string(group_record, "group_id"): group_record
            for group_record in (
                _mapping(group, "row group")
                for group in _sequence(layout.get("row_groups"), "row_groups")
            )
        }
        configured_groups = self._workspace.ensure_groups(
            organization_id,
            report_type,
            self._group_templates(report_type),
        )

        for configured_group in configured_groups:
            group_record = template_groups.get(configured_group.template_group_id)
            if group_record is None:
                raise ValueError("Настройка строки ссылается на неизвестный шаблон")
            for row in _sequence(group_record.get("rows"), "rows"):
                row_record = _mapping(row, "row")
                template_row_id = _required_string(row_record, "row_id")
                row_label = _required_string(row_record, "label")
                editable = row_record.get("value_role") == "WORKING_INPUT"
                code = _contract_code(template_row_id)
                cells: list[dict[str, object]] = []
                for column_id, period_start in periods:
                    coordinate = self._coordinate(
                        report_type=report_type,
                        organization_id=organization_id,
                        subject_kind=configured_group.subject_kind,
                        subject_id=configured_group.subject_id,
                        metric_code=code,
                        period_start=period_start,
                    )
                    current = stored.get(_coordinate_key(coordinate))
                    value = (
                        {"kind": "DATA_NOT_PROVIDED"}
                        if current is None
                        else current.value.to_dict()
                    )
                    access = "editable" if editable else "calculated"
                    cell: dict[str, object] = {
                        "column_id": column_id,
                        "coordinate": coordinate.to_dict(),
                        "value": value,
                        "state": {"access": access, "persistence": "saved"},
                    }
                    if not editable:
                        cell["lock_reason"] = "Расчёт заблокирован до утверждения бизнес-привязки."
                    cells.append(cell)

                left_values = {
                    _required_string(_mapping(column, "identifier column"), "column_id"): (
                        configured_group.party_name
                        if index == 0
                        else configured_group.position_name
                        if index == 1
                        else row_label
                        if index == len(identifier_columns) - 1
                        else "—"
                    )
                    for index, column in enumerate(identifier_columns)
                }
                rows.append(
                    {
                        "id": f"{template_row_id}-{configured_group.id}",
                        "group_id": f"workspace-group-{configured_group.id}",
                        "group_label": configured_group.position_name,
                        "left_values": left_values,
                        "cells": cells,
                        "indicator_detail": (
                            {"kind": "SUM", "label": "Сумма"}
                            if report_type == "DAILY_MOVEMENT" and editable
                            else {"kind": "CALCULATION", "label": "Расчёт"}
                            if not editable
                            else None
                        ),
                    }
                )

        return {
            "report_type": report_type,
            "organization_id": str(organization_id),
            "title": _TITLES[report_type],
            "subtitle": "Сквозной локальный контур на обезличенных данных",
            "form_status": "WORKING_REFERENCE",
            "source_notice": (
                "Значения сохраняются в SQLite как декларации рабочей формы; "
                "они не проводятся как складские операции."
            ),
            "matrix_revision": self._matrix_revision(report_type, organization_id),
            "left_columns": left_columns,
            "time_columns": [
                {
                    "id": column_id,
                    "label": period_start[8:10],
                    "group_label": period_start[:7],
                    "width": 76,
                }
                for column_id, period_start in periods
            ],
            "rows": rows,
            "capabilities": {
                "save": {"enabled": True},
                "import": {"enabled": False, "reason": _IMPORT_EXPORT_REASON},
                "export": {"enabled": False, "reason": _IMPORT_EXPORT_REASON},
            },
            "navigation": {"enter_direction": "down"},
        }

    def _editable_coordinate_keys(self, report_type: str, organization_id: int) -> set[str]:
        matrix = self._build_matrix(report_type, organization_id)
        keys: set[str] = set()
        for row in cast(list[dict[str, object]], matrix["rows"]):
            for cell in cast(list[dict[str, object]], row["cells"]):
                state = cast(dict[str, str], cell["state"])
                if state["access"] == "editable":
                    coordinate = ReportCellCoordinate.from_mapping(
                        cast(Mapping[str, object], cell["coordinate"])
                    )
                    keys.add(_coordinate_key(coordinate))
        return keys

    def _coordinate(
        self,
        *,
        report_type: str,
        organization_id: int,
        subject_kind: str,
        subject_id: int,
        metric_code: str,
        period_start: str,
    ) -> ReportCellCoordinate:
        subject = (
            {"product_id": str(subject_id)}
            if subject_kind == "product"
            else {"component_id": str(subject_id)}
        )
        time = (
            {"period_start": period_start}
            if report_type == "SUBSIDIARY"
            else {"operation_date": period_start}
        )
        return ReportCellCoordinate.from_mapping(
            {
                "report_type": report_type,
                "organization_id": str(organization_id),
                "metric_code": metric_code,
                **subject,
                **time,
            }
        )

    def _definition(self, report_type: str) -> dict[str, object]:
        filename = _DEFINITION_FILES.get(report_type)
        if filename is None:
            raise ReportCellValidationError("unsupported report_type")
        with (self._definitions_directory / filename).open(encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or value.get("status") != "WORKING_REFERENCE":
            raise ValueError("invalid WORKING_REFERENCE matrix descriptor")
        return cast(dict[str, object], value)

    def _periods(self, report_type: str) -> list[tuple[str, str]]:
        first = date.today().replace(day=1)
        if report_type == "SUBSIDIARY":
            starts: list[date] = []
            current = first
            while current.month == first.month:
                starts.append(current)
                current += timedelta(days=7)
            return [
                (f"period-{index + 1:02d}", value.isoformat()) for index, value in enumerate(starts)
            ]
        day_count = calendar.monthrange(first.year, first.month)[1]
        return [
            (f"day-{day:02d}", first.replace(day=day).isoformat())
            for day in range(1, day_count + 1)
        ]

    def _matrix_revision(self, report_type: str, organization_id: int) -> str:
        connection = connect_sqlite(self._database_path)
        try:
            row = connection.execute(
                """
                SELECT coalesce(max(id), 0)
                FROM report_fact_revisions
                WHERE report_type = ? AND organization_id = ?
                """,
                (report_type, organization_id),
            ).fetchone()
        finally:
            connection.close()
        return f"db-{int(row[0]) if row is not None else 0}"

    def _group_templates(self, report_type: str) -> tuple[WorkspaceGroupTemplate, ...]:
        definition = self._definition(report_type)
        layout = _mapping(definition.get("layout"), "layout")
        templates: list[WorkspaceGroupTemplate] = []
        for value in _sequence(layout.get("row_groups"), "row_groups"):
            group = _mapping(value, "row group")
            group_kind = _required_string(group, "group_kind")
            subject_kind: SubjectKind = (
                "component" if group_kind in {"COMPONENT_POSITION", "SUPPLIER_ITEM"} else "product"
            )
            templates.append(
                WorkspaceGroupTemplate(
                    template_group_id=_required_string(group, "group_id"),
                    group_kind=group_kind,
                    default_party_name="Изготовитель/поставщик",
                    default_position_name=_required_string(group, "label"),
                    subject_kind=subject_kind,
                    repeatable=group.get("repeatable") is True,
                )
            )
        return tuple(templates)

    def _organization_id(self, raw_value: object) -> int:
        organization_id = (
            self._default_organization.id
            if raw_value is None
            else _int_string(raw_value, "organization_id")
        )
        if organization_id not in {
            organization.id for organization in self._workspace.list_organizations()
        }:
            raise ValueError("Организация не найдена")
        return organization_id

    def _organizations_contract(self) -> list[dict[str, str]]:
        return [
            {"id": str(item.id), "name": item.name, "kind": item.kind}
            for item in self._workspace.list_organizations()
        ]

    def _layout_contract(
        self,
        report_type: str,
        organization_id: int,
        templates: tuple[WorkspaceGroupTemplate, ...],
        groups: tuple[WorkspaceGroup, ...],
    ) -> dict[str, object]:
        repeatable = {
            template.template_group_id: template for template in templates if template.repeatable
        }
        return {
            "report_type": report_type,
            "organization_id": str(organization_id),
            "templates": [
                {
                    "id": template.template_group_id,
                    "label": template.default_position_name,
                    "group_kind": template.group_kind,
                }
                for template in repeatable.values()
            ],
            "rows": [
                {
                    "id": str(group.id),
                    "template_group_id": group.template_group_id,
                    "party_name": group.party_name,
                    "position_name": group.position_name,
                }
                for group in groups
                if group.template_group_id in repeatable
            ],
        }


def _contract_code(value: str) -> str:
    result = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    if not result or not result[0].isalpha():
        result = f"WRK_{result}"
    return result


def _coordinate_key(coordinate: ReportCellCoordinate) -> str:
    return json.dumps(
        coordinate.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReportCellValidationError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ReportCellValidationError(f"{name} must be an array")
    return value


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ReportCellValidationError(f"{name} must be a non-empty string")
    return value


def _int_string(value: object, name: str) -> int:
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0:
        raise ReportCellValidationError(f"{name} must be a positive integer string")
    return int(value)


def _required_int_string(payload: Mapping[str, object], name: str) -> int:
    return _int_string(payload.get(name), name)


def _reject_unknown(payload: Mapping[str, object], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ReportCellValidationError(f"unknown fields: {', '.join(unknown)}")


def _failure(code: str, message: str, request_id: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "field_errors": [],
            "request_id": request_id,
        },
    }


__all__ = ["WorkingReferenceApplicationBridge"]
