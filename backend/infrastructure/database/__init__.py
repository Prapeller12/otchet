"""SQLite persistence infrastructure for the reporting system."""

from .migrator import MigrationError, apply_migrations, connect_sqlite

__all__ = ["MigrationError", "apply_migrations", "connect_sqlite"]
