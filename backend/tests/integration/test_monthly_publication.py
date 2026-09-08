from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.monthly_report import monthly_snapshot
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2024}


def data(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


@pytest.fixture
def app(tmp_path: Path) -> WorkingReferenceApplicationBridge:
    return WorkingReferenceApplicationBridge(
        tmp_path / "report.db",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
    )


def save(app: WorkingReferenceApplicationBridge, quantity: str) -> None:
    matrix = data(app.get_report_matrix(QUERY))
    index = next(i for i, c in enumerate(matrix["time_columns"]) if c["id"] == "2024-02-29")
    data(
        app.save_report_cells(
            {
                **QUERY,
                "base_revision": matrix["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": [
                    {
                        "coordinate": matrix["rows"][row]["cells"][index]["coordinate"],
                        "value": {"kind": "QUANTITY", "quantity": value},
                    }
                    for row, value in [(0, quantity), (1, "12345")]
                ],
            }
        )
    )


def test_snapshot_values_and_verification_lifecycle(
    app: WorkingReferenceApplicationBridge, tmp_path: Path
) -> None:
    save(app, "99999")
    snapshot = monthly_snapshot(data(app.get_report_matrix(QUERY)), 2, "Головная площадка")
    assert len(snapshot["days"]) == 29
    assert snapshot["rows"][0]["days"][-1] == "99999"
    assert snapshot["rows"][2]["days"][-1] == "87654"
    assert snapshot["rows"][2]["summary"] == "87654"
    assert snapshot["rows"][0]["prior"] == [""]
    query = {**QUERY, "month": 2}
    status = data(app.get_report_verification(query))
    request = {
        **query,
        "signer_name": "Иванов Иван",
        "confirmed": True,
        "snapshot_sha256": status["snapshot_sha256"],
    }
    assert not app.verify_report({**request, "confirmed": False})["ok"]
    result = data(app.verify_report(request))
    assert result["status"] == "VERIFIED"
    assert data(app.verify_report(request))["id"] == result["id"]
    restarted = WorkingReferenceApplicationBridge(
        tmp_path / "report.db",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
    )
    assert data(restarted.get_report_verification(query))["status"] == "VERIFIED"
    save(app, "10000")
    assert data(app.get_report_verification(query))["status"] == "STALE"
    assert not app.verify_report(request)["ok"]
    assert not app.get_report_verification({**query, "expected_revision": "outdated"})["ok"]
    with sqlite3.connect(tmp_path / "report.db") as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE report_verifications SET signer_name='Другой'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM report_verifications")


@pytest.mark.parametrize(
    "year, month", [(2024, 1), (2024, 2), (2023, 2), (2024, 4), (2024, 8), (2024, 12)]
)
def test_pdf_a4_months_and_cancel(
    app: WorkingReferenceApplicationBridge, tmp_path: Path, year: int, month: int
) -> None:
    save(app, "99999")
    destination = tmp_path / "monthly.pdf"
    app.configure_pdf_dialog(lambda _: destination)
    result = data(app.export_pdf({**QUERY, "year": year, "month": month}))
    assert result["file_name"] == "monthly.pdf"
    content = destination.read_bytes()
    assert content.startswith(b"%PDF-")
    assert b"841.8898 595.2756" in content  # A4 landscape, no sideways continuation.
    app.configure_pdf_dialog(lambda _: None)
    assert data(app.export_pdf({**QUERY, "year": year, "month": month}))["cancelled"]
    assert destination.read_bytes() == content
    assert not app.export_pdf({**QUERY, "month": 13})["ok"]


def test_verification_migration_from_dev7(tmp_path: Path) -> None:
    import shutil

    old = tmp_path / "old"
    old.mkdir()
    for migration in (ROOT / "backend/migrations").glob("*.sql"):
        if migration.name < "0008":
            shutil.copy(migration, old / migration.name)
    connection = connect_sqlite(tmp_path / "old.db")
    try:
        apply_migrations(connection, old)
        assert apply_migrations(connection, ROOT / "backend/migrations") == ("0008", "0009")
        assert apply_migrations(connection, ROOT / "backend/migrations") == ()
    finally:
        connection.close()
