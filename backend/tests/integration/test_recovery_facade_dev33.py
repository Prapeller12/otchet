"""Recovery is available while locked and only writes to a newly created directory."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.desktop.database_bootstrap import backup_database

ROOT = Path(__file__).resolve().parents[3]


def fixture(tmp_path: Path) -> tuple[SecureDesktopBridge, Path, Path]:
    database = tmp_path / "data" / "reporting.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE fact(value INTEGER)")
        connection.execute("INSERT INTO fact VALUES (17)")
    backup = backup_database(database, tmp_path / "backups", "test")
    bridge = SecureDesktopBridge(
        database,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=tmp_path / "backups",
        device_protector=None,
    )
    return bridge, database, backup


def test_locked_recovery_copies_to_new_directory_and_keeps_live_database(tmp_path: Path) -> None:
    bridge, database, backup = fixture(tmp_path)
    before = database.read_bytes()
    assert bridge._application is None
    listed = bridge.list_recovery_backups({})
    assert listed["ok"] and len(listed["data"]["backups"]) == 1
    identity = listed["data"]["backups"][0]["backup_id"]
    assert bridge.verify_recovery_backup({"backup_id": identity})["ok"]
    result = bridge.restore_recovery_backup({"backup_id": identity})
    assert result["ok"] and not result["data"]["cancelled"]
    restored = Path(result["data"]["database_path"])
    assert restored != database and restored.parent.parent == tmp_path
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM fact").fetchone() == (17,)
    assert database.read_bytes() == before
    assert backup.is_file()
    second = bridge.restore_recovery_backup({"backup_id": identity})
    assert second["ok"] and second["data"]["directory"] != result["data"]["directory"]


def test_recovery_rejects_paths_tampering_and_honours_folder_cancel(tmp_path: Path) -> None:
    bridge, database, backup = fixture(tmp_path)
    bridge._configure_recovery_dialog(lambda: None)
    assert bridge.restore_recovery_backup({"backup_id": backup.parent.name}) == {
        "ok": True,
        "data": {"cancelled": True},
    }
    assert not bridge.restore_recovery_backup({"backup_id": "../data"})["ok"]
    assert not bridge.restore_recovery_backup(
        {"backup_id": backup.parent.name, "destination": str(database)}
    )["ok"]
    with backup.open("ab") as stream:
        stream.write(b"damaged")
    listed = bridge.list_recovery_backups({})
    assert listed["ok"] and listed["data"]["backups"][0]["valid"] is False
    assert not bridge.verify_recovery_backup({"backup_id": backup.parent.name})["ok"]
    assert not bridge.restore_recovery_backup({"backup_id": backup.parent.name})["ok"]
    assert not list(tmp_path.glob("recovery-*"))
