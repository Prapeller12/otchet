"""Persistence contracts for staged Excel imports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from backend.repositories.report_facts import ReportCellUnitOfWork

ImportClassification = Literal["NEW", "CHANGED", "SAME"]
ImportBatchStatus = Literal["STAGED", "INVALID", "COMMITTED"]


@dataclass(frozen=True, slots=True)
class ImportRowDraft:
    source_cell: str
    coordinate_json: str
    classification: ImportClassification
    value_kind: str
    quantity: str | None
    expected_revision: int | None
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportIssueDraft:
    source_cell: str | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportBatchDraft:
    id: str
    report_type: str
    organization_id: int
    source_file_name: str
    source_sha256: str
    stored_relative_path: str
    status: ImportBatchStatus
    rows: tuple[ImportRowDraft, ...]
    issues: tuple[ImportIssueDraft, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    skipped_count: int = 0


@dataclass(frozen=True, slots=True)
class ImportBatch:
    id: str
    report_type: str
    organization_id: int
    source_file_name: str
    source_sha256: str
    stored_relative_path: str
    status: ImportBatchStatus
    new_count: int
    changed_count: int
    same_count: int
    error_count: int
    rows: tuple[ImportRowDraft, ...]
    issues: tuple[ImportIssueDraft, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    skipped_count: int = 0


class ExcelImportRepository(Protocol):
    def find_committed_source(
        self, *, report_type: str, organization_id: int, source_sha256: str
    ) -> ImportBatch | None: ...

    def add_batch(self, draft: ImportBatchDraft) -> ImportBatch: ...

    def get_batch(self, batch_id: str) -> ImportBatch | None: ...

    def mark_committed(self, batch_id: str) -> ImportBatch: ...

    def commit_with_changes(
        self, batch_id: str, persist: Callable[[ReportCellUnitOfWork], None]
    ) -> ImportBatch: ...


__all__ = [
    "ExcelImportRepository",
    "ImportBatch",
    "ImportBatchDraft",
    "ImportBatchStatus",
    "ImportClassification",
    "ImportIssueDraft",
    "ImportRowDraft",
]
