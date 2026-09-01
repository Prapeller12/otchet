"""JSON-friendly local bridge for the portable PyWebView shell.

The module has no PyWebView import, so the bridge contract can be tested and
reused without starting a GUI or a localhost server.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast
from uuid import uuid4

from backend.application.report_cells import (
    IdempotencyConflictError,
    ReportCellError,
    ReportCellService,
    ReportCellValidationError,
    RevisionConflictError,
    parse_change,
)
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_report_cells import (
    SqliteReportCellUnitOfWorkFactory,
)

BridgeEnvelope = dict[str, object]


class DesktopBridge:
    """Small, synchronous API exposed to the local desktop frontend."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        migrations_directory: str | Path | None = None,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._database_path = Path(database_path)
        self._migrations_directory = (
            Path(migrations_directory) if migrations_directory is not None else None
        )
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        newly_applied = _migrate(self._database_path, self._migrations_directory)
        self._newly_applied = newly_applied
        self._service = ReportCellService(
            SqliteReportCellUnitOfWorkFactory(
                self._database_path,
                busy_timeout_ms=busy_timeout_ms,
            )
        )

    def health(self) -> BridgeEnvelope:
        return _success({"status": "ok", "transport": "PYWEBVIEW_BRIDGE"})

    def bootstrap(self) -> BridgeEnvelope:
        def operation() -> object:
            connection = connect_sqlite(self._database_path)
            try:
                versions = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
            finally:
                connection.close()
            return {
                "database_ready": True,
                "schema_versions": versions,
                "newly_applied": list(self._newly_applied),
                "contract_status": "WORKING_REFERENCE",
            }

        return _guard(operation)

    def get_cells(self, payload: object) -> BridgeEnvelope:
        def operation() -> object:
            request = _require_mapping(payload, "payload")
            allowed = {"report_type", "organization_id", "operation_date", "period_start"}
            _reject_unknown_fields(request, allowed)
            cells = self._service.get_cells(
                report_type=_required_string(request, "report_type"),
                organization_id=_required_string(request, "organization_id"),
                operation_date=_optional_string(request, "operation_date"),
                period_start=_optional_string(request, "period_start"),
            )
            return {"cells": [cell.to_dict() for cell in cells]}

        return _guard(operation)

    def save_cells(self, payload: object) -> BridgeEnvelope:
        def operation() -> object:
            request = _require_mapping(payload, "payload")
            _reject_unknown_fields(request, {"changes", "idempotency_key", "actor_ref"})
            raw_changes = request.get("changes")
            if not isinstance(raw_changes, Sequence) or isinstance(
                raw_changes, (str, bytes, bytearray)
            ):
                raise ReportCellValidationError("changes must be an array")
            changes = []
            for raw_change in raw_changes:
                changes.append(parse_change(_require_mapping(raw_change, "change")))
            cells = self._service.save_cells(
                changes,
                idempotency_key=_required_string(request, "idempotency_key"),
                actor_ref=_required_string(request, "actor_ref"),
            )
            return {"cells": [cell.to_dict() for cell in cells]}

        return _guard(operation)


def _migrate(database_path: Path, migrations_directory: Path | None) -> tuple[str, ...]:
    connection = connect_sqlite(database_path)
    try:
        if migrations_directory is None:
            return apply_migrations(connection)
        return apply_migrations(connection, migrations_directory)
    finally:
        connection.close()


def _guard(operation: Callable[[], object]) -> BridgeEnvelope:
    request_id = uuid4().hex
    try:
        return _success(operation(), request_id=request_id)
    except ReportCellError as error:
        return _failure(error.code, str(error), request_id)
    except sqlite3.IntegrityError:
        return _failure(
            "DATA_INTEGRITY_ERROR",
            "The requested report fact violates the persisted data contract",
            request_id,
        )
    except sqlite3.Error:
        return _failure(
            "DATABASE_ERROR",
            "The local database could not complete the request",
            request_id,
        )


def _success(data: object, *, request_id: str | None = None) -> BridgeEnvelope:
    return {"ok": True, "data": data, "request_id": request_id or uuid4().hex}


def _failure(code: str, message: str, request_id: str) -> BridgeEnvelope:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "field_errors": [],
            "request_id": request_id,
        },
    }


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReportCellValidationError(f"{name} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ReportCellValidationError(f"{name} keys must be strings")
    return cast(Mapping[str, object], value)


def _reject_unknown_fields(payload: Mapping[str, object], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ReportCellValidationError(f"unknown fields: {', '.join(unknown)}")


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ReportCellValidationError(f"{name} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReportCellValidationError(f"{name} must be a non-empty string")
    return value


__all__ = [
    "DesktopBridge",
    "IdempotencyConflictError",
    "RevisionConflictError",
]
