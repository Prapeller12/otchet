"""Draft transactions and safe, owner-scoped durable outcome recovery."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from types import TracebackType
from typing import Any

from backend.application.report_cells import ReportCellValidationError
from backend.application.report_draft import (
    DRAFT_COMMAND,
    ReportDraftOutcomeUnknown,
    canonical,
    result_for_current,
)
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.report_matrix_revision import matrix_revision
from backend.infrastructure.database.sqlite_report_cells import SqliteReportCellUnitOfWork
from backend.infrastructure.database.sqlite_report_workspace import SqliteReportWorkspaceRepository


class SqliteReportDraftUnitOfWork(SqliteReportCellUnitOfWork):
    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path, 5000)
        self._workspace = SqliteReportWorkspaceRepository(str(database_path))

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return super().__exit__(exception_type, exception, traceback)
        except sqlite3.Error as error:
            if exception_type is None:
                raise ReportDraftOutcomeUnknown(
                    "Не удалось подтвердить завершение записи. Проверьте результат сохранения"
                ) from error
            raise

    def revision(self, report_type: str, organization_id: int) -> str:
        return matrix_revision(self.connection, report_type, organization_id)

    def presentation(self, report_type: str, organization_id: int) -> dict[str, object]:
        return self._workspace.get_presentation(
            organization_id, report_type, connection=self.connection
        )

    def save_presentation(
        self, report_type: str, organization_id: int, patch: Mapping[str, object], actor_ref: str
    ) -> dict[str, object]:
        return self._workspace.save_presentation(
            organization_id, report_type, patch, connection=self.connection, actor_ref=actor_ref
        )


class SqliteReportDraftOutcomes:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def read(
        self, idempotency_key: str, actor_ref: str, context: Mapping[str, object]
    ) -> dict[str, Any] | None:
        # A consistent read snapshot prevents a later writer from changing the
        # revision between loading the operation and its current-state token.
        with closing(connect_sqlite(self._database_path)) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT response_json FROM idempotency_records "
                "WHERE command_name = ? AND idempotency_key = ?",
                (DRAFT_COMMAND, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            envelope = json.loads(row[0])
            if envelope["actor_ref"] != actor_ref or envelope["context"] != dict(context):
                raise ReportCellValidationError(
                    "Результат сохранения недоступен этому пользователю"
                )
            revision = matrix_revision(
                connection, str(context["report_type"]), int(str(context["organization_id"]))
            )
            return result_for_current(envelope, revision, replay=True)

    def finalize(self, idempotency_key: str, actor_ref: str, result: Mapping[str, Any]) -> None:
        """Only operational metadata changes; committed facts/audits stay immutable."""
        with closing(connect_sqlite(self._database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT response_json FROM idempotency_records "
                "WHERE command_name = ? AND idempotency_key = ?",
                (DRAFT_COMMAND, idempotency_key),
            ).fetchone()
            if row is None:
                raise ReportCellValidationError("Не найден сохранённый пакет")
            envelope = json.loads(row[0])
            if envelope["actor_ref"] != actor_ref:
                raise ReportCellValidationError("Неверный автор результата сохранения")
            # Never replace a previously finalized successful result with a retry.
            safe_fields = {
                "verification",
                "verification_error",
                "verification_state",
                "backup_file",
                "backup_complete",
                "backup_warning",
                "backup_state",
            }
            envelope["result"].update({k: result[k] for k in safe_fields if k in result})
            connection.execute(
                "UPDATE idempotency_records SET response_json = ? "
                "WHERE command_name = ? AND idempotency_key = ?",
                (canonical(envelope), DRAFT_COMMAND, idempotency_key),
            )
