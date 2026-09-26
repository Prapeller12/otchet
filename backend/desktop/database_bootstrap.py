"""Backup and migrate the local SQLite database before desktop startup."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.infrastructure.database.encrypted_sqlite import encrypted_backup, has_database_key
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_file(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    # Directory handles cannot be opened this way on Windows. Files themselves
    # are flushed there before the same-volume atomic directory rename.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_backup(database: Path) -> dict[str, Any]:
    """Verify a published recovery set without requiring an unlocked database.

    A version-2 set includes the key envelope when encrypted. Both files are
    hashed; incomplete sets and unsafe manifest filenames are rejected.
    """
    manifest_path = database.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format_version") != 2:
        raise ValueError("Неподдерживаемый или неполный комплект резервной копии")
    if manifest.get("backup_file") != database.name:
        raise ValueError("Имя базы не соответствует манифесту резервной копии")
    files = manifest.get("files")
    if not isinstance(files, dict) or database.name not in files:
        raise ValueError("В резервной копии отсутствует описание базы")
    if manifest.get("encrypted") is True:
        key_name = database.name + ".keys.json"
        if manifest.get("key_file") != key_name or key_name not in files:
            raise ValueError("В резервной копии отсутствует файл доступа")
    for name, checksum in files.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or not isinstance(checksum, str)
            or len(checksum) != 64
        ):
            raise ValueError("Некорректный манифест резервной копии")
        candidate = database.parent / name
        if candidate.is_symlink() or not candidate.is_file() or _sha256(candidate) != checksum:
            raise ValueError(f"Не пройдена проверка файла резервной копии: {name}")
    return manifest


def backup_database(database: Path, backups: Path, app_version: str) -> Path:
    """Publish a complete verified recovery set with one atomic directory rename.

    Until database, key envelope and checksums are durable, no final-named set
    exists. Failure removes the pending set and leaves the live database intact.
    The returned path is the database inside the published directory.
    """
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"snapshot-{stamp}-{uuid.uuid4().hex}"
    pending_directory = backups / f".{stem}.pending"
    destination_directory = backups / stem
    pending_directory.mkdir()
    pending = pending_directory / "reporting.sqlite3"
    encrypted = has_database_key(database)
    vault = database.with_suffix(database.suffix + ".keys.json")
    try:
        if encrypted and not vault.is_file():
            raise ValueError("Нельзя создать полную резервную копию: отсутствует файл доступа")
        if encrypted:
            encrypted_backup(database, pending)
        else:
            source_connection = connect_sqlite(database)
            try:
                target_connection = sqlite3.connect(pending)
                try:
                    source_connection.backup(target_connection)
                    integrity = target_connection.execute("PRAGMA integrity_check").fetchone()
                    if integrity != ("ok",):
                        raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity!r}")
                finally:
                    target_connection.close()
            finally:
                source_connection.close()
            _sync_file(pending)
        files = {pending.name: _sha256(pending)}
        key_file = None
        if vault.exists():
            key_pending = pending.with_suffix(pending.suffix + ".keys.json")
            shutil.copyfile(vault, key_pending)
            _sync_file(key_pending)
            key_file = key_pending.name
            files[key_file] = _sha256(key_pending)
        lifecycle = database.with_suffix(database.suffix + ".lifecycle.json")
        if lifecycle.exists():
            lifecycle_pending = pending.with_suffix(pending.suffix + ".lifecycle.json")
            shutil.copyfile(lifecycle, lifecycle_pending)
            _sync_file(lifecycle_pending)
            files[lifecycle_pending.name] = _sha256(lifecycle_pending)
        manifest = {
            "format_version": 2,
            "application_version": app_version,
            "backup_file": pending.name,
            "created_at": datetime.now(UTC).isoformat(),
            "sha256": files[pending.name],
            "encrypted": encrypted,
            "key_file": key_file,
            "files": files,
        }
        manifest_path = pending.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _sync_file(manifest_path)
        verify_backup(pending)
        _sync_directory(pending_directory)
        os.replace(pending_directory, destination_directory)
        _sync_directory(backups)
    except BaseException:
        shutil.rmtree(pending_directory, ignore_errors=True)
        raise
    return destination_directory / pending.name


def list_backups(backups: Path) -> list[dict[str, Any]]:
    """List complete recovery directories; keep damaged sets visible for diagnosis."""
    results: list[dict[str, Any]] = []
    if not backups.exists():
        return results
    for folder in sorted(backups.glob("snapshot-*"), reverse=True):
        if not folder.is_dir() or folder.is_symlink():
            continue
        database = folder / "reporting.sqlite3"
        try:
            manifest = verify_backup(database)
            results.append({**manifest, "path": str(database), "valid": True})
        except (OSError, ValueError) as error:
            results.append({"path": str(database), "valid": False, "error": str(error)})
    return results


def restore_backup(database: Path, destination_directory: Path) -> Path:
    """Restore a verified pair into a NEW directory, never overwrite live data.

    The caller can open the returned encrypted database with a known personal
    code and run integrity checks before selecting it for normal work. Device
    unlock envelopes are intentionally not restored across computers.
    """
    if destination_directory.exists():
        raise ValueError("Восстановление допускается только в новую папку")
    manifest = verify_backup(database)
    destination_directory.parent.mkdir(parents=True, exist_ok=True)
    pending = destination_directory.with_name(
        f".{destination_directory.name}.{uuid.uuid4().hex}.pending"
    )
    pending.mkdir()
    try:
        for name in [*manifest["files"], database.with_suffix(".manifest.json").name]:
            target = pending / name
            shutil.copyfile(database.parent / name, target)
            _sync_file(target)
        restored = pending / database.name
        verify_backup(restored)
        _sync_directory(pending)
        # Never replace even an empty existing folder on platforms which permit it.
        if destination_directory.exists():
            raise ValueError("Папка восстановления уже существует")
        os.rename(pending, destination_directory)
        _sync_directory(destination_directory.parent)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return destination_directory / database.name


def backup_and_migrate(
    database: Path,
    migrations: Path,
    backups: Path,
    app_version: str,
) -> tuple[Path | None, tuple[str, ...]]:
    """Back up an existing database, then apply sequential migrations."""

    database.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_database(database, backups, app_version) if database.exists() else None
    connection = connect_sqlite(database)
    try:
        applied = apply_migrations(connection, migrations)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise sqlite3.DatabaseError(f"Database integrity check failed: {integrity!r}")
    finally:
        connection.close()
    return backup, applied
