"""Explicit desktop security boundary. No database is opened before access setup/unlock."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from backend.api.report_confirmation import (
    confirm_print_report,
    confirm_saved_report,
    prepare_save_confirmation,
)
from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.access_policy import classify_permission
from backend.application.report_cells import ReportCellError
from backend.desktop.database_bootstrap import backup_and_migrate
from backend.infrastructure.access_vault import AccessVault
from backend.infrastructure.database.migrator import MigrationError, connect_sqlite
from backend.infrastructure.database.sqlite_report_signers import (
    SqliteReportSignersRepository,
    load_signer,
)
from backend.infrastructure.report_crypto import unlock_key


def _success(data: object) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _failure(error: Exception) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "ACCESS_DENIED", "message": str(error)}}


def _request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Некорректный запрос")
    return dict(payload)


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("Выберите пользователя и введите код")
    return value


class SecureDesktopBridge:
    def __init__(
        self,
        database_path: str | Path,
        *,
        migrations_directory: str | Path,
        definitions_directory: str | Path,
        inbox_directory: str | Path | None = None,
        backups_directory: str | Path | None = None,
        application_version: str = "development",
    ) -> None:
        self._database = Path(database_path)
        self._kwargs: dict[str, Any] = {
            "migrations_directory": migrations_directory,
            "definitions_directory": definitions_directory,
            "inbox_directory": inbox_directory,
            "backups_directory": backups_directory,
            "application_version": application_version,
        }
        backups = Path(backups_directory or self._database.parent.parent / "backups")
        self._vault = AccessVault(self._database, backups)
        self._application: WorkingReferenceApplicationBridge | None = None
        self._current_user: dict[str, Any] | None = None
        self._administrator: dict[str, Any] | None = None
        self._mutex = threading.RLock()
        self._dialogs: dict[str, Any] = {}

    def _legacy_users(self) -> list[dict[str, Any]]:
        if not self._database.exists():
            return []
        with closing(
            sqlite3.connect(self._database.resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_signers'"
            ).fetchone()
            if not exists:
                return []
            return [
                {"id": row[0], "display_name": row[1], "role": "admin"}
                for row in conn.execute(
                    "SELECT id,display_name FROM report_signers WHERE role='admin'"
                ).fetchall()
            ]

    def _status(self) -> dict[str, Any]:
        if self._application is not None:
            users = self._application._signers.list()
            enrolled = {p["id"] for p in self._vault.users()}
            return {
                "state": "ready",
                "current_user": self._current_user,
                "users": [{**p, "can_unlock": p["id"] in enrolled} for p in users],
            }
        if self._vault.path.exists():
            users = self._vault.users()
            state = "locked"
            if self._vault.pending_setup():
                state = "setup" if any(p["id"] == "setup-admin" for p in users) else "legacy"
            return {"state": state, "users": [] if state == "setup" else users}
        return {
            "state": "legacy" if self._database.exists() else "setup",
            "users": self._legacy_users(),
        }

    def get_access_status(self, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            try:
                return _success(self._status())
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    def _open(self, *, initialize: bool) -> None:
        self._application = WorkingReferenceApplicationBridge(
            self._database,
            **self._kwargs,
            initialize_workspace=initialize,
        )
        if "pdf" in self._dialogs:
            self._application.configure_pdf_dialog(self._dialogs["pdf"])
        if "excel" in self._dialogs:
            self._application.configure_excel_dialogs(**self._dialogs["excel"])

    def setup_access(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            if self._application is not None:
                return _failure(ValueError("Администратор уже создан"))
            try:
                request = _request(payload)
                pin = _text(request, "pin")
                if self._vault.path.exists():
                    if not self._vault.pending_setup():
                        raise ValueError("Доступ уже настроен. Войдите своим кодом")
                    users = self._vault.users()
                    signer_id = (
                        "setup-admin"
                        if any(p["id"] == "setup-admin" for p in users)
                        else _text(request, "signer_id")
                    )
                    profile = self._vault.unlock(signer_id, pin)
                else:
                    admins = self._legacy_users()
                    if admins:
                        signer_id = _text(request, "signer_id")
                        if len(admins) != 1 or signer_id != admins[0]["id"]:
                            raise ValueError("Для переноса базы нужен код её администратора")
                        with closing(sqlite3.connect(self._database)) as conn:
                            unlock_key(load_signer(conn, signer_id), pin)
                        profile = admins[0]
                    else:
                        name = _text(request, "display_name").strip()
                        if not 1 <= len(name) <= 120 or not all(c.isprintable() for c in name):
                            raise ValueError("Укажите ФИО администратора, не более 120 символов")
                        profile = {"id": "setup-admin", "display_name": name, "role": "admin"}
                    self._vault.initialize(profile, pin)
                self._open(initialize=True)
                assert self._application is not None
                repo = self._application._signers
                existing = repo.list()
                if not existing:
                    profile = repo.create(profile["display_name"], pin, "", "")
                else:
                    admin = next(p for p in existing if p["role"] == "admin")
                    profile = repo.authorize(admin["id"], pin, admin_only=True)
                self._vault.enroll(profile, pin)
                self._vault.finish_setup()
                self._current_user = profile
                return _success(self._status())
            except (OSError, ValueError, KeyError, StopIteration, sqlite3.Error) as error:
                self._application = None
                self._current_user = None
                self._vault.lock()
                return _failure(error)

    def unlock_access(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            if self._application is not None:
                return _failure(ValueError("База уже открыта"))
            try:
                request = _request(payload)
                if self._vault.pending_setup():
                    raise ValueError("Сначала завершите настройку базы кодом администратора")
                signer_id, pin = _text(request, "signer_id"), _text(request, "pin")
                self._vault.unlock(signer_id, pin)
                repo = SqliteReportSignersRepository(str(self._database))
                profile = repo.authenticate(signer_id, pin)
                self._migrate_after_unlock()
                self._open(initialize=False)
                self._current_user = profile
                return _success(self._status())
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                self._application = None
                self._current_user = None
                self._vault.lock()
                return _failure(error)

    def _migrate_after_unlock(self) -> None:
        """Upgrade only after a valid vault code, preserving ordinary read-only reopen."""
        migrations = Path(self._kwargs["migrations_directory"])
        available = tuple(path.name.split("_", 1)[0] for path in sorted(migrations.glob("*.sql")))
        with closing(connect_sqlite(self._database)) as connection:
            applied = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
        if applied != available:
            backup_and_migrate(
                self._database,
                migrations,
                Path(self._kwargs["backups_directory"] or self._database.parent.parent / "backups"),
                self._kwargs["application_version"],
            )

    def authenticate_access(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            self._administrator = None
            try:
                if self._application is None:
                    raise ValueError("Сначала войдите в программу")
                request = _request(payload)
                profile = self._application._signers.authenticate(
                    _text(request, "signer_id"), _text(request, "pin")
                )
                if profile["role"] == "admin":
                    self._administrator = profile
                return _success(profile)
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    def end_administration(self, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            self._administrator = None
            return _success({"ended": True})

    def _lock(self) -> None:
        with self._mutex:
            self._application = None
            self._current_user = None
            self._administrator = None
            self._vault.lock()

    def enroll_access(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            try:
                if self._application is None:
                    raise ValueError("Сначала войдите в программу")
                request = _request(payload)
                auth = _request(request.get("authorization"))
                repo = self._application._signers
                admin = repo.authorize(
                    _text(auth, "signer_id"), _text(auth, "pin"), admin_only=True
                )
                profile = repo.authenticate(_text(request, "signer_id"), _text(request, "pin"))
                if profile["role"] not in {"admin", "reviewer"}:
                    raise ValueError(
                        "Руководитель проекта заполняет уже открытую ответственным базу"
                    )
                repo.record_access(admin, "enroll_access", "authorized")
                self._vault.enroll(profile, request["pin"])
                return _success(profile)
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    def _call(self, method: str, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            try:
                if self._application is None:
                    raise ValueError("Сначала войдите в программу своим кодом")
                request = _request(payload) if payload is not None else {}
                authorization = request.pop("authorization", None)
                permission = classify_permission(method, request)
                approver = None
                auth: dict[str, Any] = {}
                if permission is not None:
                    auth = _request(authorization)
                    approver = self._application._signers.authorize(
                        _text(auth, "signer_id"),
                        _text(auth, "pin"),
                        admin_only=permission == "admin",
                    )
                    self._application._signers.record_access(approver, method, "authorized")
                context = None
                print_verification = None
                if method in {"save_report_cells", "save_report_presentation"}:
                    request, context = prepare_save_confirmation(self._application, method, request)
                if method == "export_pdf":
                    print_verification = confirm_print_report(self._application, request, auth)
                function = getattr(self._application, method)
                result: dict[str, Any] = (
                    function() if method in {"health", "bootstrap"} else function(request)
                )
                if method in {"save_report_cells", "save_report_presentation"}:
                    result = confirm_saved_report(self._application, result, context, auth)
                if result.get("ok") and print_verification is not None:
                    result["data"]["verification"] = print_verification
                if result.get("ok") and method == "list_report_signers":
                    enrolled = {p["id"] for p in self._vault.users()}
                    result["data"] = [
                        {**p, "can_unlock": p["id"] in enrolled} for p in result["data"]
                    ]
                return result
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    def _configure_excel_dialogs(self, **dialogs: Any) -> None:
        self._dialogs["excel"] = dialogs

    def _configure_pdf_dialog(self, save_file: Any) -> None:
        self._dialogs["pdf"] = save_file

    def health(self) -> dict[str, Any]:
        return _success({"status": "ok", "access": self._status()["state"]})

    def bootstrap(self) -> dict[str, Any]:
        return self._call("bootstrap")

    def get_report_matrix(self, payload: object) -> dict[str, Any]:
        return self._call("get_report_matrix", payload)

    def save_report_cells(self, payload: object) -> dict[str, Any]:
        return self._call("save_report_cells", payload)

    def validate_import(self, payload: object) -> dict[str, Any]:
        return self._call("validate_import", payload)

    def commit_import(self, payload: object) -> dict[str, Any]:
        return self._call("commit_import", payload)

    def export_report(self, payload: object) -> dict[str, Any]:
        return self._call("export_report", payload)

    def reference_report(self, payload: object) -> dict[str, Any]:
        return self._call("reference_report", payload)

    def list_organizations(self, payload: object) -> dict[str, Any]:
        return self._call("list_organizations", payload)

    def create_organization(self, payload: object) -> dict[str, Any]:
        return self._call("create_organization", payload)

    def rename_organization(self, payload: object) -> dict[str, Any]:
        return self._call("rename_organization", payload)

    def archive_organization(self, payload: object) -> dict[str, Any]:
        return self._call("archive_organization", payload)

    def get_report_layout(self, payload: object) -> dict[str, Any]:
        return self._call("get_report_layout", payload)

    def save_report_layout(self, payload: object) -> dict[str, Any]:
        return self._call("save_report_layout", payload)

    def save_report_presentation(self, payload: object) -> dict[str, Any]:
        return self._call("save_report_presentation", payload)

    def list_report_signers(self, payload: object) -> dict[str, Any]:
        return self._call("list_report_signers", payload)

    def create_report_signer(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            try:
                if self._application is None or self._administrator is None:
                    raise ValueError("Сначала откройте раздел администратора своим кодом")
                request = _request(payload)
                if request.keys() - {"display_name", "pin", "role"}:
                    raise ValueError("Некорректный запрос создания ответственного лица")
                repo = self._application._signers
                repo.record_access(self._administrator, "create_report_signer", "authorized")
                profile = repo.create_in_session(
                    _text(request, "display_name"),
                    _text(request, "pin"),
                    self._administrator["id"],
                    role=_text(request, "role"),
                )
                if profile["role"] in {"admin", "reviewer"}:
                    self._vault.enroll(profile, request["pin"])
                return _success(profile)
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    def get_report_verification(self, payload: object) -> dict[str, Any]:
        return self._call("get_report_verification", payload)

    def verify_report(self, payload: object) -> dict[str, Any]:
        return self._call("verify_report", payload)

    def export_pdf(self, payload: object) -> dict[str, Any]:
        return self._call("export_pdf", payload)
