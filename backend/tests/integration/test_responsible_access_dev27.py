"""Responsible report writes and administrator-session lifecycle at the IPC boundary."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.infrastructure.database.migrator import connect_sqlite

ROOT = Path(__file__).resolve().parents[3]
PIN = "administrator-code-27"


def data(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


def bridge(tmp_path: Path, migrations: Path | None = None) -> SecureDesktopBridge:
    return SecureDesktopBridge(
        tmp_path / "data" / "report.db",
        migrations_directory=migrations or ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=tmp_path / "backups",
    )


def setup(app: SecureDesktopBridge) -> dict[str, str]:
    status = data(app.setup_access({"display_name": "Администратор", "pin": PIN}))
    return {"signer_id": status["current_user"]["id"], "pin": PIN}


def test_account_creation_uses_admin_session_without_retaining_pin(tmp_path: Path) -> None:
    app = bridge(tmp_path)
    admin = setup(app)
    request = {"display_name": "Руководитель", "pin": "manager-code-27", "role": "project_manager"}
    # Opening the database is not the same as opening administrator tools.
    assert not app.create_report_signer(request)["ok"]
    assert not app.create_report_signer({**request, "authorization": admin})["ok"]
    data(app.authenticate_access(admin))
    assert app._administrator is not None and "pin" not in app._administrator
    manager = data(app.create_report_signer(request))
    reviewer = data(
        app.create_report_signer({**request, "display_name": "Проверяющий", "role": "reviewer"})
    )
    assert manager["role"] == "project_manager" and reviewer["role"] == "reviewer"
    assert not app.create_report_signer({**request, "display_name": "Второй", "role": "admin"})[
        "ok"
    ]
    data(app.end_administration({}))
    assert not app.create_report_signer({**request, "display_name": "После выхода"})["ok"]
    data(app.authenticate_access(admin))
    data(app.authenticate_access({"signer_id": manager["id"], "pin": request["pin"]}))
    assert not app.create_report_signer({**request, "display_name": "Чужая сессия"})["ok"]
    data(app.authenticate_access(admin))
    assert not app.authenticate_access({**admin, "pin": "incorrect"})["ok"]
    assert app._administrator is None
    data(app.authenticate_access(admin))
    app._lock()
    data(app.unlock_access(admin))
    assert not app.create_report_signer({**request, "display_name": "После блокировки"})["ok"]


def test_encrypted_dev26_upgrade_preserves_audit_and_backs_up_vault(tmp_path: Path) -> None:
    old = tmp_path / "old-migrations"
    old.mkdir()
    for migration in (ROOT / "backend/migrations").glob("*.sql"):
        if migration.name < "0014":
            shutil.copyfile(migration, old / migration.name)
    app = bridge(tmp_path, old)
    admin = setup(app)
    data(app.authenticate_access(admin))
    data(
        app.create_report_signer(
            {"display_name": "Прежний проверяющий", "pin": PIN, "role": "reviewer"}
        )
    )
    database = tmp_path / "data/report.db"
    with closing(connect_sqlite(database)) as connection:
        previous = connection.execute("SELECT * FROM report_access_events ORDER BY id").fetchall()
        assert previous
    app._lock()
    upgraded = bridge(tmp_path)
    assert not upgraded.unlock_access({**admin, "pin": "wrong"})["ok"]
    assert not list((tmp_path / "backups").glob("*.manifest.json"))
    data(upgraded.unlock_access(admin))
    with closing(connect_sqlite(database)) as connection:
        assert (
            connection.execute("SELECT * FROM report_access_events ORDER BY id").fetchall()
            == previous
        )
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone() == (
            "0014",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM report_access_events")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE report_access_events SET command='tamper'")
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    assert backups[0].with_suffix(".sqlite3.keys.json").is_file()
    assert not backups[0].read_bytes().startswith(b"SQLite format 3")
    data(upgraded.authenticate_access(admin))
    manager = data(
        upgraded.create_report_signer(
            {"display_name": "Новый руководитель", "pin": PIN, "role": "project_manager"}
        )
    )
    assert upgraded._application is not None
    upgraded._application._signers.record_access(manager, "save_report_cells", "authorized")
    with closing(connect_sqlite(database)) as connection:
        assert connection.execute(
            "SELECT role FROM report_access_events ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("project_manager",)
    upgraded._lock()
    before = database.read_bytes()
    data(upgraded.unlock_access(admin))
    assert database.read_bytes() == before
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
