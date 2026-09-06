from decimal import Decimal

import pytest

from backend.domain.worksheet_formulas import FormulaError, evaluate_formula


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("= 0.1+0.2 ", "0.3"),
        ("=SUM(1;2;3)", "6"),
        ("=MIN(4,2)", "2"),
        ("=MAX(4,2)", "4"),
        ("=IF(1=1,71/3,1/0)", None),
        ("=ROUNDDOWN(-71/3,0)", "-23"),
        ("=IF(2<>1,30,0)", "30"),
        ("=MISSING+2", None),
        ("=CUM(A)", "19"),
        ("=0e-999999999", "0"),
        ("=0.123456789012345678901234567890123", "0.123456789012345678901234567890123"),
    ],
)
def test_formulas(formula: str, expected: str | None) -> None:
    result = evaluate_formula(formula, lambda _: None, lambda _: Decimal(19))
    if formula.startswith("=IF(1=1"):
        assert result is not None and int(result) == 23
    else:
        assert result == (Decimal(expected) if expected is not None else None)


@pytest.mark.parametrize(
    "formula",
    [
        "=1/0",
        "=A.x",
        "=A[0]",
        "=2**1000000",
        "=SUM()",
        "=IF(1,2)",
        "='text'",
        "=ROUNDDOWN(1,999)",
        "=1e999",
        "=1e-999999999",
        "=open(1)",
    ],
)
def test_unsafe_or_invalid_formulas_are_errors(formula: str) -> None:
    with pytest.raises(FormulaError):
        evaluate_formula(formula, lambda _: Decimal(1), lambda _: Decimal(1))
