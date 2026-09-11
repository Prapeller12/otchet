"""Exercise packaged application services using disposable synthetic data only."""

from __future__ import annotations

import base64
import io
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge


def run_application_self_test(database: Path, migrations: Path, definitions: Path) -> None:
    root = database.parent
    app = WorkingReferenceApplicationBridge(
        database,
        migrations_directory=migrations,
        definitions_directory=definitions,
        inbox_directory=root / "inbox",
        backups_directory=root / "backups",
        application_version="self-test",
    )

    def checked(response: dict[str, Any]) -> Any:
        if not response.get("ok"):
            raise RuntimeError(f"Application self-test failed: {response}")
        return response["data"]

    query = {"report_type": "DAILY_MOVEMENT", "organization_id": "1"}
    layout = checked(app.get_report_layout(query))
    config = layout["rows"][0]["configuration"]
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(stream, "PNG")
    config.update(
        category="PKI",
        norm="3",
        opening="0",
        image="data:image/png;base64," + base64.b64encode(stream.getvalue()).decode(),
    )
    for item in layout["presets"][0]["indicators"]:
        existing = next(
            (field for field in config["indicators"] if field["code"] == item["code"]), None
        )
        if existing is not None:
            existing.update(item)
        else:
            config["indicators"].append(item)
    checked(app.save_report_layout({**query, "rows": layout["rows"]}))
    matrix = checked(app.get_report_matrix(query))
    changes = []
    for row in matrix["rows"]:
        values = {"WRK_DAILY_RECEIVED": ["71", "19"], "WRK_DAILY_USED": ["0", "0"]}.get(
            row["metric_code"], []
        )
        for index, value in enumerate(values):
            changes.append(
                {
                    "coordinate": row["cells"][index]["coordinate"],
                    "value": {"kind": "QUANTITY", "quantity": value},
                }
            )
    checked(
        app.save_report_cells(
            {
                **query,
                "base_revision": matrix["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": changes,
            }
        )
    )
    matrix = checked(app.get_report_matrix(query))
    ready = next(row for row in matrix["rows"] if row["metric_code"] == "WRK_DAILY_READY_SETS")
    if [cell["value"].get("quantity") for cell in ready["cells"][:2]] != ["23", "30"]:
        raise RuntimeError("Application self-test: readiness must be 23 and 30")
    if matrix["rows"][0]["image"] != config["image"]:
        raise RuntimeError("Application self-test: image was not retained")
    organization = checked(app.create_organization({"name": "Calendar self-test"}))["organization"]
    calendar_query = {**query, "organization_id": organization["id"], "year": 2024}
    annual = checked(app.get_report_matrix(calendar_query))
    index = next(
        i for i, column in enumerate(annual["time_columns"]) if column["id"] == "2024-09-14"
    )
    changes = [
        {
            "coordinate": annual["rows"][row]["cells"][index]["coordinate"],
            "value": {"kind": "QUANTITY", "quantity": quantity},
        }
        for row, quantity in [(0, "20"), (1, "3")]
    ]
    preview = checked(app.get_report_matrix({**calendar_query, "preview_changes": changes}))
    if (
        len(preview["time_columns"]) != 366
        or preview["rows"][2]["cells"][index]["value"].get("quantity") != "17"
    ):
        raise RuntimeError("Application self-test: annual balance preview must be 17")
    checked(
        app.save_report_presentation(
            {
                **query,
                "title": "Alpha report",
                "widths": {str(matrix["left_columns"][0]["id"]): 180},
            }
        )
    )
    if checked(app.get_report_matrix(query))["title"] != "Alpha report":
        raise RuntimeError("Application self-test: report title was not retained")
    for report in ("DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"):
        destination = root / f"{report}.xlsx"
        app.configure_excel_dialogs(
            open_file=partial(_destination, destination),
            save_file=partial(_destination, destination),
        )
        query["report_type"] = report
        checked(app.export_report(query))
        preview = checked(app.validate_import(query))
        checked(app.commit_import({"batch_id": preview["batch_id"]}))

    prepare_subsidiary_window_test(app)

    publication_query = {**calendar_query, "month": 9}
    verification = checked(app.get_report_verification(publication_query))
    checked(
        app.verify_report(
            {
                **publication_query,
                "signer_name": "Self-test",
                "confirmed": True,
                "snapshot_sha256": verification["snapshot_sha256"],
            }
        )
    )
    app.configure_pdf_dialog(partial(_destination, root / "self-test.pdf"))
    checked(app.export_pdf(publication_query))
    if not (root / "self-test.pdf").read_bytes().startswith(b"%PDF-"):
        raise RuntimeError("Application self-test: PDF was not generated")


def _destination(path: Path, _suggested: str = "") -> Path:
    return path


def prepare_reference_window_test(database: Path, directory: Path) -> None:
    """Seed only the disposable --ui-self-test directory with a weekly source form."""
    from typing import cast

    from openpyxl import Workbook
    from openpyxl.worksheet.worksheet import Worksheet

    from backend.infrastructure.database.sqlite_reference_reports import ReferenceReports

    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet["A2"] = "Контрольный импорт готового отчёта"
    sheet["E7"] = "Входимость в изделие"
    sheet["J7"] = "Общий дефицит"
    sheet["K7"] = "Факт"
    sheet["L9"] = 375
    sheet["M9"] = 300
    sheet["I9"] = "=SUM(L9:M9)"
    sheet["F9"] = "Контрольный изготовитель"
    source = directory / "ui-reference.xlsx"
    workbook.save(source)
    workbook.close()
    service = ReferenceReports(database)
    preview = service.stage(source, 1)
    service.commit(preview["batch_id"])


def prepare_subsidiary_window_test(app: WorkingReferenceApplicationBridge) -> None:
    """Exercise the approved supplier calculation in disposable desktop self-tests."""
    from datetime import date

    from backend.application.report_calendar import reporting_weeks

    def checked(response: dict[str, Any]) -> Any:
        if not response.get("ok"):
            raise RuntimeError(f"Subsidiary self-test: {response}")
        return response["data"]

    today = date.today()
    period = today.strftime("%Y-%m")
    query = {"report_type": "SUBSIDIARY", "organization_id": "1"}
    layout = checked(app.get_report_layout(query))
    row = layout["rows"][0]
    row["position_name"] = "Контрольная ДСЕ"
    row["configuration"]["norm"] = "4"
    row["configuration"]["category"] = "DSE"
    row["configuration"]["subsidiary"] = {
        "number": "1.1",
        "designation": "000.01",
        "suppliers": [
            {"id": "PRIMARY", "name": "Производитель А", "contract": "2500", "archived": False},
            {"id": "SECOND", "name": "Производитель Б", "contract": "2000", "archived": False},
        ],
    }
    checked(app.save_report_layout({**query, "rows": [row]}))
    annual = {**query, "year": today.year}
    matrix = checked(app.get_report_matrix(annual))
    checked(
        app.save_report_presentation(
            {**query, "expected_revision": matrix["matrix_revision"], "plans": {period: "1000"}}
        )
    )
    matrix = checked(app.get_report_matrix(annual))
    weeks = reporting_weeks(today.year, today.month)
    indices = {c["id"]: i for i, c in enumerate(matrix["time_columns"])}
    changes = [
        {
            "coordinate": matrix["rows"][r]["cells"][indices[column]]["coordinate"],
            "value": {"kind": "QUANTITY", "quantity": value},
        }
        for r, column, value in [
            (0, period + "-OPENING", "500"),
            (0, period + "-RECEIVED", "2000"),
            (1, period + "-RECEIVED", "1000"),
            (0, weeks[0].start.isoformat(), "100"),
            (1, weeks[1].start.isoformat(), "200"),
        ]
    ]
    checked(
        app.save_report_cells(
            {
                **annual,
                "base_revision": matrix["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": changes,
            }
        )
    )
    matrix = checked(app.get_report_matrix(annual))
    values = {c["column_id"]: c["value"].get("quantity") for c in matrix["rows"][0]["cells"]}
    if values[period + "-STOCK"] != "3200" or values[period + "-VARIANCE"] != "-500":
        raise RuntimeError("Subsidiary self-test: expected stock 3200 and variance -500")
