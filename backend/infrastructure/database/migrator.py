"""Sequential, dependency-free SQLite migration runner.

The runner deliberately leaves SQLite's durable journal mode unchanged.  It enables
foreign keys for every connection and applies each migration in one explicit
transaction together with its schema-version record.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_MIGRATION_FILE = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
_DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"


class MigrationError(RuntimeError):
    """Raised when the migration history or a migration file is invalid."""


@dataclass(frozen=True, slots=True)
class Migration:
    """An immutable migration discovered on disk."""

    version: str
    path: Path
    sql: str
    checksum: str


def connect_sqlite(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with mandatory referential-integrity checks."""

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    if enabled != (1,):
        connection.close()
        raise MigrationError("SQLite foreign-key enforcement could not be enabled")
    return connection


def _discover_migrations(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"Migration directory does not exist: {directory}")

    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_FILE.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration file name: {path.name}")
        version = match.group("version")
        if version in versions:
            raise MigrationError(f"Duplicate migration version: {version}")
        versions.add(version)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return migrations


def _iter_statements(sql: str) -> Iterable[str]:
    """Split a script without breaking trigger bodies containing semicolons."""

    buffer: list[str] = []
    for line in sql.splitlines(keepends=True):
        buffer.append(line)
        statement = "".join(buffer)
        if sqlite3.complete_statement(statement):
            if statement.strip():
                yield statement
            buffer.clear()

    remainder = "".join(buffer)
    if remainder.strip():
        raise MigrationError("Migration contains an incomplete SQL statement")


def _ensure_history_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL
                CHECK (
                    length(checksum) = 64
                    AND checksum NOT GLOB '*[^0-9a-f]*'
                ),
            applied_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ) STRICT
        """
    )
    connection.commit()


def apply_migrations(
    connection: sqlite3.Connection,
    migrations_directory: str | Path = _DEFAULT_MIGRATIONS_DIRECTORY,
) -> tuple[str, ...]:
    """Validate migration history and atomically apply all pending migrations."""

    if connection.in_transaction:
        raise MigrationError("Migrations require a connection without an active transaction")

    connection.execute("PRAGMA foreign_keys = ON")
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    if enabled != (1,):
        raise MigrationError("SQLite foreign-key enforcement must be enabled")

    migrations = _discover_migrations(Path(migrations_directory))
    _ensure_history_table(connection)

    applied_rows = connection.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    applied_versions = tuple(str(version) for version, _ in applied_rows)
    applied = {str(version): str(checksum) for version, checksum in applied_rows}
    discovered = {migration.version: migration for migration in migrations}
    discovered_versions = tuple(migration.version for migration in migrations)

    unknown_versions = sorted(set(applied) - set(discovered))
    if unknown_versions:
        joined = ", ".join(unknown_versions)
        raise MigrationError(f"Database contains unknown migration versions: {joined}")

    expected_prefix = discovered_versions[: len(applied_versions)]
    if applied_versions != expected_prefix:
        expected = ", ".join(expected_prefix) or "<empty>"
        actual = ", ".join(applied_versions) or "<empty>"
        raise MigrationError(
            "Applied migration history is not a contiguous prefix "
            f"(expected: {expected}; found: {actual})"
        )

    for version, checksum in applied.items():
        if discovered[version].checksum != checksum:
            raise MigrationError(f"Applied migration {version} has been modified")

    newly_applied: list[str] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _iter_statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (?, ?)",
                (migration.version, migration.checksum),
            )
            connection.commit()
        except (MigrationError, sqlite3.Error):
            connection.rollback()
            raise
        newly_applied.append(migration.version)

    return tuple(newly_applied)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply reporting-system SQLite migrations")
    parser.add_argument("database", type=Path, help="Path to the SQLite database")
    parser.add_argument(
        "--migrations",
        type=Path,
        default=_DEFAULT_MIGRATIONS_DIRECTORY,
        help="Directory containing sequential SQL migrations",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by development and the portable launcher."""

    arguments = _build_parser().parse_args(argv)
    connection = connect_sqlite(arguments.database)
    try:
        apply_migrations(connection, arguments.migrations)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
