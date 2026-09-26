"""SQLite staging repository for auditable Excel imports."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_cells import SqliteReportCellUnitOfWork
from backend.repositories.excel_imports import (
    ImportBatch,
    ImportBatchDraft,
    ImportBatchStatus,
    ImportClassification,
    ImportIssueDraft,
    ImportRowDraft,
)
from backend.repositories.report_facts import ReportCellUnitOfWork


class SqliteExcelImportRepository:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def find_committed_source(
        self, *, report_type: str, organization_id: int, source_sha256: str
    ) -> ImportBatch | None:
        connection = connect_sqlite(self._database_path)
        try:
            row = connection.execute(
                """
                SELECT id FROM import_batches
                WHERE report_type = ? AND organization_id = ?
                  AND source_sha256 = ? AND status = 'COMMITTED'
                """,
                (report_type, organization_id, source_sha256),
            ).fetchone()
            return None if row is None else _read_batch(connection, str(row[0]))
        finally:
            connection.close()

    def add_batch(self, draft: ImportBatchDraft) -> ImportBatch:
        counts = {
            value: sum(row.classification == value for row in draft.rows)
            for value in ("NEW", "CHANGED", "SAME")
        }
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO import_batches (
                    id, report_type, organization_id, source_file_name, source_sha256,
                    stored_relative_path, status, new_count, changed_count, same_count,
                    error_count, metadata_json, skipped_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.report_type,
                    draft.organization_id,
                    draft.source_file_name,
                    draft.source_sha256,
                    draft.stored_relative_path,
                    draft.status,
                    counts["NEW"],
                    counts["CHANGED"],
                    counts["SAME"],
                    len(draft.issues),
                    json.dumps(dict(draft.metadata), ensure_ascii=False, sort_keys=True),
                    draft.skipped_count,
                ),
            )
            connection.executemany(
                """
                INSERT INTO import_rows (
                    batch_id, source_cell, coordinate_json, classification,
                    value_kind, quantity, expected_revision, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        draft.id,
                        row.source_cell,
                        row.coordinate_json,
                        row.classification,
                        row.value_kind,
                        row.quantity,
                        row.expected_revision,
                        json.dumps(dict(row.provenance), ensure_ascii=False, sort_keys=True),
                    )
                    for row in draft.rows
                ],
            )
            connection.executemany(
                """
                INSERT INTO import_errors (batch_id, source_cell, code, message)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (draft.id, issue.source_cell, issue.code, issue.message)
                    for issue in draft.issues
                ],
            )
            _audit(
                connection,
                draft.id,
                "STAGE_EXCEL_IMPORT",
                {
                    "report_type": draft.report_type,
                    "organization_id": draft.organization_id,
                    "source_file_name": draft.source_file_name,
                    "source_sha256": draft.source_sha256,
                    "status": draft.status,
                    "new_count": counts["NEW"],
                    "changed_count": counts["CHANGED"],
                    "same_count": counts["SAME"],
                    "error_count": len(draft.issues),
                },
            )
            connection.commit()
            batch = _read_batch(connection, draft.id)
            if batch is None:
                raise sqlite3.DatabaseError("staged import batch could not be read")
            return batch
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_batch(self, batch_id: str) -> ImportBatch | None:
        connection = connect_sqlite(self._database_path)
        try:
            return _read_batch(connection, batch_id)
        finally:
            connection.close()

    def mark_committed(self, batch_id: str) -> ImportBatch:
        return self.commit_with_changes(batch_id, lambda _transaction: None)

    def commit_with_changes(
        self, batch_id: str, persist: Callable[[ReportCellUnitOfWork], None]
    ) -> ImportBatch:
        """Commit facts, structural changes, import status and audit together.

        The callback must use the supplied unit of work, not open a second writer.
        Any exception, including an error updating the batch status, rolls back all
        writes. A retried committed batch never invokes the callback again.
        """
        with SqliteReportCellUnitOfWork(Path(self._database_path), 5000) as transaction:
            connection = transaction.connection
            current = _read_batch(connection, batch_id)
            if current is None:
                raise ValueError("Пакет импорта не найден")
            if current.status == "COMMITTED":
                return current
            if current.status != "STAGED" or current.error_count:
                raise ValueError("Пакет импорта нельзя провести")
            persist(transaction)
            connection.execute(
                """
                UPDATE import_batches
                SET status = 'COMMITTED',
                    committed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'STAGED'
                """,
                (batch_id,),
            )
            _audit(connection, batch_id, "COMMIT_EXCEL_IMPORT", {"status": "COMMITTED"})
            batch = _read_batch(connection, batch_id)
            if batch is None:
                raise sqlite3.DatabaseError("committed import batch could not be read")
            return batch


def _read_batch(connection: sqlite3.Connection, batch_id: str) -> ImportBatch | None:
    row = connection.execute(
        """
        SELECT id, report_type, organization_id, source_file_name, source_sha256,
               stored_relative_path, status, new_count, changed_count, same_count, error_count,
               metadata_json, skipped_count
        FROM import_batches WHERE id = ?
        """,
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    row_records = connection.execute(
        """
        SELECT source_cell, coordinate_json, classification, value_kind, quantity,
               expected_revision, provenance_json
        FROM import_rows WHERE batch_id = ? ORDER BY id
        """,
        (batch_id,),
    ).fetchall()
    issue_records = connection.execute(
        """
        SELECT source_cell, code, message
        FROM import_errors WHERE batch_id = ? ORDER BY id
        """,
        (batch_id,),
    ).fetchall()
    return ImportBatch(
        id=str(row[0]),
        report_type=str(row[1]),
        organization_id=int(row[2]),
        source_file_name=str(row[3]),
        source_sha256=str(row[4]),
        stored_relative_path=str(row[5]),
        status=cast(ImportBatchStatus, row[6]),
        new_count=int(row[7]),
        changed_count=int(row[8]),
        same_count=int(row[9]),
        error_count=int(row[10]),
        metadata=json.loads(row[11]),
        skipped_count=int(row[12]),
        rows=tuple(
            ImportRowDraft(
                source_cell=str(item[0]),
                coordinate_json=str(item[1]),
                classification=cast(ImportClassification, item[2]),
                value_kind=str(item[3]),
                quantity=None if item[4] is None else str(item[4]),
                expected_revision=None if item[5] is None else int(item[5]),
                provenance=json.loads(item[6]),
            )
            for item in row_records
        ),
        issues=tuple(
            ImportIssueDraft(
                source_cell=None if item[0] is None else str(item[0]),
                code=str(item[1]),
                message=str(item[2]),
            )
            for item in issue_records
        ),
    )


def _audit(
    connection: sqlite3.Connection,
    batch_id: str,
    action: str,
    after: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events (
            actor_ref, entity_type, entity_id, action, before_json, after_json
        ) VALUES ('local-excel-import', 'import_batch', ?, ?, NULL, ?)
        """,
        (batch_id, action, json.dumps(after, ensure_ascii=False, sort_keys=True)),
    )


__all__ = ["SqliteExcelImportRepository"]
