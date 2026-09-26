"""Recoverable credential lifecycle; immutable identities keep old signatures valid."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.infrastructure.access_vault import AccessVault
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_signers import (
    SqliteReportSignersRepository,
    access_role,
    load_signer,
    summary,
)
from backend.infrastructure.report_crypto import rewrap_key, unlock_key


def _text(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError("Укажите пользователя и необходимые коды")
    return value


class AccessLifecycleService:
    def __init__(self, database: str | Path, vault: AccessVault) -> None:
        self.database = database
        self.vault = vault

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or request.keys() - {
            "action",
            "signer_id",
            "current_pin",
            "new_pin",
            "authorization",
        }:
            raise ValueError("Некорректный запрос управления доступом")
        action = request.get("action")
        if not isinstance(action, str) or action not in {"change_pin", "revoke", "transfer_admin"}:
            raise ValueError("Неизвестное действие управления доступом")
        auth = request.get("authorization")
        if not isinstance(auth, dict):
            raise ValueError("Подтвердите действие действующим кодом администратора")
        actor_id, actor_pin = _text(auth, "signer_id"), _text(auth, "pin")
        repository = SqliteReportSignersRepository(str(self.database))
        actor = repository.authorize(actor_id, actor_pin, admin_only=True)
        signer_id = _text(request, "signer_id")
        self.vault.recover_lifecycle()
        before = self.vault._read()
        after = {**before, "users": list(before["users"])}
        operation_id = uuid4().hex
        with closing(connect_sqlite(self.database)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                # Recheck current roles inside the transaction; never trust UI/session.
                if access_role(connection, actor_id) != "admin":
                    raise ValueError("Нужен действующий администратор")
                target = load_signer(connection, signer_id)
                role = access_role(connection, signer_id)
                if action == "revoke":
                    if role == "admin":
                        raise ValueError(
                            "Сначала передайте права администратора другому пользователю"
                        )
                    after["users"] = [p for p in after["users"] if p["id"] != signer_id]
                    connection.execute(
                        "UPDATE report_signer_status SET revoked=1 WHERE signer_id=?", (signer_id,)
                    )
                elif action == "change_pin":
                    pin = _text(request, "new_pin")
                    updated = rewrap_key(target, _text(request, "current_pin"), pin)
                    connection.execute(
                        "UPDATE report_signers SET encrypted_private_key=?,salt=?,nonce=? "
                        "WHERE id=?",
                        (
                            updated["encrypted_private_key"],
                            updated["salt"],
                            updated["nonce"],
                            signer_id,
                        ),
                    )
                    if role in {"admin", "reviewer"}:
                        wrapped = self.vault._wrap(summary(target, role), pin)
                        after["users"] = [p for p in after["users"] if p["id"] != signer_id] + [
                            wrapped
                        ]
                else:
                    if signer_id == actor_id:
                        raise ValueError("Выберите другого действующего ответственного")
                    target_pin = _text(request, "current_pin")
                    unlock_key(target, target_pin)
                    # Signing profile.role stays immutable: only application roles change.
                    connection.execute(
                        "UPDATE report_access_roles SET role='reviewer' WHERE signer_id=?",
                        (actor_id,),
                    )
                    connection.execute(
                        "UPDATE report_access_roles SET role='admin' WHERE signer_id=?",
                        (signer_id,),
                    )
                    after["users"] = [
                        p for p in after["users"] if p["id"] not in {actor_id, signer_id}
                    ]
                    after["users"].extend(
                        [
                            self.vault._wrap({**actor, "role": "reviewer"}, actor_pin),
                            self.vault._wrap(summary(target, "admin"), target_pin),
                        ]
                    )
                self.vault.prepare_lifecycle(operation_id, after)
                connection.execute(
                    "INSERT INTO report_access_lifecycle_commits"
                    "(operation_id,action,signer_id,actor_id) "
                    "VALUES (?,?,?,?)",
                    (operation_id, action, signer_id, actor_id),
                )
                connection.execute(
                    "INSERT INTO report_access_events(signer_id,role,command,outcome) "
                    "VALUES (?,?,?,?)",
                    (actor_id, "admin", "access_" + action, "succeeded"),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                # Journal deliberately survives: next operation/start resolves it from
                # the DB commit marker even if disk failure also blocks cleanup now.
                raise
        # A post-commit disk error is an explicit partial result, not an apparent rollback.
        warning = None
        try:
            self.vault.recover_lifecycle()
        except (OSError, ValueError, sqlite3.Error) as error:
            warning = (
                "Изменение доступа сохранено. Завершение записи файла ключей отложено; "
                "сохраните вместе базу, .keys.json и .lifecycle.json. " + str(error)
            )
        enrolled = {entry["id"] for entry in after["users"]}
        return {
            "users": [{**user, "can_unlock": user["id"] in enrolled} for user in repository.list()],
            "administrator_changed": action == "transfer_admin",
            "access_warning": warning,
        }
