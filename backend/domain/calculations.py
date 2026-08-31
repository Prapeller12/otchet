"""Confirmed, transport-independent production reporting calculations.

All quantities are :class:`~decimal.Decimal`.  Binary floating-point values
are rejected at the domain boundary so that callers cannot introduce an
implicit loss of precision.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum


class QuantityState(StrEnum):
    """The three states needed to distinguish a blank cell from numeric zero."""

    DATA_NOT_PROVIDED = "DATA_NOT_PROVIDED"
    CONFIRMED_ZERO = "CONFIRMED_ZERO"
    CONFIRMED_VALUE = "CONFIRMED_VALUE"


@dataclass(frozen=True, slots=True)
class QuantityValue:
    """A reported quantity that preserves the difference between blank and zero."""

    value: Decimal | None

    def __post_init__(self) -> None:
        if self.value is not None:
            _require_decimal("value", self.value)

    @classmethod
    def data_not_provided(cls) -> "QuantityValue":
        return cls(value=None)

    @classmethod
    def confirmed(cls, value: Decimal) -> "QuantityValue":
        return cls(value=value)

    @property
    def state(self) -> QuantityState:
        if self.value is None:
            return QuantityState.DATA_NOT_PROVIDED
        if self.value == Decimal("0"):
            return QuantityState.CONFIRMED_ZERO
        return QuantityState.CONFIRMED_VALUE


class StockOperationType(StrEnum):
    """Operation types explicitly present in the confirmed stock formula."""

    RECEIPT = "RECEIPT"
    PRODUCTION_RECEIPT = "PRODUCTION_RECEIPT"
    RETURN = "RETURN"
    CONSUMPTION = "CONSUMPTION"
    SHIPMENT = "SHIPMENT"
    WRITE_OFF = "WRITE_OFF"
    SIGNED_ADJUSTMENT = "SIGNED_ADJUSTMENT"


_STOCK_OPERATION_SIGNS: dict[StockOperationType, Decimal] = {
    StockOperationType.RECEIPT: Decimal("1"),
    StockOperationType.PRODUCTION_RECEIPT: Decimal("1"),
    StockOperationType.RETURN: Decimal("1"),
    StockOperationType.CONSUMPTION: Decimal("-1"),
    StockOperationType.SHIPMENT: Decimal("-1"),
    StockOperationType.WRITE_OFF: Decimal("-1"),
}


@dataclass(frozen=True, slots=True)
class StockOperation:
    """One separately recorded stock operation.

    ``is_reversal`` represents a separate reversal record and negates the
    effect of the operation being reversed.  A signed adjustment keeps its
    explicit sign because that sign is part of the confirmed formula.
    """

    operation_type: StockOperationType
    quantity: Decimal
    is_reversal: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.operation_type, StockOperationType):
            raise TypeError("operation_type must be StockOperationType")
        _require_decimal("quantity", self.quantity)
        if type(self.is_reversal) is not bool:
            raise TypeError("is_reversal must be bool")
        if self.quantity == Decimal("0"):
            raise ValueError("operation quantity must not be zero")
        if (
            self.operation_type is not StockOperationType.SIGNED_ADJUSTMENT
            and self.quantity < Decimal("0")
        ):
            raise ValueError("operation quantity must be positive; direction is set by type")

    @property
    def stock_effect(self) -> Decimal:
        if self.operation_type is StockOperationType.SIGNED_ADJUSTMENT:
            effect = self.quantity
        else:
            effect = self.quantity * _STOCK_OPERATION_SIGNS[self.operation_type]
        return -effect if self.is_reversal else effect


def calculate_stock(
    opening_balance: Decimal,
    operations: Iterable[StockOperation],
) -> Decimal:
    """Calculate stock from an opening balance and separate operations."""

    _require_decimal("opening_balance", opening_balance)
    balance = opening_balance
    for operation in operations:
        if not isinstance(operation, StockOperation):
            raise TypeError("operations must contain StockOperation values")
        balance += operation.stock_effect
    return balance


def calculate_required_quantity(
    product_plan_quantity: Decimal,
    quantity_per_product: Decimal,
    loss_factor: Decimal,
) -> Decimal:
    """Return ``plan * quantity_per_product * (1 + loss_factor)``."""

    _require_non_negative_decimal("product_plan_quantity", product_plan_quantity)
    _require_positive_decimal("quantity_per_product", quantity_per_product)
    _require_non_negative_decimal("loss_factor", loss_factor)
    return product_plan_quantity * quantity_per_product * (Decimal("1") + loss_factor)


@dataclass(frozen=True, slots=True)
class ComponentAvailability:
    """Available quantity and BOM quantity for one mandatory component."""

    component_ref: str
    available_quantity: QuantityValue
    quantity_per_product: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.component_ref, str):
            raise TypeError("component_ref must be a string")
        if not self.component_ref.strip():
            raise ValueError("component_ref must not be empty")
        if not isinstance(self.available_quantity, QuantityValue):
            raise TypeError("available_quantity must be a QuantityValue")
        if self.available_quantity.value is not None:
            _require_non_negative_decimal("available_quantity.value", self.available_quantity.value)
        _require_positive_decimal("quantity_per_product", self.quantity_per_product)


@dataclass(frozen=True, slots=True)
class ComponentReadiness:
    """Calculated readiness of one mandatory component."""

    component_ref: str
    available_quantity: QuantityValue
    quantity_per_product: Decimal
    available_sets: int


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Ready set count together with every equally limiting component."""

    ready_sets: int
    components: tuple[ComponentReadiness, ...]
    bottlenecks: tuple[ComponentReadiness, ...]


