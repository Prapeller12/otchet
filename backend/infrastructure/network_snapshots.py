"""Serialized publication of immutable encrypted snapshots, never SQLite over SMB.

The CURRENT replacement is the only authoritative commit. Local SQL commits are
tentative until it is acknowledged. A failed/ambiguous publication restores the
complete local checkpoint; durable, secret-free identity permits later recovery.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import uuid4

from backend.infrastructure.access_vault import AccessVault
from backend.infrastructure.database.encrypted_sqlite import (
    configure_database_key,
    encrypted_backup,
    forget_database_key,
    is_encrypted_database,
)
from backend.infrastructure.database.migrator import connect_sqlite

_NAME = re.compile(r"[0-9a-f]{32}")
_SECRETS = frozenset({"pin", "current_pin", "new_pin", "authorization"})
_DATABASE_NAME = "reporting.sqlite3"
_DATA_FILES = (_DATABASE_NAME, _DATABASE_NAME + ".keys.json", _DATABASE_NAME + ".lifecycle.json")


class NetworkSnapshotError(Exception):
    def __init__(self, code: str, message: str, operation_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.operation_id = operation_id


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _without_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): _without_secrets(v)
            for k, v in value.items()
            if k not in _SECRETS and k not in {"base_global_revision", "operation_id"}
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def request_digest(command: str, request: Mapping[str, Any], actor_id: str) -> str:
    """Never turn a short personal code into a persisted password verifier."""
    return hashlib.sha256(
        _json(
            {"command": command, "actor_id": actor_id, "request": _without_secrets(request)}
        ).encode("utf-8")
    ).hexdigest()


def _flush(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Flush the renamed handle too: Windows cannot fsync a directory, and
        # acknowledgement must follow an SMB FLUSH of the actual CURRENT file.
        _flush(path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Некорректный сетевой манифест")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Некорректный сетевой манифест")
    return value


class SharedWriterLock:
    """Held OS file lock; the marker is never unlinked or reclaimed by timeouts.

    Windows CRT locking uses an OS byte-range lock propagated by SMB. POSIX
    flock is only the local test adapter, not evidence of Windows SMB behavior.
    """

    def __init__(self, path: Path, timeout_seconds: float = 15) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._stream: BinaryIO | None = None

    def __enter__(self) -> SharedWriterLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("Некорректный файл блокировки хранилища")
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    msvcrt = importlib.import_module("msvcrt")

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._stream = stream
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise NetworkSnapshotError(
                        "NETWORK_BUSY", "Хранилище занято другим сохранением. Повторите позже."
                    ) from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def assert_owned(self) -> None:
        if self._stream is None or self._stream.closed:
            raise NetworkSnapshotError("NETWORK_UNAVAILABLE", "Блокировка хранилища потеряна")
        # A flushed I/O on the held handle surfaces disconnected/revoked SMB handles.
        self._stream.seek(0)
        self._stream.write(b"1")
        os.fsync(self._stream.fileno())

    def __exit__(self, *args: Any) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


class SnapshotCoordinator:
    def __init__(
        self, share_root: Path, database_path: Path, lock_timeout_seconds: float = 15
    ) -> None:
        self.share_root = Path(share_root)
        self.database_path = Path(database_path)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.loaded_global_revision: str | None = None
        self.dataset_id: str | None = None
        self.prepared = False
        self._pending_path = self.database_path.with_suffix(".network-pending.json")
        self._local_state_path = self.database_path.with_suffix(".network-local.json")
        self._abort_path = self.database_path.with_suffix(".network-aborted.json")
        self._fault: Callable[[str], None] = lambda _stage: None

    def writer_lock(self) -> SharedWriterLock:
        return SharedWriterLock(self.share_root / "writer.lock", self.lock_timeout_seconds)

    @property
    def pending_operation_id(self) -> str | None:
        if not self._pending_path.exists():
            return None
        try:
            document = _read_json(self._pending_path)
            operation_id = document["operation_id"]
            if (
                not isinstance(operation_id, str)
                or not 1 <= len(operation_id) <= 200
                or not isinstance(document.get("command"), str)
            ):
                raise ValueError("Некорректная идентичность операции")
            return operation_id
        except (OSError, ValueError, KeyError) as error:
            raise NetworkSnapshotError(
                "NETWORK_STORE_INVALID",
                "Повреждено локальное состояние незавершённой операции. Запись заблокирована.",
            ) from error

    @property
    def pending_command(self) -> str | None:
        if not self._pending_path.exists():
            return None
        return str(_read_json(self._pending_path)["command"])

    @property
    def pending_source_database_path(self) -> str | None:
        if not self._pending_path.exists():
            return None
        value = _read_json(self._pending_path).get("source_database_path")
        return str(value) if value else None

    def recovery_users(self) -> list[dict[str, Any]]:
        """Public keyholders plus this local pending owner, no shared catalogue."""
        with self.writer_lock():
            pointer = self._pointer()
            users: list[dict[str, Any]] = []
            if pointer is not None:
                directory = self._verified_generation(pointer)
                document = AccessVault._validate(
                    _read_json(directory / (_DATABASE_NAME + ".keys.json"))
                )
                users = [
                    {k: p[k] for k in ("id", "display_name", "role")} for p in document["users"]
                ]
            if self.pending_operation_id is not None:
                owner = _read_json(self._pending_path).get("owner")
                if (
                    isinstance(owner, dict)
                    and owner.get("role") in {"admin", "reviewer", "project_manager"}
                    and not any(p["id"] == owner.get("id") for p in users)
                ):
                    users.append({k: owner[k] for k in ("id", "display_name", "role")})
            return users

    def read_current(self, reader: Callable[[Path], Any], key: bytes | None = None) -> Any:
        """Verified temporary copy, without activation, audit or publication."""
        with self.writer_lock():
            pointer = self._pointer()
            if pointer is None:
                raise NetworkSnapshotError(
                    "NETWORK_INITIALIZATION_REQUIRED", "Хранилище не настроено"
                )
            with self._authoritative_database(pointer, key) as database:
                return reader(database)

    def _clear_pending(self) -> None:
        checkpoint = None
        if self._pending_path.exists():
            name = _read_json(self._pending_path).get("checkpoint_directory")
            if (
                isinstance(name, str)
                and Path(name).name == name
                and name.startswith("network-checkpoint-")
            ):
                checkpoint = self.database_path.parent / name
        self._pending_path.unlink(missing_ok=True)
        if checkpoint is not None:
            shutil.rmtree(checkpoint, ignore_errors=True)

    @staticmethod
    def _owner(database: Path, actor: str, key: bytes | None) -> dict[str, Any] | None:
        if key is None or not database.exists():
            return None
        with closing(connect_sqlite(database)) as connection:
            row = connection.execute(
                "SELECT s.id,s.display_name,r.role FROM report_signers s "
                "JOIN report_access_roles r ON r.signer_id=s.id WHERE s.id=?",
                (actor,),
            ).fetchone()
        return dict(zip(("id", "display_name", "role"), row, strict=True)) if row else None

    @contextmanager
    def _checkpoint_directory(self) -> Iterator[Path]:
        directory = Path(
            tempfile.mkdtemp(prefix="network-checkpoint-", dir=self.database_path.parent)
        )
        try:
            yield directory
        finally:
            # A failed restoration must keep the only baseline, including pristine
            # setup's existing local source. Pending identity binds this directory.
            if not self._pending_path.exists():
                shutil.rmtree(directory, ignore_errors=True)

    def _repair_unpublished(self, journal: dict[str, Any]) -> None:
        if journal.get("dataset_id") != self.dataset_id:
            raise ValueError("Состояние клиента относится к другому хранилищу")
        name = journal.get("checkpoint_directory")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.startswith("network-checkpoint-")
        ):
            raise ValueError("Не найден полный локальный комплект восстановления")
        checkpoint = self.database_path.parent / name
        hashes = journal.get("baseline_hashes")
        if checkpoint.is_symlink() or not isinstance(hashes, dict):
            raise ValueError("Повреждён локальный комплект восстановления")
        baseline = {}
        for index, file in enumerate(self._state_files()):
            checksum = hashes.get(str(index))
            if checksum is not None:
                source = checkpoint / str(index)
                if source.is_symlink() or _digest_file(source) != checksum:
                    raise ValueError("Не пройдена проверка локального комплекта")
                baseline[file] = checksum
        pointer = self._pointer(journal.get("generation_id"))
        if pointer is not None and pointer.get("generation_id") == journal.get("generation_id"):
            raise ValueError("Локальная отметка противоречит опубликованному поколению")
        if pointer is not None:
            with tempfile.TemporaryDirectory(
                prefix="network-repair-", dir=self.database_path.parent
            ) as folder:
                incoming = Path(folder)
                self._copy_generation(pointer, incoming)
                self._activate(pointer, incoming)
        else:
            self._restore(checkpoint, baseline)
            self.loaded_global_revision = None
        _atomic_json(
            self._abort_path,
            {
                "dataset_id": self.dataset_id,
                "operation_id": journal["operation_id"],
                "actor_id": journal.get("actor_id"),
                "command": journal["command"],
                "publication_attempted": False,
            },
        )
        generation_id = journal.get("generation_id")
        if isinstance(generation_id, str) and _NAME.fullmatch(generation_id):
            own_unpublished = self.share_root / "generations" / generation_id
            if own_unpublished.exists():
                shutil.rmtree(own_unpublished)
        self._clear_pending()

    def _pointer(self, unpublished_generation: str | None = None) -> dict[str, Any] | None:
        marker = _read_json(self.share_root / "STORE.json")
        if marker.get("version") != 1 or not _NAME.fullmatch(str(marker.get("dataset_id", ""))):
            raise ValueError("Некорректный идентификатор общего хранилища")
        if self.dataset_id is not None and marker["dataset_id"] != self.dataset_id:
            raise ValueError("Общее хранилище заменено другим набором данных")
        self.dataset_id = marker["dataset_id"]
        current = self.share_root / "CURRENT"
        if not current.exists():
            generations = self.share_root / "generations"
            if any(
                p.is_dir() and not p.name.startswith(".") and p.name != unpublished_generation
                for p in generations.iterdir()
            ):
                raise ValueError("CURRENT отсутствует в существующем хранилище; требуется проверка")
            return None
        pointer = _read_json(current)
        if (
            pointer.get("version") != 1
            or pointer.get("dataset_id") != self.dataset_id
            or not _NAME.fullmatch(str(pointer.get("generation_id", "")))
            or not isinstance(pointer.get("manifest_sha256"), str)
            or len(pointer["manifest_sha256"]) != 64
        ):
            raise ValueError("Повреждён CURRENT общего хранилища")
        return pointer

    def _revision(self, pointer: dict[str, Any] | None) -> str | None:
        if pointer is None:
            return None
        return f"{pointer['dataset_id']}:{pointer['generation_id']}"

    def _verified_generation(self, pointer: dict[str, Any]) -> Path:
        directory = self.share_root / "generations" / str(pointer["generation_id"])
        manifest_path = directory / "manifest.json"
        if directory.is_symlink() or _digest_file(manifest_path) != pointer["manifest_sha256"]:
            raise ValueError("Не пройдена проверка манифеста общего снимка")
        manifest = _read_json(manifest_path)
        if (
            manifest.get("version") != 1
            or manifest.get("dataset_id") != pointer["dataset_id"]
            or manifest.get("generation_id") != pointer["generation_id"]
            or not isinstance(manifest.get("files"), dict)
            or not {_DATABASE_NAME, _DATABASE_NAME + ".keys.json"} <= manifest["files"].keys()
            or set(manifest["files"]) - set(_DATA_FILES)
        ):
            raise ValueError("Неполный общий снимок")
        for name, checksum in manifest["files"].items():
            file = directory / name
            if file.is_symlink() or not isinstance(checksum, str) or _digest_file(file) != checksum:
                raise ValueError("Не пройдена проверка файлов общего снимка")
        return directory

    def _copy_generation(self, pointer: dict[str, Any], directory: Path) -> Path:
        source = self._verified_generation(pointer)
        for name in _DATA_FILES:
            if (source / name).exists():
                shutil.copyfile(source / name, directory / name)
                _flush(directory / name)
        manifest = _read_json(source / "manifest.json")
        for name, checksum in manifest["files"].items():
            if _digest_file(directory / name) != checksum:
                raise ValueError("Контрольная сумма локальной копии не совпала")
        database = directory / _DATABASE_NAME
        if not is_encrypted_database(database):
            raise ValueError("Общий снимок должен содержать зашифрованную базу")
        AccessVault._validate(_read_json(directory / (_DATABASE_NAME + ".keys.json")))
        return database

    def _activate(self, pointer: dict[str, Any], incoming: Path) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        old_revision = self.loaded_global_revision
        with self._checkpoint_directory() as checkpoint:
            files = self._checkpoint(checkpoint)
            try:
                for suffix in ("", ".keys.json", ".lifecycle.json"):
                    source = incoming / (_DATABASE_NAME + suffix)
                    destination = Path(str(self.database_path) + suffix)
                    if source.exists():
                        os.replace(source, destination)
                    else:
                        destination.unlink(missing_ok=True)
                    if suffix == "":
                        self._fault("after_database_activation")
                    if suffix == ".keys.json":
                        self._fault("after_keys_activation")
                for suffix in ("-wal", "-shm", "-journal"):
                    Path(str(self.database_path) + suffix).unlink(missing_ok=True)
                self._fault("before_local_state_replace")
                revision = self._revision(pointer)
                _atomic_json(self._local_state_path, {"revision": revision})
                self.loaded_global_revision = revision
                shutil.rmtree(checkpoint, ignore_errors=True)
            except BaseException:
                try:
                    self._restore(checkpoint, files)
                except BaseException as error:
                    if not self._pending_path.exists():
                        operation_id = uuid4().hex
                        _atomic_json(
                            self._pending_path,
                            {
                                "operation_id": operation_id,
                                "command": "refresh_network_data",
                                "phase": "activation_failed",
                                "publication_attempted": False,
                                "dataset_id": self.dataset_id,
                                "actor_id": "system",
                                "checkpoint_directory": checkpoint.name,
                                "baseline_hashes": {
                                    str(i): files[p]
                                    for i, p in enumerate(self._state_files())
                                    if p in files
                                },
                            },
                        )
                    raise NetworkSnapshotError(
                        "NETWORK_LOCAL_RECOVERY_REQUIRED",
                        "Локальная пара не восстановлена. Запись заблокирована до восстановления.",
                        self.pending_operation_id,
                    ) from error
                self.loaded_global_revision = old_revision
                shutil.rmtree(checkpoint, ignore_errors=True)
                raise

    def prepare(self) -> None:
        """Accept the authoritative encrypted pair locally before opening SQLCipher."""
        try:
            self.share_root.mkdir(parents=True, exist_ok=True)
            with self.writer_lock():
                generations = self.share_root / "generations"
                generations.mkdir(exist_ok=True)
                marker = self.share_root / "STORE.json"
                if not marker.exists():
                    if (self.share_root / "CURRENT").exists() or any(generations.iterdir()):
                        raise ValueError("Нельзя автоматически переинициализировать хранилище")
                    _atomic_json(marker, {"version": 1, "dataset_id": uuid4().hex})
                if self.pending_operation_id is not None:
                    journal = _read_json(self._pending_path)
                    if journal.get("publication_attempted") is False:
                        self._pointer(journal.get("generation_id"))
                        self._repair_unpublished(journal)
                pointer = self._pointer()
                if pointer is not None:
                    self.database_path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(
                        prefix="network-incoming-", dir=self.database_path.parent
                    ) as folder:
                        incoming = Path(folder)
                        self._copy_generation(pointer, incoming)
                        self._activate(pointer, incoming)
                elif self._local_state_path.exists():
                    raise ValueError("Общее поколение исчезло; локальная копия не публикуется")
                self.prepared = True
        except NetworkSnapshotError:
            raise
        except (OSError, ValueError, KeyError) as error:
            raise NetworkSnapshotError("NETWORK_STORE_INVALID", str(error)) from error

    def status(self) -> dict[str, Any]:
        current: str | None = None
        message: str | None = None
        state = "ready"
        try:
            pointer = self._pointer()
            if pointer is not None:
                self._verified_generation(pointer)
            current = self._revision(pointer)
            if current != self.loaded_global_revision:
                state, message = "stale", "В общих данных есть изменения. Обновите данные."
        except (OSError, ValueError, KeyError):
            state, message = "unavailable", "Общее хранилище недоступно или повреждено."
        try:
            pending = self.pending_operation_id
        except NetworkSnapshotError:
            state, message = (
                "outcome_unknown",
                ("Повреждено локальное состояние незавершённой операции. Запись заблокирована."),
            )
            pending = None
        if pending is not None:
            state, message = "outcome_unknown", "Результат сохранения требуется проверить."
        return {
            "mode": "network",
            "state": state,
            "loaded_global_revision": self.loaded_global_revision,
            "current_global_revision": current,
            "snapshot_changed": current != self.loaded_global_revision,
            "can_write": state == "ready" and not self._pending_path.exists(),
            "message": message,
            "pending_operation_id": pending,
        }

    def refresh(self, *, has_unsaved_changes: bool, has_unknown_outcome: bool) -> None:
        if has_unsaved_changes or has_unknown_outcome or self.pending_operation_id is not None:
            raise NetworkSnapshotError(
                "NETWORK_REFRESH_BLOCKED", "Сначала завершите работу с текущим черновиком."
            )
        self.prepare()

    @contextmanager
    def _authoritative_database(self, pointer: dict[str, Any], key: bytes | None) -> Iterator[Path]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="network-read-", dir=self.database_path.parent
        ) as folder:
            database = self._copy_generation(pointer, Path(folder))
            if key is not None:
                configure_database_key(database, key)
            try:
                yield database
            finally:
                forget_database_key(database)

    @staticmethod
    def _receipt(database: Path, operation_id: str) -> dict[str, Any] | None:
        with closing(connect_sqlite(database)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='network_operation_receipts'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT command,request_sha256,actor_id,global_revision,result_json "
                "FROM network_operation_receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(zip(("command", "digest", "actor", "revision", "result"), row, strict=True))

    def recover(
        self, operation_id: str, key: bytes | None, authorize: Callable[[Path], str]
    ) -> dict[str, Any]:
        with self.writer_lock():
            if self.pending_operation_id == operation_id:
                journal = _read_json(self._pending_path)
                if journal.get("publication_attempted") is False:
                    self._pointer(journal.get("generation_id"))
                    self._repair_unpublished(journal)
            pointer = self._pointer()
            current = self._revision(pointer)
            receipt = None
            if pointer is not None:
                with self._authoritative_database(pointer, key) as database:
                    actor = authorize(database)
                    receipt = self._receipt(database, operation_id)
                    if receipt is not None and receipt["actor"] not in {actor, "system"}:
                        raise NetworkSnapshotError(
                            "ACCESS_DENIED", "Результат принадлежит другому автору"
                        )
            if receipt is None:
                if self._abort_path.exists():
                    proof = _read_json(self._abort_path)
                    if (
                        proof.get("operation_id") == operation_id
                        and proof.get("dataset_id") == self.dataset_id
                        and proof.get("publication_attempted") is False
                        and (pointer is None or proof.get("actor_id") in {actor, "system"})
                    ):
                        failure = {
                            "ok": False,
                            "error": {
                                "code": "NETWORK_UNAVAILABLE",
                                "message": "Публикация не началась; локальная копия восстановлена.",
                                "operation_id": operation_id,
                            },
                        }
                        return {
                            "found": True,
                            "operation_id": operation_id,
                            "data_state": "not_committed",
                            "result": failure,
                            "global_revision": current,
                            "current_global_revision": current,
                            "proof": "verified_unpublished_local_journal",
                        }
                return {
                    "found": False,
                    "operation_id": operation_id,
                    "data_state": "unknown",
                    "result": None,
                    "global_revision": None,
                    "current_global_revision": current,
                }
            result = json.loads(receipt["result"])
            if self.pending_operation_id == operation_id:
                self._clear_pending()
            return {
                "found": True,
                "operation_id": operation_id,
                "data_state": "committed" if result.get("ok") else "not_committed",
                "result": result,
                "global_revision": receipt["revision"],
                "current_global_revision": current,
            }

    def _state_files(self) -> list[Path]:
        return [
            Path(str(self.database_path) + suffix)
            for suffix in (
                "",
                ".keys.json",
                ".lifecycle.json",
                ".device.json",
                "-wal",
                "-shm",
                "-journal",
            )
        ] + [self._local_state_path]

    def _checkpoint(self, directory: Path) -> dict[Path, str]:
        files = {}
        for index, path in enumerate(self._state_files()):
            if path.exists():
                shutil.copyfile(path, directory / str(index))
                _flush(directory / str(index))
                files[path] = _digest_file(path)
        return files

    def _restore(self, directory: Path, files: dict[Path, str]) -> None:
        for index, path in enumerate(self._state_files()):
            if path in files:
                shutil.copyfile(directory / str(index), path)
                _flush(path)
            else:
                path.unlink(missing_ok=True)

    def _dirty(self, files: dict[Path, str]) -> bool:
        return any(
            (path.exists() and _digest_file(path) != files.get(path))
            or (not path.exists() and path in files)
            for path in self._state_files()[:3]
        )

    @staticmethod
    def _audit_rows(database: Path, after_id: int = 0) -> list[tuple[Any, ...]]:
        with closing(connect_sqlite(database)) as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='report_access_events'"
            ).fetchone():
                return []
            return connection.execute(
                "SELECT id,signer_id,role,command,outcome,created_at "
                "FROM report_access_events WHERE id>? ORDER BY id",
                (after_id,),
            ).fetchall()

    def execute(
        self,
        command: str,
        request: dict[str, Any],
        operation: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        key: Callable[[], bytes | None],
        restore_memory: Callable[[], None],
        authorize: Callable[[Path], str],
        maintenance: bool = False,
    ) -> dict[str, Any]:
        operation_id = request.get("operation_id") or request.get("idempotency_key")
        if maintenance and not operation_id:
            operation_id = uuid4().hex
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise NetworkSnapshotError("VALIDATION_ERROR", "Не указан идентификатор операции")
        pending_operation = self.pending_operation_id
        if pending_operation is not None and pending_operation != operation_id:
            raise NetworkSnapshotError(
                "SAVE_OUTCOME_UNKNOWN",
                "Сначала проверьте результат предыдущего сохранения.",
                pending_operation,
            )
        expected = request.get(
            "base_global_revision", self.loaded_global_revision if maintenance else "missing"
        )
        clean = {
            k: v for k, v in request.items() if k not in {"base_global_revision", "operation_id"}
        }
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.writer_lock() as lock:
            pointer = self._pointer()
            current = self._revision(pointer)
            # Replay is read-only and fresh authorization is checked against latest data.
            if pointer is not None and (
                key() is not None or not maintenance or pending_operation == operation_id
            ):
                with self._authoritative_database(pointer, key()) as latest:
                    actor = authorize(latest)
                    receipt = self._receipt(latest, operation_id)
                    if receipt is not None:
                        digest = request_digest(command, request, actor)
                        if (
                            receipt["actor"] != actor
                            or receipt["digest"] != digest
                            or receipt["command"] != command
                        ):
                            raise NetworkSnapshotError(
                                "NETWORK_OPERATION_CONFLICT",
                                "Идентификатор уже использован другим запросом",
                            )
                        result = cast(dict[str, Any], json.loads(receipt["result"]))
                        if isinstance(result.get("data"), dict):
                            result["data"]["idempotent_replay"] = True
                        if pending_operation == operation_id:
                            self._clear_pending()
                        return result
            if pending_operation == operation_id:
                raise NetworkSnapshotError(
                    "SAVE_OUTCOME_UNKNOWN", "Подтверждённый результат пока не найден.", operation_id
                )
            if expected != current or self.loaded_global_revision != current:
                raise NetworkSnapshotError(
                    "GLOBAL_REVISION_CONFLICT", "Общие данные изменены. Черновик сохранён в форме."
                )
            if (
                pointer is None
                and self.database_path.exists()
                and command not in {"setup_access", "import_local_store"}
            ):
                raise NetworkSnapshotError(
                    "NETWORK_INITIALIZATION_REQUIRED",
                    "Выберите первичную настройку или явный перенос существующей базы.",
                )
            # Fresh profile in the loaded (now current) DB, before any audit/domain write.
            actor = authorize(self.database_path)
            digest = request_digest(command, request, actor)
            baseline_key = key()
            audits = self._audit_rows(self.database_path) if baseline_key is not None else []
            audit_id = int(audits[-1][0]) if audits else 0
            with self._checkpoint_directory() as checkpoint:
                files = self._checkpoint(checkpoint)
                published = False
                attempted = False
                acknowledged = False
                generation: Path | None = None
                _atomic_json(
                    self._pending_path,
                    {
                        "operation_id": operation_id,
                        "command": command,
                        "request_sha256": digest,
                        "base_global_revision": current,
                        "generation_id": None,
                        "phase": "executing",
                        "source_database_path": request.get("source_database_path"),
                        "publication_attempted": False,
                        "dataset_id": self.dataset_id,
                        "actor_id": actor,
                        "owner": self._owner(self.database_path, actor, baseline_key),
                        "checkpoint_directory": checkpoint.name,
                        "baseline_hashes": {
                            str(i): files[p]
                            for i, p in enumerate(self._state_files())
                            if p in files
                        },
                    },
                )
                try:
                    result = operation(clean)
                    active_key = key() or baseline_key
                    if active_key is not None:
                        configure_database_key(self.database_path, active_key)
                    if not result.get("ok"):
                        rows = self._audit_rows(self.database_path, audit_id) if active_key else []
                        self._restore(checkpoint, files)
                        restore_memory()
                        if not rows:
                            self._pending_path.unlink(missing_ok=True)
                            return result
                        with closing(connect_sqlite(self.database_path)) as connection, connection:
                            connection.executemany(
                                "INSERT INTO report_access_events"
                                "(signer_id,role,command,outcome,created_at) "
                                "VALUES (?,?,?,?,?)",
                                [r[1:] for r in rows],
                            )
                    if not self._dirty(files):
                        self._pending_path.unlink(missing_ok=True)
                        return result
                    if active_key is None or not is_encrypted_database(self.database_path):
                        raise ValueError("Публикация незашифрованной базы запрещена")
                    generation_id = uuid4().hex
                    revision = f"{self.dataset_id}:{generation_id}"
                    if command in {"setup_access", "import_local_store"} and result.get("ok"):
                        actor = result["data"]["current_user"]["id"]
                        digest = request_digest(command, request, actor)
                    field = "data" if result.get("ok") else "error"
                    if isinstance(result.get(field), dict):
                        result[field].update(global_revision=revision, operation_id=operation_id)
                    with closing(connect_sqlite(self.database_path)) as connection, connection:
                        connection.execute(
                            "INSERT INTO network_operation_receipts "
                            "VALUES (?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                            (operation_id, command, digest, actor, revision, _json(result)),
                        )
                    _atomic_json(
                        self._pending_path,
                        {
                            **_read_json(self._pending_path),
                            "operation_id": operation_id,
                            "command": command,
                            "request_sha256": digest,
                            "base_global_revision": current,
                            "generation_id": generation_id,
                            "source_database_path": request.get("source_database_path"),
                            "phase": "prepared",
                            "actor_id": actor,
                            "owner": self._owner(self.database_path, actor, active_key),
                        },
                    )
                    self._fault("before_generation_copy")
                    local_export = checkpoint / "export"
                    local_export.mkdir()
                    encrypted_backup(self.database_path, local_export / _DATABASE_NAME)
                    for suffix in (".keys.json", ".lifecycle.json"):
                        source = Path(str(self.database_path) + suffix)
                        if source.exists():
                            shutil.copyfile(source, local_export / (_DATABASE_NAME + suffix))
                    generation = self.share_root / "generations" / generation_id
                    pending = generation.with_name("." + generation_id + ".pending")
                    pending.mkdir()
                    hashes = {}
                    for source in local_export.iterdir():
                        _flush(source)
                        shutil.copyfile(source, pending / source.name)
                        _flush(pending / source.name)
                        hashes[source.name] = _digest_file(source)
                        if _digest_file(pending / source.name) != hashes[source.name]:
                            raise ValueError("Проверка опубликованной копии не пройдена")
                    _atomic_json(
                        pending / "manifest.json",
                        {
                            "version": 1,
                            "dataset_id": self.dataset_id,
                            "generation_id": generation_id,
                            "parent_global_revision": current,
                            "files": hashes,
                        },
                    )
                    _sync_directory(pending)
                    os.rename(pending, generation)
                    _sync_directory(generation.parent)
                    self._fault("after_generation_copy")
                    new_pointer = {
                        "version": 1,
                        "dataset_id": self.dataset_id,
                        "generation_id": generation_id,
                        "manifest_sha256": _digest_file(generation / "manifest.json"),
                    }
                    self._verified_generation(new_pointer)
                    lock.assert_owned()
                    if self._revision(self._pointer(generation_id)) != current:
                        raise NetworkSnapshotError(
                            "GLOBAL_REVISION_CONFLICT", "Общие данные изменены"
                        )
                    self._fault("before_current_replace")
                    _atomic_json(
                        self._pending_path,
                        {
                            **_read_json(self._pending_path),
                            "publication_attempted": True,
                            "phase": "publication_attempted",
                        },
                    )
                    attempted = True
                    _atomic_json(self.share_root / "CURRENT", new_pointer)
                    published = True
                    self._fault("after_current_replace")
                    self._fault("before_ack")
                    if self._pointer() != new_pointer:
                        raise ValueError("Не удалось подтвердить CURRENT")
                    acknowledged = True
                    self._fault("before_local_activation")
                    self.loaded_global_revision = revision
                    _atomic_json(self._local_state_path, {"revision": revision})
                    self._pending_path.unlink(missing_ok=True)
                    return result
                except BaseException as error:
                    self.loaded_global_revision = current
                    try:
                        self._restore(checkpoint, files)
                        restore_memory()
                    except BaseException as restore_error:
                        raise NetworkSnapshotError(
                            "NETWORK_LOCAL_RECOVERY_REQUIRED",
                            "Локальная копия не восстановлена. Запись заблокирована; "
                            "повторно откройте клиент после проверки диска.",
                            operation_id,
                        ) from restore_error
                    if acknowledged:
                        self._pending_path.unlink(missing_ok=True)
                        self.loaded_global_revision = current
                        field = "data" if result.get("ok") else "error"
                        if isinstance(result.get(field), dict):
                            result[field]["local_activation_warning"] = (
                                "Сохранено на сервере; обновите локальные данные."
                            )
                        return result
                    if attempted or published:
                        raise NetworkSnapshotError(
                            "SAVE_OUTCOME_UNKNOWN",
                            "Результат публикации требуется проверить.",
                            operation_id,
                        ) from error
                    self._pending_path.unlink(missing_ok=True)
                    if generation is not None and generation.exists():
                        shutil.rmtree(generation)
                    if isinstance(error, NetworkSnapshotError):
                        raise
                    raise NetworkSnapshotError(
                        "NETWORK_UNAVAILABLE", "Сохранение отменено: публикация недоступна."
                    ) from error
