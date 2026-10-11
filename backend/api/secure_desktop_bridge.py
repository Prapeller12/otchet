"""Desktop boundary: automatic Windows reading, fresh personal codes for writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from backend.api.report_confirmation import (
    confirm_print_report,
    confirm_saved_report,
    prepare_save_confirmation,
)
from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.access_lifecycle import AccessLifecycleService
from backend.application.access_policy import classify_permission
from backend.application.report_cells import ReportCellError
from backend.desktop.database_bootstrap import (
    backup_and_migrate,
    backup_database,
    list_backups,
    restore_backup,
    verify_backup,
)
from backend.infrastructure.access_vault import AccessVault
from backend.infrastructure.database.encrypted_sqlite import (
    configure_database_key,
    forget_database_key,
    has_database_key,
    is_encrypted_database,
)
from backend.infrastructure.database.migrator import MigrationError, connect_sqlite
from backend.infrastructure.database.sqlite_report_signers import (
    SqliteReportSignersRepository,
    access_role,
    load_signer,
)
from backend.infrastructure.network_snapshots import NetworkSnapshotError, SnapshotCoordinator
from backend.infrastructure.report_crypto import decode, unlock_key
from backend.infrastructure.ui_preferences import UiPreferences
from backend.infrastructure.windows_data_protection import DeviceProtector


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


def _network_boundary(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(function)
    def wrapped(self: SecureDesktopBridge, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            if self._snapshot_coordinator is None or self._network_running:
                return function(self, payload)
            return self._network_execute(
                function.__name__, payload, lambda request: function(self, request)
            )

    return wrapped


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
        preferences_path: str | Path | None = None,
        device_protector: DeviceProtector | Literal["windows"] | None = "windows",
        snapshot_coordinator: SnapshotCoordinator | None = None,
    ) -> None:
        self._ui_preferences = UiPreferences(preferences_path)
        self._database = Path(database_path)
        self._kwargs: dict[str, Any] = {
            "migrations_directory": migrations_directory,
            "definitions_directory": definitions_directory,
            "inbox_directory": inbox_directory,
            "backups_directory": backups_directory,
            "application_version": application_version,
        }
        backups = Path(backups_directory or self._database.parent.parent / "backups")
        self._vault = AccessVault(self._database, backups, device_protector=device_protector)
        self._application: WorkingReferenceApplicationBridge | None = None
        self._current_user: dict[str, Any] | None = None
        self._administrator: dict[str, Any] | None = None
        self._mutex = threading.RLock()
        self._dialogs: dict[str, Any] = {}
        self._automatic_open_attempted = False
        self._automatic_open_error: str | None = None
        self._snapshot_coordinator = snapshot_coordinator
        self._network_running = False
        self._network_access_maintenance = False
        self._network_recovery_key: bytes | None = None
        if snapshot_coordinator is not None:
            if snapshot_coordinator.database_path.resolve() != self._database.resolve():
                raise ValueError("Координатор относится к другой локальной базе")
            if not snapshot_coordinator.prepared:
                snapshot_coordinator.prepare()

    def _network_envelope(self, result: dict[str, Any], method: str = "") -> dict[str, Any]:
        coordinator = self._snapshot_coordinator
        if coordinator is not None:
            result["network"] = coordinator.status()
            if method == "get_report_matrix" and result.get("ok"):
                result["data"]["global_revision"] = coordinator.loaded_global_revision
        return result

    def _network_authorize(self, database: Path, method: str, request: dict[str, Any]) -> str:
        if method == "get_access_status":
            return str(request.get("signer_id", "system"))
        if method == "unlock_access" and database == self._database:
            # Lifecycle recovery belongs inside the checkpointed operation. Only
            # read-only authoritative receipt replay is pre-authenticated here.
            return _text(request, "signer_id")
        if method == "import_local_store":
            auth = _request(request.get("authorization"))
            pin = _text(auth, "pin")
            if not database.exists():
                return str(auth.get("signer_id", "import-admin"))
            vault = AccessVault(database, self._vault.backups, device_protector=None)
            admins = [p for p in vault.users() if p["role"] == "admin"]
            if len(admins) != 1 or (auth.get("signer_id") and auth["signer_id"] != admins[0]["id"]):
                raise ValueError("Нужен действующий администратор")
            import_admin_id = str(admins[0]["id"])
            if not has_database_key(database):
                vault.unlock(import_admin_id, pin)
            self._check_network_schema(database, allow_pending=True)
            return str(
                SqliteReportSignersRepository(str(database)).authorize(
                    import_admin_id, pin, admin_only=True
                )["id"]
            )
        if method == "setup_access":
            if not database.exists():
                return "setup-admin"
            vault = AccessVault(database, self._vault.backups, device_protector=None)
            if vault.pending_setup():
                return str(request.get("signer_id", "setup-admin"))
        if method == "create_report_signer":
            if self._administrator is None:
                raise ValueError("Сначала откройте раздел администратора своим кодом")
            self._check_network_schema(database)
            with closing(connect_sqlite(database)) as connection:
                if access_role(connection, self._administrator["id"]) != "admin":
                    raise ValueError("Нужен действующий администратор")
            return str(self._administrator["id"])
        auth = (
            request
            if method in {"setup_access", "authenticate_access", "unlock_access"}
            else _request(request.get("authorization"))
        )
        signer_id = auth.get("signer_id")
        coordinator = self._snapshot_coordinator
        if (
            method == "get_network_operation_result"
            and not signer_id
            and coordinator is not None
            and coordinator.pending_operation_id == request.get("operation_id")
            and coordinator.pending_command in {"setup_access", "import_local_store"}
        ):
            admins = [
                p
                for p in AccessVault(database, self._vault.backups, device_protector=None).users()
                if p["role"] == "admin"
            ]
            if len(admins) != 1:
                raise ValueError("Не удалось определить действующего администратора")
            signer_id = admins[0]["id"]
        if method == "setup_access" and not signer_id:
            # The first setup request predates its permanent signing identity.
            users = AccessVault(database, self._vault.backups, device_protector=None).users()
            signer_id = next((p["id"] for p in users if p["role"] == "admin"), "setup-admin")
        pin = _text(auth, "pin")
        if not has_database_key(database):
            temporary_vault = AccessVault(database, self._vault.backups, device_protector=None)
            if method == "get_network_operation_result" and not any(
                p["id"] == signer_id for p in temporary_vault.users()
            ):
                raise NetworkSnapshotError(
                    "NETWORK_RECOVERY_UNLOCK_REQUIRED",
                    "Сначала откройте проверенную копию кодом администратора или проверяющего, "
                    "затем проверьте результат кодом автора.",
                )
            temporary_vault.unlock(str(signer_id), pin)
        self._check_network_schema(
            database,
            allow_pending=method
            in {"setup_access", "unlock_access", "get_network_operation_result"},
        )
        repository = SqliteReportSignersRepository(str(database))
        if method in {"setup_access", "authenticate_access", "unlock_access"}:
            profile = repository.authenticate(str(signer_id), pin)
        else:
            admin_only = method in {"enroll_access", "manage_report_signer"}
            if method not in {"get_network_operation_result", "get_report_draft_save_result"}:
                admin_only = admin_only or classify_permission(method, request) == "admin"
            profile = repository.authorize(str(signer_id), pin, admin_only=admin_only)
        return str(profile["id"])

    def _check_network_schema(self, database: Path, *, allow_pending: bool = False) -> None:
        migrations = {
            p.name[:4]: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for p in sorted(Path(self._kwargs["migrations_directory"]).glob("*.sql"))
        }
        with closing(connect_sqlite(database)) as connection:
            rows = connection.execute(
                "SELECT version,checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        expected = list(migrations)
        if (
            [r[0] for r in rows] != expected[: len(rows)]
            or any(migrations.get(version) != checksum for version, checksum in rows)
            or (not allow_pending and len(rows) != len(migrations))
        ):
            raise NetworkSnapshotError(
                "NETWORK_CLIENT_UPGRADE_REQUIRED",
                "Версия клиента несовместима с общими данными. Откройте актуальную сборку.",
            )

    def _network_read_key(self) -> bytes | None:
        key = self._vault._key or self._network_recovery_key
        if key is not None:
            return key
        path, protector = self._vault.device_path, self._vault._device_protector
        if protector is None or not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > 131072:
            raise ValueError("Повреждён файл автоматического открытия")
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or document.get("protection") != "windows-dpapi-current-user"
            or not isinstance(document.get("key"), str)
        ):
            raise ValueError("Повреждён файл автоматического открытия")
        key = protector.unprotect(decode(document["key"]))
        if len(key) != 32:
            raise ValueError("Некорректный ключ автоматического открытия")
        return key

    def get_network_recovery_users(self, payload: object = None) -> dict[str, Any]:
        coordinator = self._snapshot_coordinator
        if coordinator is None:
            return _success({"users": []})
        with self._mutex:
            try:
                if payload not in (None, {}):
                    raise ValueError("Некорректный запрос")
                return self._network_envelope(_success({"users": coordinator.recovery_users()}))
            except NetworkSnapshotError as error:
                return self._network_envelope(
                    {"ok": False, "error": {"code": error.code, "message": str(error)}}
                )
            except (OSError, ValueError, KeyError) as error:
                return self._network_envelope(_failure(error))

    def unlock_network_recovery(self, payload: object) -> dict[str, Any]:
        coordinator = self._snapshot_coordinator
        if coordinator is None:
            return _failure(ValueError("Сетевое восстановление недоступно"))
        with self._mutex:
            try:
                request = _request(payload)
                auth = _request(request.get("authorization"))
                signer_id, pin = _text(auth, "signer_id"), _text(auth, "pin")

                def open_copy(database: Path) -> bytes:
                    vault = AccessVault(database, self._vault.backups, device_protector=None)
                    vault.unlock(signer_id, pin)
                    profile = SqliteReportSignersRepository(str(database)).authenticate(
                        signer_id, pin
                    )
                    if profile["role"] not in {"admin", "reviewer"}:
                        raise ValueError("Базу открывает проверяющий или администратор")
                    self._check_network_schema(database, allow_pending=True)
                    assert vault._key is not None
                    return vault._key

                self._network_recovery_key = coordinator.read_current(open_copy)
                return self._network_envelope(_success({"unlocked": True}))
            except NetworkSnapshotError as error:
                return self._network_envelope(
                    {"ok": False, "error": {"code": error.code, "message": str(error)}}
                )
            except (OSError, ValueError, KeyError, sqlite3.Error) as error:
                return self._network_envelope(_failure(error))

    def _network_execute(
        self,
        method: str,
        payload: object,
        function: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        coordinator = self._snapshot_coordinator
        assert coordinator is not None
        with self._mutex:
            artifact_directory: tempfile.TemporaryDirectory[str] | None = None
            artifact_destination: Path | None = None
            artifact_staged: Path | None = None
            dialog_application = self._application
            original_pdf_dialog = None
            original_excel_dialog = None
            try:
                request = _request(payload) if payload is not None else {}
                if method == "import_local_store" and not request.get("source_database_path"):
                    if (
                        coordinator.pending_operation_id == request.get("operation_id")
                        and coordinator.pending_source_database_path
                    ):
                        request["source_database_path"] = coordinator.pending_source_database_path
                    else:
                        dialog = self._dialogs.get("local_store")
                        if dialog is None:
                            raise ValueError("Выбор исходной базы доступен в desktop-версии")
                        source = dialog()
                        if source is None:
                            return self._network_envelope(_success({"cancelled": True}), method)
                        request["source_database_path"] = str(source)
                maintenance = method in {"setup_access", "unlock_access", "get_access_status"}
                shared_write = maintenance or method in {
                    "enroll_access",
                    "create_report_signer",
                    "manage_report_signer",
                    "import_local_store",
                }
                if not shared_write and method not in {"authenticate_access", "end_administration"}:
                    shared_write = classify_permission(method, request) is not None
                if method == "get_access_status" and (
                    self._automatic_open_attempted or coordinator.pending_operation_id is not None
                ):
                    shared_write = False
                if not shared_write and method != "authenticate_access":
                    clean = {
                        k: v
                        for k, v in request.items()
                        if k not in {"base_global_revision", "operation_id"}
                    }
                    self._network_running = True
                    try:
                        return self._network_envelope(function(clean), method)
                    finally:
                        self._network_running = False
                # Native interaction does not hold the shared writer lock. PDF bytes
                # remain local staging until the authoritative publication is ACKed.
                if dialog_application is not None and method == "export_pdf":
                    self._network_authorize(self._database, method, request)
                    original_pdf_dialog = dialog_application._save_pdf_file
                    if original_pdf_dialog is None:
                        raise ValueError("Сохранение PDF доступно в desktop-версии")
                    snapshot = dialog_application._monthly_snapshot(request)
                    artifact_destination = original_pdf_dialog(
                        f"{snapshot['report_type'].lower()}-{snapshot['period']}.pdf"
                    )
                    if artifact_destination is not None:
                        if artifact_destination.suffix.lower() != ".pdf":
                            raise ValueError("Выберите файл с расширением .pdf")
                        artifact_directory = tempfile.TemporaryDirectory(
                            prefix="network-pdf-",
                            dir=self._database.parent,
                            ignore_cleanup_errors=True,
                        )
                        artifact_staged = Path(artifact_directory.name) / "report.pdf"
                    dialog_application._save_pdf_file = lambda _name: artifact_staged
                if (
                    dialog_application is not None
                    and method == "validate_import"
                    and not request.get("batch_id")
                ):
                    self._network_authorize(self._database, method, request)
                    original_excel_dialog = dialog_application._open_excel_file
                    if original_excel_dialog is not None:
                        selected = original_excel_dialog()
                        dialog_application._open_excel_file = lambda: selected
                memory = (
                    self._application,
                    self._current_user,
                    self._administrator,
                    self._vault._key,
                    self._automatic_open_attempted,
                    self._automatic_open_error,
                )

                def restore_memory() -> None:
                    (
                        self._application,
                        self._current_user,
                        self._administrator,
                        old_key,
                        self._automatic_open_attempted,
                        self._automatic_open_error,
                    ) = memory
                    self._vault._key = old_key
                    if old_key is None:
                        forget_database_key(self._database)
                    else:
                        configure_database_key(self._database, old_key)

                def execute(clean: dict[str, Any]) -> dict[str, Any]:
                    self._network_running = True
                    self._network_access_maintenance = method == "get_access_status"
                    try:
                        result = function(clean)
                        if method == "export_pdf" and result.get("ok") and artifact_destination:
                            result["data"].update(
                                file_path=str(artifact_destination),
                                file_name=artifact_destination.name,
                                export_state="pending",
                            )
                        return result
                    finally:
                        self._network_access_maintenance = False
                        self._network_running = False

                result = coordinator.execute(
                    method,
                    request,
                    execute,
                    key=lambda: self._vault._key,
                    restore_memory=restore_memory,
                    authorize=lambda db: self._network_authorize(db, method, request),
                    maintenance=maintenance or method == "authenticate_access",
                )
                if method == "export_pdf" and result.get("ok") and artifact_destination:
                    if artifact_staged is not None and artifact_staged.exists():
                        pending_artifact = artifact_destination.with_name(
                            "." + uuid4().hex + ".pdf.pending"
                        )
                        try:
                            shutil.copyfile(artifact_staged, pending_artifact)
                            with pending_artifact.open("r+b") as stream:
                                os.fsync(stream.fileno())
                            os.replace(pending_artifact, artifact_destination)
                            result["data"]["export_state"] = "complete"
                        except OSError:
                            result["data"]["export_state"] = "warning"
                            result["data"]["export_warning"] = (
                                "Отчёт подтверждён на сервере, но файл PDF не сохранён."
                            )
                        finally:
                            # Cleanup cannot turn an acknowledged server commit
                            # into an unqualified command failure.
                            try:
                                pending_artifact.unlink(missing_ok=True)
                            except OSError:
                                pass
                    else:
                        result["data"]["export_state"] = "warning"
                        result["data"]["export_warning"] = (
                            "Подтверждение найдено; копию PDF необходимо сохранить отдельно."
                        )
                return self._network_envelope(result, method)
            except NetworkSnapshotError as error:
                result = {"ok": False, "error": {"code": error.code, "message": str(error)}}
                if error.operation_id:
                    result["error"]["operation_id"] = error.operation_id
                return self._network_envelope(result, method)
            except (OSError, ValueError, KeyError, sqlite3.Error, MigrationError) as error:
                return self._network_envelope(_failure(error), method)
            finally:
                if dialog_application is not None:
                    if original_pdf_dialog is not None:
                        dialog_application._save_pdf_file = original_pdf_dialog
                    if original_excel_dialog is not None:
                        dialog_application._open_excel_file = original_excel_dialog
                if artifact_directory is not None:
                    artifact_directory.cleanup()

    def get_network_status(self, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            if self._snapshot_coordinator is None:
                return _success(
                    {
                        "mode": "local",
                        "state": "ready",
                        "loaded_global_revision": None,
                        "current_global_revision": None,
                        "snapshot_changed": False,
                        "can_write": True,
                        "message": None,
                        "pending_operation_id": None,
                    }
                )
            return self._network_envelope(_success(self._snapshot_coordinator.status()))

    def refresh_network_data(self, payload: object) -> dict[str, Any]:
        coordinator = self._snapshot_coordinator
        if coordinator is None:
            return self.get_network_status()
        with self._mutex:
            try:
                request = _request(payload)
                if set(request) != {"has_unsaved_changes", "has_unknown_outcome"} or any(
                    type(v) is not bool for v in request.values()
                ):
                    raise ValueError("Укажите состояние черновика перед обновлением")
                old_key = self._vault._key
                was_open = self._application is not None
                coordinator.refresh(**request)
                self._administrator = None
                self._current_user = None
                if was_open and old_key is not None:
                    configure_database_key(self._database, old_key)
                    self._vault._key = old_key
                    self._open(initialize=False)
                return self.get_network_status()
            except NetworkSnapshotError as error:
                return self._network_envelope(
                    {"ok": False, "error": {"code": error.code, "message": str(error)}}
                )
            except (OSError, ValueError, sqlite3.Error) as error:
                return self._network_envelope(_failure(error))

    def get_network_operation_result(self, payload: object) -> dict[str, Any]:
        coordinator = self._snapshot_coordinator
        if coordinator is None:
            return _failure(ValueError("Проверка сетевой операции доступна в сетевом режиме"))
        with self._mutex:
            try:
                request = _request(payload)
                result = coordinator.recover(
                    _text(request, "operation_id"),
                    self._network_read_key(),
                    lambda db: self._network_authorize(db, "get_network_operation_result", request),
                )
                if result.get("proof") == "verified_unpublished_local_journal":
                    # A failed restoration may have left tentative in-memory access
                    # state. Verified repair restores bytes, never that session.
                    self._application = None
                    self._current_user = None
                    self._administrator = None
                    self._vault.lock()
                    self._automatic_open_attempted = False
                return self._network_envelope(_success(result))
            except NetworkSnapshotError as error:
                return self._network_envelope(
                    {"ok": False, "error": {"code": error.code, "message": str(error)}}
                )
            except (OSError, ValueError, KeyError, sqlite3.Error) as error:
                return self._network_envelope(_failure(error))

    @_network_boundary
    def import_local_store(self, payload: object) -> dict[str, Any]:
        if self._snapshot_coordinator is None:
            return _failure(ValueError("Перенос доступен только в новое сетевое хранилище"))
        try:
            if self._snapshot_coordinator.loaded_global_revision is not None:
                raise ValueError("Общее хранилище уже настроено")
            request = _request(payload)
            source = Path(_text(request, "source_database_path"))
            if source.resolve() == self._database.resolve():
                raise ValueError("Выберите отдельную исходную папку; она будет сохранена")
            if source.is_symlink() or not source.is_file():
                raise ValueError("Исходная база не найдена")
            if any(
                Path(str(source) + suffix).exists()
                and Path(str(source) + suffix).stat().st_size > 0
                for suffix in ("-wal", "-journal")
            ):
                raise ValueError("Закройте исходную программу или выберите проверенную копию")
            for suffix in ("", ".keys.json", ".lifecycle.json"):
                origin = Path(str(source) + suffix)
                target = Path(str(self._database) + suffix)
                if origin.exists():
                    if origin.is_symlink():
                        raise ValueError("Некорректный исходный комплект")
                    shutil.copyfile(origin, target)
                else:
                    target.unlink(missing_ok=True)
            if not is_encrypted_database(self._database):
                raise ValueError("Сначала настройте защиту исходной локальной базы")
            auth = _request(request.get("authorization"))
            admins = [p for p in self._vault.users() if p["role"] == "admin"]
            if len(admins) != 1 or (auth.get("signer_id") and auth["signer_id"] != admins[0]["id"]):
                raise ValueError("Нужен код единственного действующего администратора источника")
            signer_id = str(admins[0]["id"])
            self._vault.unlock(signer_id, _text(auth, "pin"))
            profile = SqliteReportSignersRepository(str(self._database)).authorize(
                signer_id,
                auth["pin"],
                admin_only=True,
            )
            self._backup_before_write()
            self._migrate_after_unlock()
            self._open(initialize=False)
            self._current_user = profile
            self._administrator = None
            self._remember_device()
            return self._backup_after_write(_success(self._status_with_device()))
        except (OSError, ValueError, KeyError, sqlite3.Error, MigrationError) as error:
            return _failure(error)

    def _configure_local_store_dialog(self, open_database_file: Callable[[], Path | None]) -> None:
        self._dialogs["local_store"] = open_database_file

    def _backup_before_write(self) -> None:
        """Do not start a persisted business change without a complete recovery point."""
        if self._database.exists():
            try:
                backup_database(
                    self._database,
                    self._vault.backups,
                    str(self._kwargs["application_version"]),
                )
            except (OSError, ValueError, sqlite3.Error) as error:
                raise ValueError(
                    "Сохранение отменено: не удалось создать полную резервную копию. "
                    "Проверьте свободное место и права записи в папку backups."
                ) from error

    def _backup_after_write(self, result: dict[str, Any]) -> dict[str, Any]:
        """A failed post-save snapshot must not misrepresent committed data as unsaved."""
        if not result.get("ok"):
            return result
        data = result.get("data")
        if not isinstance(data, dict) or data.get("cancelled"):
            return result
        try:
            path = backup_database(
                self._database,
                self._vault.backups,
                str(self._kwargs["application_version"]),
            )
            data["backup_file"] = str(path.relative_to(self._vault.backups))
            data["backup_complete"] = True
            if data.get("operation_revision"):
                data["backup_state"] = "complete"
        except (OSError, ValueError, sqlite3.Error):
            data["backup_complete"] = False
            if data.get("operation_revision"):
                data["backup_state"] = "post_backup_warning"
            data["backup_warning"] = (
                "Данные сохранены, но резервная копия нового состояния не создана. "
                "Копия до сохранения сохранена. Проверьте свободное место и права записи "
                "в папку backups перед следующим сохранением."
            )
        return result

    def get_ui_preferences(self, payload: object) -> dict[str, Any]:
        try:
            if _request(payload):
                raise ValueError("Запрос настроек интерфейса должен быть пустым")
            return _success(self._ui_preferences.read())
        except ValueError as error:
            return {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(error)}}

    def save_ui_preferences(self, payload: object) -> dict[str, Any]:
        # Presentation-only local preference: no business write, PIN, or session change.
        try:
            return _success(self._ui_preferences.save(payload))
        except ValueError as error:
            return {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(error)}}
        except OSError:
            return {
                "ok": False,
                "error": {
                    "code": "PREFERENCES_SAVE_FAILED",
                    "message": "Не удалось сохранить настройку подсказок",
                },
            }

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

    @_network_boundary
    def get_access_status(self, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            try:
                if (
                    self._snapshot_coordinator is not None
                    and self._snapshot_coordinator.pending_operation_id is not None
                    and not self._network_access_maintenance
                ):
                    return _success(self._status_with_device())
                if not self._automatic_open_attempted:
                    self._automatic_open_attempted = True
                    if self._application is None:
                        self._try_automatic_open()
                return _success(self._status_with_device())
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    def _status_with_device(self) -> dict[str, Any]:
        status = self._status()
        status["automatic_open_available"] = (
            self._vault.supports_device_unlock
            and self._vault.device_path.exists()
            and self._automatic_open_error is None
        )
        if self._automatic_open_error is not None:
            status["automatic_open_error"] = self._automatic_open_error
        return status

    def _try_automatic_open(self) -> None:
        try:
            if self._vault.unlock_device():
                self._migrate_after_unlock()
                self._open(initialize=False)
                # Windows account access is not a personal or administrator session.
                self._current_user = None
                self._administrator = None
        except (OSError, ValueError, KeyError, sqlite3.Error, MigrationError, ReportCellError):
            self._application = None
            self._current_user = None
            self._administrator = None
            self._vault.lock()
            self._automatic_open_error = (
                "Автоматическое открытие недоступно для этой базы или учётной записи Windows. "
                "Введите код администратора или проверяющего один раз. "
                "Если ошибка повторится, проверьте базу и резервную копию."
            )

    def _remember_device(self) -> None:
        self._automatic_open_attempted = True
        try:
            self._vault.remember_device()
            self._automatic_open_error = None
        except (OSError, ValueError):
            self._automatic_open_error = (
                "Отчёты открыты, но сохранить автоматическое открытие не удалось. "
                "При следующем запуске понадобится код администратора или проверяющего. "
                "Проверьте права записи в папку программы."
            )

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

    @_network_boundary
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
                self._remember_device()
                return self._backup_after_write(_success(self._status_with_device()))
            except (OSError, ValueError, KeyError, StopIteration, sqlite3.Error) as error:
                self._application = None
                self._current_user = None
                self._vault.lock()
                return _failure(error)

    @_network_boundary
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
                self._remember_device()
                return _success(self._status_with_device())
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
        """Upgrade after a verified PIN/Windows key, preserving ordinary read-only reopen."""
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

    @_network_boundary
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

    @_network_boundary
    def end_administration(self, payload: object = None) -> dict[str, Any]:
        with self._mutex:
            self._administrator = None
            return _success({"ended": True})

    def _lock(self) -> None:
        with self._mutex:
            self._automatic_open_attempted = True
            self._application = None
            self._current_user = None
            self._administrator = None
            self._vault.lock()

    @_network_boundary
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
                self._backup_before_write()
                repo.record_access(admin, "enroll_access", "authorized")
                self._vault.enroll(profile, request["pin"])
                return self._backup_after_write(_success(profile))
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
        # Reentry belongs to the thread holding the mutex. Another thread must
        # wait before deciding whether the coordinator is already active.
        with self._mutex:
            return self._call_locked(method, payload)

    def _call_locked(self, method: str, payload: object = None) -> dict[str, Any]:
        if self._snapshot_coordinator is not None and not self._network_running:
            if method == "get_report_draft_save_result":
                request = _request(payload)
                operation_id = request.get("operation_id") or request.get("idempotency_key")
                result = self.get_network_operation_result(
                    {"operation_id": operation_id, "authorization": request.get("authorization")}
                )
                if result.get("ok"):
                    outcome = result["data"]
                    if outcome["found"]:
                        if outcome["result"].get("ok"):
                            result["data"] = outcome["result"]["data"]
                        else:
                            result["data"] = {
                                "data_state": "not_committed",
                                "error": outcome["result"]["error"],
                                "global_revision": outcome["global_revision"],
                                "operation_id": outcome["operation_id"],
                            }
                    else:
                        result["data"] = {
                            "found": False,
                            "data_state": "unknown",
                            "outcome_unknown": True,
                        }
                return result
            return self._network_execute(
                method, payload, lambda request: self._call(method, request)
            )
        with self._mutex:
            try:
                if self._application is None:
                    raise ValueError("Сначала войдите в программу своим кодом")
                request = _request(payload) if payload is not None else {}
                authorization = request.pop("authorization", None)
                permission = classify_permission(method, request)
                approver = None
                auth: dict[str, Any] = {}
                is_draft_recovery = method == "get_report_draft_save_result"
                if permission is not None or is_draft_recovery:
                    auth = _request(authorization)
                    approver = self._application._signers.authorize(
                        _text(auth, "signer_id"),
                        _text(auth, "pin"),
                        admin_only=permission == "admin",
                    )
                    if not is_draft_recovery:
                        self._backup_before_write()
                        self._application._signers.record_access(approver, method, "authorized")
                context = None
                print_verification = None
                if method in {"save_report_cells", "save_report_presentation", "save_report_draft"}:
                    request, context = prepare_save_confirmation(self._application, method, request)
                if method == "export_pdf":
                    print_verification = confirm_print_report(self._application, request, auth)
                function = getattr(self._application, method)
                if method in {"save_report_draft", "get_report_draft_save_result"}:
                    assert approver is not None
                    result = function(request, actor_ref=approver["id"])
                else:
                    result = function() if method in {"health", "bootstrap"} else function(request)
                if method in {"save_report_cells", "save_report_presentation", "save_report_draft"}:
                    result = confirm_saved_report(self._application, result, context, auth)
                if result.get("ok") and print_verification is not None:
                    result["data"]["verification"] = print_verification
                if result.get("ok") and method == "list_report_signers":
                    enrolled = {p["id"] for p in self._vault.users()}
                    result["data"] = [
                        {**p, "can_unlock": p["id"] in enrolled} for p in result["data"]
                    ]
                if method == "save_report_draft" and result.get("ok"):
                    assert approver is not None
                    draft_data = result["data"]
                    if not draft_data.get("idempotent_replay"):
                        result = self._backup_after_write(result)
                        try:
                            current_revision = self._application._matrix_revision(
                                _text(request, "report_type"),
                                int(_text(request, "organization_id")),
                            )
                            result["data"]["matrix_revision"] = current_revision
                            result["data"]["current_revision"] = current_revision
                            if result["data"]["operation_revision"] != current_revision:
                                result["data"]["verification_state"] = "stale"
                            self._application._draft_outcomes.finalize(
                                _text(request, "idempotency_key"), approver["id"], result["data"]
                            )
                        except (OSError, ValueError, sqlite3.Error):
                            result["data"]["outcome_warning"] = (
                                "Данные сохранены; результат подтверждения и копии не удалось "
                                "зафиксировать для восстановления ответа"
                            )
                    return result
                return self._backup_after_write(result) if permission is not None else result
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

    def _configure_recovery_dialog(self, select_directory: Any) -> None:
        self._dialogs["recovery"] = select_directory

    def _recovery_directory(self) -> Path:
        return Path(self._kwargs["backups_directory"] or self._database.parent.parent / "backups")

    def _recovery_source(self, payload: object) -> Path:
        request = _request(payload)
        if set(request) != {"backup_id"}:
            raise ValueError("Выберите резервную копию из списка")
        identity = _text(request, "backup_id")
        if not identity.startswith("snapshot-") or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            for character in identity
        ):
            raise ValueError("Некорректный идентификатор резервной копии")
        directory = self._recovery_directory().resolve()
        folder = directory / identity
        if folder.is_symlink() or folder.resolve().parent != directory:
            raise ValueError("Копия должна находиться в папке резервирования программы")
        return folder / "reporting.sqlite3"

    def list_recovery_backups(self, payload: object = None) -> dict[str, Any]:
        # Listing encrypted recovery sets needs no live database or personal session.
        with self._mutex:
            try:
                if payload is not None and _request(payload):
                    raise ValueError("Запрос списка резервных копий должен быть пустым")
                results = list_backups(self._recovery_directory())
                return _success(
                    {
                        "backups": [
                            {**item, "backup_id": Path(item["path"]).parent.name}
                            for item in results
                        ]
                    }
                )
            except (OSError, ValueError) as error:
                return {
                    "ok": False,
                    "error": {"code": "BACKUP_RECOVERY_FAILED", "message": str(error)},
                }

    def verify_recovery_backup(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            try:
                source = self._recovery_source(payload)
                manifest = verify_backup(source)
                return _success({"valid": True, "created_at": manifest["created_at"]})
            except (OSError, ValueError, KeyError) as error:
                return {
                    "ok": False,
                    "error": {"code": "BACKUP_RECOVERY_FAILED", "message": str(error)},
                }

    def restore_recovery_backup(self, payload: object) -> dict[str, Any]:
        # Copies a verified set into a NEW directory. It never selects/replaces live
        # data, writes business records, or grants permission to decrypt the result.
        with self._mutex:
            try:
                source = self._recovery_source(payload)
                verify_backup(source)
                selector = self._dialogs.get("recovery")
                parent = selector() if selector is not None else self._recovery_directory().parent
                if parent is None:
                    return _success({"cancelled": True})
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                destination = Path(parent) / f"recovery-{stamp}-{uuid4().hex[:8]}"
                restored = restore_backup(source, destination)
                return _success(
                    {
                        "cancelled": False,
                        "directory": str(destination.resolve()),
                        "database_path": str(restored.resolve()),
                        "instructions": [
                            "Распакуйте эту же или более новую сборку программы в отдельную папку.",
                            "До первого запуска скопируйте все файлы из папки восстановления "
                            "в папку data новой копии программы.",
                            "Запустите новую копию и введите действовавший на дату резервной копии "
                            "код администратора или проверяющего. Проверьте отчёты перед работой.",
                            "Исходная программа и её рабочая база не изменены.",
                        ],
                    }
                )
            except (OSError, ValueError, KeyError, sqlite3.Error) as error:
                return {
                    "ok": False,
                    "error": {"code": "BACKUP_RECOVERY_FAILED", "message": str(error)},
                }

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

    def save_report_draft(self, payload: object) -> dict[str, Any]:
        return self._call("save_report_draft", payload)

    def get_report_draft_save_result(self, payload: object) -> dict[str, Any]:
        return self._call("get_report_draft_save_result", payload)

    def list_blank_report_profiles(self, payload: object) -> dict[str, Any]:
        return self._call("list_blank_report_profiles", payload)

    def create_blank_report_profiles(self, payload: object) -> dict[str, Any]:
        return self._call("create_blank_report_profiles", payload)

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

    @_network_boundary
    def create_report_signer(self, payload: object) -> dict[str, Any]:
        with self._mutex:
            try:
                if self._application is None or self._administrator is None:
                    raise ValueError("Сначала откройте раздел администратора своим кодом")
                request = _request(payload)
                if request.keys() - {"display_name", "pin", "role"}:
                    raise ValueError("Некорректный запрос создания ответственного лица")
                repo = self._application._signers
                self._backup_before_write()
                repo.record_access(self._administrator, "create_report_signer", "authorized")
                profile = repo.create_in_session(
                    _text(request, "display_name"),
                    _text(request, "pin"),
                    self._administrator["id"],
                    role=_text(request, "role"),
                )
                if profile["role"] in {"admin", "reviewer"}:
                    self._vault.enroll(profile, request["pin"])
                return self._backup_after_write(_success(profile))
            except (
                OSError,
                ValueError,
                KeyError,
                sqlite3.Error,
                MigrationError,
                ReportCellError,
            ) as error:
                return _failure(error)

    @_network_boundary
    def manage_report_signer(self, payload: object) -> dict[str, Any]:
        """Fresh administrator approval plus recoverable credential-file updates."""
        with self._mutex:
            try:
                if self._application is None:
                    raise ValueError("Сначала откройте базу")
                request = _request(payload)
                authorization = _request(request.get("authorization"))
                self._application._signers.authorize(
                    _text(authorization, "signer_id"),
                    _text(authorization, "pin"),
                    admin_only=True,
                )
                self._backup_before_write()
                result = AccessLifecycleService(self._database, self._vault).execute(request)
                self._administrator = None
                self._current_user = None
                return self._backup_after_write(_success(result))
            except (OSError, ValueError, KeyError, sqlite3.Error, MigrationError) as error:
                return _failure(error)

    def get_report_verification(self, payload: object) -> dict[str, Any]:
        return self._call("get_report_verification", payload)

    def verify_report(self, payload: object) -> dict[str, Any]:
        return self._call("verify_report", payload)

    def export_pdf(self, payload: object) -> dict[str, Any]:
        return self._call("export_pdf", payload)
