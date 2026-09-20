"""Migration, unique administrator and freshly checked save authorization."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest

from backend.application.access_policy import classify_permission
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_report_signers import (
    SqliteReportSignersRepository,
    load_signer,
)
from backend.infrastructure.report_crypto import protect_key, unlock_key

ROOT = Path(__file__).resolve().parents[3]
PIN = "test-admin-code-26"


@pytest.fixture
def repository(tmp_path: Path) -> SqliteReportSignersRepository:
    path = tmp_path / "roles.db"
    with closing(connect_sqlite(path)) as connection:
        apply_migrations(connection, ROOT / "backend/migrations")
    return SqliteReportSignersRepository(str(path))


def test_roles_and_fresh_write_authorization(repository: SqliteReportSignersRepository) -> None:
    admin = repository.create("Администратор", PIN, "", "")
    reviewer = repository.create("Проверяющий", PIN, admin["id"], PIN, role="reviewer")
    manager = repository.create("Руководитель", PIN, admin["id"], PIN, role="project_manager")
    reviewer2 = repository.create("Проверяющий 2", PIN, admin["id"], PIN)
    assert [admin["role"], reviewer["role"], manager["role"], reviewer2["role"]] == [
        "admin",
        "reviewer",
        "project_manager",
        "reviewer",
    ]
    assert repository.authorize(reviewer["id"], PIN) == reviewer
    assert repository.authorize(admin["id"], PIN, admin_only=True) == admin
    with pytest.raises(ValueError, match="Неверный PIN"):
        repository.authorize(reviewer["id"], "wrong-code-after-success")
    with pytest.raises(ValueError, match="администратора"):
        repository.authorize(reviewer["id"], PIN, admin_only=True)
    assert repository.authorize(manager["id"], PIN) == manager
    with pytest.raises(ValueError, match="один администратор"):
        repository.create("Второй администратор", PIN, admin["id"], PIN, role="admin")
    with pytest.raises(ValueError, match="администратора"):
        repository.create("Ещё один", PIN, reviewer["id"], PIN)
    assert len(repository.list()) == 4


def test_database_enforces_one_immutable_admin(repository: SqliteReportSignersRepository) -> None:
    admin = repository.create("Администратор", PIN, "", "")
    reviewer = repository.create("Проверяющий", PIN, admin["id"], PIN)
    with closing(connect_sqlite(repository.database_path)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO report_signers SELECT 'second','Второй',role,'other',"
                "key_fingerprint,encrypted_private_key,salt,nonce,created_at "
                "FROM report_signers WHERE id=?",
                (admin["id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE report_access_roles SET role='admin' WHERE signer_id=?", (reviewer["id"],)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM report_access_roles WHERE signer_id=?", (admin["id"],))


def test_legacy_crypto_identity_survives_role_migration(tmp_path: Path) -> None:
    old = tmp_path / "migrations"
    old.mkdir()
    for path in (ROOT / "backend/migrations").glob("*.sql"):
        if path.name < "0013":
            shutil.copyfile(path, old / path.name)
    database = tmp_path / "old.db"
    old_profile = protect_key(
        {
            "id": "legacy-reviewer",
            "display_name": "Проверяющий",
            "role": "signer",
            "created_at": "2026-09-01T00:00:00Z",
        },
        PIN,
    )
    with closing(connect_sqlite(database)) as connection:
        apply_migrations(connection, old)
        connection.execute(
            "INSERT INTO report_signers(id,display_name,role,public_key,key_fingerprint,"
            "encrypted_private_key,salt,nonce,created_at) "
            "VALUES (:id,:display_name,:role,:public_key,:key_fingerprint,"
            ":encrypted_private_key,:salt,:nonce,:created_at)",
            old_profile,
        )
        connection.commit()
        assert apply_migrations(connection, ROOT / "backend/migrations") == (
            "0013",
            "0014",
        )
        preserved = load_signer(connection, "legacy-reviewer")
        assert preserved == old_profile
        assert unlock_key(preserved, PIN).public_key().public_bytes_raw() == (
            unlock_key(old_profile, PIN).public_key().public_bytes_raw()
        )
    assert SqliteReportSignersRepository(str(database)).list()[0]["role"] == "reviewer"


@pytest.mark.parametrize(
    "method,payload,expected",
    [
        (
            "save_report_cells",
            {"changes": [{"coordinate": {"metric_code": "WRK_DAILY_RECEIVED"}}]},
            "responsible",
        ),
        (
            "save_report_cells",
            {"changes": [{"coordinate": {"metric_code": "WRK_DAILY_ASSEMBLY_PLAN_C1"}}]},
            "responsible",
        ),
        ("save_report_presentation", {"actuals": {}}, "responsible"),
        ("save_report_presentation", {"plans": {}, "actuals": {}}, "responsible"),
        ("save_report_presentation", {"header": {}}, "responsible"),
        ("validate_import", {}, "admin"),
        ("commit_import", {}, "admin"),
        ("reference_report", {"action": "transfer"}, "admin"),
        ("reference_report", {"action": "save"}, "admin"),
        ("reference_report", {"action": "get"}, None),
        ("verify_report", {}, "responsible"),
    ],
)
def test_policy_covers_plan_bypass_and_staging(
    method: str, payload: dict[str, Any], expected: str | None
) -> None:
    assert classify_permission(method, payload) == expected


def test_unknown_command_is_denied() -> None:
    with pytest.raises(ValueError):
        classify_permission("save_unknown", {})


def test_authenticated_role_audit_is_append_only(repository: SqliteReportSignersRepository) -> None:
    admin = repository.create("Администратор", PIN, "", "")
    reviewer = repository.create("Проверяющий", PIN, admin["id"], PIN)
    # A caller-provided role label cannot make a reviewer an administrator in the log.
    repository.record_access({**reviewer, "role": "admin"}, "save_report_cells", "authorized")
    with closing(connect_sqlite(repository.database_path)) as connection:
        assert connection.execute(
            "SELECT signer_id,role,command,outcome FROM report_access_events"
        ).fetchall() == [(reviewer["id"], "reviewer", "save_report_cells", "authorized")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM report_access_events")


def test_regular_reads_and_readonly_constructor_do_not_change_database(tmp_path: Path) -> None:
    from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge

    database = tmp_path / "read-only.db"
    kwargs: dict[str, Any] = {
        "migrations_directory": ROOT / "backend/migrations",
        "definitions_directory": ROOT / "resources/report-definitions",
    }
    initialized = WorkingReferenceApplicationBridge(database, **kwargs)
    data = cast(dict[str, Any], initialized.create_organization({"name": "Завод"})["data"])
    organization_id = data["organization"]["id"]
    before = database.read_bytes()
    reader = WorkingReferenceApplicationBridge(database, **kwargs, initialize_workspace=False)
    for report_type in ("DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"):
        for organization in ("1", organization_id):
            query = {"report_type": report_type, "organization_id": organization}
            assert reader.get_report_matrix(query)["ok"]
            assert reader.get_report_layout(query)["ok"]
            assert reader.get_report_verification({**query, "year": 2026, "month": 9})["ok"]
    assert database.read_bytes() == before
