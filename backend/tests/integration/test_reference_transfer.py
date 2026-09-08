from pathlib import Path
from typing import Any

import pytest

from backend.application.reference_transfer import numeric_sources
from backend.tests.integration.test_reference_reports import bridge, fixture_book, unwrap


@pytest.mark.parametrize("kind", ["HEAD_SITE", "SUBSIDIARY"])
def test_transfer_into_real_workspace_with_validation_restart_and_duplicate(
    tmp_path: Path, kind: str
) -> None:
    source = tmp_path / "source.xlsx"
    fixture_book(source, kind)
    app = bridge(tmp_path)
    app.configure_excel_dialogs(open_file=lambda: source, save_file=lambda _: tmp_path / "out.xlsx")
    query = {"report_type": kind, "organization_id": "1", "year": 2026}
    preview = unwrap(app.validate_import(query))
    document = preview["reference_workbook"]
    matrix = unwrap(app.get_report_matrix(query))
    assert matrix["time_columns"][0]["label"] == "01–04"
    row = next(r for r in matrix["rows"] if r["cells"][0]["state"]["access"] == "editable")
    request = {"action": "transfer", "id": document["id"], **query, "mappings": []}
    assert unwrap(app.reference_report(request))["error_count"] > 0
    mappings: list[dict[str, Any]] = []
    for source_key in numeric_sources(document):
        if source_key == "0:I9":
            mappings.append(
                {
                    "source": source_key,
                    "skip_reason": "Контрольный итог, исходные значения перенесены отдельно",
                }
            )
        else:
            index = ["0:L9", "0:M9", "0:N9"].index(source_key)
            mappings.append(
                {
                    "source": source_key,
                    "coordinate": row["cells"][index]["coordinate"],
                    "quantity": ["375", "300", "0"][index],
                    "confirmed": True,
                }
            )
    request["mappings"] = mappings
    # Duplicate target cells must not be silently summed or overwritten.
    duplicate = [dict(m) for m in mappings]
    duplicate[-1]["coordinate"] = duplicate[-2]["coordinate"]
    assert unwrap(app.reference_report({**request, "mappings": duplicate}))["error_count"] > 0
    checked = unwrap(app.reference_report(request))
    assert checked["error_count"] == 0
    assert all(
        c["value"]["kind"] == "DATA_NOT_PROVIDED"
        for c in unwrap(app.get_report_matrix(query))["rows"][0]["cells"]
    )
    result = unwrap(app.commit_import({"batch_id": checked["batch_id"], "year": 2026}))
    assert "reference_workbook_id" not in result
    reopened = unwrap(bridge(tmp_path).get_report_matrix(query))
    saved = next(r for r in reopened["rows"] if r["id"] == row["id"])
    assert [c["value"]["quantity"] for c in saved["cells"][:3]] == ["375", "300", "0"]
    assert unwrap(app.reference_report(request))["already_imported"]
    assert unwrap(app.commit_import({"batch_id": checked["batch_id"], "year": 2026}))[
        "already_committed"
    ]
    assert unwrap(app.export_report(query))["exported_cell_count"] > 0
