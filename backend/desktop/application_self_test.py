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


def _destination(path: Path, _suggested: str = "") -> Path:
    return path