def calculate_readiness(
    components: Iterable[ComponentAvailability],
) -> ReadinessResult:
    """Calculate ready sets and return all components at the minimum.

    A mandatory component whose quantity was not provided allows zero ready
    sets, but its missing state remains intact in the returned bottleneck.
    """

    calculated: list[ComponentReadiness] = []
    for component in components:
        if not isinstance(component, ComponentAvailability):
            raise TypeError("components must contain ComponentAvailability values")
        available_sets = _available_sets(component)
        calculated.append(
            ComponentReadiness(
                component_ref=component.component_ref,
                available_quantity=component.available_quantity,
                quantity_per_product=component.quantity_per_product,
                available_sets=available_sets,
            )
        )

    if not calculated:
        raise ValueError("at least one mandatory component is required")

    ready_sets = min(component.available_sets for component in calculated)
    bottlenecks = tuple(
        component for component in calculated if component.available_sets == ready_sets
    )
    return ReadinessResult(
        ready_sets=ready_sets,
        components=tuple(calculated),
        bottlenecks=bottlenecks,
    )


def calculate_shortage_to_target(
    target_sets: Decimal,
    quantity_per_product: Decimal,
    available_quantity: Decimal,
) -> Decimal:
    """Return the non-negative shortage needed to reach a target set count."""

    _require_non_negative_decimal("target_sets", target_sets)
    _require_positive_decimal("quantity_per_product", quantity_per_product)
    _require_non_negative_decimal("available_quantity", available_quantity)
    shortage = target_sets * quantity_per_product - available_quantity
    return max(Decimal("0"), shortage)


def calculate_contract_variance(
    supplied_quantity_to_date: Decimal,
    contract_planned_quantity_to_date: Decimal,
) -> Decimal:
    """Return supplied quantity minus contract planned quantity."""

    _require_non_negative_decimal("supplied_quantity_to_date", supplied_quantity_to_date)
    _require_non_negative_decimal(
        "contract_planned_quantity_to_date", contract_planned_quantity_to_date
    )
    return supplied_quantity_to_date - contract_planned_quantity_to_date


class CompletionRateStatus(StrEnum):
    CALCULATED = "CALCULATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class CompletionRate:
    percentage: Decimal | None
    status: CompletionRateStatus


def calculate_completion_rate(
    fact_quantity: Decimal,
    plan_quantity: Decimal,
) -> CompletionRate:
    """Calculate fact/plan as a percentage, unless the plan is zero."""

    _require_non_negative_decimal("fact_quantity", fact_quantity)
    _require_non_negative_decimal("plan_quantity", plan_quantity)
    if plan_quantity == Decimal("0"):
        return CompletionRate(
            percentage=None,
            status=CompletionRateStatus.NOT_APPLICABLE,
        )
    return CompletionRate(
        percentage=fact_quantity / plan_quantity * Decimal("100"),
        status=CompletionRateStatus.CALCULATED,
    )


def _available_sets(component: ComponentAvailability) -> int:
    available = component.available_quantity.value
    if available is None:
        return 0
    quotient = available / component.quantity_per_product
    return int(quotient.to_integral_value(rounding=ROUND_FLOOR))


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _require_non_negative_decimal(name: str, value: object) -> Decimal:
    decimal_value = _require_decimal(name, value)
    if decimal_value < Decimal("0"):
        raise ValueError(f"{name} must not be negative")
    return decimal_value


def _require_positive_decimal(name: str, value: object) -> Decimal:
    decimal_value = _require_decimal(name, value)
    if decimal_value <= Decimal("0"):
        raise ValueError(f"{name} must be positive")
    return decimal_value
