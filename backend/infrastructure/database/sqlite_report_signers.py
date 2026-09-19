"""Local key profiles. No secrets are returned to the frontend."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.report_crypto import protect_key, unlock_key


def load_signer(connection: sqlite3.Connection, signer_id: str) -> dict[str, Any]:
    cursor = connection.execute("SELECT * FROM report_signers WHERE id=?", (signer_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("Выберите существующего пользователя")
    return dict(zip((c[0] for c in cursor.description), row, strict=True))


def summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {k: profile[k] for k in ("id", "display_name", "role", "key_fingerprint", "created_at")}


class SqliteReportSignersRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def list(self) -> list[dict[str, Any]]:
        with closing(connect_sqlite(self.database_path)) as connection:
            ids = connection.execute(
                "SELECT id FROM report_signers ORDER BY display_name"
            ).fetchall()
            return [summary(load_signer(connection, row[0])) for row in ids]

    def create(self, name: str, pin: str, admin_id: str, admin_pin: str) -> dict[str, Any]:
        name = name.strip()
        if not 1 <= len(name) <= 120 or any(not c.isprintable() for c in name):
            raise ValueError("Укажите ФИО пользователя, не более 120 символов")
        with closing(connect_sqlite(self.database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            first = connection.execute("SELECT count(*) FROM report_signers").fetchone()[0] == 0
            if not first:
                admin = load_signer(connection, admin_id)
                if admin["role"] != "admin":
                    raise ValueError("Создание профиля требует PIN администратора ключей")
                unlock_key(admin, admin_pin)
            profile = protect_key(
                {
                    "id": uuid4().hex,
                    "display_name": name,
                    "role": "admin" if first else "signer",
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                pin,
            )
            if connection.execute(
                "SELECT 1 FROM report_signers WHERE display_name=?", (name,)
            ).fetchone():
                raise ValueError("Пользователь с таким ФИО уже существует")
            connection.execute(
                "INSERT INTO report_signers(id,display_name,role,public_key,key_fingerprint,"
                "encrypted_private_key,salt,nonce,created_at) "
                "VALUES (:id,:display_name,:role,:public_key,:key_fingerprint,"
                ":encrypted_private_key,:salt,:nonce,:created_at)",
                profile,
            )
            return summary(profile)
