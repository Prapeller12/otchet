"""Fail-closed static verification of an unpacked Windows x64 release."""

from __future__ import annotations

import argparse
import re
import struct
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.release_manifest import ManifestError, verify_manifest
else:
    try:
        from scripts.release_manifest import ManifestError, verify_manifest
    except ModuleNotFoundError:  # Direct execution adds scripts/ to sys.path.
        from release_manifest import ManifestError, verify_manifest

REQUIRED_DIRECTORIES = (
    "app/backend",
    "app/frontend",
    "app/migrations",
    "runtime",
    "config",
    "resources",
    "data",
    "attachments",
    "imports/inbox",
    "exports",
    "backups",
    "temp",
    "docs",
)
REQUIRED_FILES = (
    "ReportingSystem.exe",
    "start.cmd",
    "TESTING.txt",
    "VERSION",
    "release-manifest.json",
    "app/frontend/index.html",
    "config/app.defaults.toml",
    "config/logging.yaml",
    "config/reporting_rules.yaml",
    "config/roles.yaml",
)
NETWORK_REFERENCE = re.compile(rb"(?:https?|wss?)://[^\s\"'`<>]+", re.IGNORECASE)
NETWORK_API = re.compile(
    rb"(?:\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\s*\(|\bEventSource\s*\(|"
    rb"\bsendBeacon\s*\()"
)
INERT_FRONTEND_URIS = frozenset(
    {
        b"http://www.w3.org/1998/Math/MathML",
        b"http://www.w3.org/1999/xhtml",
        b"http://www.w3.org/1999/xlink",
        b"http://www.w3.org/2000/svg",
        b"http://www.w3.org/XML/1998/namespace",
        b"https://reactjs.org/docs/error-decoder.html?invariant=",
    }
)


class ReleaseVerificationError(RuntimeError):
    """Raised for an incomplete or unsafe portable release."""


def verify_frontend_network_policy(frontend_root: Path) -> None:
    """Reject executable browser-network APIs and non-inert external URI literals."""

    for path in frontend_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".css",
            ".html",
            ".js",
            ".json",
            ".map",
        }:
            continue
        content = path.read_bytes()
        if NETWORK_API.search(content):
            raise ReleaseVerificationError(f"Browser network API found in frontend asset: {path}")
        references = set(NETWORK_REFERENCE.findall(content))
        unexpected = sorted(references - INERT_FRONTEND_URIS)
        if unexpected:
            rendered = ", ".join(value.decode("utf-8", errors="replace") for value in unexpected)
            raise ReleaseVerificationError(
                f"External network URL found in frontend asset {path}: {rendered}"
            )


def _pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ReleaseVerificationError(f"Not a Windows PE executable: {path}")
        stream.seek(0x3C)
        offset_bytes = stream.read(4)
        if len(offset_bytes) != 4:
            raise ReleaseVerificationError(f"Truncated Windows PE executable: {path}")
        offset = struct.unpack("<I", offset_bytes)[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise ReleaseVerificationError(f"Invalid Windows PE signature: {path}")
        machine_bytes = stream.read(2)
        if len(machine_bytes) != 2:
            raise ReleaseVerificationError(f"Missing Windows PE architecture: {path}")
        return int(struct.unpack("<H", machine_bytes)[0])


def verify_release(root: Path) -> None:
    root = root.resolve()
    if not root.is_dir():
        raise ReleaseVerificationError(f"Release directory does not exist: {root}")
    try:
        with (root / "config" / "app.defaults.toml").open("rb") as stream:
            defaults = tomllib.load(stream)
        local_path = root / "config" / "app.local.toml"
        local = {}
        if local_path.is_file():
            with local_path.open("rb") as stream:
                local = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseVerificationError(f"Invalid app configuration: {exc}") from exc
    runtime_mode = local.get("webview2", {}).get(
        "runtime_mode", defaults.get("webview2", {}).get("runtime_mode", "fixed")
    )
    if runtime_mode not in {"fixed", "evergreen"}:
        raise ReleaseVerificationError("Unknown WebView2 runtime mode")

    required_directories = list(REQUIRED_DIRECTORIES)
    required_files = list(REQUIRED_FILES)
    if runtime_mode == "fixed":
        required_directories.append("runtime/webview2")
        required_files.append("runtime/webview2/msedgewebview2.exe")
    missing_directories = [path for path in required_directories if not (root / path).is_dir()]
    missing_files = [path for path in required_files if not (root / path).is_file()]
    if missing_directories or missing_files:
        raise ReleaseVerificationError(
            "Portable layout is incomplete "
            f"(directories={missing_directories}, files={missing_files})"
        )
    migrations = tuple((root / "app" / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise ReleaseVerificationError("Portable release contains no sequential SQL migrations")

    python_dlls = tuple((root / "runtime").glob("python3*.dll"))
    if not python_dlls or not (root / "runtime" / "base_library.zip").is_file():
        raise ReleaseVerificationError("PyInstaller embedded Python runtime is incomplete")
    if _pe_machine(root / "ReportingSystem.exe") != 0x8664:
        raise ReleaseVerificationError("ReportingSystem.exe is not Windows x64")
    if (
        runtime_mode == "fixed"
        and _pe_machine(root / "runtime" / "webview2" / "msedgewebview2.exe") != 0x8664
    ):
        raise ReleaseVerificationError("WebView2 Fixed Runtime is not Windows x64")

    app = defaults.get("app", {})
    logging_settings = defaults.get("logging", {})
    if app.get("telemetry_enabled") is not False:
        raise ReleaseVerificationError("Telemetry is not disabled by default")
    if app.get("external_network_access_enabled") is not False:
        raise ReleaseVerificationError("External network access is not disabled by default")
    if logging_settings.get("technical_file_logs_enabled") is not False:
        raise ReleaseVerificationError("Technical file logs are not disabled by default")

    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReleaseVerificationError(f"Symbolic link is forbidden: {path}")
        if path.is_file() and path.suffix.lower() == ".log":
            raise ReleaseVerificationError(f"Technical log file is packaged: {path}")
    verify_frontend_network_policy(root / "app" / "frontend")
    try:
        verify_manifest(root)
    except ManifestError as exc:
        raise ReleaseVerificationError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        verify_release(arguments.root)
    except ReleaseVerificationError as exc:
        print(f"Portable release verification failed: {exc}")
        return 1
    print("Portable release verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
