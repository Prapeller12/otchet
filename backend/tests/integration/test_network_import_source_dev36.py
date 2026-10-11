"""Independent canonical source acceptance through actual publication and refresh."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.network_snapshots import SnapshotCoordinator

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "SUBSIDIARY", "organization_id": "1", "year": 2026}
PIN = "network-source-acceptance-pin"


def data(response: dict[str, Any]) -> dict[str, Any]:
    assert response["ok"] is True, response
    return cast(dict[str, Any], response["data"])


def client(store: Path, local: Path) -> tuple[SecureDesktopBridge, SnapshotCoordinator]:
    coordinator = SnapshotCoordinator(store, local / "data" / "reporting.sqlite3", 0.5)
    coordinator.prepare()
    return (
        SecureDesktopBridge(
            coordinator.database_path,
            migrations_directory=ROOT / "backend/migrations",
            definitions_directory=ROOT / "resources/report-definitions",
            backups_directory=local / "backups",
            inbox_directory=local / "imports" / "inbox",
            device_protector=None,
            snapshot_coordinator=coordinator,
        ),
        coordinator,
    )


def source_fixture(
    store: Path, local: Path, source: Path
) -> tuple[SecureDesktopBridge, SnapshotCoordinator, dict[str, str], bytes]:
    app, coordinator = client(store, local)
    app._configure_excel_dialogs(open_file=lambda: source, save_file=lambda _name: source)
    setup = data(
        app.setup_access(
            {
                "display_name": "Source network acceptance",
                "pin": PIN,
                "operation_id": uuid4().hex,
                "base_global_revision": None,
            }
        )
    )
    auth = {"signer_id": setup["current_user"]["id"], "pin": PIN}
    data(app.export_report(QUERY))
    book = load_workbook(source)
    address = str(book["_Системная карта"]["A6"].value)
    book["Отчёт"][address] = 25.5
    book.save(source)
    book.close()
    return app, coordinator, auth, source.read_bytes()


def packet(coordinator: SnapshotCoordinator, auth: dict[str, str], **fields: Any) -> dict[str, Any]:
    return {
        **fields,
        "authorization": auth,
        "base_global_revision": coordinator.loaded_global_revision,
        "operation_id": uuid4().hex,
    }


def counts(app: SecureDesktopBridge) -> dict[str, int]:
    with closing(connect_sqlite(app._database)) as connection:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "import_batches",
                "import_rows",
                "import_workbook_sources",
                "import_batch_sources",
                "report_fact_revisions",
                "report_access_events",
                "audit_events",
                "network_operation_receipts",
            )
        }


def test_published_workbook_blob_refreshes_and_restages_on_another_client(tmp_path: Path) -> None:
    store = tmp_path / "share"
    source = tmp_path / "Original network workbook.xlsx"
    first, first_coordinator, auth, original = source_fixture(store, tmp_path / "a", source)
    second, second_coordinator = client(store, tmp_path / "b")

    def unexpected_dialog() -> Path:
        pytest.fail("Restaging a shared canonical source must not ask for a local workbook")

    second._configure_excel_dialogs(open_file=unexpected_dialog, save_file=lambda _name: None)
    data(second.unlock_access(auth))
    assert first._application is not None and second._application is not None
    second_inbox = second._application._excel._inbox_directory
    before_revision = second_coordinator.loaded_global_revision
    initial_pointer = (store / "CURRENT").read_bytes()
    staged = data(first.validate_import(packet(first_coordinator, auth, **QUERY)))
    assert staged["error_count"] == 0
    assert staged["global_revision"] == first_coordinator.loaded_global_revision
    assert (store / "CURRENT").read_bytes() != initial_pointer
    source_batch = first._application._excel._imports.get_batch(staged["batch_id"])
    assert source_batch is not None
    assert list(first._application._excel._inbox_directory.iterdir()) == []
    assert second._application._excel._imports.get_batch(source_batch.id) is None
    assert data(second.get_network_status())["state"] == "stale"
    assert second_coordinator.loaded_global_revision == before_revision
    source.unlink()
    assert not second_inbox.exists()

    pointer = json.loads((store / "CURRENT").read_bytes())
    generation = store / "generations" / pointer["generation_id"]
    assert not list(store.rglob("*.xlsx"))
    assert not (generation / "reporting.sqlite3").read_bytes().startswith(b"SQLite format 3\x00")
    assert original not in (generation / "reporting.sqlite3").read_bytes()
    manifest = json.loads((generation / "manifest.json").read_text())
    assert set(manifest["files"]) <= {
        "reporting.sqlite3",
        "reporting.sqlite3.keys.json",
        "reporting.sqlite3.lifecycle.json",
    }
    data(second.refresh_network_data({"has_unsaved_changes": False, "has_unknown_outcome": False}))
    assert second_coordinator.loaded_global_revision == staged["global_revision"]
    assert second._application is not None
    restored_source = second._application._excel._imports.get_workbook_source(source_batch.id)
    assert restored_source is not None
    assert restored_source.content == original
    assert restored_source.sha256 == hashlib.sha256(original).hexdigest()
    assert not second_inbox.exists()
    layout_query = {"report_type": "SUBSIDIARY", "organization_id": "1"}
    layout_before = data(second.get_report_layout(layout_query))
    presentation_before = data(second.get_report_matrix(QUERY))["presentation"]

    restage_request = packet(second_coordinator, auth, **QUERY, batch_id=source_batch.id)
    restaged = data(second.validate_import(restage_request))
    assert restaged["file_name"] == "Original network workbook.xlsx"
    assert restaged["error_count"] == 0
    assert restaged["source_sha256"] == staged["source_sha256"]
    restored_batch = second._application._excel._imports.get_batch(restaged["batch_id"])
    assert restored_batch is not None
    assert restored_batch.rows == source_batch.rows
    assert restored_batch.metadata == source_batch.metadata
    assert counts(second)["import_workbook_sources"] == 1
    assert counts(second)["import_batch_sources"] == 2
    assert data(second.get_report_layout(layout_query)) == layout_before
    assert data(second.get_report_matrix(QUERY))["presentation"] == presentation_before
    assert list(second_inbox.iterdir()) == []
    restaged_counts = counts(second)
    restaged_pointer = (store / "CURRENT").read_bytes()
    assert data(second.validate_import(restage_request))["idempotent_replay"] is True
    assert counts(second) == restaged_counts
    assert (store / "CURRENT").read_bytes() == restaged_pointer

    commit_request = packet(second_coordinator, auth, batch_id=restored_batch.id, year=2026)
    committed = data(second.commit_import(commit_request))
    assert committed["status"] == "COMMITTED"
    committed_counts = counts(second)
    assert committed_counts["report_fact_revisions"] == 1
    committed_pointer = (store / "CURRENT").read_bytes()
    assert data(second.commit_import(commit_request))["idempotent_replay"] is True
    assert counts(second) == committed_counts
    assert (store / "CURRENT").read_bytes() == committed_pointer
    with closing(connect_sqlite(second._database)) as connection:
        receipts = connection.execute(
            "SELECT result_json FROM network_operation_receipts"
        ).fetchall()
        assert all(PIN not in row[0] and '"authorization"' not in row[0] for row in receipts)
        assert all('"workbook_source"' not in row[0] for row in receipts)
    data(first.refresh_network_data({"has_unsaved_changes": False, "has_unknown_outcome": False}))
    assert first_coordinator.loaded_global_revision == second_coordinator.loaded_global_revision
    matrix = data(first.get_report_matrix(QUERY))
    assert any(c["value"].get("quantity") == "25.5" for r in matrix["rows"] for c in r["cells"])
    assert not list(store.rglob("*.xlsx"))
    assert all(PIN not in path.read_text() for path in store.rglob("*.json"))


def test_failed_publication_rolls_back_source_blob_batch_and_audit(tmp_path: Path) -> None:
    store = tmp_path / "share"
    source = tmp_path / "original.xlsx"
    app, coordinator, auth, original = source_fixture(store, tmp_path / "a", source)
    original_counts = counts(app)
    original_pointer = (store / "CURRENT").read_bytes()
    request = packet(coordinator, auth, **QUERY)

    def failure(stage: str) -> None:
        if stage == "before_current_replace":
            raise OSError("Independent source publication fault")

    coordinator._fault = failure
    response = app.validate_import(request)
    assert response["ok"] is False
    assert response["error"]["code"] == "NETWORK_UNAVAILABLE"
    assert counts(app) == original_counts
    assert (store / "CURRENT").read_bytes() == original_pointer
    assert coordinator.pending_operation_id is None
    assert source.read_bytes() == original
    assert app._application is not None
    assert list(app._application._excel._inbox_directory.iterdir()) == []
    coordinator._fault = lambda _stage: None
    result = data(app.validate_import(request))
    assert result["error_count"] == 0
    assert counts(app)["import_workbook_sources"] == counts(app)["import_batch_sources"] == 1
    assert not list(store.rglob("*.xlsx"))
