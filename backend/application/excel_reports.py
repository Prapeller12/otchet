"""Application service for two-phase Excel import and database-backed export."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from backend.application.report_cells import (
    ReportCellChange,
    ReportCellCoordinate,
    ReportCellService,
    ReportCellValue,
)
from backend.desktop.database_bootstrap import backup_database
from backend.repositories.excel_imports import (
    ExcelImportRepository,
    ImportBatch,
    ImportBatchDraft,
    ImportClassification,
    ImportIssueDraft,
    ImportRowDraft,
)
from backend.repositories.report_facts import ReportCellUnitOfWork

_MAX_WORKBOOK_SIZE = 50 * 1024 * 1024


class ExcelReportError(ValueError):
    code = "EXCEL_REPORT_ERROR"


class ExcelWorkbookValidationError(ExcelReportError):
    code = "EXCEL_VALIDATION_ERROR"


@dataclass(frozen=True, slots=True)
class ParsedExcelCell:
    source_cell: str
    coordinate: ReportCellCoordinate
    value: ReportCellValue
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkbookIssue:
    source_cell: str | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedWorkbook:
    cells: tuple[ParsedExcelCell, ...]
    issues: tuple[WorkbookIssue, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    skipped_count: int = 0


class ExcelWorkbookAdapter(Protocol):
    def parse(
        self,
        source: Path,
        *,
        report_type: str,
        organization_id: int,
        matrix: Mapping[str, object],
    ) -> ParsedWorkbook: ...

    def write(self, destination: Path, matrix: Mapping[str, object]) -> int: ...


@dataclass(frozen=True, slots=True)
class ImportPreview:
    batch_id: str
    file_name: str
    source_sha256: str
    status: str
    new_count: int
    changed_count: int
    same_count: int
    error_count: int
    issues: tuple[WorkbookIssue, ...]
    already_imported: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)
    skipped_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "cancelled": False,
            "batch_id": self.batch_id,
            "file_name": self.file_name,
            "source_sha256": self.source_sha256,
            "status": self.status,
            "new_count": self.new_count,
            "changed_count": self.changed_count,
            "same_count": self.same_count,
            "error_count": self.error_count,
            "already_imported": self.already_imported,
            "metadata": dict(self.metadata),
            "skipped_count": self.skipped_count,
            "issues": [
                {
                    "source_cell": issue.source_cell,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in self.issues
            ],
        }


class ExcelReportService:
    def __init__(
        self,
        *,
        report_cells: ReportCellService,
        import_repository: ExcelImportRepository,
        workbook_adapter: ExcelWorkbookAdapter,
        database_path: Path,
        inbox_directory: Path,
        backups_directory: Path,
        application_version: str,
    ) -> None:
        self._report_cells = report_cells
        self._imports = import_repository
        self._workbooks = workbook_adapter
        self._database_path = database_path
        self._inbox_directory = inbox_directory
        self._backups_directory = backups_directory
        self._application_version = application_version

    def stage_import(
        self,
        source: Path,
        *,
        report_type: str,
        organization_id: int,
        matrix: Mapping[str, object],
        mode: str = "UPDATE",
        profile_version: str = "canonical-v2",
        source_file_name: str | None = None,
    ) -> ImportPreview:
        _validate_mode(mode)
        _validate_source_file(source)
        original_sha256 = _sha256(source)
        batch_id = uuid4().hex
        self._inbox_directory.mkdir(parents=True, exist_ok=True)
        staged_path = self._inbox_directory / f"{batch_id}.xlsx"
        shutil.copyfile(source, staged_path)
        if _sha256(staged_path) != original_sha256:
            staged_path.unlink(missing_ok=True)
            raise ExcelReportError("Контрольная сумма копии Excel не совпала с исходным файлом")

        try:
            parsed = self._workbooks.parse(
                staged_path,
                report_type=report_type,
                organization_id=organization_id,
                matrix=matrix,
            )
        except BaseException:
            staged_path.unlink(missing_ok=True)
            raise
        context = {
            "report_type": report_type,
            "organization_id": organization_id,
            "year": matrix.get("year"),
            "mode": mode,
            "profile_version": profile_version,
            "target_identity": matrix.get("exchange_identity", {}),
        }
        source_sha256 = _source_context_hash(original_sha256, context)
        committed = self._imports.find_committed_source(
            report_type=report_type,
            organization_id=organization_id,
            source_sha256=source_sha256,
        )
        if committed is not None:
            staged_path.unlink(missing_ok=True)
            return _preview(committed, already_imported=True)
        skipped_count = parsed.skipped_count
        mode_issues: list[ImportIssueDraft] = []
        current = {
            _coordinate_json(cell.coordinate): cell
            for cell in self._report_cells.get_cells(
                report_type=report_type,
                organization_id=str(organization_id),
            )
        }
        rows: list[ImportRowDraft] = []
        for item in parsed.cells:
            if item.value.kind == "DATA_NOT_PROVIDED" and item.provenance.get("action") != "CLEAR":
                continue
            coordinate_json = _coordinate_json(item.coordinate)
            previous = current.get(coordinate_json)
            occupied = previous is not None and previous.value.kind != "DATA_NOT_PROVIDED"
            if occupied and mode == "APPEND":
                skipped_count += 1
                continue
            if occupied and mode == "CREATE":
                mode_issues.append(
                    ImportIssueDraft(
                        item.source_cell,
                        "CREATE_TARGET_NOT_EMPTY",
                        "В режиме создания нельзя заменять существующее значение; "
                        "выберите обновление",
                    )
                )
            classification = _classification(previous.value if previous else None, item.value)
            rows.append(
                ImportRowDraft(
                    source_cell=item.source_cell,
                    coordinate_json=coordinate_json,
                    classification=classification,
                    value_kind=item.value.kind,
                    quantity=item.value.quantity,
                    expected_revision=None if previous is None else previous.revision,
                    provenance=item.provenance,
                )
            )
        issues = tuple(
            ImportIssueDraft(issue.source_cell, issue.code, issue.message)
            for issue in parsed.issues
        ) + tuple(mode_issues)
        batch = self._imports.add_batch(
            ImportBatchDraft(
                id=batch_id,
                report_type=report_type,
                organization_id=organization_id,
                source_file_name=source_file_name or source.name,
                source_sha256=source_sha256,
                stored_relative_path=staged_path.name,
                status="INVALID" if issues else "STAGED",
                rows=tuple(rows),
                issues=issues,
                metadata={
                    **parsed.metadata,
                    "original_source_sha256": original_sha256,
                    "import_context": context,
                },
                skipped_count=skipped_count,
            )
        )
        return _preview(batch)

    def stage_transfer(
        self,
        *,
        source_document: dict[str, object],
        report_type: str,
        organization_id: int,
        changes: list[dict[str, object]],
        mode: str = "UPDATE",
        profile_version: str = "manual-v1",
        provenance: Mapping[str, object] | None = None,
        year: int | None = None,
        target_identity: Mapping[str, object] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Persist a checked mapping as a normal fact import, preserving source provenance."""
        _validate_mode(mode)
        original_sha256 = str(source_document["sha256"])
        context = {
            "report_type": report_type,
            "organization_id": organization_id,
            "year": year
            if year is not None
            else sorted(
                {
                    str(c.get("operation_date") or c.get("period_start") or "")[:4]
                    for item in changes
                    if isinstance((c := item.get("coordinate")), Mapping)
                }
            ),
            "mode": mode,
            "profile_version": profile_version,
            "target_identity": dict(target_identity or {}),
        }
        source_hash = _source_context_hash(original_sha256, context)
        previous = self._imports.find_committed_source(
            report_type=report_type,
            organization_id=organization_id,
            source_sha256=source_hash,
        )
        if previous is not None:
            return _preview(previous, already_imported=True).to_dict()
        current = {
            _coordinate_json(item.coordinate): item
            for item in self._report_cells.get_cells(
                report_type=report_type,
                organization_id=str(organization_id),
            )
        }
        rows = []
        skipped_count = 0
        issues = []
        for change in changes:
            coordinate = ReportCellCoordinate.from_mapping(
                cast(Mapping[str, object], change["coordinate"])
            )
            value = ReportCellValue.from_mapping(cast(Mapping[str, object], change["value"]))
            key = _coordinate_json(coordinate)
            old = current.get(key)
            raw_provenance = change.get("provenance", {})
            if not isinstance(raw_provenance, Mapping):
                raise ExcelWorkbookValidationError("Некорректное происхождение значения")
            value_provenance = {**dict(provenance or {}), **dict(raw_provenance)}
            if value.kind == "DATA_NOT_PROVIDED" and value_provenance.get("action") != "CLEAR":
                skipped_count += 1
                continue
            occupied = old is not None and old.value.kind != "DATA_NOT_PROVIDED"
            if occupied and mode == "APPEND":
                skipped_count += 1
                continue
            if occupied and mode == "CREATE":
                issues.append(
                    ImportIssueDraft(
                        str(change["source_cell"]),
                        "CREATE_TARGET_NOT_EMPTY",
                        "В режиме создания нельзя заменять существующее значение; "
                        "выберите обновление",
                    )
                )
            rows.append(
                ImportRowDraft(
                    source_cell=str(change["source_cell"]),
                    coordinate_json=key,
                    classification=_classification(old.value if old else None, value),
                    value_kind=value.kind,
                    quantity=value.quantity,
                    expected_revision=None if old is None else old.revision,
                    provenance=value_provenance,
                )
            )
        batch = self._imports.add_batch(
            ImportBatchDraft(
                id=uuid4().hex,
                report_type=report_type,
                organization_id=organization_id,
                source_file_name=str(source_document["file_name"]),
                source_sha256=source_hash,
                stored_relative_path="reference:" + str(source_document["id"]),
                status="INVALID" if issues else "STAGED",
                rows=tuple(rows),
                issues=tuple(issues),
                metadata={
                    **dict(metadata or {}),
                    "original_source_sha256": original_sha256,
                    "import_context": context,
                },
                skipped_count=skipped_count,
            )
        )
        return _preview(batch).to_dict()

    def commit_import(
        self,
        batch_id: str,
        *,
        allowed_coordinates: Callable[[str, str], set[str]],
        before_commit: Callable[[ReportCellUnitOfWork], None] | None = None,
    ) -> dict[str, object]:
        if len(batch_id) != 32 or not batch_id.isalnum():
            raise ExcelWorkbookValidationError("Некорректный идентификатор пакета импорта")
        batch = self._imports.get_batch(batch_id)
        if batch is None:
            raise ExcelWorkbookValidationError("Пакет импорта не найден")
        if batch.status == "INVALID" or batch.error_count:
            raise ExcelWorkbookValidationError("Импорт содержит ошибки и не может быть проведён")
        if batch.status == "COMMITTED":
            return _commit_result(batch, backup_file=None, already_committed=True)

        allowed = allowed_coordinates(batch.report_type, str(batch.organization_id))
        for row in batch.rows:
            if row.coordinate_json not in allowed:
                raise ExcelWorkbookValidationError(
                    "Настройка формы изменилась после предпросмотра; проверьте импорт заново"
                )

        changes = [
            ReportCellChange(
                coordinate=ReportCellCoordinate.from_mapping(
                    cast(Mapping[str, object], json.loads(row.coordinate_json))
                ),
                value=ReportCellValue(kind=row.value_kind, quantity=row.quantity),
                expected_revision=row.expected_revision,
            )
            for row in batch.rows
            if row.classification != "SAME"
        ]
        backup = None
        if changes or before_commit is not None:
            backup = backup_database(
                self._database_path,
                self._backups_directory,
                self._application_version,
            )

        def persist(transaction: ReportCellUnitOfWork) -> None:
            if before_commit is not None:
                before_commit(transaction)
            if changes:
                self._report_cells.save_cells(
                    changes,
                    idempotency_key=f"excel-import-{batch.id}",
                    actor_ref="local-excel-import",
                    unit_of_work=transaction,
                )

        committed = self._imports.commit_with_changes(batch.id, persist)
        return _commit_result(
            committed,
            backup_file=None
            if backup is None
            else str(backup.relative_to(self._backups_directory)),
            already_committed=False,
        )

    def export(self, destination: Path, matrix: Mapping[str, object]) -> dict[str, object]:
        if destination.suffix.lower() != ".xlsx":
            destination = destination.with_suffix(".xlsx")
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.stem}.{uuid4().hex}.xlsx")
        try:
            exported_cell_count = self._workbooks.write(pending, matrix)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
        return {
            "cancelled": False,
            "file_name": destination.name,
            "file_path": str(destination),
            "sha256": _sha256(destination),
            "exported_cell_count": exported_cell_count,
        }


