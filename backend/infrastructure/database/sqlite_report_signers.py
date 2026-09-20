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


def access_role(connection: sqlite3.Connection, signer_id: str) -> str:
    row = connection.execute(
        "SELECT role FROM report_access_roles WHERE signer_id=?", (signer_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Профиль доступа не найден. Обратитесь к администратору")
    return str(row[0])


def summary(profile: dict[str, Any], role: str) -> dict[str, Any]:
    result = {k: profile[k] for k in ("id", "display_name", "key_fingerprint", "created_at")}
    return {**result, "role": role}


class SqliteReportSignersRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def list(self) -> list[dict[str, Any]]:
        with closing(connect_sqlite(self.database_path)) as connection:
            ids = connection.execute(
                "SELECT id FROM report_signers ORDER BY display_name"
            ).fetchall()
            return [
                summary(load_signer(connection, row[0]), access_role(connection, row[0]))
                for row in ids
            ]

    def authenticate(self, signer_id: str, pin: str) -> dict[str, Any]:
        """Authenticate any of the three roles for opening/reading the workspace."""
        with closing(connect_sqlite(self.database_path)) as connection:
            profile = load_signer(connection, signer_id)
            unlock_key(profile, pin)
            return summary(profile, access_role(connection, signer_id))

    def authorize(self, signer_id: str, pin: str, *, admin_only: bool = False) -> dict[str, Any]:
        """Freshly validate a write code. No authorization token or PIN is retained."""
        with closing(connect_sqlite(self.database_path)) as connection:
            profile = load_signer(connection, signer_id)
            role = access_role(connection, signer_id)
            if role not in ({"admin"} if admin_only else {"admin", "reviewer"}):
                raise ValueError(
                    "Нужен код администратора"
                    if admin_only
                    else "Для сохранения нужен код проверяющего или администратора"
                )
            unlock_key(profile, pin)
            return summary(profile, role)

    def record_access(self, profile: dict[str, Any], command: str, outcome: str) -> None:
        """Record an authorization attempt independently of the business transaction.

        'authorized' is not a claim that the subsequent business change succeeded.
        Only a freshly validated profile from authorize() should reach this method.
        """
        if outcome not in {"authorized", "succeeded", "failed"} or not 1 <= len(command) <= 80:
            raise ValueError("Некорректная запись журнала доступа")
        with closing(connect_sqlite(self.database_path)) as connection, connection:
            role = access_role(connection, profile["id"])
            if role not in {"admin", "reviewer"}:
                raise ValueError("Запись требует кода проверяющего или администратора")
            connection.execute(
                "INSERT INTO report_access_events(signer_id,role,command,outcome) VALUES (?,?,?,?)",
                (profile["id"], role, command, outcome),
            )

    def create(
        self, name: str, pin: str, admin_id: str, admin_pin: str, *, role: str = "reviewer"
    ) -> dict[str, Any]:
        if role not in {"admin", "reviewer", "project_manager"}:
            raise ValueError("Неизвестная роль пользователя")
        name = name.strip()
        if not 1 <= len(name) <= 120 or any(not c.isprintable() for c in name):
            raise ValueError("Укажите ФИО пользователя, не более 120 символов")
        with closing(connect_sqlite(self.database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            first = connection.execute("SELECT count(*) FROM report_signers").fetchone()[0] == 0
            effective_role = "admin" if first else role
            if not first:
                if role == "admin":
                    raise ValueError("В программе может быть только один администратор")
                admin = load_signer(connection, admin_id)
                if access_role(connection, admin_id) != "admin":
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
            connection.execute(
                "INSERT INTO report_access_roles(signer_id,role) VALUES (?,?)",
                (profile["id"], effective_role),
            )
            return summary(profile, effective_role)
