"""Portable presentation preferences, separate from protected business data."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


def validate_preferences(payload: object) -> dict[str, bool]:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"field_hints_enabled"}
        or type(payload["field_hints_enabled"]) is not bool
    ):
        raise ValueError("Настройка подсказок должна содержать только логическое значение")
    return {"field_hints_enabled": payload["field_hints_enabled"]}


class UiPreferences:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._mutex = threading.RLock()
        self._memory = {"field_hints_enabled": True}

    def read(self) -> dict[str, bool]:
        with self._mutex:
            if self._path is None:
                return dict(self._memory)
            try:
                return validate_preferences(json.loads(self._path.read_text(encoding="utf-8")))
            except (OSError, ValueError, UnicodeError):
                # Missing/corrupt preferences must not prevent opening the application.
                return {"field_hints_enabled": True}

    def save(self, payload: object) -> dict[str, bool]:
        preferences = validate_preferences(payload)
        with self._mutex:
            if self._path is None:
                self._memory = preferences
                return dict(preferences)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            pending: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self._path.parent,
                    prefix=".ui-preferences-",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    pending = Path(stream.name)
                    stream.write(json.dumps(preferences, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending, self._path)
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)
            return dict(preferences)
