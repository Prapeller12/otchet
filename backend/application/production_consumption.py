"""Read-only seam between head-site consumption and future daily production.

An adapter provides one actual-output snapshot for the requested product/period.
The result replaces a calculated view; it must never be added as a second stock
operation when the same production is displayed in another report.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from backend.domain.calculations import QuantityValue, calculate_component_consumption


@dataclass(frozen=True)
class ProductionScope:
    organization_id: str
    product_id: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if not self.organization_id.strip() or not self.product_id.strip():
            raise ValueError("Organization and finished-product identifiers are required")
        if self.end < self.start:
            raise ValueError("Period end precedes start")


@dataclass(frozen=True)
class ProductionSnapshot:
    scope: ProductionScope
    report_type: Literal["HEAD_SITE", "DAILY_MOVEMENT"]
    revision: str
    actual: QuantityValue

    def __post_init__(self) -> None:
        if self.report_type not in ("HEAD_SITE", "DAILY_MOVEMENT"):
            raise ValueError("Unsupported finished-production source")
        if not self.revision.strip():
            raise ValueError("Source revision is required")


@dataclass(frozen=True)
class ComponentNorm:
    component_id: str
    bom_version_id: str
    quantity_per_product: Decimal

    def __post_init__(self) -> None:
        if not self.component_id.strip() or not self.bom_version_id.strip():
            raise ValueError("Component and BOM version identifiers are required")
        # Validate even when the production fact is missing.
        calculate_component_consumption(
            QuantityValue.data_not_provided(), self.quantity_per_product
        )


@dataclass(frozen=True)
class ConsumptionLine:
    norm: ComponentNorm
    quantity: QuantityValue


@dataclass(frozen=True)
class ConsumptionSnapshot:
    source: ProductionSnapshot
    lines: tuple[ConsumptionLine, ...]


class ActualProductionReader(Protocol):
    """Future daily adapter must return the actual finished-product output.

    It must not return component consumption or plan values. Dates, product and
    organization must match exactly; no proportional allocation to days occurs.
    """

    def read_actual(self, scope: ProductionScope) -> ProductionSnapshot: ...


def prepare_consumption(
    source: ProductionSnapshot, norms: Sequence[ComponentNorm]
) -> ConsumptionSnapshot:
    """Calculate one component line per stable identifier, without persistence."""

    seen: set[str] = set()
    lines = []
    for norm in norms:
        if norm.component_id in seen:
            raise ValueError("A component occurs twice in the same production calculation")
        seen.add(norm.component_id)
        lines.append(
            ConsumptionLine(
                norm, calculate_component_consumption(source.actual, norm.quantity_per_product)
            )
        )
    return ConsumptionSnapshot(source, tuple(lines))


def read_consumption(
    reader: ActualProductionReader,
    scope: ProductionScope,
    norms: Sequence[ComponentNorm],
) -> ConsumptionSnapshot:
    """Use exactly one selected source, rather than summing head and daily facts."""

    source = reader.read_actual(scope)
    if source.scope != scope:
        raise ValueError("Production source returned a different organization, product or period")
    return prepare_consumption(source, norms)
