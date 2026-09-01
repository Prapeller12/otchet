from __future__ import annotations

import pytest

from backend.application.report_cells import (
    ReportCellValidationError,
    parse_change,
)


def change_payload(value: dict[str, str]) -> dict[str, object]:
    return {
        "coordinate": {
            "report_type": "DAILY_MOVEMENT",
            "organization_id": "1",
            "component_id": "2",
            "operation_type": "RECEIPT",
            "operation_date": "2026-09-01",
        },
        "value": value,
        "expected_revision": None,
    }


def test_confirmed_zero_and_missing_data_are_distinct_declarations() -> None:
    confirmed_zero = parse_change(change_payload({"kind": "QUANTITY", "quantity": "0"}))
    missing = parse_change(change_payload({"kind": "DATA_NOT_PROVIDED"}))

    assert confirmed_zero.value.quantity == "0"
    assert confirmed_zero.value.kind == "QUANTITY"
    assert missing.value.quantity is None
    assert missing.value.kind == "DATA_NOT_PROVIDED"


@pytest.mark.parametrize(
    "quantity",
    ["", "+1", "01", ".5", "5.", "1e3", "1,5", "1.2.3"],
)
def test_quantity_rejects_non_plain_decimal_strings(quantity: str) -> None:
    with pytest.raises(ReportCellValidationError, match="plain-decimal"):
        parse_change(change_payload({"kind": "QUANTITY", "quantity": quantity}))


def test_quantity_rejects_json_number_to_prevent_float_conversion() -> None:
    payload = change_payload({"kind": "QUANTITY", "quantity": "1"})
    value = payload["value"]
    assert isinstance(value, dict)
    value["quantity"] = 1

    with pytest.raises(ReportCellValidationError, match="non-empty string"):
        parse_change(payload)


def test_coordinate_rejects_ambiguous_subject() -> None:
    payload = change_payload({"kind": "QUANTITY", "quantity": "1"})
    coordinate = payload["coordinate"]
    assert isinstance(coordinate, dict)
    coordinate["product_id"] = "3"

    with pytest.raises(ReportCellValidationError, match="subject"):
        parse_change(payload)


def test_expected_revision_must_be_positive_integer_or_null() -> None:
    payload = change_payload({"kind": "QUANTITY", "quantity": "1"})
    payload["expected_revision"] = 0

    with pytest.raises(ReportCellValidationError, match="expected_revision"):
        parse_change(payload)
