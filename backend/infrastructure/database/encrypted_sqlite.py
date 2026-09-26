"""SQLCipher storage primitives; plaintext keys live only in this process.

The desktop access vault supplies a random 256-bit key after credential verification.
SQLCipher retains SQLite's normal durable journal and encrypts its database pages.
No plaintext temporary copy is used for conversion or backup.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlcipher3 import dbapi2 as cipher

_keys: dict[Path, bytes] = {}
_key_lock = threading.RLock()
_SQLITE_HEADER = b"SQLite format 3\x00"


def configure_database_key(path: str | Path, key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("Database key must contain exactly 32 bytes")
    with _key_lock:
        _keys[Path(path).resolve()] = bytes(key)


def forget_database_key(path: str | Path) -> None:
    with _key_lock:
        _keys.pop(Path(path).resolve(), None)


def has_database_key(path: str | Path) -> bool:
    with _key_lock:
        return Path(path).resolve() in _keys


def _database_key(path: str | Path) -> bytes:
    with _key_lock:
        key = _keys.get(Path(path).resolve())
    if key is None:
        raise sqlite3.DatabaseError("База заблокирована. Введите код доступа.")
    return key


def is_encrypted_database(path: str | Path) -> bool:
    candidate = Path(path)
    if not candidate.exists() or candidate.stat().st_size == 0:
        return False
    with candidate.open("rb") as stream:
        return stream.read(16) != _SQLITE_HEADER


def _translate_error(error: Exception) -> sqlite3.Error:
    error_type = getattr(sqlite3, type(error).__name__, sqlite3.DatabaseError)
    return cast(sqlite3.Error, error_type(str(error)))


class _CompatibleObject:
    """Keep repositories' stdlib SQLite exception contract across native drivers."""

    def __init__(self, native: Any) -> None:
        self._native = native

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._native, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            arguments = tuple(
                arg._native if isinstance(arg, _CompatibleObject) else arg for arg in args
            )
            named = {
                key: value._native if isinstance(value, _CompatibleObject) else value
                for key, value in kwargs.items()
            }
            try:
                result = attribute(*arguments, **named)
            except cipher.Error as error:
                raise _translate_error(error) from error
            if isinstance(result, cipher.Cursor):
                return _CompatibleObject(result)
            return result

        return call

    def __iter__(self) -> _CompatibleObject:
        return self

    def __next__(self) -> Any:
        try:
            return next(self._native)
        except cipher.Error as error:
            raise _translate_error(error) from error

    def __enter__(self) -> _CompatibleObject:
        self._native.__enter__()
        return self

    def __exit__(self, *arguments: Any) -> Any:
        try:
            return self._native.__exit__(*arguments)
        except cipher.Error as error:
            raise _translate_error(error) from error


def _connect(path: Path, key: bytes | None) -> sqlite3.Connection:
    native = cipher.connect(str(path))
    try:
        # Disable SQLCipher diagnostic output; never enable query tracing with keys.
        native.execute("PRAGMA cipher_log_level = NONE")
        if key is not None:
            if len(key) != 32:
                raise ValueError("Database key must contain exactly 32 bytes")
            native.execute(f'''PRAGMA key = "x'{key.hex()}'"''')
            if not native.execute("PRAGMA cipher_version").fetchone():
                raise sqlite3.DatabaseError("SQLCipher encryption is unavailable")
            native.execute("PRAGMA cipher_memory_security = ON")
        native.execute("PRAGMA temp_store = MEMORY")
        native.execute("PRAGMA foreign_keys = ON")
        native.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except cipher.Error as error:
        native.close()
        raise _translate_error(error) from error
    except Exception:
        native.close()
        raise
    return cast(sqlite3.Connection, _CompatibleObject(native))


def connect_encrypted(path: str | Path) -> sqlite3.Connection:
    return _connect(Path(path), _database_key(path))


def _verify(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise sqlite3.DatabaseError("Не пройдена проверка целостности базы")
    if connection.execute("PRAGMA cipher_integrity_check").fetchall():
        raise sqlite3.DatabaseError("Не пройдена проверка шифрования базы")


def create_encrypted_database(path: str | Path, key: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.exists() and destination.stat().st_size > 0
    if existing:
        if not is_encrypted_database(destination):
            raise sqlite3.DatabaseError("Существующая база требует защищённого преобразования")
    connection = _connect(destination, key)
    try:
        if not existing:
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
        _verify(connection)
    finally:
        connection.close()
    configure_database_key(destination, key)


def _fsync(path: Path) -> None:
    # Windows FlushFileBuffers requires a writable handle; r+b preserves contents.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def encrypted_backup(source: Path, destination: Path) -> None:
    """Write a consistent encrypted SQLite backup using the same random key."""
    key = _database_key(source)
    source_connection = _connect(source, key)
    target_connection = _connect(destination, key)
    try:
        source_connection.backup(target_connection)
        _verify(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    _fsync(destination)


def encrypt_existing_database(path: str | Path, key: bytes, backups: Path) -> Path | None:
    """Export legacy SQLite into SQLCipher, verify and atomically replace it.

    Caller MUST durably save a password-wrapped key envelope before this operation.
    On failure the original database remains readable. On success the backup is
    encrypted with the same key. Existing historical plaintext backups are untouched.
    """
    database = Path(path)
    if not database.exists() or database.stat().st_size == 0:
        create_encrypted_database(database, key)
        return None
    if is_encrypted_database(database):
        connection = _connect(database, key)
        try:
            _verify(connection)
        finally:
            connection.close()
        configure_database_key(database, key)
        return None
    if len(key) != 32:
        raise ValueError("Database key must contain exactly 32 bytes")
    pending = database.with_name(f".{database.name}.{uuid4().hex}.encrypted-pending")
    source = _connect(database, None)
    try:
        if source.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("Исходная база повреждена; преобразование отменено")
        # A single app instance owns the database throughout conversion. The SQLCipher
        # export copies schema, tables, indexes, views and triggers in a transaction.
        source.execute("ATTACH DATABASE ? AS encrypted KEY ?", (str(pending), f"x'{key.hex()}'"))
        source.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        source.commit()
        source.execute("DETACH DATABASE encrypted")
        checkpoint = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and checkpoint[0] != 0:
            raise sqlite3.DatabaseError("Закройте другие подключения к базе и повторите")
    finally:
        source.close()
    target = _connect(pending, key)
    try:
        _verify(target)
    finally:
        target.close()
    _fsync(pending)
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"pre-encryption-{uuid4().hex}.sqlite3"
    # The verified export itself is a consistent encrypted pre-migration backup.
    with pending.open("rb") as source_stream, backup.open("xb") as backup_stream:
        while chunk := source_stream.read(1024 * 1024):
            backup_stream.write(chunk)
        backup_stream.flush()
        os.fsync(backup_stream.fileno())
    vault = database.with_suffix(database.suffix + ".keys.json")
    if vault.exists():
        backup_vault = backup.with_suffix(backup.suffix + ".keys.json")
        shutil.copyfile(vault, backup_vault)
        _fsync(backup_vault)
    os.replace(pending, database)
    configure_database_key(database, key)
    return backup
