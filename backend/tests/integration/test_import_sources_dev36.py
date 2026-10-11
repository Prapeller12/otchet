"""Canonical evidence survives encrypted snapshots and never depends on client inboxes."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from openpyxl import load_workbook

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.excel_reports import (
    ExcelSourceIntegrityError,
    ExcelSourceReselectRequiredError,
)
from backend.infrastructure.access_vault import AccessVault
from backend.infrastructure.database.encrypted_sqlite import encrypted_backup
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_excel_imports import SqliteExcelImportRepository
from backend.repositories.excel_imports import ImportBatchDraft, ImportWorkbookSource

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "SUBSIDIARY", "organization_id": "1", "year": 2026}


def data(response: dict[str, object]) -> dict[str, Any]:
    assert response["ok"] is True, response
    return cast(dict[str, Any], response["data"])


def application(root: Path, *, initialize: bool = True) -> WorkingReferenceApplicationBridge:
    database = root / "data" / "reporting.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    if initialize:
        vault = AccessVault(database, root / "backups", device_protector=None)
        vault.initialize({"id": "admin", "display_name": "Test", "role": "admin"}, "test-pin")
        vault.finish_setup()
    return WorkingReferenceApplicationBridge(
        database,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=root / "backups",
        inbox_directory=root / "imports" / "inbox",
        initialize_workspace=initialize,
    )


def clone_client(
    source: WorkingReferenceApplicationBridge, root: Path
) -> WorkingReferenceApplicationBridge:
    database = root / "data" / "reporting.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    encrypted_backup(source._database_path, database)
    for suffix in (".keys.json", ".lifecycle.json"):
        original = source._database_path.with_suffix(source._database_path.suffix + suffix)
        if original.exists():
            shutil.copyfile(original, database.with_suffix(database.suffix + suffix))
    AccessVault(database, root / "backups", device_protector=None).unlock("admin", "test-pin")
    return application(root, initialize=False)


def canonical_source(app: WorkingReferenceApplicationBridge, path: Path) -> bytes:
    app.configure_excel_dialogs(open_file=lambda: path, save_file=lambda _name: path)
    data(app.export_report(QUERY))
    workbook = load_workbook(path)
    address = str(workbook["_Системная карта"]["A6"].value)
    workbook["Отчёт"][address] = 37.5
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


def counts(app: WorkingReferenceApplicationBridge) -> dict[str, int]:
    with closing(connect_sqlite(app._database_path)) as connection:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "import_batches",
                "import_rows",
                "import_workbook_sources",
                "import_batch_sources",
                "report_fact_revisions",
                "audit_events",
            )
        }


def legacy_batch(
    app: WorkingReferenceApplicationBridge,
    content: bytes,
    *,
    stored_path: str | None = None,
) -> str:
    identity = uuid4().hex
    sha256 = hashlib.sha256(content).hexdigest()
    app._excel._imports.add_batch(
        ImportBatchDraft(
            id=identity,
            report_type="SUBSIDIARY",
            organization_id=1,
            source_file_name="Original name.xlsx",
            source_sha256=sha256,
            stored_relative_path=stored_path or f"{identity}.xlsx",
            status="STAGED",
            rows=(),
            issues=(),
            metadata={"original_source_sha256": sha256},
        )
    )
    return identity


def test_encrypted_cross_client_restage_preserves_provenance_and_commit_idempotence(
    tmp_path: Path,
) -> None:
    first = application(tmp_path / "client-a")
    source = tmp_path / "Workbook original name.xlsx"
    original = canonical_source(first, source)
    preview_a = data(first.validate_import(QUERY))
    assert preview_a["error_count"] == 0
    batch_a = first._excel._imports.get_batch(preview_a["batch_id"])
    assert batch_a is not None
    assert list(first._excel._inbox_directory.iterdir()) == []
    assert original not in first._database_path.read_bytes()
    assert first._database_path.read_bytes()[:16] != b"SQLite format 3\x00"

    second = clone_client(first, tmp_path / "client-b")
    # Client B has neither the source workbook nor the source client's local cache.
    source.unlink()
    assert not second._excel._inbox_directory.exists()

    def unexpected_dialog() -> Path:
        pytest.fail("Restaging an archived batch must not open a file dialog")

    second.configure_excel_dialogs(open_file=unexpected_dialog, save_file=lambda _name: None)
    layout_before = data(
        second.get_report_layout({"report_type": "SUBSIDIARY", "organization_id": "1"})
    )
    matrix_before = data(second.get_report_matrix(QUERY))
    preview_b = data(second.validate_import({**QUERY, "batch_id": batch_a.id}))
    assert preview_b["error_count"] == 0
    assert preview_b["file_name"] == "Workbook original name.xlsx"
    assert preview_b["source_sha256"] == preview_a["source_sha256"]
    assert preview_b["batch_id"] != batch_a.id
    batch_b = second._excel._imports.get_batch(preview_b["batch_id"])
    assert batch_b is not None
    assert batch_b.rows == batch_a.rows
    assert batch_b.metadata == batch_a.metadata
    assert second._excel._imports.get_workbook_source(batch_b.id) == ImportWorkbookSource(
        hashlib.sha256(original).hexdigest(), original
    )
    assert counts(second)["import_workbook_sources"] == 1
    assert counts(second)["import_batch_sources"] == 2
    assert list(second._excel._inbox_directory.iterdir()) == []
    layout_after = data(
        second.get_report_layout({"report_type": "SUBSIDIARY", "organization_id": "1"})
    )
    assert layout_after == layout_before
    assert data(second.get_report_matrix(QUERY))["presentation"] == matrix_before["presentation"]
    # Original workbook content travels inside the encrypted snapshot, never as a sidecar.
    assert not list((tmp_path / "client-b").rglob("*.xlsx"))

    result = data(second.commit_import({"batch_id": batch_b.id, "year": 2026}))
    assert result["status"] == "COMMITTED"
    committed_counts = counts(second)
    assert committed_counts["report_fact_revisions"] == 1
    retry = data(second.commit_import({"batch_id": batch_b.id, "year": 2026}))
    assert retry["already_committed"] is True
    assert counts(second) == committed_counts
    duplicate = data(second.validate_import({**QUERY, "batch_id": batch_a.id}))
    assert duplicate["already_imported"] is True
    assert duplicate["batch_id"] == batch_b.id
    assert duplicate["file_name"] == "Workbook original name.xlsx"
    assert counts(second) == committed_counts
    assert list(second._excel._inbox_directory.iterdir()) == []
    matrix = data(second.get_report_matrix(QUERY))
    assert any(c["value"].get("quantity") == "37.5" for r in matrix["rows"] for c in r["cells"])


def test_legacy_source_missing_has_precise_reselect_error_and_no_writes(tmp_path: Path) -> None:
    app = application(tmp_path)
    identity = legacy_batch(app, b"previously checked source")
    before = counts(app)
    result = app.validate_import({**QUERY, "batch_id": identity})
    assert result["ok"] is False
    error = cast(dict[str, object], result["error"])
    assert error["code"] == "EXCEL_SOURCE_RESELECT_REQUIRED"
    assert "Выберите исходный файл Excel заново" in str(error["message"])
    assert counts(app) == before
    assert not app._excel._inbox_directory.exists()


def test_legacy_exact_checksum_backfills_once_and_moves_to_another_client(tmp_path: Path) -> None:
    app = application(tmp_path / "a")
    content = b"exact legacy workbook bytes"
    identity = legacy_batch(app, content)
    inbox = app._excel._inbox_directory
    inbox.mkdir(parents=True)
    original = inbox / f"{identity}.xlsx"
    original.write_bytes(content)
    for _ in range(2):
        with app._excel.materialize_import_source(identity) as restored:
            assert restored != original
            assert restored.read_bytes() == content
        assert not restored.exists()
    assert original.read_bytes() == content
    assert counts(app)["import_workbook_sources"] == counts(app)["import_batch_sources"] == 1
    with closing(connect_sqlite(app._database_path)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='ARCHIVE_EXCEL_SOURCE'"
        ).fetchone() == (1,)
    second = clone_client(app, tmp_path / "b")
    original.unlink()
    before = counts(second)
    with second._excel.materialize_import_source(identity) as restored:
        assert restored.read_bytes() == content
    assert counts(second) == before
    assert list(second._excel._inbox_directory.iterdir()) == []


@pytest.mark.parametrize("stored_path", ["../escaped.xlsx", "/tmp/source.xlsx", "report.xlsx"])
def test_legacy_arbitrary_paths_are_not_followed(tmp_path: Path, stored_path: str) -> None:
    app = application(tmp_path)
    identity = legacy_batch(app, b"source", stored_path=stored_path)
    before = counts(app)
    with pytest.raises(ExcelSourceReselectRequiredError):
        with app._excel.materialize_import_source(identity):
            pytest.fail("An untrusted legacy filename must never select a source")
    assert counts(app) == before
    assert not app._excel._inbox_directory.exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_legacy_changed_or_symlinked_source_is_not_archived(tmp_path: Path, symlink: bool) -> None:
    app = application(tmp_path)
    identity = legacy_batch(app, b"original workbook")
    inbox = app._excel._inbox_directory
    inbox.mkdir(parents=True)
    source = inbox / f"{identity}.xlsx"
    if symlink:
        target = tmp_path / "unrelated.xlsx"
        target.write_bytes(b"original workbook")
        source.symlink_to(target)
    else:
        source.write_bytes(b"different workbook")
    before = counts(app)
    with pytest.raises(ExcelSourceIntegrityError):
        with app._excel.materialize_import_source(identity):
            pytest.fail("Unverified legacy bytes must not be materialized")
    assert counts(app) == before
    assert list(inbox.iterdir()) == [source]


def test_archived_checksum_failure_precedes_local_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(tmp_path)
    content = b"original workbook"
    identity = legacy_batch(app, content)
    monkeypatch.setattr(
        app._excel._imports,
        "get_workbook_source",
        lambda _identity: ImportWorkbookSource(hashlib.sha256(content).hexdigest(), b"corrupted"),
    )
    before = counts(app)
    with pytest.raises(ExcelSourceIntegrityError):
        with app._excel.materialize_import_source(identity):
            pytest.fail("Corrupted archived evidence must not be written to the local cache")
    assert counts(app) == before
    assert not app._excel._inbox_directory.exists()


def test_local_destination_collision_neither_overwrites_nor_deletes_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(tmp_path)
    content = b"original workbook"
    identity = legacy_batch(app, content)
    app._excel._imports.archive_workbook_source(
        identity, ImportWorkbookSource(hashlib.sha256(content).hexdigest(), content)
    )
    collision_uuid = UUID(int=1)
    monkeypatch.setattr("backend.application.excel_reports.uuid4", lambda: collision_uuid)
    inbox = app._excel._inbox_directory
    inbox.mkdir(parents=True)
    existing = inbox / f"{identity}-{collision_uuid.hex}.xlsx"
    existing.write_bytes(b"unrelated local file")
    with pytest.raises(FileExistsError):
        with app._excel.materialize_import_source(identity):
            pytest.fail("An existing file must not be adopted as a materialization")
    assert existing.read_bytes() == b"unrelated local file"


def test_source_binding_failure_rolls_back_batch_rows_blob_and_audit(tmp_path: Path) -> None:
    app = application(tmp_path)
    source = tmp_path / "source.xlsx"
    original = canonical_source(app, source)
    with closing(connect_sqlite(app._database_path)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER injected_source_failure BEFORE INSERT ON import_batch_sources "
            "BEGIN SELECT RAISE(ABORT,'source archive write failed'); END"
        )
    before = counts(app)
    result = app.validate_import(QUERY)
    assert result["ok"] is False
    assert "source archive write failed" in str(result)
    assert counts(app) == before
    assert source.read_bytes() == original
    assert list(app._excel._inbox_directory.iterdir()) == []


@pytest.mark.parametrize(
    ("content", "sha256", "message"),
    [
        (b"", hashlib.sha256(b"").hexdigest(), "Размер"),
        (b"changed", hashlib.sha256(b"original").hexdigest(), "сумма"),
        (b"other workbook", hashlib.sha256(b"other workbook").hexdigest(), "не соответствует"),
    ],
)
def test_repository_rejects_invalid_or_unrelated_evidence_without_side_effects(
    tmp_path: Path, content: bytes, sha256: str, message: str
) -> None:
    app = application(tmp_path)
    identity = legacy_batch(app, b"original")
    before = counts(app)
    with pytest.raises(ValueError, match=message):
        app._excel._imports.archive_workbook_source(identity, ImportWorkbookSource(sha256, content))
    assert counts(app) == before


def test_source_schema_bounds_bytes_and_rejects_dangling_batch_binding(tmp_path: Path) -> None:
    app = application(tmp_path)
    before = counts(app)
    with closing(connect_sqlite(app._database_path)) as connection:
        for statement, parameters in (
            (
                "INSERT INTO import_workbook_sources VALUES (?, ?, ?, 'test')",
                ("invalid hash", b"x", 1),
            ),
            (
                "INSERT INTO import_workbook_sources VALUES (?, ?, ?, 'test')",
                ("a" * 64, b"", 0),
            ),
            (
                "INSERT INTO import_workbook_sources VALUES (?, ?, ?, 'test')",
                ("a" * 64, b"x", 2),
            ),
            (
                "INSERT INTO import_workbook_sources "
                "VALUES (?, zeroblob(52428801), 52428801, 'test')",
                ("a" * 64,),
            ),
            (
                "INSERT INTO import_batch_sources VALUES (?, ?)",
                (uuid4().hex, "a" * 64),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, parameters)
            connection.rollback()
    assert counts(app) == before


def test_migration_0018_adopts_legacy_schema_without_guessing_source_bytes(tmp_path: Path) -> None:
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for path in (ROOT / "backend/migrations").glob("*.sql"):
        if path.name[:4] <= "0017":
            shutil.copyfile(path, old_migrations / path.name)
    database = tmp_path / "legacy.sqlite3"
    with closing(connect_sqlite(database)) as connection, connection:
        apply_migrations(connection, old_migrations)
        connection.execute("INSERT INTO organizations(id,code,name) VALUES(1,'TEST','Test')")
    repository = SqliteExcelImportRepository(str(database))
    content = b"legacy source bytes not available on another client"
    identity = uuid4().hex
    sha256 = hashlib.sha256(content).hexdigest()
    repository.add_batch(
        ImportBatchDraft(
            identity, "SUBSIDIARY", 1, "old.xlsx", sha256, f"{identity}.xlsx", "STAGED", (), ()
        )
    )
    with closing(connect_sqlite(database)) as connection:
        assert "0018" in apply_migrations(connection)
        assert apply_migrations(connection) == ()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert repository.get_batch(identity) is not None
    assert repository.get_workbook_source(identity) is None
    repository.archive_workbook_source(identity, ImportWorkbookSource(sha256, content))
    with closing(connect_sqlite(database)) as connection:
        for statement in (
            "UPDATE import_workbook_sources SET workbook_bytes=x'00'",
            "DELETE FROM import_workbook_sources",
            "UPDATE import_batch_sources SET source_sha256=source_sha256",
            "DELETE FROM import_batch_sources",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
