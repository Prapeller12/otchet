"""Pure domain values and calculations for production reporting."""

from .calculations import (
    CompletionRate,
    CompletionRateStatus,
    ComponentAvailability,
    ComponentReadiness,
    QuantityState,
    QuantityValue,
    ReadinessResult,
    StockOperation,
    StockOperationType,
    calculate_completion_rate,
    calculate_contract_variance,
    calculate_readiness,
    calculate_required_quantity,
    calculate_shortage_to_target,
    calculate_stock,
)

__all__ = [
    "CompletionRate",
    "CompletionRateStatus",
    "ComponentAvailability",
    "ComponentReadiness",
    "QuantityState",
    "QuantityValue",
    "ReadinessResult",
    "StockOperation",
    "StockOperationType",
    "calculate_completion_rate",
    "calculate_contract_variance",
    "calculate_readiness",
    "calculate_required_quantity",
    "calculate_shortage_to_target",
    "calculate_stock",
]
