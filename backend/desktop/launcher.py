"""Entry point for the offline PyWebView desktop application."""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import importlib
import logging
import mimetypes
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.desktop.application_self_test import (
    prepare_reference_window_test,
    prepare_subsidiary_window_test,
    run_application_self_test,
)
from backend.desktop.instance_lock import AlreadyRunningError, SingleInstanceLock
from backend.desktop.paths import PortableLayoutError, PortablePaths
from backend.desktop.window_health import monitor_window
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
        run_application_self_test(
            database, paths.migrations, paths.resources / "report-definitions"
        )


def _run_window(
    paths: PortablePaths, *, ui_self_test: bool = False, ui_reopen_test: bool = False
) -> None:
    # Windows registry MIME associations must not turn JS modules into text/plain.
    mimetypes.init()
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    webview: Any = importlib.import_module("webview")

    def new_bridge() -> SecureDesktopBridge:
        return SecureDesktopBridge(
            paths.database,
            migrations_directory=paths.migrations,
            definitions_directory=paths.resources / "report-definitions",
            inbox_directory=paths.imports_inbox,
            backups_directory=paths.backups,
            application_version=_version(paths),
        )

    bridge = new_bridge()

    if ui_self_test:
        setup = bridge.setup_access({"display_name": "Контроль окна", "pin": "window-test-pin"})
        if not setup["ok"] or bridge._application is None:
            raise RuntimeError("Не удалось подготовить защищённую проверку окна")
        prepare_reference_window_test(paths.database, paths.temp)
        prepare_subsidiary_window_test(bridge._application)
        bridge._lock()
        # A distinct instance proves automatic reopen, rather than setup identity reuse.
        bridge = new_bridge()

    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    webview.settings["REMOTE_DEBUGGING_PORT"] = None
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = (
        str(paths.webview2_runtime) if paths.webview2_runtime_mode == "fixed" else None
    )

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

    def open_excel_file() -> Path | None:
        selected = window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(paths.imports_inbox),
            allow_multiple=False,
            file_types=("Книга Excel (*.xlsx)",),
        )
        if selected is None:
            return None
        if isinstance(selected, (str, Path)):
            return Path(selected)
        return Path(selected[0]) if selected else None

    def save_excel_file(suggested_name: str) -> Path | None:
        selected = window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(paths.exports),
            save_filename=suggested_name,
            file_types=("Книга Excel (*.xlsx)",),
        )
        if selected is None:
            return None
        if isinstance(selected, (str, Path)):
            return Path(selected)
        return Path(selected[0]) if selected else None

    def save_pdf_file(suggested_name: str) -> Path | None:
        if ui_self_test:
            return paths.temp / suggested_name
        selected = window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(paths.exports),
            save_filename=suggested_name,
            file_types=("Документ PDF (*.pdf)",),
        )
        if not selected:
            return None
        return Path(selected) if isinstance(selected, (str, Path)) else Path(selected[0])

    bridge._configure_pdf_dialog(save_pdf_file)
    bridge._configure_excel_dialogs(
        open_file=open_excel_file,
        save_file=save_excel_file,
    )
    failures: list[str] = []

    def check_window() -> None:
        monitor_window(
            window,
            paths,
            ui_self_test=ui_self_test,
            ui_reopen_test=ui_reopen_test,
            failures=failures,
        )

    webview.start(
        check_window,
        gui="edgechromium",
        debug=False,
        http_server=True,
        private_mode=True,
        storage_path=str(paths.webview2_profile),
    )
    shutil.rmtree(paths.webview2_profile, ignore_errors=True)
    bridge._lock()
    if failures:
        raise RuntimeError(failures[0])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the portable reporting application")
    parser.add_argument(
        "--self-test", action="store_true", help="Validate release without opening UI"
    )
    ui_tests = parser.add_mutually_exclusive_group()
    ui_tests.add_argument(
        "--ui-self-test", action="store_true", help="Open and test the real window"
    )
    ui_tests.add_argument(
        "--ui-reopen-test",
        action="store_true",
        help="Reopen the existing UI self-test database in a separate process",
    )
    parser.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _disable_technical_logging()
    arguments = _build_parser().parse_args(argv)
    paths = PortablePaths.discover(arguments.root)
    try:
        if arguments.self_test:
            _self_test(paths)
            if arguments.self_test_report is not None:
                arguments.self_test_report.write_text("ok\n", encoding="utf-8")
            return 0
        paths.validate_release_layout(require_frontend=True)
        paths.prepare_writable_directories()
        lock = SingleInstanceLock(paths.root, paths.lock_file)
        with lock:
            _run_window(
                paths, ui_self_test=arguments.ui_self_test, ui_reopen_test=arguments.ui_reopen_test
            )
        if (
            arguments.ui_self_test or arguments.ui_reopen_test
        ) and arguments.self_test_report is not None:
            arguments.self_test_report.write_text("ok\n", encoding="utf-8")
        return 0
    except AlreadyRunningError as exc:
        _show_error(str(exc))
        return 2
    except (ImportError, OSError, PortableLayoutError, RuntimeError, sqlite3.Error) as exc:
        if arguments.self_test_report is not None:
            arguments.self_test_report.parent.mkdir(parents=True, exist_ok=True)
            arguments.self_test_report.write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )
        if not (arguments.ui_self_test or arguments.ui_reopen_test):
            _show_error(f"Программа не может быть запущена:\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
