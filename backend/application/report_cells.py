"""Atomic application service for versioned report fact declarations.

The coordinate contract is approved at the transport level. Its mapping to
specific Excel rows remains a WORKING_REFERENCE and is deliberately absent
from this service.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from backend.repositories.report_facts import (
    FactCoordinateRecord,
    ReportCellUnitOfWorkFactory,
    ReportFactRecord,
)

_REPORT_TYPES = frozenset({"DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"})
_CONTRACT_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DECIMAL_STRING = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SAVE_COMMAND = "SAVE_REPORT_FACT_BATCH"


class ReportCellError(ValueError):
    """Base class for safe application errors."""

    code = "REPORT_CELL_ERROR"


class ReportCellValidationError(ReportCellError):
    code = "VALIDATION_ERROR"


class RevisionConflictError(ReportCellError):
    code = "REVISION_CONFLICT"


class IdempotencyConflictError(ReportCellError):
    code = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True, slots=True)
class ReportCellCoordinate:
    report_type: str
    organization_id: str
    product_id: str | None = None
    component_id: str | None = None
    metric_code: str | None = None
    operation_type: str | None = None
    operation_date: str | None = None
    period_start: str | None = None
    bom_version_id: str | None = None

    def __post_init__(self) -> None:
        if self.report_type not in _REPORT_TYPES:
            raise ReportCellValidationError("unsupported report_type")
        _require_opaque_id("organization_id", self.organization_id)
        _require_exactly_one("subject", self.product_id, self.component_id)
        _require_exactly_one("indicator", self.metric_code, self.operation_type)
        _require_exactly_one("time", self.operation_date, self.period_start)
        if self.product_id is not None:
            _require_opaque_id("product_id", self.product_id)
        if self.component_id is not None:
            _require_opaque_id("component_id", self.component_id)
        if self.bom_version_id is not None:
            _require_opaque_id("bom_version_id", self.bom_version_id)
        if self.metric_code is not None:
            _require_contract_code("metric_code", self.metric_code)
        if self.operation_type is not None:
            _require_contract_code("operation_type", self.operation_type)
        if self.operation_date is not None:
            _require_iso_date("operation_date", self.operation_date)
        if self.period_start is not None:
            _require_iso_date("period_start", self.period_start)

    @property
    def key(self) -> tuple[str | None, ...]:
        return (
            self.report_type,
            self.organization_id,
            self.product_id,
            self.component_id,
            self.metric_code,
            self.operation_type,
            self.operation_date,
            self.period_start,
            self.bom_version_id,
        )

    def to_record(self) -> FactCoordinateRecord:
        return FactCoordinateRecord(
            report_type=self.report_type,
            organization_id=self.organization_id,
            product_id=self.product_id,
            component_id=self.component_id,
            metric_code=self.metric_code,
            operation_type=self.operation_type,
            operation_date=self.operation_date,
            period_start=self.period_start,
            bom_version_id=self.bom_version_id,
        )

    def to_dict(self) -> dict[str, str]:
        result = {
            "report_type": self.report_type,
            "organization_id": self.organization_id,
        }
        for field_name in (
            "product_id",
            "component_id",
            "metric_code",
            "operation_type",
            "operation_date",
            "period_start",
            "bom_version_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        return result

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> ReportCellCoordinate:
        allowed = {
            "report_type",
            "organization_id",
            "product_id",
            "component_id",
            "metric_code",
            "operation_type",
            "operation_date",
            "period_start",
            "bom_version_id",
        }
        _reject_unknown_fields(payload, allowed, "coordinate")
        return cls(**{name: _optional_string(payload, name) for name in allowed})  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ReportCellValue:
    kind: str
    quantity: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "DATA_NOT_PROVIDED":
            if self.quantity is not None:
                raise ReportCellValidationError("DATA_NOT_PROVIDED must not contain quantity")
            return
        if self.kind != "QUANTITY":
            raise ReportCellValidationError("unsupported value kind")
        if self.quantity is None or _DECIMAL_STRING.fullmatch(self.quantity) is None:
            raise ReportCellValidationError("quantity must be an exact plain-decimal string")

    def to_dict(self) -> dict[str, str]:
        result = {"kind": self.kind}
        if self.quantity is not None:
            result["quantity"] = self.quantity
        return result

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> ReportCellValue:
        _reject_unknown_fields(payload, {"kind", "quantity"}, "value")
        kind = _required_string(payload, "kind")
        quantity_raw = payload.get("quantity")
        if quantity_raw is None:
            quantity = None
        elif isinstance(quantity_raw, str):
            quantity = quantity_raw
        else:
            raise ReportCellValidationError(
                "quantity must be a non-empty string in exact plain-decimal form"
            )
        return cls(kind=kind, quantity=quantity)


@dataclass(frozen=True, slots=True)
class ReportCellChange:
    coordinate: ReportCellCoordinate
    value: ReportCellValue
    expected_revision: int | None


@dataclass(frozen=True, slots=True)
class SavedReportCell:
    coordinate: ReportCellCoordinate
    value: ReportCellValue
    revision: int
    contract_status: str = "WORKING_REFERENCE"

    def to_dict(self) -> dict[str, object]:
        return {
            "coordinate": self.coordinate.to_dict(),
            "value": self.value.to_dict(),
            "revision": self.revision,
            "contract_status": self.contract_status,
        }


class ReportCellService:
    def __init__(self, unit_of_work_factory: ReportCellUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get_cells(
        self,
        *,
        report_type: str,
        organization_id: str,
        operation_date: str | None = None,
        period_start: str | None = None,
    ) -> tuple[SavedReportCell, ...]:
        if report_type not in _REPORT_TYPES:
            raise ReportCellValidationError("unsupported report_type")
        _require_opaque_id("organization_id", organization_id)
        if operation_date is not None and period_start is not None:
            raise ReportCellValidationError(
                "operation_date and period_start cannot be filtered together"
            )
        if operation_date is not None:
            _require_iso_date("operation_date", operation_date)
        if period_start is not None:
            _require_iso_date("period_start", period_start)
        with self._unit_of_work_factory() as unit_of_work:
            records = unit_of_work.facts.list_current(
                report_type=report_type,
                organization_id=organization_id,
                operation_date=operation_date,
                period_start=period_start,
            )
        return tuple(_saved_from_record(record) for record in records)

    def save_cells(
        self,
        changes: Sequence[ReportCellChange],
        *,
        idempotency_key: str,
        actor_ref: str,
    ) -> tuple[SavedReportCell, ...]:
        if not changes:
            raise ReportCellValidationError("changes must not be empty")
        _require_non_empty("idempotency_key", idempotency_key)
        _require_non_empty("actor_ref", actor_ref)
        keys = [change.coordinate.key for change in changes]
        if len(keys) != len(set(keys)):
            raise ReportCellValidationError("batch contains duplicate coordinates")

        request_json = _canonical_json([_change_to_dict(change) for change in changes])
        request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()

        with self._unit_of_work_factory() as unit_of_work:
            previous_command = unit_of_work.idempotency.get(_SAVE_COMMAND, idempotency_key)
            if previous_command is not None:
                if previous_command.request_sha256 != request_sha256:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    )
                return _saved_cells_from_json(previous_command.response_json)

            saved: list[SavedReportCell] = []
            for change in changes:
                current = unit_of_work.facts.get_current(change.coordinate.to_record())
                _check_expected_revision(change.expected_revision, current)
                next_revision = 1 if current is None else current.revision + 1
                added = unit_of_work.facts.add_revision(
                    coordinate=change.coordinate.to_record(),
                    value_kind=change.value.kind,
                    quantity=change.value.quantity,
                    revision=next_revision,
                    previous_revision_id=None if current is None else current.id,
                )
                saved_cell = _saved_from_record(added)
                saved.append(saved_cell)
                unit_of_work.audit.add(
                    actor_ref=actor_ref,
                    entity_type="REPORT_FACT",
                    entity_id=str(added.id),
                    action="DECLARE",
                    before_json=(
                        None if current is None else _canonical_json(_record_to_dict(current))
                    ),
                    after_json=_canonical_json(saved_cell.to_dict()),
                )

            response_json = _canonical_json([cell.to_dict() for cell in saved])
            unit_of_work.idempotency.add(
                command_name=_SAVE_COMMAND,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                response_json=response_json,
            )
            return tuple(saved)


def parse_change(payload: Mapping[str, object]) -> ReportCellChange:
    _reject_unknown_fields(payload, {"coordinate", "value", "expected_revision"}, "change")
    coordinate_payload = payload.get("coordinate")
    value_payload = payload.get("value")
    if not isinstance(coordinate_payload, Mapping) or not isinstance(value_payload, Mapping):
        raise ReportCellValidationError("coordinate and value must be objects")
    expected_revision_raw = payload.get("expected_revision")
    if expected_revision_raw is None:
        expected_revision = None
    elif type(expected_revision_raw) is int and expected_revision_raw > 0:
        expected_revision = expected_revision_raw
    else:
        raise ReportCellValidationError("expected_revision must be a positive integer or null")
    return ReportCellChange(
        coordinate=ReportCellCoordinate.from_mapping(coordinate_payload),
        value=ReportCellValue.from_mapping(value_payload),
        expected_revision=expected_revision,
    )


def _check_expected_revision(
    expected_revision: int | None, current: ReportFactRecord | None
) -> None:
    actual_revision = None if current is None else current.revision
    if expected_revision != actual_revision:
        raise RevisionConflictError(
            f"expected revision {expected_revision!r}, current revision is {actual_revision!r}"
        )


def _saved_from_record(record: ReportFactRecord) -> SavedReportCell:
    stored = record.coordinate
    coordinate = ReportCellCoordinate(
        report_type=stored.report_type,
        organization_id=stored.organization_id,
        product_id=stored.product_id,
        component_id=stored.component_id,
        metric_code=stored.metric_code,
        operation_type=stored.operation_type,
        operation_date=stored.operation_date,
        period_start=stored.period_start,
        bom_version_id=stored.bom_version_id,
    )
    return SavedReportCell(
        coordinate=coordinate,
        value=ReportCellValue(kind=record.value_kind, quantity=record.quantity),
        revision=record.revision,
        contract_status=record.contract_status,
    )


def _record_to_dict(record: ReportFactRecord) -> dict[str, object]:
    return _saved_from_record(record).to_dict()


def _change_to_dict(change: ReportCellChange) -> dict[str, object]:
    return {
        "coordinate": change.coordinate.to_dict(),
        "value": change.value.to_dict(),
        "expected_revision": change.expected_revision,
    }


def _saved_cells_from_json(payload: str) -> tuple[SavedReportCell, ...]:
    decoded: object = json.loads(payload)
    if not isinstance(decoded, list):
        raise RuntimeError("stored idempotency response is invalid")
    result: list[SavedReportCell] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise RuntimeError("stored idempotency response is invalid")
        coordinate = item.get("coordinate")
        value = item.get("value")
        revision = item.get("revision")
        contract_status = item.get("contract_status")
        if (
            not isinstance(coordinate, dict)
            or not isinstance(value, dict)
            or type(revision) is not int
            or contract_status != "WORKING_REFERENCE"
        ):
            raise RuntimeError("stored idempotency response is invalid")
        result.append(
            SavedReportCell(
                coordinate=ReportCellCoordinate.from_mapping(coordinate),
                value=ReportCellValue.from_mapping(value),
                revision=revision,
            )
        )
    return tuple(result)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_non_empty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportCellValidationError(f"{name} must be a non-empty string")
    return value


def _require_opaque_id(name: str, value: object) -> str:
    return _require_non_empty(name, value)


def _require_contract_code(name: str, value: object) -> str:
    text = _require_non_empty(name, value)
    if _CONTRACT_CODE.fullmatch(text) is None:
        raise ReportCellValidationError(f"{name} must be an uppercase contract code")
    return text


def _require_iso_date(name: str, value: object) -> str:
    text = _require_non_empty(name, value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ReportCellValidationError(f"{name} must be an ISO date") from error
    if parsed.isoformat() != text:
        raise ReportCellValidationError(f"{name} must be an ISO date")
    return text


def _require_exactly_one(name: str, left: object | None, right: object | None) -> None:
    if (left is None) == (right is None):
        raise ReportCellValidationError(f"{name} must contain exactly one coordinate")


def _required_string(payload: Mapping[str, object], name: str) -> str:
    if name not in payload:
        raise ReportCellValidationError(f"missing required field: {name}")
    return _require_non_empty(name, payload[name])


def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    return _require_non_empty(name, value)


def _reject_unknown_fields(payload: Mapping[str, object], allowed: set[str], context: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ReportCellValidationError(f"unknown {context} fields: {', '.join(unknown)}")
