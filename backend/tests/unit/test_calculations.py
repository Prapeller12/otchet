from decimal import Decimal

import pytest

from backend.domain import (
    CompletionRateStatus,
    ComponentAvailability,
    QuantityState,
    QuantityValue,
    StockOperation,
    StockOperationType,
    calculate_completion_rate,
    calculate_contract_variance,
    calculate_readiness,
    calculate_required_quantity,
    calculate_shortage_to_target,
    calculate_stock,
)

D = Decimal


def availability(
    component_ref: str,
    available: str | None,
    quantity_per_product: str,
) -> ComponentAvailability:
    quantity = (
        QuantityValue.data_not_provided()
        if available is None
        else QuantityValue.confirmed(D(available))
    )
    return ComponentAvailability(component_ref, quantity, D(quantity_per_product))


class TestQuantityValue:
    def test_missing_and_numeric_zero_are_different_states(self) -> None:
        missing = QuantityValue.data_not_provided()
        zero = QuantityValue.confirmed(D("0"))

        assert missing.state is QuantityState.DATA_NOT_PROVIDED
        assert missing.value is None
        assert zero.state is QuantityState.CONFIRMED_ZERO
        assert zero.value == D("0")
        assert missing != zero

    def test_non_zero_is_confirmed_value(self) -> None:
        value = QuantityValue.confirmed(D("2.500"))

        assert value.state is QuantityState.CONFIRMED_VALUE
        assert value.value == D("2.500")

    @pytest.mark.parametrize("invalid", [0, 0.0, "0"])
    def test_rejects_non_decimal_confirmed_values(self, invalid: object) -> None:
        with pytest.raises(TypeError, match="value must be Decimal"):
            QuantityValue.confirmed(invalid)  # type: ignore[arg-type]

    @pytest.mark.parametrize("invalid", [D("NaN"), D("Infinity"), D("-Infinity")])
    def test_rejects_non_finite_confirmed_values(self, invalid: Decimal) -> None:
        with pytest.raises(ValueError, match="value must be finite"):
            QuantityValue.confirmed(invalid)


class TestStock:
    def test_calculates_every_confirmed_operation_term_separately(self) -> None:
        operations = [
            StockOperation(StockOperationType.RECEIPT, D("10.25")),
            StockOperation(StockOperationType.PRODUCTION_RECEIPT, D("4.75")),
            StockOperation(StockOperationType.RETURN, D("1.50")),
            StockOperation(StockOperationType.CONSUMPTION, D("3.25")),
            StockOperation(StockOperationType.SHIPMENT, D("2.00")),
            StockOperation(StockOperationType.WRITE_OFF, D("0.25")),
            StockOperation(StockOperationType.SIGNED_ADJUSTMENT, D("-0.50")),
            StockOperation(StockOperationType.SIGNED_ADJUSTMENT, D("0.25")),
        ]

        assert calculate_stock(D("20"), operations) == D("30.75")

    def test_empty_operations_leave_opening_balance_unchanged(self) -> None:
        assert calculate_stock(D("12.340"), []) == D("12.340")

    def test_reversal_is_a_separate_inverse_operation(self) -> None:
        operations = [
            StockOperation(StockOperationType.RECEIPT, D("7")),
            StockOperation(StockOperationType.RECEIPT, D("7"), is_reversal=True),
            StockOperation(StockOperationType.CONSUMPTION, D("3")),
            StockOperation(StockOperationType.CONSUMPTION, D("3"), is_reversal=True),
            StockOperation(StockOperationType.SIGNED_ADJUSTMENT, D("-2")),
            StockOperation(
                StockOperationType.SIGNED_ADJUSTMENT,
                D("-2"),
                is_reversal=True,
            ),
        ]

        assert calculate_stock(D("100"), operations) == D("100")

    @pytest.mark.parametrize(
        "operation_type",
        [
            StockOperationType.RECEIPT,
            StockOperationType.PRODUCTION_RECEIPT,
            StockOperationType.RETURN,
            StockOperationType.CONSUMPTION,
            StockOperationType.SHIPMENT,
            StockOperationType.WRITE_OFF,
            StockOperationType.SIGNED_ADJUSTMENT,
        ],
    )
    def test_zero_quantity_operation_is_rejected(self, operation_type: StockOperationType) -> None:
        with pytest.raises(ValueError, match="must not be zero"):
            StockOperation(operation_type, D("0"))

    @pytest.mark.parametrize(
        "operation_type",
        [
            StockOperationType.RECEIPT,
            StockOperationType.PRODUCTION_RECEIPT,
            StockOperationType.RETURN,
            StockOperationType.CONSUMPTION,
            StockOperationType.SHIPMENT,
            StockOperationType.WRITE_OFF,
        ],
    )
    def test_unsigned_operation_rejects_negative_quantity(
        self, operation_type: StockOperationType
    ) -> None:
        with pytest.raises(ValueError, match="direction is set by type"):
            StockOperation(operation_type, D("-1"))

    def test_stock_rejects_float_at_domain_boundary(self) -> None:
        with pytest.raises(TypeError, match="opening_balance must be Decimal"):
            calculate_stock(1.0, [])  # type: ignore[arg-type]

    def test_stock_rejects_non_operation_items(self) -> None:
        with pytest.raises(TypeError, match="StockOperation"):
            calculate_stock(D("0"), [D("1")])  # type: ignore[list-item]

    def test_operation_requires_typed_operation_code(self) -> None:
        with pytest.raises(TypeError, match="operation_type"):
            StockOperation("RECEIPT", D("1"))  # type: ignore[arg-type]

    @pytest.mark.parametrize("invalid", [0, 1, "false"])
    def test_operation_requires_boolean_reversal_flag(self, invalid: object) -> None:
        with pytest.raises(TypeError, match="is_reversal"):
            StockOperation(
                StockOperationType.RECEIPT,
                D("1"),
                is_reversal=invalid,  # type: ignore[arg-type]
            )