def _validate_mode(mode: str) -> None:
    if mode not in {"CREATE", "APPEND", "UPDATE"}:
        raise ExcelWorkbookValidationError("Выберите режим: создать, дополнить или обновить")


def _source_context_hash(original_sha256: str, context: Mapping[str, object]) -> str:
    """Distinguish the same source imported into another period/profile/mode."""
    payload = json.dumps(
        {"source_sha256": original_sha256, "context": dict(context)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_source_file(path: Path) -> None:
    if path.suffix.lower() != ".xlsx":
        raise ExcelWorkbookValidationError("Поддерживаются только книги Excel формата .xlsx")
    if not path.is_file():
        raise ExcelWorkbookValidationError("Выбранный Excel-файл не найден")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_WORKBOOK_SIZE:
        raise ExcelWorkbookValidationError("Размер Excel-файла должен быть от 1 байта до 50 МБ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coordinate_json(coordinate: ReportCellCoordinate) -> str:
    return json.dumps(
        coordinate.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _classification(
    previous: ReportCellValue | None, imported: ReportCellValue
) -> ImportClassification:
    if previous is None:
        return "SAME" if imported.kind == "DATA_NOT_PROVIDED" else "NEW"
    return "SAME" if previous == imported else "CHANGED"


def _preview(batch: ImportBatch, *, already_imported: bool = False) -> ImportPreview:
    return ImportPreview(
        batch_id=batch.id,
        file_name=batch.source_file_name,
        source_sha256=str(batch.metadata.get("original_source_sha256", batch.source_sha256)),
        status=batch.status,
        new_count=0 if already_imported else batch.new_count,
        changed_count=0 if already_imported else batch.changed_count,
        same_count=(
            batch.new_count + batch.changed_count + batch.same_count
            if already_imported
            else batch.same_count
        ),
        error_count=batch.error_count,
        already_imported=already_imported,
        metadata=batch.metadata,
        skipped_count=batch.skipped_count,
        issues=tuple(
            WorkbookIssue(issue.source_cell, issue.code, issue.message) for issue in batch.issues
        ),
    )


def _commit_result(
    batch: ImportBatch,
    *,
    backup_file: str | None,
    already_committed: bool,
) -> dict[str, object]:
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "imported_count": 0 if already_committed else batch.new_count + batch.changed_count,
        "same_count": (
            batch.new_count + batch.changed_count + batch.same_count
            if already_committed
            else batch.same_count
        ),
        "backup_file": backup_file,
        "already_committed": already_committed,
    }


__all__ = [
    "ExcelReportError",
    "ExcelReportService",
    "ExcelWorkbookAdapter",
    "ExcelWorkbookValidationError",
    "ImportPreview",
    "ParsedExcelCell",
    "ParsedWorkbook",
    "WorkbookIssue",
]
