"""Resolve and validate paths owned by the portable application."""

from __future__ import annotations

import ctypes
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PortableLayoutError(RuntimeError):
    """Raised when a portable directory is incomplete or unsafe to use."""


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _windows_drive_is_remote(path: Path) -> bool:
    if os.name != "nt":
        return False
    resolved = str(path.resolve())
    if resolved.startswith(("\\\\", "//")):
        return True
    drive = Path(resolved).drive
    if not drive:
        return False
    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW  # type: ignore[attr-defined]
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    return int(get_drive_type(f"{drive}\\")) == 4  # DRIVE_REMOTE


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_dict(current, value)
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True, slots=True)
class PortablePaths:
    """Canonical locations relative to an unpacked portable release."""

    root: Path

    @classmethod
    def discover(cls, explicit_root: str | Path | None = None) -> PortablePaths:
        if explicit_root is not None:
            root = Path(explicit_root)
        elif getattr(sys, "frozen", False):
            root = Path(sys.executable).resolve().parent
        else:
            root = _source_root()
        return cls(root=root.resolve())

    @property
    def app(self) -> Path:
        return self.root / "app"

    @property
    def frontend(self) -> Path:
        return self.app / "frontend"

    @property
    def migrations(self) -> Path:
        packaged = self.app / "migrations"
        if packaged.is_dir():
            return packaged
        return self.root / "backend" / "migrations"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def webview2_runtime(self) -> Path:
        return self.runtime / "webview2"

    @property
    def webview2_runtime_mode(self) -> str:
        mode = self.settings().get("webview2", {}).get("runtime_mode", "fixed")
        if mode not in {"fixed", "evergreen"}:
            raise PortableLayoutError("webview2.runtime_mode must be fixed or evergreen")
        return str(mode)

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def resources(self) -> Path:
        return self.root / "resources"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    @property
    def database(self) -> Path:
        configured = self.settings().get("database", {}).get("path", "data/reporting.sqlite3")
        path = Path(str(configured))
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if _windows_drive_is_remote(resolved.parent):
            raise PortableLayoutError(
                "SQLite database cannot be stored on a network or UNC location"
            )
        return resolved

    @property
    def lock_file(self) -> Path:
        return self.data / "app.lock"

    @property
    def webview2_profile(self) -> Path:
        return self.temp / "webview2-profile"

    def settings(self) -> dict[str, Any]:
        defaults_path = self.config / "app.defaults.toml"
        try:
            with defaults_path.open("rb") as stream:
                settings = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PortableLayoutError(f"Cannot read app.defaults.toml: {exc}") from exc

        local_path = self.config / "app.local.toml"
        if local_path.is_file():
            try:
                with local_path.open("rb") as stream:
                    settings = _merge_dict(settings, tomllib.load(stream))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise PortableLayoutError(f"Cannot read app.local.toml: {exc}") from exc
        return settings

    def validate_offline_defaults(self) -> None:
        settings = self.settings()
        app = settings.get("app", {})
        logging_settings = settings.get("logging", {})
        if app.get("telemetry_enabled") is not False:
            raise PortableLayoutError("Telemetry must remain disabled in local mode")
        if app.get("external_network_access_enabled") is not False:
            raise PortableLayoutError("External network access must remain disabled in local mode")
        if logging_settings.get("technical_file_logs_enabled") is not False:
            raise PortableLayoutError("Technical file logs must remain disabled in local mode")

    def validate_release_layout(self, *, require_frontend: bool = True) -> None:
        if _windows_drive_is_remote(self.root):
            raise PortableLayoutError("Portable mode cannot run from a network or UNC location")

        required_files = [
            self.root / "VERSION",
            self.config / "app.defaults.toml",
            self.config / "logging.yaml",
            self.config / "reporting_rules.yaml",
            self.config / "roles.yaml",
        ]
        if require_frontend:
            required_files.append(self.frontend / "index.html")
        missing = [
            str(path.relative_to(self.root)) for path in required_files if not path.is_file()
        ]
        if missing:
            raise PortableLayoutError("Missing required portable files: " + ", ".join(missing))
        if not self.migrations.is_dir() or not tuple(
            self.migrations.glob("[0-9][0-9][0-9][0-9]_*.sql")
        ):
            raise PortableLayoutError("No sequential SQL migrations are available")
        if (
            require_frontend
            and self.webview2_runtime_mode == "fixed"
            and not (self.webview2_runtime / "msedgewebview2.exe").is_file()
        ):
            raise PortableLayoutError("WebView2 Fixed Runtime is missing from runtime/webview2")
        self.validate_offline_defaults()

    def prepare_writable_directories(self) -> None:
        directories = (
            self.data,
            self.database.parent,
            self.root / "attachments",
            self.root / "imports" / "inbox",
            self.root / "exports",
            self.backups,
            self.temp,
        )
        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise PortableLayoutError(
                    f"Cannot create writable directory {directory}: {exc}"
                ) from exc

        probe = self.temp / f".write-probe-{os.getpid()}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise PortableLayoutError(
                "The unpacked application directory is not writable; "
                "move it to a local writable folder"
            ) from exc
