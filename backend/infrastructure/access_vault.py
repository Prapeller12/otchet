"""PIN and optional Windows-wrapped SQLCipher keys; never persist plaintext keys."""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from backend.infrastructure.database.encrypted_sqlite import (
    configure_database_key,
    connect_encrypted,
    encrypt_existing_database,
    forget_database_key,
)
from backend.infrastructure.report_crypto import canonical, decode, encode
from backend.infrastructure.windows_data_protection import (
    DeviceProtector,
    default_device_protector,
)


def _derive(pin: str, salt: bytes) -> bytes:
    if not 6 <= len(pin) <= 128:
        raise ValueError("Код должен содержать от 6 до 128 символов")
    return Argon2id(salt=salt, length=32, iterations=3, lanes=1, memory_cost=65536).derive(
        pin.encode("utf-8")
    )


def _identity(entry: dict[str, Any]) -> bytes:
    return canonical({k: entry[k] for k in ("id", "display_name", "role")}).encode("utf-8")


class AccessVault:
    def __init__(
        self,
        database: Path,
        backups: Path,
        *,
        device_protector: DeviceProtector | Literal["windows"] | None = "windows",
    ) -> None:
        self.database = database
        self.path = database.with_suffix(database.suffix + ".keys.json")
        self.backups = backups
        self._key: bytes | None = None
        self.device_path = database.with_suffix(database.suffix + ".device.json")
        self.lifecycle_path = database.with_suffix(database.suffix + ".lifecycle.json")
        self._device_protector = (
            default_device_protector() if device_protector == "windows" else device_protector
        )

    @property
    def supports_device_unlock(self) -> bool:
        return self._device_protector is not None

    def remember_device(self) -> bool:
        """Remember only the DB key, never a PIN, signer, or authorization session."""
        if self._device_protector is None:
            return False
        if self._key is None or self.pending_setup():
            raise ValueError("Сначала завершите настройку и откройте базу")
        wrapped = self._device_protector.protect(self._key)
        self._write_file(
            self.device_path,
            {"version": 1, "protection": "windows-dpapi-current-user", "key": encode(wrapped)},
        )
        return True

    def unlock_device(self) -> bool:
        """Open an established encrypted database without authenticating a person."""
        if self._device_protector is None or not self.device_path.exists():
            return False
        if not self.path.exists() or self.pending_setup():
            return False
        from backend.infrastructure.database.encrypted_sqlite import is_encrypted_database

        if not self.database.exists() or not is_encrypted_database(self.database):
            raise ValueError("Файл зашифрованной базы отсутствует или повреждён")
        # Bound untrusted JSON before loading and reject unrelated formats.
        if self.device_path.stat().st_size > 131072:
            raise ValueError("Повреждён файл автоматического открытия")
        data = json.loads(self.device_path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or data.get("protection") != "windows-dpapi-current-user"
            or not isinstance(data.get("key"), str)
        ):
            raise ValueError("Повреждён файл автоматического открытия")
        key = self._device_protector.unprotect(decode(data["key"]))
        if len(key) != 32:
            raise ValueError("Некорректный ключ автоматического открытия")
        try:
            configure_database_key(self.database, key)
            with closing(connect_encrypted(self.database)) as connection:
                if connection.execute("PRAGMA cipher_integrity_check").fetchall():
                    raise ValueError("Не пройдена проверка шифрования базы")
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ValueError("Не пройдена проверка целостности базы")
            self._key = key
            self.recover_lifecycle()
        except Exception:
            self.lock()
            raise
        return True

    @staticmethod
    def _validate(data: Any) -> dict[str, Any]:
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data["version"] != 1
        ):
            raise ValueError("Неизвестный формат файла доступа к базе")
        if not isinstance(data.get("users"), list) or not 1 <= len(data["users"]) <= 10000:
            raise ValueError("Повреждён файл доступа к базе")
        for flag in ("pending_setup", "source_exists"):
            if flag in data and not isinstance(data[flag], bool):
                raise ValueError("Повреждены параметры файла доступа")
        identities: set[str] = set()
        administrators = 0
        for entry in data["users"]:
            if not isinstance(entry, dict) or set(entry) != {
                "id",
                "display_name",
                "role",
                "salt",
                "nonce",
                "key",
            }:
                raise ValueError("Повреждён профиль в файле доступа")
            for field, maximum in (("id", 128), ("display_name", 120)):
                value = entry[field]
                if (
                    not isinstance(value, str)
                    or not 1 <= len(value.strip()) <= maximum
                    or not value.isprintable()
                ):
                    raise ValueError("Повреждено имя или идентификатор профиля доступа")
            if (
                entry["id"] in identities
                or not isinstance(entry["role"], str)
                or entry["role"] not in {"admin", "reviewer"}
            ):
                raise ValueError("Повреждены роли или идентификаторы доступа")
            identities.add(entry["id"])
            administrators += entry["role"] == "admin"
            for field, length in (("salt", 16), ("nonce", 12), ("key", 48)):
                value = entry[field]
                if not isinstance(value, str) or len(value) > 128:
                    raise ValueError("Повреждён зашифрованный ключ доступа")
                try:
                    valid = len(decode(value)) == length
                except (ValueError, TypeError):
                    valid = False
                if not valid:
                    raise ValueError("Повреждён зашифрованный ключ доступа")
        if administrators != 1:
            raise ValueError("В файле доступа должен быть один администратор")
        return data

    @staticmethod
    def _read_json(path: Path) -> Any:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Файл доступа превышает допустимый размер")
        return json.loads(path.read_text(encoding="utf-8"))

    def _read(self) -> dict[str, Any]:
        return self._validate(self._read_json(self.path))

    def _lifecycle(self) -> dict[str, Any]:
        data = self._read_json(self.lifecycle_path)
        if not isinstance(data, dict) or set(data) != {
            "version",
            "operation_id",
            "before",
            "after",
        }:
            raise ValueError("Повреждён журнал изменения доступа; восстановите резервную копию")
        operation = data["operation_id"]
        if (
            type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(operation, str)
            or len(operation) != 32
            or any(c not in "0123456789abcdef" for c in operation)
        ):
            raise ValueError("Повреждён журнал изменения доступа")
        self._validate(data["before"])
        self._validate(data["after"])
        return data

    def prepare_lifecycle(self, operation_id: str, after: dict[str, Any]) -> None:
        self.recover_lifecycle()
        self._write_file(
            self.lifecycle_path,
            {
                "version": 1,
                "operation_id": operation_id,
                "before": self._read(),
                "after": self._validate(after),
            },
        )

    def recover_lifecycle(self) -> None:
        """Select the envelope matching the committed SQLite transaction after a crash."""
        if not self.lifecycle_path.exists():
            return
        if self._key is None:
            raise ValueError("Для восстановления изменения доступа сначала откройте базу")
        journal = self._lifecycle()
        with closing(connect_encrypted(self.database)) as connection:
            committed = connection.execute(
                "SELECT 1 FROM report_access_lifecycle_commits WHERE operation_id=?",
                (journal["operation_id"],),
            ).fetchone()
        self._write(journal["after"] if committed else journal["before"])
        self.lifecycle_path.unlink()

    def users(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            {k: entry[k] for k in ("id", "display_name", "role")} for entry in self._read()["users"]
        ]

    def pending_setup(self) -> bool:
        return self.path.exists() and self._read().get("pending_setup") is True

    def finish_setup(self) -> None:
        data = self._read()
        self._write({**data, "pending_setup": False})

    def _write(self, data: dict[str, Any]) -> None:
        self._write_file(self.path, data)

    @staticmethod
    def _write_file(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(path.suffix + ".pending")
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)

    def _wrap(self, profile: dict[str, Any], pin: str) -> dict[str, Any]:
        if self._key is None:
            raise ValueError("Сначала откройте базу своим кодом")
        entry = {k: profile[k] for k in ("id", "display_name", "role")}
        salt, nonce = os.urandom(16), os.urandom(12)
        ciphertext = ChaCha20Poly1305(_derive(pin, salt)).encrypt(
            nonce, self._key, _identity(entry)
        )
        return {**entry, "salt": encode(salt), "nonce": encode(nonce), "key": encode(ciphertext)}

    def initialize(self, profile: dict[str, Any], pin: str) -> None:
        if profile["role"] != "admin":
            raise ValueError("Первую настройку выполняет администратор")
        if self.path.exists():
            raise ValueError("Доступ уже настроен. Введите код существующего пользователя")
        self._key = os.urandom(32)
        self._write(
            {
                "version": 1,
                "users": [self._wrap(profile, pin)],
                "pending_setup": True,
                "source_exists": self.database.exists(),
            }
        )
        # Envelope precedes conversion: interruption can resume using the same code/key.
        encrypt_existing_database(self.database, self._key, self.backups)

    def unlock(self, signer_id: str, pin: str) -> dict[str, Any]:
        if self.lifecycle_path.exists():
            journal = self._lifecycle()
            # Either password wrapper may be the committed one. Resolve using the
            # authenticated database marker before accepting a user's credentials.
            for document in (journal["after"], journal["before"]):
                for entry in document["users"]:
                    if entry["id"] != signer_id:
                        continue
                    try:
                        key = ChaCha20Poly1305(_derive(pin, decode(entry["salt"]))).decrypt(
                            decode(entry["nonce"]), decode(entry["key"]), _identity(entry)
                        )
                    except (InvalidTag, ValueError):
                        continue
                    self._key = key
                    configure_database_key(self.database, key)
                    try:
                        self.recover_lifecycle()
                        return self.unlock(signer_id, pin)
                    except Exception:
                        self.lock()
                        raise
            raise ValueError("Неверный код или повреждён файл доступа")
        data = self._read()
        entries = [p for p in data["users"] if p["id"] == signer_id]
        if len(entries) != 1:
            raise ValueError("Доступ этого пользователя к зашифрованной базе ещё не настроен")
        entry = entries[0]
        if entry.get("role") not in {"admin", "reviewer"}:
            raise ValueError("Базу открывает проверяющий или администратор")
        try:
            key = ChaCha20Poly1305(_derive(pin, decode(entry["salt"]))).decrypt(
                decode(entry["nonce"]), decode(entry["key"]), _identity(entry)
            )
        except (InvalidTag, ValueError) as error:
            raise ValueError("Неверный код или повреждён файл доступа") from error
        self._key = key
        # A preceding interrupted conversion may still have a plaintext/absent database.
        from backend.infrastructure.database.encrypted_sqlite import is_encrypted_database

        if not self.database.exists() and (
            data.get("pending_setup") is not True or data.get("source_exists") is True
        ):
            raise ValueError(
                "Файл базы отсутствует. Восстановите базу и файл доступа из резервной копии"
            )
        if not self.database.exists() or not is_encrypted_database(self.database):
            if data.get("pending_setup") is not True:
                raise ValueError(
                    "Ожидалась зашифрованная база. Проверьте выбранную резервную копию"
                )
            encrypt_existing_database(self.database, key, self.backups)
        else:
            configure_database_key(self.database, key)
        return {k: entry[k] for k in ("id", "display_name", "role")}

    def enroll(self, profile: dict[str, Any], pin: str) -> None:
        if profile["role"] not in {"admin", "reviewer"}:
            raise ValueError("Ключ базы выдаётся только проверяющему или администратору")
        data = self._read()
        entries = [
            item for item in data["users"] if item["id"] not in {profile["id"], "setup-admin"}
        ]
        if profile["role"] == "admin" and any(p["role"] == "admin" for p in entries):
            raise ValueError("В программе может быть только один администратор")
        entries.append(self._wrap(profile, pin))
        self._write({**data, "users": entries})

    def lock(self) -> None:
        self._key = None
        forget_database_key(self.database)
