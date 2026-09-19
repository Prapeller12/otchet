"""Fail closed before Excel truncates an exact production quantity."""

from decimal import Decimal
from pathlib import Path

import pytest

from backend.application.excel_reports import ExcelWorkbookValidationError
from backend.infrastructure.excel.openpyxl_matrix_workbook import OpenpyxlMatrixWorkbookAdapter
from backend.tests.integration.test_subsidiary_consumption import app_at, data


@pytest.mark.parametrize(
    ("quantity", "representable"),
    [
        ("10000000000000000000000000001", False),
        ("0.1234567890123456789", False),
        ("-1234567890123456", False),
        ("12345", True),
        ("0", True),
        ("0.123456789012345", True),
        ("-0.1", True),
        ("10000000000000000000000000000", True),
        ("1234567890123450", True),
    ],
)
def test_exact_quantity_excel_roundtrip_or_explicit_rejection(
    tmp_path: Path, quantity: str, representable: bool
) -> None:
    app = app_at(tmp_path)
    matrix = data(
        app.get_report_matrix(
            {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}
        )
    )
    row = next(row for row in matrix["rows"] if row["cells"][0]["state"]["access"] == "editable")
    cell = row["cells"][0]
    cell["value"] = {"kind": "QUANTITY", "quantity": quantity}
    row["cells"] = [cell]
    matrix["rows"] = [row]
    matrix["time_columns"] = matrix["time_columns"][:1]
    destination = tmp_path / "exact.xlsx"
    adapter = OpenpyxlMatrixWorkbookAdapter()
    if representable:
        assert adapter.write(destination, matrix) == 1
        parsed = adapter.parse(
            destination, report_type="DAILY_MOVEMENT", organization_id=1, matrix=matrix
        )
        assert not parsed.issues
        assert Decimal(parsed.cells[0].value.quantity or "0") == Decimal(quantity)
    else:
        destination.write_bytes(b"existing user file")
        with pytest.raises(ExcelWorkbookValidationError, match="без потери точности"):
            adapter.write(destination, matrix)
        assert destination.read_bytes() == b"existing user file"
        assert cell["value"]["quantity"] == quantity
