"""PyWebView application facade for the local reporting workspace."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from backend.api.bridge import DesktopBridge
from backend.application.excel_reports import (
    ExcelReportError,
    ExcelReportService,
    ExcelWorkbookValidationError,
)
from backend.application.monthly_report import monthly_snapshot
from backend.application.reference_transfer import validate_transfer
from backend.application.report_cells import (
    ReportCellChange,
    ReportCellCoordinate,
    ReportCellError,
    ReportCellService,
    ReportCellValidationError,
    ReportCellValue,
)
from backend.application.subsidiary_report import build_rows, default_detail, quantity
from backend.application.workspace_fields import (
    calculate_fields,
    calculate_ready_sets,
    configuration_json,
)
from backend.desktop.database_bootstrap import backup_database
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_excel_imports import (
    SqliteExcelImportRepository,
)
from backend.infrastructure.database.sqlite_reference_reports import ReferenceReports
from backend.infrastructure.database.sqlite_report_cells import (
    SqliteReportCellUnitOfWorkFactory,
)
from backend.infrastructure.database.sqlite_report_verification import (
    SqliteReportVerificationRepository,
    database_stamp,
)
from backend.infrastructure.database.sqlite_report_workspace import (
    SqliteReportWorkspaceRepository,
)
from backend.infrastructure.excel.openpyxl_matrix_workbook import (
    OpenpyxlMatrixWorkbookAdapter,
)
from backend.infrastructure.monthly_pdf import render_monthly_pdf
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
_FILE_DIALOG_REASON = "Выбор Excel-файла доступен только в запущенной desktop-версии."


class WorkingReferenceApplicationBridge:
    """Expose the matrix contract expected by the React PyWebView adapter."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        migrations_directory: str | Path,
        definitions_directory: str | Path,
        inbox_directory: str | Path | None = None,
        backups_directory: str | Path | None = None,
        application_version: str = "development",
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
        root = self._database_path.parent.parent
        self._backups_directory = Path(backups_directory or root / "backups")
        self._application_version = application_version
        self._excel = ExcelReportService(
            report_cells=self._service,
            import_repository=SqliteExcelImportRepository(str(self._database_path)),
            workbook_adapter=OpenpyxlMatrixWorkbookAdapter(),
            database_path=self._database_path,
            inbox_directory=Path(inbox_directory or root / "imports" / "inbox"),
            backups_directory=Path(backups_directory or root / "backups"),
            application_version=application_version,
        )
        self._references = ReferenceReports(self._database_path)
        self._verification = SqliteReportVerificationRepository(str(self._database_path))
        self._save_pdf_file: Callable[[str], Path | None] | None = None
        self._open_excel_file: Callable[[], Path | None] | None = None
        self._save_excel_file: Callable[[str], Path | None] | None = None

    def configure_excel_dialogs(
        self,
        *,
        open_file: Callable[[], Path | None],
        save_file: Callable[[str], Path | None],
    ) -> None:
        """Attach native file dialogs after the PyWebView window exists."""

        self._open_excel_file = open_file
        self._save_excel_file = save_file

    def configure_pdf_dialog(self, save_file: Callable[[str], Path | None]) -> None:
        self._save_pdf_file = save_file

    def _monthly_snapshot(self, request: Mapping[str, object]) -> dict[str, Any]:
        report_type = _required_string(request, "report_type")
        organization_id = self._organization_id(request.get("organization_id"))
        year = _year(request) or date.today().year
        # Initialise configured groups before taking a stable database stamp.
        self._build_matrix(report_type, organization_id, year)
        with closing(connect_sqlite(self._database_path)) as connection:
            before = database_stamp(connection)
        matrix = self._build_matrix(report_type, organization_id, year)
        organization = next(
            o.name for o in self._workspace.list_organizations() if o.id == organization_id
        )
        with closing(connect_sqlite(self._database_path)) as connection:
            if before != database_stamp(connection):
                raise ValueError("Данные изменились при чтении. Повторите действие")
        if (
            "expected_revision" in request
            and request["expected_revision"] != matrix["matrix_revision"]
        ):
            raise ValueError(
                "Отчёт изменился. Перезагрузите форму перед печатью или подтверждением"
            )
        snapshot = monthly_snapshot(
            matrix, cast(int, request.get("month")), organization, request.get("week_start")
        )
        snapshot["_stamp"] = before
        return snapshot

    def get_report_verification(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request,
                {
                    "report_type",
                    "organization_id",
                    "year",
                    "month",
                    "expected_revision",
                    "week_start",
                },
            )
            snapshot = self._monthly_snapshot(request)
            return {
                "ok": True,
                "data": self._verification.status(snapshot),
                "request_id": request_id,
            }
        except (OSError, ValueError, KeyError, sqlite3.Error, ReportCellError) as error:
            return _failure("VERIFICATION_ERROR", str(error), request_id)

    def verify_report(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request,
                {
                    "report_type",
                    "organization_id",
                    "year",
                    "month",
                    "week_start",
                    "signer_name",
                    "snapshot_sha256",
                    "confirmed",
                    "expected_revision",
                },
            )
            if request.get("confirmed") is not True:
                raise ValueError("Подтвердите, что данные проверены")
            snapshot = self._monthly_snapshot(request)
            result = self._verification.verify(
                snapshot,
                _required_string(request, "signer_name"),
                _required_string(request, "snapshot_sha256"),
            )
            return {"ok": True, "data": result, "request_id": request_id}
        except (OSError, ValueError, KeyError, sqlite3.Error, ReportCellError) as error:
            return _failure("VERIFICATION_ERROR", str(error), request_id)

    def export_pdf(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request,
                {
                    "report_type",
                    "organization_id",
                    "year",
                    "month",
                    "expected_revision",
                    "week_start",
                },
            )
            if self._save_pdf_file is None:
                raise ValueError("Сохранение PDF доступно в desktop-версии")
            snapshot = self._monthly_snapshot(request)
            if any(row["errors"] for row in snapshot["rows"]):
                raise ValueError(
                    "Печать невозможна. Исправьте расхождения:\n"
                    + "\n".join(
                        dict.fromkeys(error for row in snapshot["rows"] for error in row["errors"])
                    )
                )
            destination = self._save_pdf_file(
                f"{snapshot['report_type'].lower()}-{snapshot['period']}.pdf"
            )
            if destination is None:
                return {"ok": True, "data": {"cancelled": True}, "request_id": request_id}
            if destination.suffix.lower() != ".pdf":
                raise ValueError("Выберите файл с расширением .pdf")
            content = render_monthly_pdf(
                snapshot,
                self._verification.status(snapshot),
                self._definitions_directory.parent / "fonts" / "ReportingSerif.ttf",
            )
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, suffix=".pdf.tmp", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                os.replace(temporary, destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return {
                "ok": True,
                "data": {
                    "cancelled": False,
                    "file_name": destination.name,
                    "file_path": str(destination),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
                "request_id": request_id,
            }
        except (OSError, ValueError, KeyError, sqlite3.Error, ReportCellError) as error:
            return _failure("PDF_EXPORT_ERROR", str(error), request_id)

    def health(self) -> dict[str, object]:
        return self._transport.health()

    def bootstrap(self) -> dict[str, object]:
        return self._transport.bootstrap()

    def get_report_matrix(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"report_type", "organization_id", "year", "preview_changes"})
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            matrix = self._build_matrix(
                report_type, organization_id, _year(request), request.get("preview_changes")
            )
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
                    "year",
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

            allowed = self._editable_coordinate_keys(report_type, organization_id, _year(request))
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

    def validate_import(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"report_type", "organization_id", "year"})
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            if self._open_excel_file is None:
                raise ExcelReportError(_FILE_DIALOG_REASON)
            source = self._open_excel_file()
            if source is None:
                return {"ok": True, "data": {"cancelled": True}, "request_id": request_id}
            try:
                preview = self._excel.stage_import(
                    source,
                    report_type=report_type,
                    organization_id=organization_id,
                    matrix=self._build_matrix(report_type, organization_id, _year(request)),
                )
                result = preview.to_dict()
            except ExcelWorkbookValidationError as exc:
                if str(exc) != "Книга не создана этой программой: системная карта отсутствует":
                    raise
                try:
                    result = self._references.stage(source, organization_id, allow_errors=True)
                    # Archiving a source workbook is not an import into working facts.
                    result["already_imported"] = False
                    result["status"] = "STAGED"
                except (ValueError, zipfile.BadZipFile) as invalid:
                    raise ExcelWorkbookValidationError(str(invalid)) from invalid
            return {"ok": True, "data": result, "request_id": request_id}
        except (ExcelReportError, ReportCellError, OSError, sqlite3.Error, ValueError) as error:
            code = error.code if isinstance(error, ExcelReportError) else "EXCEL_IMPORT_ERROR"
            return _failure(code, str(error), request_id)

    def commit_import(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(request, {"batch_id", "year"})
            identity = _required_string(request, "batch_id")
            if self._references.owns(identity):
                raise ExcelWorkbookValidationError(
                    "Сначала сопоставьте исходные ячейки с рабочими полями и выполните проверку"
                )
            result = self._excel.commit_import(
                _required_string(request, "batch_id"),
                allowed_coordinates=lambda report, org: self._editable_coordinate_keys(
                    report, self._organization_id(str(org)), _year(request)
                ),
            )
            return {"ok": True, "data": result, "request_id": request_id}
        except (ExcelReportError, ReportCellError, OSError, sqlite3.Error, ValueError) as error:
            code = (
                error.code
                if isinstance(error, (ExcelReportError, ReportCellError))
                else "EXCEL_IMPORT_ERROR"
            )
            return _failure(code, str(error), request_id)

    def export_report(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request, {"report_type", "organization_id", "year", "visible_months", "stock_weeks"}
            )
            report_type = _required_string(request, "report_type")
            organization_id = self._organization_id(request.get("organization_id"))
            if self._save_excel_file is None:
                raise ExcelReportError(_FILE_DIALOG_REASON)
            suggested = f"{report_type.lower()}-{organization_id}-{date.today().isoformat()}.xlsx"
            destination = self._save_excel_file(suggested)
            if destination is None:
                return {"ok": True, "data": {"cancelled": True}, "request_id": request_id}
            matrix = cast(
                dict[str, Any], self._build_matrix(report_type, organization_id, _year(request))
            )
            months = request.get("visible_months")
            known = {c["group_label"] for c in matrix["time_columns"]}
            if months is not None:
                if not isinstance(months, list) or any(
                    not isinstance(m, str) or m not in known for m in months
                ):
                    raise ValueError("Неверные месяцы экспорта")
                matrix["visible_months"] = months
            weeks = request.get("stock_weeks", {})
            if not isinstance(weeks, dict):
                raise ValueError("Неверные недели экспорта")
            for month, week in weeks.items():
                if not any(
                    c["group_label"] == month and c["id"] == week and c.get("kind") == "USED"
                    for c in matrix["time_columns"]
                ):
                    raise ValueError("Неделя экспорта не принадлежит месяцу")
                for row in matrix["rows"]:
                    for cell in row["cells"]:
                        if cell["column_id"] == month + "-STOCK" and week in row.get(
                            "stock_by_week", {}
                        ):
                            cell["value"] = row["stock_by_week"][week]
            result = self._excel.export(destination, matrix)
            return {"ok": True, "data": result, "request_id": request_id}
        except (ExcelReportError, ReportCellError, OSError, sqlite3.Error, ValueError) as error:
            code = error.code if isinstance(error, ExcelReportError) else "EXCEL_EXPORT_ERROR"
            return _failure(code, str(error), request_id)

    def reference_report(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request,
                {
                    "action",
                    "organization_id",
                    "id",
                    "revision",
                    "changes",
                    "report_type",
                    "year",
                    "mappings",
                },
            )
            organization = self._organization_id(request.get("organization_id"))
            action = _required_string(request, "action")
            result: object
            if action == "list":
                result = self._references.list_reports(organization)
            elif action == "transfer":
                document = self._references.get(
                    _required_string(request, "id"), organization, staged=True, original=True
                )
                report_type = _required_string(request, "report_type")
                matrix = self._build_matrix(report_type, organization, _year(request))
                checked = validate_transfer(
                    document, cast(dict[str, Any], matrix), request.get("mappings")
                )
                if checked["issues"]:
                    result = {"issues": checked["issues"], "error_count": len(checked["issues"])}
                else:
                    result = self._excel.stage_transfer(
                        source_document=document,
                        report_type=report_type,
                        organization_id=organization,
                        changes=checked["changes"],
                    )
                    if not result.get("already_imported"):
                        with closing(connect_sqlite(self._database_path)) as connection:
                            connection.execute(
                                "INSERT INTO reference_transfer_decisions"
                                "(batch_id,workbook_id,decisions) VALUES(?,?,?)",
                                (
                                    result["batch_id"],
                                    document["id"],
                                    json.dumps(request["mappings"], ensure_ascii=False),
                                ),
                            )
                            connection.commit()
            elif action == "get":
                result = self._references.get(_required_string(request, "id"), organization)
            elif action == "save":
                changes = request.get("changes")
                revision = request.get("revision")
                if not isinstance(changes, list) or not isinstance(revision, int):
                    raise ValueError("Некорректные изменения отчёта")
                backup_database(
                    self._database_path, self._backups_directory, self._application_version
                )
                result = self._references.save(
                    _required_string(request, "id"), organization, revision, changes
                )
            elif action == "export":
                identity = _required_string(request, "id")
                doc = self._references.get(identity, organization)
                if self._save_excel_file is None:
                    raise ExcelReportError(_FILE_DIALOG_REASON)
                destination = self._save_excel_file(doc["file_name"])
                if destination is None:
                    result = {"cancelled": True}
                else:
                    if destination.suffix.lower() != ".xlsx":
                        destination = destination.with_suffix(".xlsx")
                    self._references.export(identity, organization, destination)
                    result = {"cancelled": False, "file_name": destination.name}
            else:
                raise ValueError("Неизвестное действие")
            return {"ok": True, "data": result, "request_id": request_id}
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
            return _failure("REFERENCE_REPORT_ERROR", str(exc), request_id)

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
                _reject_unknown(
                    row, {"id", "template_group_id", "party_name", "position_name", "configuration"}
                )
                raw_id = row.get("id")
                row_id = None if raw_id is None else _int_string(raw_id, "row.id")
                drafts.append(
                    WorkspaceGroupDraft(
                        id=row_id,
                        template_group_id=_required_string(row, "template_group_id"),
                        party_name=_required_string(row, "party_name"),
                        position_name=_required_string(row, "position_name"),
                        configuration_json=(
                            configuration_json(row["configuration"])
                            if "configuration" in row
                            else None
                        ),
                    )
                )
            templates = self._group_templates(report_type)
            backup_database(self._database_path, self._backups_directory, self._application_version)
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

    def save_report_presentation(self, payload: object) -> dict[str, object]:
        request_id = uuid4().hex
        try:
            request = _mapping(payload, "payload")
            _reject_unknown(
                request,
                {
                    "report_type",
                    "organization_id",
                    "title",
                    "widths",
                    "plans",
                    "actuals",
                    "expected_revision",
                },
            )
            report_type = _required_string(request, "report_type")
            self._definition(report_type)
            organization_id = self._organization_id(request.get("organization_id"))
            patch: dict[str, object] = {}
            if "plans" in request or "actuals" in request:
                if report_type not in {"SUBSIDIARY", "HEAD_SITE"}:
                    raise ValueError("План и выпуск доступны в месячных отчётах")
                if request.get("expected_revision") != self._matrix_revision(
                    report_type, organization_id
                ):
                    raise ValueError(
                        "Форма изменилась. Перезагрузите данные перед сохранением плана"
                    )
                for field in ("plans", "actuals"):
                    if field not in request:
                        continue
                    values = _mapping(request[field], field)
                    if len(values) > 1200 or any(
                        re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", key) is None for key in values
                    ):
                        raise ValueError("Неверные месяцы выпуска")
                    patch[field] = {key: quantity(value) for key, value in values.items()}

            if "title" in request:
                title = _required_string(request, "title").strip()
                if not title or len(title) > 200:
                    raise ValueError("Название отчёта: от 1 до 200 символов")
                patch["title"] = title
            if "widths" in request:
                widths = _mapping(request["widths"], "widths")
                if len(widths) > 400 or any(
                    not isinstance(key, str)
                    or len(key) > 80
                    or isinstance(width, bool)
                    or not isinstance(width, int)
                    or not 48 <= width <= 600
                    for key, width in widths.items()
                ):
                    raise ValueError("Ширина столбца: от 48 до 600 пикселей")
                patch["widths"] = dict(widths)
            result = self._workspace.save_presentation(organization_id, report_type, patch)
            return {"ok": True, "data": result, "request_id": request_id}
        except (ValueError, sqlite3.Error, ReportCellError) as error:
            return _failure("PRESENTATION_ERROR", str(error), request_id)

    def _build_matrix(
        self,
        report_type: str,
        organization_id: int,
        year: int | None = None,
        preview_changes: object = None,
    ) -> dict[str, object]:
        definition = self._definition(report_type)
        periods = self._periods(report_type, year)
        presentation = self._workspace.get_presentation(organization_id, report_type)
        if report_type in {"SUBSIDIARY", "HEAD_SITE"}:
            from backend.application.production_progress import completion

            presentation["completion"] = completion(
                cast(dict[str, str], presentation.get("plans", {})),
                cast(dict[str, str], presentation.get("actuals", {})),
            )

        widths = cast(dict[str, int], presentation.get("widths", {}))
        previews: dict[str, ReportCellValue] = {}
        if preview_changes is not None:
            changes = _sequence(preview_changes, "preview_changes")
            if len(changes) > 10000:
                raise ValueError("Слишком много изменений предпросмотра")
            for raw in changes:
                change = _mapping(raw, "change")
                _reject_unknown(change, {"coordinate", "value"})
                coordinate = ReportCellCoordinate.from_mapping(
                    _mapping(change.get("coordinate"), "coordinate")
                )
                key = _coordinate_key(coordinate)
                if key in previews:
                    raise ValueError("Повторная ячейка предпросмотра")
                previews[key] = ReportCellValue.from_mapping(_mapping(change.get("value"), "value"))
        rows: list[dict[str, object]] = []
        stored = {
            _coordinate_key(cell.coordinate): cell
            for cell in self._service.get_cells(
                report_type=report_type,
                organization_id=str(organization_id),
            )
        }
        period_labels: dict[str, str] = {}
        calendar_notice = ""
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
        for left_column in left_columns:
            left_column["width"] = widths.get(
                str(left_column["id"]), cast(int, left_column["width"])
            )
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

        if report_type in {"SUBSIDIARY", "HEAD_SITE"}:

            def read(
                group: WorkspaceGroup, code: str, day: str, column: str, editable: bool
            ) -> dict[str, Any]:
                coordinate = self._coordinate(
                    report_type=report_type,
                    organization_id=organization_id,
                    subject_kind=group.subject_kind,
                    subject_id=group.subject_id,
                    metric_code=code,
                    period_start=day,
                )
                key = _coordinate_key(coordinate)
                current = stored.get(key)
                value = (
                    {"kind": "DATA_NOT_PROVIDED"} if current is None else current.value.to_dict()
                )
                preview = previews.pop(key, None)
                if preview is not None:
                    if not editable:
                        raise ValueError("Расчётная или архивная ячейка не редактируется")
                    value = preview.to_dict()
                return {
                    "column_id": column,
                    "coordinate": coordinate.to_dict(),
                    "value": value,
                    "state": {
                        "access": "editable" if editable else "calculated",
                        "persistence": "saved",
                    },
                }

            groups = [
                (group, self._field_configuration(report_type, group))
                for group in configured_groups
            ]
            if report_type == "HEAD_SITE":
                from backend.application.head_site_report import build_head_rows

                subsidiaries = {
                    str(org.id): {
                        **self._workspace.get_presentation(org.id, "SUBSIDIARY"),
                        "name": org.name,
                    }
                    for org in self._workspace.list_organizations()
                }
                structure = build_head_rows(
                    groups, year or date.today().year, read, presentation, subsidiaries
                )
            else:
                structure = build_rows(
                    groups,
                    year or date.today().year,
                    read,
                    cast(dict[str, str], presentation.get("plans", {})),
                )
            if previews:
                raise ValueError("Ячейка вне действующей формы")
            legacy = [
                {"coordinate": item.coordinate.to_dict(), "value": item.value.to_dict()}
                for item in stored.values()
                if not (item.coordinate.metric_code or "").startswith(
                    "HEAD_" if report_type == "HEAD_SITE" else "SUB_"
                )
            ]
            return {
                "report_type": report_type,
                "organization_id": str(organization_id),
                "title": presentation.get("title", _TITLES[report_type]),
                "year": year or date.today().year,
                "presentation": presentation,
                "subtitle": "",
                "source_notice": "",
                "form_status": "WORKING_REFERENCE",
                "matrix_revision": self._matrix_revision(report_type, organization_id),
                "subsidiary": True,
                "head_site": report_type == "HEAD_SITE",
                "legacy_cells": legacy,
                **structure,
                "capabilities": {
                    "save": {"enabled": True},
                    "import": {
                        "enabled": self._open_excel_file is not None,
                        "reason": _FILE_DIALOG_REASON,
                    },
                    "export": {
                        "enabled": self._save_excel_file is not None,
                        "reason": _FILE_DIALOG_REASON,
                    },
                },
                "navigation": {"enter_direction": "down"},
            }

        for configured_group in configured_groups:
            group_record = template_groups.get(configured_group.template_group_id)
            if group_record is None:
                raise ValueError("Настройка строки ссылается на неизвестный шаблон")
            config = self._field_configuration(report_type, configured_group)
            base_rows = {
                _contract_code(str(item["row_id"])): item
                for item in cast(list[dict[str, Any]], group_record["rows"])
            }
            group_rows: list[dict[str, Any]] = []
            for row in config["indicators"]:
                row_record = _mapping(row, "row")
                code = _required_string(row_record, "code")
                template_row_id = str(
                    base_rows.get(code, {}).get("row_id", code.lower().replace("_", "-"))
                )
                row_label = _required_string(row_record, "label")
                editable = (
                    not row_record.get("formula")
                    and base_rows.get(code, {}).get("value_role", "WORKING_INPUT")
                    == "WORKING_INPUT"
                )
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
                        if current is None or not editable
                        else current.value.to_dict()
                    )
                    preview = previews.pop(_coordinate_key(coordinate), None)
                    if preview is not None:
                        if not editable:
                            raise ValueError("Расчётная ячейка не редактируется")
                        value = preview.to_dict()
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
                group_rows.append(
                    {
                        "id": f"{template_row_id}-{configured_group.id}",
                        "group_id": f"workspace-group-{configured_group.id}",
                        "group_label": configured_group.position_name,
                        "metric_code": code,
                        "category": config["category"],
                        "image": config["image"] if not group_rows else "",
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
            calculate_fields(config, group_rows)
            rows.extend(group_rows)

        calculate_ready_sets(rows)
        if previews:
            raise ValueError("Ячейка предпросмотра вне выбранного отчёта или периода")

        return {
            "report_type": report_type,
            "organization_id": str(organization_id),
            "title": presentation.get("title", _TITLES[report_type]),
            "year": year,
            "presentation": presentation,
            "subtitle": "Сквозной локальный контур на обезличенных данных",
            "form_status": "WORKING_REFERENCE",
            "calendar_notice": calendar_notice,
            "source_notice": (
                "Значения сохраняются в SQLite как декларации рабочей формы; "
                "они не проводятся как складские операции."
            ),
            "matrix_revision": self._matrix_revision(report_type, organization_id),
            "left_columns": left_columns,
            "time_columns": [
                {
                    "id": column_id,
                    "label": period_labels.get(period_start, period_start[8:10]),
                    "group_label": period_start[:7],
                    "width": widths.get(column_id, 76),
                }
                for column_id, period_start in periods
            ],
            "rows": rows,
            "capabilities": {
                "save": {"enabled": True},
                "import": (
                    {"enabled": True}
                    if self._open_excel_file is not None
                    else {"enabled": False, "reason": _FILE_DIALOG_REASON}
                ),
                "export": (
                    {"enabled": True}
                    if self._save_excel_file is not None
                    else {"enabled": False, "reason": _FILE_DIALOG_REASON}
                ),
            },
            "navigation": {"enter_direction": "down"},
        }

    def _editable_coordinate_keys(
        self, report_type: str, organization_id: int, year: int | None = None
    ) -> set[str]:
        matrix = self._build_matrix(report_type, organization_id, year)
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

    def _periods(self, report_type: str, year: int | None = None) -> list[tuple[str, str]]:
        if year is not None:
            result: list[tuple[str, str]] = []
            for month in range(1, 13):
                count = calendar.monthrange(year, month)[1]
                for day in range(1, count + 1, 7 if report_type == "SUBSIDIARY" else 1):
                    value = date(year, month, day).isoformat()
                    result.append((value, value))
            return result
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
            layout_revision = connection.execute(
                "SELECT coalesce(max(id), 0) FROM audit_events "
                "WHERE entity_type = 'report_workspace' AND entity_id = ?",
                (f"{organization_id}:{report_type}",),
            ).fetchone()[0]
            if report_type in {"SUBSIDIARY", "HEAD_SITE"}:
                layout_revision = max(
                    layout_revision,
                    connection.execute(
                        "SELECT coalesce(max(id), 0) FROM audit_events "
                        "WHERE entity_type = 'report_presentation' AND entity_id = ? "
                        "AND (json_extract(before_json, '$.plans') IS NOT "
                        "json_extract(after_json, '$.plans') OR "
                        "json_extract(before_json, '$.actuals') IS NOT "
                        "json_extract(after_json, '$.actuals'))",
                        (f"{organization_id}:{report_type}",),
                    ).fetchone()[0],
                )
            if report_type == "HEAD_SITE":
                layout_revision = max(
                    layout_revision,
                    connection.execute("SELECT coalesce(max(id), 0) FROM audit_events").fetchone()[
                        0
                    ],
                )
        finally:
            connection.close()
        revision = f"db-{int(row[0]) if row is not None else 0}"
        return revision if not layout_revision else f"{revision}-layout-{layout_revision}"

    def _field_configuration(self, report_type: str, group: WorkspaceGroup) -> dict[str, Any]:
        config: dict[str, Any] = json.loads(group.configuration_json)
        config.setdefault("category", "UNSPECIFIED")
        config.setdefault("image", "")
        config.setdefault("norm", "")
        config.setdefault("opening", "")
        if report_type in {"SUBSIDIARY", "HEAD_SITE"}:
            config.setdefault("subsidiary", default_detail(group.party_name))
        if "indicators" not in config:
            config["indicators"] = self._default_indicators(report_type, group.template_group_id)
        defaults = self._preset_data().get("defaults", {})
        codes = {item["code"] for item in config["indicators"]}
        if {"WRK_DAILY_RECEIVED", "WRK_DAILY_USED"} <= codes:
            for item in config["indicators"]:
                if item["code"] in defaults and not item["formula"].strip():
                    item["formula"] = defaults[item["code"]]
        return config

    def _preset_data(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(
                (self._definitions_directory / "presets" / "field-presets.v1.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def _default_indicators(self, report_type: str, template_id: str) -> list[dict[str, str]]:
        definition = cast(dict[str, Any], self._definition(report_type))
        group = next(
            item for item in definition["layout"]["row_groups"] if item["group_id"] == template_id
        )
        return [
            {
                "code": _contract_code(item["row_id"]),
                "label": item["label"],
                "formula": self._preset_data()
                .get("defaults", {})
                .get(_contract_code(item["row_id"]), ""),
            }
            for item in group["rows"]
        ]

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
        with (self._definitions_directory / "presets" / "field-presets.v1.json").open(
            encoding="utf-8"
        ) as stream:
            presets = json.load(stream)["presets"]
        return {
            "report_type": report_type,
            "organization_id": str(organization_id),
            "presets": [] if report_type == "SUBSIDIARY" else presets,
            "templates": [
                {
                    "id": template.template_group_id,
                    "label": template.default_position_name,
                    "group_kind": template.group_kind,
                    "indicators": self._default_indicators(report_type, template.template_group_id),
                }
                for template in repeatable.values()
            ],
            "rows": [
                {
                    "id": str(group.id),
                    "template_group_id": group.template_group_id,
                    "party_name": group.party_name,
                    "position_name": group.position_name,
                    "configuration": self._field_configuration(report_type, group),
                }
                for group in groups
                if group.template_group_id in repeatable
            ],
        }


def _year(request: Mapping[str, object]) -> int | None:
    value = request.get("year")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1900 <= value <= 2100:
        raise ValueError("Год должен быть целым числом от 1900 до 2100")
    return value


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
