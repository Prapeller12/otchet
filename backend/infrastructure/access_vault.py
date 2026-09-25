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
        except Exception:
            self.lock()
            raise
        return True

    def _read(self) -> dict[str, Any]:
        data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Неизвестный формат файла доступа к базе")
        if not isinstance(data.get("users"), list):
            raise ValueError("Повреждён файл доступа к базе")
        return data

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
