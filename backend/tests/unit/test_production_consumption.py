from dataclasses import replace
from datetime import date
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest

from backend.application.production_consumption import (
    ComponentNorm,
    ProductionScope,
    ProductionSnapshot,
    prepare_consumption,
    read_consumption,
)
from backend.domain.calculations import QuantityValue, calculate_component_consumption

D = Decimal
SCOPE = ProductionScope("1", "finished-1", date(2026, 9, 1), date(2026, 9, 30))
NORMS = (ComponentNorm("assembly-1", "bom-1", D("4")),)


def snapshot(value: str | None, revision: str = "1") -> ProductionSnapshot:
    return ProductionSnapshot(
        SCOPE, "HEAD_SITE", revision, QuantityValue(None if value is None else D(value))
    )


@pytest.mark.parametrize("actual,expected", [("1000", "4000"), ("0", "0"), (None, None)])
def test_actual_output_times_norm_preserves_missing_and_zero(
    actual: str | None, expected: str | None
) -> None:
    result = prepare_consumption(snapshot(actual), NORMS)
    assert result.lines[0].quantity.value == (None if expected is None else D(expected))
    assert result.source.scope == SCOPE
    assert result.lines[0].norm.bom_version_id == "bom-1"


def test_calculation_does_not_round_under_process_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = calculate_component_consumption(
            QuantityValue(D("10000000000000000000000000001")), D("4")
        )
    assert result.value == D("40000000000000000000000000004")


@pytest.mark.parametrize("norm", [D("0"), D("-1"), D("NaN"), D("Infinity")])
def test_invalid_norm_rejected_even_without_output(norm: Decimal) -> None:
    with pytest.raises(ValueError):
        ComponentNorm("assembly-1", "bom-1", norm)


def test_negative_output_rejected() -> None:
    with pytest.raises(ValueError):
        prepare_consumption(snapshot("-1"), NORMS)


def test_duplicate_component_cannot_double_consumption() -> None:
    with pytest.raises(ValueError, match="twice"):
        prepare_consumption(snapshot("1000"), NORMS * 2)


class Reader:
    def __init__(self, source: ProductionSnapshot) -> None:
        self.source = source

    def read_actual(self, scope: ProductionScope) -> ProductionSnapshot:
        return self.source


def test_daily_source_replaces_snapshot_without_accumulating_repeated_reads() -> None:
    reader = Reader(replace(snapshot("1000"), report_type="DAILY_MOVEMENT"))
    first = read_consumption(reader, SCOPE, NORMS)
    assert read_consumption(reader, SCOPE, NORMS) == first
    reader.source = replace(snapshot("900", "2"), report_type="DAILY_MOVEMENT")
    corrected = read_consumption(reader, SCOPE, NORMS)
    assert corrected.lines[0].quantity.value == D("3600")
    assert first.lines[0].quantity.value == D("4000")
    assert corrected.source.revision == "2"


@pytest.mark.parametrize(
    "scope",
    [
        replace(SCOPE, organization_id="2"),
        replace(SCOPE, product_id="other"),
        replace(SCOPE, end=date(2026, 9, 29)),
    ],
)
def test_adapter_cannot_mix_organizations_products_or_periods(scope: ProductionScope) -> None:
    reader = Reader(replace(snapshot("1000"), scope=scope))
    with pytest.raises(ValueError, match="different"):
        read_consumption(reader, SCOPE, NORMS)