class TestRequiredQuantity:
    def test_calculates_requirement_with_zero_loss_factor(self) -> None:
        result = calculate_required_quantity(D("12"), D("3"), D("0"))

        assert result == D("36")

    def test_calculates_requirement_with_decimal_loss_factor(self) -> None:
        result = calculate_required_quantity(D("12.5"), D("2.4"), D("0.05"))

        assert result == D("31.5000")

    @pytest.mark.parametrize(
        ("plan", "per_product", "loss_factor", "message"),
        [
            (D("-1"), D("1"), D("0"), "product_plan_quantity"),
            (D("1"), D("0"), D("0"), "quantity_per_product"),
            (D("1"), D("-1"), D("0"), "quantity_per_product"),
            (D("1"), D("1"), D("-0.1"), "loss_factor"),
        ],
    )
    def test_rejects_invalid_requirement_inputs(
        self,
        plan: Decimal,
        per_product: Decimal,
        loss_factor: Decimal,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            calculate_required_quantity(plan, per_product, loss_factor)

    def test_rejects_float_loss_factor(self) -> None:
        with pytest.raises(TypeError, match="loss_factor must be Decimal"):
            calculate_required_quantity(D("1"), D("1"), 0.0)  # type: ignore[arg-type]


class TestReadiness:
    def test_control_value_23_from_daily_workbook_18_march_2024(self) -> None:
        components = [
            availability("Изделие 1", "200", "1"),
            availability("Изделие 2", "144", "1"),
            availability("Изделие 3", "71", "3"),
            availability("Изделие 4", "50", "1"),
            availability("Изделие 5", "130", "1"),
            availability("Изделие 6", "130", "1"),
            availability("Изделие 7", "133", "2"),
            availability("Изделие 8", "180", "3"),
            availability("Изделие 9", "500", "4"),
            availability("Изделие 10", "600", "1"),
            availability("Изделие 11", "200", "1"),
            availability("Изделие 12", "166", "1"),
        ]

        result = calculate_readiness(components)

        assert result.ready_sets == 23
        assert [item.component_ref for item in result.bottlenecks] == ["Изделие 3"]
        assert result.bottlenecks[0].available_sets == 23

    def test_control_value_30_from_daily_workbook_19_march_2024(self) -> None:
        components = [
            availability("Изделие 1", "1000", "1"),
            availability("Изделие 2", "500", "1"),
            availability("Изделие 3", "90", "3"),
            availability("Изделие 4", "87", "1"),
            availability("Изделие 5", "221", "1"),
            availability("Изделие 6", "569", "1"),
            availability("Изделие 7", "996", "2"),
            availability("Изделие 8", "108", "3"),
            availability("Изделие 9", "500", "4"),
            availability("Изделие 10", "800", "1"),
            availability("Изделие 11", "1200", "1"),
            availability("Изделие 12", "89", "1"),
        ]

        result = calculate_readiness(components)

        assert result.ready_sets == 30
        assert [item.component_ref for item in result.bottlenecks] == ["Изделие 3"]

    def test_returns_all_equal_bottlenecks_in_input_order(self) -> None:
        components = [
            availability("A", "10", "2"),
            availability("B", "15", "3"),
            availability("C", "100", "1"),
        ]

        result = calculate_readiness(components)

        assert result.ready_sets == 5
        assert [item.component_ref for item in result.bottlenecks] == ["A", "B"]

    def test_floor_is_applied_to_each_component_before_minimum(self) -> None:
        components = [
            availability("A", "71", "3"),
            availability("B", "47.999", "2"),
        ]

        result = calculate_readiness(components)

        assert [item.available_sets for item in result.components] == [23, 23]
        assert result.ready_sets == 23

    def test_missing_mandatory_component_yields_zero_and_is_bottleneck(self) -> None:
        components = [
            availability("missing", None, "2"),
            availability("available", "100", "1"),
        ]

        result = calculate_readiness(components)

        assert result.ready_sets == 0
        assert [item.component_ref for item in result.bottlenecks] == ["missing"]
        assert result.bottlenecks[0].available_quantity.state is QuantityState.DATA_NOT_PROVIDED

    def test_confirmed_zero_yields_zero_but_preserves_zero_state(self) -> None:
        result = calculate_readiness([availability("zero", "0", "2")])

        assert result.ready_sets == 0
        assert result.bottlenecks[0].available_quantity.state is QuantityState.CONFIRMED_ZERO

    def test_missing_and_confirmed_zero_are_both_returned_as_equal_bottlenecks(self) -> None:
        result = calculate_readiness(
            [availability("missing", None, "1"), availability("zero", "0", "1")]
        )

        assert [item.component_ref for item in result.bottlenecks] == ["missing", "zero"]
        assert [item.available_quantity.state for item in result.bottlenecks] == [
            QuantityState.DATA_NOT_PROVIDED,
            QuantityState.CONFIRMED_ZERO,
        ]

    def test_requires_at_least_one_mandatory_component(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            calculate_readiness([])

    @pytest.mark.parametrize("per_product", ["0", "-1"])
    def test_rejects_non_positive_bom_quantity(self, per_product: str) -> None:
        with pytest.raises(ValueError, match="quantity_per_product"):
            availability("A", "1", per_product)

    def test_rejects_negative_available_quantity(self) -> None:
        with pytest.raises(ValueError, match="available_quantity"):
            availability("A", "-1", "1")

    def test_rejects_blank_component_reference(self) -> None:
        with pytest.raises(ValueError, match="component_ref"):
            availability("   ", "1", "1")


class TestShortageAndContractVariance:
    def test_calculates_shortage_to_target(self) -> None:
        assert calculate_shortage_to_target(D("36"), D("3"), D("71")) == D("37")

    @pytest.mark.parametrize("available", ["108", "109", "1000"])
    def test_shortage_never_goes_below_zero(self, available: str) -> None:
        assert calculate_shortage_to_target(D("36"), D("3"), D(available)) == D("0")

    @pytest.mark.parametrize(
        ("supplied", "planned", "expected"),
        [
            ("70", "100", "-30"),
            ("100", "100", "0"),
            ("125.50", "100", "25.50"),
        ],
    )
    def test_contract_variance_preserves_deficit_zero_and_surplus(
        self, supplied: str, planned: str, expected: str
    ) -> None:
        assert calculate_contract_variance(D(supplied), D(planned)) == D(expected)

    @pytest.mark.parametrize(
        ("target", "per_product", "available", "message"),
        [
            (D("-1"), D("1"), D("0"), "target_sets"),
            (D("1"), D("0"), D("0"), "quantity_per_product"),
            (D("1"), D("1"), D("-1"), "available_quantity"),
        ],
    )
    def test_shortage_rejects_invalid_inputs(
        self,
        target: Decimal,
        per_product: Decimal,
        available: Decimal,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            calculate_shortage_to_target(target, per_product, available)


class TestCompletionRate:
    def test_calculates_decimal_percentage(self) -> None:
        result = calculate_completion_rate(D("7.5"), D("10"))

        assert result.percentage == D("75.00")
        assert result.status is CompletionRateStatus.CALCULATED

    def test_zero_fact_is_a_calculated_zero_percent(self) -> None:
        result = calculate_completion_rate(D("0"), D("10"))

        assert result.percentage == D("0")
        assert result.status is CompletionRateStatus.CALCULATED

    @pytest.mark.parametrize("fact", ["0", "10"])
    def test_zero_plan_is_not_applicable(self, fact: str) -> None:
        result = calculate_completion_rate(D(fact), D("0"))

        assert result.percentage is None
        assert result.status is CompletionRateStatus.NOT_APPLICABLE

    @pytest.mark.parametrize(
        ("fact", "plan", "message"),
        [
            (D("-1"), D("1"), "fact_quantity"),
            (D("1"), D("-1"), "plan_quantity"),
        ],
    )
    def test_rejects_negative_inputs(self, fact: Decimal, plan: Decimal, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            calculate_completion_rate(fact, plan)

    def test_rejects_float_fact(self) -> None:
        with pytest.raises(TypeError, match="fact_quantity must be Decimal"):
            calculate_completion_rate(1.0, D("1"))  # type: ignore[arg-type]
