"""Real encrypted files, migration, transactions and encrypted recovery copies."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from backend.desktop.database_bootstrap import backup_database
from backend.infrastructure.database.encrypted_sqlite import (
    configure_database_key,
    create_encrypted_database,
    encrypt_existing_database,
    forget_database_key,
    is_encrypted_database,
)
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite


def test_encrypted_database_unreadable_without_key_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "data.sqlite3"
    key = os.urandom(32)
    create_encrypted_database(path, key)
    with closing(connect_sqlite(path)) as connection, connection:
        apply_migrations(connection)
        connection.execute("CREATE TABLE confidentiality(value TEXT UNIQUE)")
        connection.execute("INSERT INTO confidentiality VALUES ('secret-value-12345')")
    assert is_encrypted_database(path)
    assert b"secret-value-12345" not in path.read_bytes()
    with closing(sqlite3.connect(path)) as standard:
        with pytest.raises(sqlite3.DatabaseError):
            standard.execute("SELECT * FROM confidentiality").fetchall()
    forget_database_key(path)
    with pytest.raises(sqlite3.DatabaseError, match="заблокирована"):
        connect_sqlite(path)
    configure_database_key(path, os.urandom(32))
    with pytest.raises(sqlite3.DatabaseError):
        connect_sqlite(path)
    configure_database_key(path, key)
    with closing(connect_sqlite(path)) as connection:
        assert connection.execute("SELECT * FROM confidentiality").fetchall() == [
            ("secret-value-12345",)
        ]
        assert apply_migrations(connection) == ()


def test_cipher_errors_transactions_and_rollback_keep_sqlite_contract(tmp_path: Path) -> None:
    path = tmp_path / "database"
    create_encrypted_database(path, os.urandom(32))
    with closing(connect_sqlite(path)) as connection:
        connection.execute("CREATE TABLE values_table(value TEXT UNIQUE)")
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute("INSERT INTO values_table VALUES ('same')")
            connection.execute("INSERT INTO values_table VALUES ('same')")
        assert connection.execute("SELECT count(*) FROM values_table").fetchone() == (0,)


def test_legacy_migration_and_backups_keep_data_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = os.fsync

    def require_writable_flush(descriptor: int) -> None:
        # Enforce the Windows flush requirement on Linux too. This writes no data,
        # but rejects a read-only descriptor before the actual durability call.
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.write(descriptor, b"")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", require_writable_flush)
    path = tmp_path / "старая база.sqlite3"
    key = os.urandom(32)
    with closing(connect_sqlite(path)) as connection, connection:
        apply_migrations(connection)
        connection.execute("CREATE TABLE preserved(value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('данные до обновления')")
        history = connection.execute("SELECT * FROM schema_migrations").fetchall()
    vault = path.with_suffix(path.suffix + ".keys.json")
    vault.write_text('{"wrapped":"test-envelope"}', encoding="utf-8")
    converted_backup = encrypt_existing_database(path, key, tmp_path / "backups")
    assert converted_backup is not None
    for candidate in [path, converted_backup]:
        assert is_encrypted_database(candidate)
        configure_database_key(candidate, key)
        with closing(connect_sqlite(candidate)) as connection:
            assert connection.execute("SELECT * FROM schema_migrations").fetchall() == history
            assert connection.execute("SELECT * FROM preserved").fetchone() == (
                "данные до обновления",
            )
    backup = backup_database(path, tmp_path / "backups", "test")
    assert is_encrypted_database(backup)
    assert backup.with_suffix(backup.suffix + ".keys.json").read_bytes() == vault.read_bytes()
    assert converted_backup.with_suffix(converted_backup.suffix + ".keys.json").read_bytes() == (
        vault.read_bytes()
    )
    configure_database_key(backup, key)
    with closing(connect_sqlite(backup)) as connection:
        assert connection.execute("SELECT * FROM preserved").fetchone() == ("данные до обновления",)


def test_cipher_tamper_rejected(tmp_path: Path) -> None:
    path = tmp_path / "database"
    key = os.urandom(32)
    create_encrypted_database(path, key)
    with closing(connect_sqlite(path)) as connection, connection:
        connection.execute("CREATE TABLE sensitive(value TEXT)")
        connection.execute("INSERT INTO sensitive VALUES ('private')")
    content = bytearray(path.read_bytes())
    content[-100] ^= 1
    path.write_bytes(content)
    with closing(connect_sqlite(path)) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("SELECT * FROM sensitive").fetchall()


def test_failed_conversion_keeps_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE preserved(value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('original')")
    original = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated failure before replacement")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated failure"):
        encrypt_existing_database(path, os.urandom(32), tmp_path / "backups")
    assert path.read_bytes() == original
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT * FROM preserved").fetchone() == ("original",)
