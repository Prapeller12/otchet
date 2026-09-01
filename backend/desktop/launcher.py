"""Entry point for the offline PyWebView desktop application."""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import importlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.desktop.database_bootstrap import backup_and_migrate
from backend.desktop.instance_lock import AlreadyRunningError, SingleInstanceLock
from backend.desktop.paths import PortableLayoutError, PortablePaths
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite


def _disable_technical_logging() -> None:
    faulthandler.disable()
    logging.shutdown()
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.NullHandler())
    logging.disable(logging.CRITICAL)


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None, message, "Система отчётности", 0x10
        )
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def _version(paths: PortablePaths) -> str:
    return (paths.root / "VERSION").read_text(encoding="utf-8").strip()


def _self_test(paths: PortablePaths) -> None:
    paths.validate_release_layout(require_frontend=True)
    paths.prepare_writable_directories()
    with tempfile.TemporaryDirectory(dir=paths.temp, prefix="self-test-") as directory:
        database = Path(directory) / "self-test.sqlite3"
        connection = connect_sqlite(database)
        try:
            apply_migrations(connection, paths.migrations)
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise sqlite3.DatabaseError("Self-test database integrity check failed")
        finally:
            connection.close()


def _run_window(paths: PortablePaths) -> None:
    webview: Any = importlib.import_module("webview")
    bridge = WorkingReferenceApplicationBridge(
        paths.database,
        migrations_directory=paths.migrations,
        definitions_directory=paths.resources / "report-definitions",
    )

    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    webview.settings["REMOTE_DEBUGGING_PORT"] = None
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(paths.webview2_runtime)

    shutil.rmtree(paths.webview2_profile, ignore_errors=True)
    window = webview.create_window(
        "Система производственной отчётности",
        url=str((paths.frontend / "index.html").resolve()),
        js_api=bridge,
        width=1440,
        height=900,
        min_size=(1024, 700),
        text_select=True,
    )
    webview.start(
        gui="edgechromium",
        debug=False,
        http_server=True,
        private_mode=True,
        storage_path=str(paths.webview2_profile),
    )
    del window
    shutil.rmtree(paths.webview2_profile, ignore_errors=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the portable reporting application")
    parser.add_argument(
        "--self-test", action="store_true", help="Validate release without opening UI"
    )
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _disable_technical_logging()
    arguments = _build_parser().parse_args(argv)
    paths = PortablePaths.discover(arguments.root)
    try:
        if arguments.self_test:
            _self_test(paths)
            return 0
        paths.validate_release_layout(require_frontend=True)
        paths.prepare_writable_directories()
        lock = SingleInstanceLock(paths.root, paths.lock_file)
        with lock:
            backup_and_migrate(paths.database, paths.migrations, paths.backups, _version(paths))
            _run_window(paths)
        return 0
    except AlreadyRunningError as exc:
        _show_error(str(exc))
        return 2
    except (ImportError, OSError, PortableLayoutError, RuntimeError, sqlite3.Error) as exc:
        _show_error(f"Программа не может быть запущена:\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
