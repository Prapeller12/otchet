"""Persistence ports for report fact declarations and command control."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self


@dataclass(frozen=True, slots=True)
class FactCoordinateRecord:
    report_type: str
    organization_id: str
    product_id: str | None
    component_id: str | None
    metric_code: str | None
    operation_type: str | None
    operation_date: str | None
    period_start: str | None
    bom_version_id: str | None


@dataclass(frozen=True, slots=True)
class ReportFactRecord:
    id: int
    coordinate: FactCoordinateRecord
    value_kind: str
    quantity: str | None
    revision: int
    previous_revision_id: int | None
    contract_status: str
    created_at: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    request_sha256: str
    response_json: str


class ReportFactRepository(Protocol):
    def get_current(self, coordinate: FactCoordinateRecord) -> ReportFactRecord | None: ...

    def list_current(
        self,
        *,
        report_type: str,
        organization_id: str,
        operation_date: str | None,
        period_start: str | None,
    ) -> tuple[ReportFactRecord, ...]: ...

    def add_revision(
        self,
        *,
        coordinate: FactCoordinateRecord,
        value_kind: str,
        quantity: str | None,
        revision: int,
        previous_revision_id: int | None,
    ) -> ReportFactRecord: ...


class IdempotencyRepository(Protocol):
    def get(self, command_name: str, idempotency_key: str) -> IdempotencyRecord | None: ...

    def add(
        self,
        *,
        command_name: str,
        idempotency_key: str,
        request_sha256: str,
        response_json: str,
    ) -> None: ...


class AuditRepository(Protocol):
    def add(
        self,
        *,
        actor_ref: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before_json: str | None,
        after_json: str,
    ) -> None: ...


class ReportCellUnitOfWork(Protocol):
    facts: ReportFactRepository
    idempotency: IdempotencyRepository
    audit: AuditRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class ReportCellUnitOfWorkFactory(Protocol):
    def __call__(self) -> ReportCellUnitOfWork: ...
