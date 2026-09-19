from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.application.monthly_report import monthly_snapshot
from backend.infrastructure.database.sqlite_report_workspace import SqliteReportWorkspaceRepository
from backend.tests.integration.test_report_signatures import create, sign_request
from backend.tests.integration.test_subsidiary_consumption import app_at, data


@pytest.mark.parametrize("report_type", ["SUBSIDIARY", "HEAD_SITE", "DAILY_MOVEMENT"])
def test_header_persists_changes_signature_and_rejects_stale_update(
    tmp_path: Path, report_type: str
) -> None:
    app = app_at(tmp_path)
    query = {"report_type": report_type, "organization_id": "1", "year": 2026}
    matrix = data(app.get_report_matrix(query))
    before_revision = matrix["matrix_revision"]
    request = {
        "report_type": report_type,
        "organization_id": "1",
        "expected_revision": before_revision,
        "header": {"product_designation": "TEST-01", "factory_name": "Тестовый изготовитель"},
    }
    data(app.save_report_presentation(request))
    matrix = data(app_at(tmp_path).get_report_matrix(query))
    assert matrix["matrix_revision"] != before_revision
    snapshot = monthly_snapshot(matrix, 9, "Проверка")
    assert snapshot["header"]["product_designation"] == "TEST-01"
    assert not app.save_report_presentation({**request, "header": {"factory_name": "Подмена"}})[
        "ok"
    ]
    signing_query = {**query, "month": 9}
    data(app.verify_report(sign_request(app, create(app), signing_query)))
    assert data(app.get_report_verification(signing_query))["status"] == "VERIFIED"
    data(
        app.save_report_presentation(
            {
                **request,
                "expected_revision": matrix["matrix_revision"],
                "header": {"product_designation": "TEST-02"},
            }
        )
    )
    assert data(app.get_report_verification(signing_query))["status"] == "STALE"


def test_head_code_totals_drive_consumption_and_preserve_legacy_source(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    query = {"report_type": "HEAD_SITE", "organization_id": "1", "year": 2026}
    scope = {key: value for key, value in query.items() if key != "year"}
    layout = data(app.get_report_layout(scope))
    row = layout["rows"][0]
    row["configuration"]["norm"] = "4"
    data(app.save_report_layout({**scope, "rows": [row]}))
    matrix = data(app.get_report_matrix(query))
    data(
        app.save_report_presentation(
            {
                **scope,
                "expected_revision": matrix["matrix_revision"],
                "plans": {"2025-12": "20", "2026-09": "30"},
                "actuals": {"2026-09": "999"},
            }
        )
    )
    matrix = data(app.get_report_matrix(query))
    data(
        app.save_report_presentation(
            {**scope, "expected_revision": matrix["matrix_revision"], "plans": {"2026-10": "40"}}
        )
    )
    matrix = data(app.get_report_matrix(query))
    assert matrix["presentation"]["plans"]["2025-12"] == "20"
    codes = [
        {"id": "A", "label": "Код A", "plans": {"2026-09": "10"}, "actuals": {"2026-09": "8"}},
        {"id": "B", "label": "Код B", "plans": {"2026-09": "20"}, "actuals": {"2026-09": "7"}},
    ]
    request = {**scope, "expected_revision": matrix["matrix_revision"], "production_codes": codes}
    assert not app.save_report_presentation(request)["ok"]
    data(app.save_report_presentation({**request, "confirm_production_totals": True}))
    matrix = data(app.get_report_matrix(query))
    assert matrix["presentation"]["actuals"]["2026-09"] == "15"
    used = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == "2026-09-USED")
    assert used["value"]["quantity"] == "60"
    persisted = SqliteReportWorkspaceRepository(str(tmp_path / "reporting.db")).get_presentation(
        1, "HEAD_SITE"
    )
    assert persisted["actuals"] == {"2026-09": "999"}
    assert monthly_snapshot(matrix, 9, "Проверка")["production_codes"] == codes
    assert monthly_snapshot(matrix, 9, "Проверка")["production_code_annual"] == {"A": "8", "B": "7"}
    changes = []
    for month, value in (("2026-01", "0.1"), ("2026-09", "0.2")):
        cell = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == month + "-FACT")
        changes.append(
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": value}}
        )
    data(
        app.save_report_cells(
            {
                **query,
                "base_revision": matrix["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": changes,
            }
        )
    )
    matrix = data(app.get_report_matrix(query))
    assert matrix["rows"][0]["manufactured_total"] == "0.3"
    assert monthly_snapshot(matrix, 9, "Проверка")["rows"][0]["manufactured_total"] == "0.3"


def test_daily_summary_uses_backend_saved_values(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    query = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}
    matrix = data(app.get_report_matrix(query))
    row: dict[str, Any] = next(
        row for row in matrix["rows"] if row["cells"][0]["state"]["access"] == "editable"
    )
    cells = [row["cells"][0], row["cells"][31]]
    data(
        app.save_report_cells(
            {
                **query,
                "base_revision": matrix["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": [
                    {
                        "coordinate": cell["coordinate"],
                        "value": {"kind": "QUANTITY", "quantity": value},
                    }
                    for cell, value in zip(cells, ["0", "5"], strict=True)
                ],
            }
        )
    )
    result = data(app.get_report_matrix(query))
    summary = next(item for item in result["daily_summary"]["rows"] if item["row_id"] == row["id"])
    assert summary["annual"] == "5"
    assert summary["monthly"][:3] == ["0", "5", ""]
    assert summary["through_month"][:3] == ["0", "5", "5"]
