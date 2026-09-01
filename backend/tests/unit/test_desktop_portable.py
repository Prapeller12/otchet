from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.desktop.database_bootstrap import backup_and_migrate
from backend.desktop.instance_lock import AlreadyRunningError, SingleInstanceLock
from backend.desktop.paths import PortableLayoutError, PortablePaths


def _write_config(root: Path, *, network: bool = False) -> None:
    config = root / "config"
    config.mkdir(parents=True)
    (config / "app.defaults.toml").write_text(
        "\n".join(
            (
                "[app]",
                "telemetry_enabled = false",
                f"external_network_access_enabled = {str(network).lower()}",
                "[database]",
                'path = "data/reporting.sqlite3"',
                "[logging]",
                "technical_file_logs_enabled = false",
            )
        ),
        encoding="utf-8",
    )


def test_portable_paths_distinguish_release_and_writable_data(tmp_path: Path) -> None:
    _write_config(tmp_path)
    paths = PortablePaths.discover(tmp_path)
    paths.prepare_writable_directories()

    assert paths.database == (tmp_path / "data" / "reporting.sqlite3").resolve()
    assert paths.frontend == tmp_path / "app" / "frontend"
    assert (tmp_path / "imports" / "inbox").is_dir()
    assert not tuple((tmp_path / "temp").glob(".write-probe-*"))


def test_portable_paths_reject_external_network_override(tmp_path: Path) -> None:
    _write_config(tmp_path, network=True)

    with pytest.raises(PortableLayoutError, match="External network"):
        PortablePaths.discover(tmp_path).validate_offline_defaults()


def test_single_instance_lock_rejects_second_owner(tmp_path: Path) -> None:
    marker = tmp_path / "data" / "app.lock"
    first = SingleInstanceLock(tmp_path, marker)
    second = SingleInstanceLock(tmp_path, marker)
    first.acquire()
    try:
        assert json.loads(marker.read_text(encoding="utf-8"))["pid"] > 0
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()
    assert not marker.exists()


def test_existing_database_is_backed_up_before_migration(tmp_path: Path) -> None:
    database = tmp_path / "data" / "reporting.sqlite3"
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    backup, initially_applied = backup_and_migrate(
        database, migrations, tmp_path / "backups", "test-version"
    )
    assert backup is None
    assert initially_applied

    backup, reapplied = backup_and_migrate(
        database, migrations, tmp_path / "backups", "test-version"
    )
    assert backup is not None and backup.is_file()
    assert backup.with_suffix(".manifest.json").is_file()
    assert reapplied == ()
