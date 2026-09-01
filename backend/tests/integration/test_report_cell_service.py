from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from backend.api.bridge import DesktopBridge
from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.report_cells import (
    IdempotencyConflictError,
    ReportCellChange,
    ReportCellCoordinate,
    ReportCellService,
    ReportCellValue,
    RevisionConflictError,
)
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_report_cells import (
    SqliteReportCellUnitOfWorkFactory,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
DEFINITIONS = Path(__file__).resolve().parents[3] / "resources" / "report-definitions"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "reporting.db"
    connection = connect_sqlite(path)
    apply_migrations(connection, MIGRATIONS)
    connection.execute("INSERT INTO organizations (id, code, name) VALUES (1, 'ORG', 'Org')")
    connection.execute(
        """
        INSERT INTO components (id, organization_id, code, name, kind)
        VALUES (2, 1, 'COMP', 'Component', 'WORKING_REFERENCE')
        """
    )
    connection.commit()
    connection.close()
    return path


def coordinate(component_id: str = "2") -> ReportCellCoordinate:
    return ReportCellCoordinate(
        report_type="DAILY_MOVEMENT",
        organization_id="1",
        component_id=component_id,
        operation_type="RECEIPT",
        operation_date="2026-09-01",
    )


def service(database_path: Path) -> ReportCellService:
    return ReportCellService(SqliteReportCellUnitOfWorkFactory(database_path))


def test_saves_confirmed_zero_then_missing_as_separate_immutable_revisions(
    database_path: Path,
) -> None:
    report_cells = service(database_path)
    first = report_cells.save_cells(
        [
            ReportCellChange(
                coordinate=coordinate(),
                value=ReportCellValue(kind="QUANTITY", quantity="0"),
                expected_revision=None,
            )
        ],
        idempotency_key="first",
        actor_ref="test-actor",
    )
    second = report_cells.save_cells(
        [
            ReportCellChange(
                coordinate=coordinate(),
                value=ReportCellValue(kind="DATA_NOT_PROVIDED"),
                expected_revision=1,
            )
        ],
        idempotency_key="second",
        actor_ref="test-actor",
    )

    assert first[0].value.quantity == "0"
    assert first[0].revision == 1
    assert second[0].value.kind == "DATA_NOT_PROVIDED"
    assert second[0].revision == 2
    current = report_cells.get_cells(report_type="DAILY_MOVEMENT", organization_id="1")
    assert current == second

    connection = connect_sqlite(database_path)
    try:
        assert connection.execute("SELECT count(*) FROM report_fact_revisions").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM stock_operations").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE report_fact_revisions SET quantity = '7' WHERE id = 1")
    finally:
        connection.close()


def test_optimistic_revision_rejects_stale_writer(database_path: Path) -> None:
    report_cells = service(database_path)
    report_cells.save_cells(
        [
            ReportCellChange(
                coordinate=coordinate(),
                value=ReportCellValue(kind="QUANTITY", quantity="5"),
                expected_revision=None,
            )
        ],
        idempotency_key="first",
        actor_ref="test-actor",
    )

    with pytest.raises(RevisionConflictError, match="current revision is 1"):
        report_cells.save_cells(
            [
                ReportCellChange(
                    coordinate=coordinate(),
                    value=ReportCellValue(kind="QUANTITY", quantity="6"),
                    expected_revision=None,
                )
            ],
            idempotency_key="stale",
            actor_ref="test-actor",
        )


def test_idempotent_retry_returns_original_response_without_new_revision(
    database_path: Path,
) -> None:
    report_cells = service(database_path)
    changes = [
        ReportCellChange(
            coordinate=coordinate(),
            value=ReportCellValue(kind="QUANTITY", quantity="5"),
            expected_revision=None,
        )
    ]
    first = report_cells.save_cells(changes, idempotency_key="same-key", actor_ref="test-actor")
    second = report_cells.save_cells(changes, idempotency_key="same-key", actor_ref="test-actor")
    assert second == first

    connection = connect_sqlite(database_path)
    try:
        assert connection.execute("SELECT count(*) FROM report_fact_revisions").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
    finally:
        connection.close()


def test_idempotency_key_cannot_be_reused_for_different_request(database_path: Path) -> None:
    report_cells = service(database_path)
    report_cells.save_cells(
        [
            ReportCellChange(
                coordinate=coordinate(),
                value=ReportCellValue(kind="QUANTITY", quantity="5"),
                expected_revision=None,
            )
        ],
        idempotency_key="same-key",
        actor_ref="test-actor",
    )

    with pytest.raises(IdempotencyConflictError):
        report_cells.save_cells(
            [
                ReportCellChange(
                    coordinate=coordinate(),
                    value=ReportCellValue(kind="QUANTITY", quantity="6"),
                    expected_revision=1,
                )
            ],
            idempotency_key="same-key",
            actor_ref="test-actor",
        )


