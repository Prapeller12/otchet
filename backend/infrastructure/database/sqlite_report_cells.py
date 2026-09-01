"""SQLite repositories and unit of work for the report-cell vertical slice."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from backend.infrastructure.database.migrator import connect_sqlite
from backend.repositories.report_facts import (
    AuditRepository,
    FactCoordinateRecord,
    IdempotencyRecord,
    IdempotencyRepository,
    ReportCellUnitOfWork,
    ReportFactRecord,
    ReportFactRepository,
)

_SELECT_FACT_COLUMNS = """
    fact.id,
    fact.report_type,
    fact.organization_id,
    fact.product_id,
    fact.component_id,
    fact.metric_code,
    fact.operation_type,
    fact.operation_date,
    fact.period_start,
    fact.bom_version_id,
    fact.value_kind,
    fact.quantity,
    fact.revision,
    fact.previous_revision_id,
    fact.contract_status,
    fact.created_at
"""


class SqliteReportFactRepository(ReportFactRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_current(self, coordinate: FactCoordinateRecord) -> ReportFactRecord | None:
        row = self._connection.execute(
            f"""
            SELECT {_SELECT_FACT_COLUMNS}
            FROM report_fact_revisions AS fact
            WHERE fact.report_type = ?
              AND fact.organization_id = ?
              AND fact.product_id IS ?
              AND fact.component_id IS ?
              AND fact.metric_code IS ?
              AND fact.operation_type IS ?
              AND fact.operation_date IS ?
              AND fact.period_start IS ?
              AND fact.bom_version_id IS ?
              AND NOT EXISTS (
                SELECT 1
                FROM report_fact_revisions AS successor
                WHERE successor.previous_revision_id = fact.id
              )
            """,
            _coordinate_parameters(coordinate),
        ).fetchone()
        return None if row is None else _record_from_row(row)

    def list_current(
        self,
        *,
        report_type: str,
        organization_id: str,
        operation_date: str | None,
        period_start: str | None,
    ) -> tuple[ReportFactRecord, ...]:
        clauses = [
            "fact.report_type = ?",
            "fact.organization_id = ?",
            "NOT EXISTS (SELECT 1 FROM report_fact_revisions AS successor "
            "WHERE successor.previous_revision_id = fact.id)",
        ]
        parameters: list[str] = [report_type, organization_id]
        if operation_date is not None:
            clauses.append("fact.operation_date = ?")
            parameters.append(operation_date)
        if period_start is not None:
            clauses.append("fact.period_start = ?")
            parameters.append(period_start)
        where_clause = " AND ".join(clauses)
        rows = self._connection.execute(
            f"""
            SELECT {_SELECT_FACT_COLUMNS}
            FROM report_fact_revisions AS fact
            WHERE {where_clause}
            ORDER BY fact.coordinate_key
            """,
            tuple(parameters),
        ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def add_revision(
        self,
        *,
        coordinate: FactCoordinateRecord,
        value_kind: str,
        quantity: str | None,
        revision: int,
        previous_revision_id: int | None,
    ) -> ReportFactRecord:
        cursor = self._connection.execute(
            """
            INSERT INTO report_fact_revisions (
                report_type,
                organization_id,
                product_id,
                component_id,
                metric_code,
                operation_type,
                operation_date,
                period_start,
                bom_version_id,
                value_kind,
                quantity,
                revision,
                previous_revision_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *_coordinate_parameters(coordinate),
                value_kind,
                quantity,
                revision,
                previous_revision_id,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a report fact identifier")
        row = self._connection.execute(
            f"SELECT {_SELECT_FACT_COLUMNS} FROM report_fact_revisions AS fact WHERE fact.id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        if row is None:
            raise RuntimeError("inserted report fact could not be read")
        return _record_from_row(row)


class SqliteIdempotencyRepository(IdempotencyRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, command_name: str, idempotency_key: str) -> IdempotencyRecord | None:
        row = self._connection.execute(
            """
            SELECT request_sha256, response_json
            FROM idempotency_records
            WHERE command_name = ? AND idempotency_key = ?
            """,
            (command_name, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return IdempotencyRecord(request_sha256=str(row[0]), response_json=str(row[1]))

    def add(
        self,
        *,
        command_name: str,
        idempotency_key: str,
        request_sha256: str,
        response_json: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO idempotency_records (
                command_name, idempotency_key, request_sha256, response_json
            ) VALUES (?, ?, ?, ?)
            """,
            (command_name, idempotency_key, request_sha256, response_json),
        )


class SqliteAuditRepository(AuditRepository):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        *,
        actor_ref: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before_json: str | None,
        after_json: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events (
                actor_ref, entity_type, entity_id, action, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor_ref, entity_type, entity_id, action, before_json, after_json),
        )


class SqliteReportCellUnitOfWork:
    facts: ReportFactRepository
    idempotency: IdempotencyRepository
    audit: AuditRepository

    def __init__(self, database_path: Path, busy_timeout_ms: int) -> None:
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("unit of work is already active")
        connection = connect_sqlite(self._database_path)
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("BEGIN IMMEDIATE")
        self._connection = connection
        self.facts = SqliteReportFactRepository(connection)
        self.idempotency = SqliteIdempotencyRepository(connection)
        self.audit = SqliteAuditRepository(connection)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        connection = self._connection
        if connection is None:
            raise RuntimeError("unit of work is not active")
        try:
            if exception_type is None:
                connection.commit()
            else:
                connection.rollback()
        finally:
            connection.close()
            self._connection = None
        return None


class SqliteReportCellUnitOfWorkFactory:
    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        if type(busy_timeout_ms) is not int or busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be a non-negative integer")
        self._database_path = Path(database_path)
        self._busy_timeout_ms = busy_timeout_ms

    def __call__(self) -> ReportCellUnitOfWork:
        return SqliteReportCellUnitOfWork(self._database_path, self._busy_timeout_ms)


def _coordinate_parameters(coordinate: FactCoordinateRecord) -> tuple[str | None, ...]:
    return (
        coordinate.report_type,
        coordinate.organization_id,
        coordinate.product_id,
        coordinate.component_id,
        coordinate.metric_code,
        coordinate.operation_type,
        coordinate.operation_date,
        coordinate.period_start,
        coordinate.bom_version_id,
    )


def _record_from_row(row: tuple[object, ...]) -> ReportFactRecord:
    return ReportFactRecord(
        id=int(str(row[0])),
        coordinate=FactCoordinateRecord(
            report_type=str(row[1]),
            organization_id=str(row[2]),
            product_id=None if row[3] is None else str(row[3]),
            component_id=None if row[4] is None else str(row[4]),
            metric_code=None if row[5] is None else str(row[5]),
            operation_type=None if row[6] is None else str(row[6]),
            operation_date=None if row[7] is None else str(row[7]),
            period_start=None if row[8] is None else str(row[8]),
            bom_version_id=None if row[9] is None else str(row[9]),
        ),
        value_kind=str(row[10]),
        quantity=None if row[11] is None else str(row[11]),
        revision=int(str(row[12])),
        previous_revision_id=None if row[13] is None else int(str(row[13])),
        contract_status=str(row[14]),
        created_at=str(row[15]),
    )
