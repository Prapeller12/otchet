from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from backend.tests.integration.test_reference_reports import bridge, unwrap


def transfer_book(path: Path, kind: str) -> None:
    """Semantic headers and exact periods, independent of fixed legacy coordinates."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for address, value in {
        "A1": "План 2026",
        "B7": "№ п/п",
        "C7": "Обозначение",
        "D7": "Наименование",
        "E7": "Входимость",
        "F7": "Изготовитель",
        "I7": "Всего",
        "J7": "План" if kind == "HEAD_SITE" else "Дефицит",
        "B9": "1",
        "C9": "000.001",
        "D9": "Проверочная позиция",
        "E9": 1,
        "F9": "Изготовитель",
        "I9": "=SUM(L9:N9)",
        "L9": 375,
        "M9": 300,
        "N9": 0,
    }.items():
        sheet[address] = value
    for col, month, dates in [
        ("L", "Январь 2026", "01–04"),
        ("M", "Февраль 2026" if kind == "HEAD_SITE" else "Январь 2026", "05–11"),
        ("N", "Март 2026" if kind == "HEAD_SITE" else "Январь 2026", "12–18"),
    ]:
        sheet[f"{col}2"] = month
        sheet[f"{col}7"] = "Факт" if kind == "HEAD_SITE" else "Расход"
        if kind == "SUBSIDIARY":
            sheet[f"{col}3"] = dates
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize("kind", ["HEAD_SITE", "SUBSIDIARY"])
def test_transfer_into_real_workspace_with_validation_restart_and_duplicate(
    tmp_path: Path, kind: str
) -> None:
    source = tmp_path / "source.xlsx"
    transfer_book(source, kind)
    app = bridge(tmp_path)
    app.configure_excel_dialogs(open_file=lambda: source, save_file=lambda _: tmp_path / "out.xlsx")
    query = {"report_type": kind, "organization_id": "1", "year": 2026}
    preview = unwrap(app.validate_import(query))
    document = preview["reference_workbook"]
    request: dict[str, Any] = {"action": "transfer", "id": document["id"], **query, "mappings": []}
    unresolved = unwrap(
        app.reference_report({**request, "mappings": [{"source": "0:L9", "coordinate": {}}]})
    )
    assert unresolved["error_count"] > 0
    assert any(i["message"] == "Выберите позицию и показатель" for i in unresolved["issues"])
    automatic = unwrap(app.reference_report(request))
    assert automatic["error_count"] == 0, automatic["issues"]
    mappings = [
        {
            "source": key,
            "coordinate": automatic["sources"][key]["coordinate"],
            "quantity": value,
            "confirmed": True,
        }
        for key, value in [("0:L9", "375"), ("0:M9", "300"), ("0:N9", "0")]
    ]
    duplicate = [dict(m) for m in mappings]
    duplicate[-1]["coordinate"] = duplicate[-2]["coordinate"]
    assert unwrap(app.reference_report({**request, "mappings": duplicate}))["error_count"] > 0
    request["mappings"] = mappings
    checked = unwrap(app.reference_report(request))
    assert checked["error_count"] == 0, checked["issues"]
    assert not any(
        row["left_values"].get("designation") == "000.001"
        for row in unwrap(app.get_report_matrix(query))["rows"]
    )
    result = unwrap(app.commit_import({"batch_id": checked["batch_id"], "year": 2026}))
    assert "reference_workbook_id" not in result
    reopened = unwrap(bridge(tmp_path).get_report_matrix(query))
    saved = next(
        row for row in reopened["rows"] if row["left_values"].get("designation") == "000.001"
    )
    targets = [m["coordinate"] for m in mappings]
    assert [c["value"]["quantity"] for c in saved["cells"] if c["coordinate"] in targets] == [
        "375",
        "300",
        "0",
    ]
    assert unwrap(app.reference_report(request))["already_imported"]
    assert unwrap(app.commit_import({"batch_id": checked["batch_id"], "year": 2026}))[
        "already_committed"
    ]
    assert unwrap(app.export_report(query))["exported_cell_count"] > 0
