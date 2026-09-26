"""Fault injection at real SQL and recovery-publication boundaries."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from backend.application.excel_reports import (
    ExcelReportService,
    ParsedExcelCell,
    ParsedWorkbook,
)
from backend.application.report_cells import (
    ReportCellCoordinate,
    ReportCellService,
    ReportCellValue,
)
from backend.desktop.database_bootstrap import (
    backup_database,
    list_backups,
    restore_backup,
    verify_backup,
)
from backend.infrastructure.access_vault import AccessVault
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_excel_imports import SqliteExcelImportRepository
from backend.infrastructure.database.sqlite_report_cells import (
    SqliteReportCellUnitOfWork,
    SqliteReportCellUnitOfWorkFactory,
)
from backend.repositories.report_facts import ReportCellUnitOfWork


@pytest.fixture
def encrypted_database(tmp_path: Path) -> Path:
    database = tmp_path / "data.sqlite3"
    vault = AccessVault(database, tmp_path / "backups", device_protector=None)
    vault.initialize({"id": "admin", "display_name": "Audit", "role": "admin"}, "test-pin")
    vault.finish_setup()
    with closing(connect_sqlite(database)) as connection, connection:
        apply_migrations(connection)
        connection.execute("INSERT INTO organizations(id,code,name) VALUES(1,'TEST','Test')")
        connection.execute(
            "INSERT INTO components(id,organization_id,code,name,kind) "
            "VALUES(2,1,'COMP','Component','WORKING_REFERENCE')"
        )
    return database


class WorkbookStub:
    parsed: ParsedWorkbook

    def parse(self, *args: Any, **kwargs: Any) -> ParsedWorkbook:
        return self.parsed

    def write(self, destination: Path, matrix: Any) -> int:
        raise NotImplementedError


def service(database: Path, adapter: WorkbookStub) -> ExcelReportService:
    return ExcelReportService(
        report_cells=ReportCellService(SqliteReportCellUnitOfWorkFactory(database)),
        import_repository=SqliteExcelImportRepository(str(database)),
        workbook_adapter=adapter,
        database_path=database,
        inbox_directory=database.parent / "inbox",
        backups_directory=database.parent / "backups",
        application_version="dev33",
    )


def coordinate() -> ReportCellCoordinate:
    return ReportCellCoordinate(
        report_type="DAILY_MOVEMENT",
        organization_id="1",
        component_id="2",
        operation_type="RECEIPT",
        operation_date="2026-09-01",
    )


def allowed(_report: str, _organization: str) -> set[str]:
    return {
        json.dumps(
            coordinate().to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    }


def test_import_status_failure_rolls_back_facts_structure_and_idempotency(
    encrypted_database: Path,
) -> None:
    database = encrypted_database
    adapter = WorkbookStub()
    adapter.parsed = ParsedWorkbook(
        (ParsedExcelCell("A1", coordinate(), ReportCellValue("QUANTITY", "12")),),
        (),
        metadata={"profile": "test-v1"},
        skipped_count=3,
    )
    application = service(database, adapter)
    source = database.parent / "source.xlsx"
    source.write_bytes(b"source represented by validated adapter")
    preview = application.stage_import(
        source, report_type="DAILY_MOVEMENT", organization_id=1, matrix={}
    )
    with closing(connect_sqlite(database)) as connection, connection:
        connection.execute("CREATE TABLE structure_probe(value TEXT)")
        connection.execute(
            "CREATE TRIGGER injected_status_failure BEFORE UPDATE OF status ON import_batches "
            "BEGIN SELECT RAISE(ABORT,'injected disk write failure'); END"
        )

    def structure(transaction: ReportCellUnitOfWork) -> None:
        assert isinstance(transaction, SqliteReportCellUnitOfWork)
        transaction.connection.execute("INSERT INTO structure_probe VALUES ('new position')")

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        application.commit_import(
            preview.batch_id, allowed_coordinates=allowed, before_commit=structure
        )
    with closing(connect_sqlite(database)) as connection, connection:
        for table in ("report_fact_revisions", "structure_probe", "idempotency_records"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM audit_events WHERE action='DECLARE'"
        ).fetchone() == (0,)
        assert connection.execute("SELECT status FROM import_batches").fetchone() == ("STAGED",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        connection.execute("DROP TRIGGER injected_status_failure")
    result = application.commit_import(
        preview.batch_id, allowed_coordinates=allowed, before_commit=structure
    )
    assert result["status"] == "COMMITTED"
    application.commit_import(
        preview.batch_id, allowed_coordinates=allowed, before_commit=structure
    )
    with closing(connect_sqlite(database)) as connection:
        assert connection.execute("SELECT quantity FROM report_fact_revisions").fetchall() == [
            ("12",)
        ]
        assert connection.execute("SELECT count(*) FROM structure_probe").fetchone() == (1,)
    stored = SqliteExcelImportRepository(str(database)).get_batch(preview.batch_id)
    assert (
        stored is not None and stored.metadata["profile"] == "test-v1" and stored.skipped_count == 3
    )


def test_blank_keeps_existing_value_but_explicit_clear_creates_revision(
    encrypted_database: Path,
) -> None:
    adapter = WorkbookStub()
    app = service(encrypted_database, adapter)
    source = encrypted_database.parent / "source.xlsx"
    for index, (value, action) in enumerate(
        (
            (ReportCellValue("QUANTITY", "0"), "SET"),
            (ReportCellValue("DATA_NOT_PROVIDED"), "KEEP"),
            (ReportCellValue("DATA_NOT_PROVIDED"), "CLEAR"),
        )
    ):
        adapter.parsed = ParsedWorkbook(
            (ParsedExcelCell("A1", coordinate(), value, {"action": action}),),
            (),
            skipped_count=int(action == "KEEP"),
        )
        source.write_bytes(str(index).encode())
        preview = app.stage_import(
            source, report_type="DAILY_MOVEMENT", organization_id=1, matrix={}
        )
        app.commit_import(preview.batch_id, allowed_coordinates=allowed)
        if action == "KEEP":
            assert preview.skipped_count == 1
            with closing(connect_sqlite(encrypted_database)) as connection:
                assert connection.execute(
                    "SELECT quantity FROM report_fact_revisions"
                ).fetchall() == [("0",)]
    with closing(connect_sqlite(encrypted_database)) as connection:
        assert connection.execute(
            "SELECT value_kind,quantity FROM report_fact_revisions ORDER BY revision"
        ).fetchall() == [("QUANTITY", "0"), ("DATA_NOT_PROVIDED", None)]
        assert json.loads(
            connection.execute(
                "SELECT provenance_json FROM import_rows ORDER BY id DESC"
            ).fetchone()[0]
        ) == {"action": "CLEAR"}


def test_structure_only_import_commits_in_shared_transaction(encrypted_database: Path) -> None:
    adapter = WorkbookStub()
    adapter.parsed = ParsedWorkbook((), (), metadata={"structure_only": True})
    app = service(encrypted_database, adapter)
    source = encrypted_database.parent / "structure.xlsx"
    source.write_bytes(b"structure")
    preview = app.stage_import(source, report_type="DAILY_MOVEMENT", organization_id=1, matrix={})

    def structure(transaction: ReportCellUnitOfWork) -> None:
        assert isinstance(transaction, SqliteReportCellUnitOfWork)
        transaction.connection.execute(
            "UPDATE organizations SET name='Imported structure' WHERE id=1"
        )

    assert (
        app.commit_import(preview.batch_id, allowed_coordinates=allowed, before_commit=structure)[
            "status"
        ]
        == "COMMITTED"
    )
    with closing(connect_sqlite(encrypted_database)) as connection:
        assert connection.execute("SELECT name FROM organizations WHERE id=1").fetchone() == (
            "Imported structure",
        )


def test_failed_key_copy_publishes_no_incomplete_backup(
    encrypted_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    destination = encrypted_database.parent / "fault-backups"
    original = encrypted_database.read_bytes()

    def fail_copy(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected no space for key envelope")

    monkeypatch.setattr(shutil, "copyfile", fail_copy)
    with pytest.raises(OSError, match="no space"):
        backup_database(encrypted_database, destination, "dev33")
    assert list(destination.iterdir()) == []
    assert encrypted_database.read_bytes() == original


def test_complete_backup_restores_to_new_path_and_tampered_keys_are_rejected(
    encrypted_database: Path,
) -> None:
    backups = encrypted_database.parent / "recovery"
    copied = backup_database(encrypted_database, backups, "dev33")
    manifest = verify_backup(copied)
    assert manifest["encrypted"] is True
    assert len(manifest["files"]) == 2
    assert list_backups(backups)[0]["valid"] is True
    restored = restore_backup(copied, encrypted_database.parent / "restored")
    vault = AccessVault(restored, encrypted_database.parent / "unused", device_protector=None)
    vault.unlock("admin", "test-pin")
    with closing(connect_sqlite(restored)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT name FROM organizations WHERE id=1").fetchone() == (
            "Test",
        )
    with pytest.raises(ValueError, match="новую папку"):
        restore_backup(copied, restored.parent)
    copied.with_suffix(".sqlite3.keys.json").write_text("tampered")
    with pytest.raises(ValueError, match="проверка файла"):
        restore_backup(copied, encrypted_database.parent / "rejected")
    assert list_backups(backups)[0]["valid"] is False


def test_import_modes_and_context_identity_do_not_suppress_distinct_imports(
    encrypted_database: Path,
) -> None:
    adapter = WorkbookStub()
    app = service(encrypted_database, adapter)
    source = encrypted_database.parent / "context.xlsx"
    source.write_bytes(b"same source")
    adapter.parsed = ParsedWorkbook(
        (ParsedExcelCell("A1", coordinate(), ReportCellValue("QUANTITY", "10")),), ()
    )
    first = app.stage_import(
        source,
        report_type="DAILY_MOVEMENT",
        organization_id=1,
        matrix={"year": 2026},
        mode="CREATE",
    )
    app.commit_import(first.batch_id, allowed_coordinates=allowed)
    adapter.parsed = ParsedWorkbook(
        (ParsedExcelCell("A1", coordinate(), ReportCellValue("QUANTITY", "20")),), ()
    )
    append = app.stage_import(
        source,
        report_type="DAILY_MOVEMENT",
        organization_id=1,
        matrix={"year": 2026},
        mode="APPEND",
    )
    assert not append.already_imported and append.skipped_count == 1
    app.commit_import(append.batch_id, allowed_coordinates=allowed)
    with closing(connect_sqlite(encrypted_database)) as connection:
        assert connection.execute("SELECT quantity FROM report_fact_revisions").fetchall() == [
            ("10",)
        ]
    update = app.stage_import(
        source,
        report_type="DAILY_MOVEMENT",
        organization_id=1,
        matrix={"year": 2026},
        mode="UPDATE",
    )
    assert not update.already_imported and update.changed_count == 1
    app.commit_import(update.batch_id, allowed_coordinates=allowed)
    duplicate = app.stage_import(
        source,
        report_type="DAILY_MOVEMENT",
        organization_id=1,
        matrix={"year": 2026},
        mode="UPDATE",
    )
    assert duplicate.already_imported
    alternate = app.stage_import(
        source,
        report_type="DAILY_MOVEMENT",
        organization_id=1,
        matrix={"year": 2027},
        mode="UPDATE",
        profile_version="canonical-v3",
    )
    assert not alternate.already_imported
    import hashlib

    assert alternate.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    batch = SqliteExcelImportRepository(str(encrypted_database)).get_batch(alternate.batch_id)
    assert batch is not None and batch.source_sha256 != alternate.source_sha256


def test_recovery_set_preserves_pending_access_lifecycle(encrypted_database: Path) -> None:
    journal = encrypted_database.with_suffix(".sqlite3.lifecycle.json")
    journal.write_text('{"version":1,"operation_id":"pending-test"}', encoding="utf-8")
    backup = backup_database(
        encrypted_database, encrypted_database.parent / "journal-backups", "dev33"
    )
    restored = restore_backup(backup, encrypted_database.parent / "journal-restored")
    assert restored.with_suffix(".sqlite3.lifecycle.json").read_bytes() == journal.read_bytes()
    assert "reporting.sqlite3.lifecycle.json" in verify_backup(backup)["files"]