def test_failed_batch_rolls_back_facts_audit_and_idempotency(database_path: Path) -> None:
    report_cells = service(database_path)
    with pytest.raises(sqlite3.IntegrityError):
        report_cells.save_cells(
            [
                ReportCellChange(
                    coordinate=coordinate(),
                    value=ReportCellValue(kind="QUANTITY", quantity="1"),
                    expected_revision=None,
                ),
                ReportCellChange(
                    coordinate=coordinate("999"),
                    value=ReportCellValue(kind="QUANTITY", quantity="2"),
                    expected_revision=None,
                ),
            ],
            idempotency_key="atomic",
            actor_ref="test-actor",
        )

    connection = connect_sqlite(database_path)
    try:
        assert connection.execute("SELECT count(*) FROM report_fact_revisions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM idempotency_records").fetchone() == (0,)
    finally:
        connection.close()


def test_bridge_exposes_health_bootstrap_and_safe_revision_error(database_path: Path) -> None:
    bridge = DesktopBridge(database_path, migrations_directory=MIGRATIONS)
    assert bridge.health()["ok"] is True
    bootstrap = bridge.bootstrap()
    assert bootstrap["ok"] is True
    assert bootstrap["data"] == {
        "database_ready": True,
        "schema_versions": ["0001", "0002", "0003"],
        "newly_applied": [],
        "contract_status": "WORKING_REFERENCE",
    }

    payload = {
        "changes": [
            {
                "coordinate": coordinate().to_dict(),
                "value": {"kind": "QUANTITY", "quantity": "0"},
                "expected_revision": None,
            }
        ],
        "idempotency_key": "bridge-first",
        "actor_ref": "test-actor",
    }
    assert bridge.save_cells(payload)["ok"] is True
    conflict = bridge.save_cells({**payload, "idempotency_key": "bridge-stale"})
    assert conflict["ok"] is False
    error = conflict["error"]
    assert isinstance(error, dict)
    assert error["code"] == "REVISION_CONFLICT"


def test_pywebview_matrix_bridge_persists_zero_and_empty_across_reload(
    database_path: Path,
) -> None:
    bridge = WorkingReferenceApplicationBridge(
        database_path,
        migrations_directory=MIGRATIONS,
        definitions_directory=DEFINITIONS,
    )
    first_response = bridge.get_report_matrix({"report_type": "DAILY_MOVEMENT"})
    assert first_response["ok"] is True
    first_matrix = cast(dict[str, Any], first_response["data"])
    first_row = cast(list[dict[str, Any]], first_matrix["rows"])[0]
    first_cell = cast(list[dict[str, Any]], first_row["cells"])[0]
    assert first_cell["value"] == {"kind": "DATA_NOT_PROVIDED"}

    zero_response = bridge.save_report_cells(
        {
            "report_type": "DAILY_MOVEMENT",
            "base_revision": first_matrix["matrix_revision"],
            "idempotency_key": "preview-zero",
            "changes": [
                {
                    "coordinate": first_cell["coordinate"],
                    "value": {"kind": "QUANTITY", "quantity": "0"},
                }
            ],
        }
    )
    assert zero_response["ok"] is True

    reloaded = bridge.get_report_matrix({"report_type": "DAILY_MOVEMENT"})
    reloaded_matrix = cast(dict[str, Any], reloaded["data"])
    reloaded_row = cast(list[dict[str, Any]], reloaded_matrix["rows"])[0]
    reloaded_cell = cast(list[dict[str, Any]], reloaded_row["cells"])[0]
    assert reloaded_cell["value"] == {"kind": "QUANTITY", "quantity": "0"}

    empty_response = bridge.save_report_cells(
        {
            "report_type": "DAILY_MOVEMENT",
            "base_revision": reloaded_matrix["matrix_revision"],
            "idempotency_key": "preview-empty",
            "changes": [
                {
                    "coordinate": reloaded_cell["coordinate"],
                    "value": {"kind": "DATA_NOT_PROVIDED"},
                }
            ],
        }
    )
    assert empty_response["ok"] is True

    final_response = bridge.get_report_matrix({"report_type": "DAILY_MOVEMENT"})
    final_matrix = cast(dict[str, Any], final_response["data"])
    final_row = cast(list[dict[str, Any]], final_matrix["rows"])[0]
    final_cell = cast(list[dict[str, Any]], final_row["cells"])[0]
    assert final_cell["value"] == {"kind": "DATA_NOT_PROVIDED"}
    import_error = cast(
        dict[str, Any],
        bridge.validate_import({"report_type": "DAILY_MOVEMENT"})["error"],
    )
    export_error = cast(
        dict[str, Any],
        bridge.export_report({"report_type": "DAILY_MOVEMENT"})["error"],
    )
    for error in (import_error, export_error):
        assert error["code"] == "TEMPLATE_CONTRACT_NOT_APPROVED"
        assert error["message"] == ("Недоступно до утверждения версии формы и координатной карты.")
