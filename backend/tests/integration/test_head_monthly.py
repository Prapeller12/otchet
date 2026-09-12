from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.application.monthly_report import monthly_snapshot
from backend.infrastructure.excel.openpyxl_matrix_workbook import OpenpyxlMatrixWorkbookAdapter
from backend.infrastructure.monthly_pdf import render_monthly_pdf
from backend.tests.integration.test_subsidiary_consumption import ROOT, app_at, data

QUERY = {"report_type": "HEAD_SITE", "organization_id": "1", "year": 2026}


def test_head_monthly_links_calculations_and_exports(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    ids = []
    for name in ("Общество А", "Общество Б"):
        org = data(app.create_organization({"name": name}))["organization"]
        ids.append(org["id"])
        query = {"report_type": "SUBSIDIARY", "organization_id": org["id"], "year": 2026}
        matrix = data(app.get_report_matrix(query))
        data(
            app.save_report_presentation(
                {
                    **query_without_year(query),
                    "expected_revision": matrix["matrix_revision"],
                    "plans": {"2026-09": "500"},
                    "actuals": {"2026-09": "500"},
                }
            )
        )
    layout = data(app.get_report_layout(query_without_year(QUERY)))
    row = layout["rows"][0]
    row["configuration"]["norm"] = "4"
    row["configuration"]["head_links"] = ids
    row["configuration"]["subsidiary"]["suppliers"] = [
        {"id": "A", "name": "А", "contract": "500", "archived": False},
        {"id": "B", "name": "Б", "contract": "500", "archived": False},
    ]
    data(app.save_report_layout({**query_without_year(QUERY), "rows": [row]}))
    m = data(app.get_report_matrix(QUERY))
    data(
        app.save_report_presentation(
            {
                **query_without_year(QUERY),
                "expected_revision": m["matrix_revision"],
                "plans": {"2026-09": "250"},
                "actuals": {"2026-09": "200"},
            }
        )
    )
    m = data(app.get_report_matrix(QUERY))
    changes = []
    for index, code, value in [
        (0, "OPENING", "100"),
        (0, "PLAN", "500"),
        (1, "PLAN", "500"),
        (0, "FACT", "500"),
        (1, "FACT", "500"),
    ]:
        c = next(c for c in m["rows"][index]["cells"] if c["column_id"] == "2026-09-" + code)
        changes.append(
            {"coordinate": c["coordinate"], "value": {"kind": "QUANTITY", "quantity": value}}
        )
    data(
        app.save_report_cells(
            {
                **QUERY,
                "base_revision": m["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": changes,
            }
        )
    )
    m = data(app_at(tmp_path).get_report_matrix(QUERY))
    values = {c["column_id"]: c["value"].get("quantity") for c in m["rows"][0]["cells"]}
    assert values["2026-09-USED"] == "800"
    assert values["2026-09-STOCK"] == "300"
    assert values["2026-09-VARIANCE"] == "100"
    assert not any(
        c.get("issue", {}).get("code") == "PRODUCTION_MISMATCH"
        for r in m["rows"]
        for c in r["cells"]
        if c["column_id"].startswith("2026-09")
    )
    snap = monthly_snapshot(m, 9, "Головная площадка")
    assert [c["kind"] for c in snap["columns"]] == [
        "OPENING",
        "PLAN",
        "FACT",
        "USED",
        "STOCK",
        "VARIANCE",
    ]
    pdf = render_monthly_pdf(
        snap, {"status": "UNVERIFIED"}, ROOT / "resources/fonts/ReportingSerif.ttf"
    )
    assert pdf.startswith(b"%PDF")
    destination = tmp_path / "head.xlsx"
    OpenpyxlMatrixWorkbookAdapter().write(destination, m)
    # One subsidiary cannot be assigned to another component, even via the API.
    duplicate = {**row, "id": None, "position_name": "Другая часть"}
    rejected = app.save_report_layout({**query_without_year(QUERY), "rows": [row, duplicate]})
    assert not rejected["ok"]
    assert len(data(app.get_report_layout(query_without_year(QUERY)))["rows"]) == 1
    changed = changes[-1]
    changed["value"]["quantity"] = "501"
    m = data(app.get_report_matrix(QUERY))
    data(
        app.save_report_cells(
            {
                **QUERY,
                "base_revision": m["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": [changed],
            }
        )
    )
    m = data(app.get_report_matrix(QUERY))
    assert any(row["errors"] for row in monthly_snapshot(m, 9, "Головная площадка")["rows"])

    errors = "\n".join(
        e for row in monthly_snapshot(m, 9, "Головная площадка")["rows"] for e in row["errors"]
    )
    for expected in [
        "2026-09",
        "Общество А: 500",
        "Общество Б: 500",
        "1001",
        "1000",
        "Превышение: 1",
        "Факт",
        row["position_name"],
    ]:
        assert expected in errors


def query_without_year(query: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in query.items() if k != "year"}
