"""Backup and migrate the local SQLite database before desktop startup."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(database: Path, backups: Path, app_version: str) -> Path:
    """Create a consistent SQLite backup plus a small business recovery manifest."""

    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"pre-migration-{stamp}-{uuid.uuid4().hex[:8]}"
    pending = backups / f".{stem}.pending"
    destination = backups / f"{stem}.sqlite3"

    source_connection = connect_sqlite(database)
    target_connection = sqlite3.connect(pending)
    try:
        source_connection.backup(target_connection)
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity!r}")
    finally:
        target_connection.close()
        source_connection.close()
    os.replace(pending, destination)

    manifest = {
        "application_version": app_version,
        "backup_file": destination.name,
        "created_at": datetime.now(UTC).isoformat(),
        "sha256": _sha256(destination),
    }
    destination.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


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
