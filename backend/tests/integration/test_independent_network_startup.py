"""Independent startup acceptance; native readiness never invokes business APIs."""

from __future__ import annotations

import importlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.desktop import launcher
from backend.desktop.paths import PortablePaths
from backend.infrastructure.database.encrypted_sqlite import forget_database_key
from backend.infrastructure.network_snapshots import SnapshotCoordinator
from backend.infrastructure.windows_data_protection import DeviceProtector

ROOT = Path(__file__).resolve().parents[3]
PIN = "independent-startup-acceptance-pin"
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}


class _ForbiddenBusinessProbe(BaseException):
    """Do not let the readiness loop swallow a forbidden API invocation."""


class _NativeBridge:
    def __init__(self) -> None:
        self.locked = False

    def _configure_recovery_dialog(self, callback: object) -> None:
        pass

    def _configure_local_store_dialog(self, callback: object) -> None:
        pass

    def _configure_pdf_dialog(self, callback: object) -> None:
        pass

    def _configure_excel_dialogs(self, **callbacks: object) -> None:
        pass

    def get_access_status(self, *args: object) -> dict[str, Any]:
        raise _ForbiddenBusinessProbe("Native readiness called access maintenance")

    def get_network_status(self, *args: object) -> dict[str, Any]:
        raise _ForbiddenBusinessProbe("Native readiness called a bridge status API")

    def _lock(self) -> None:
        self.locked = True


class _Window:
    def __init__(self, marker: Path, *, settles: bool) -> None:
        self.marker = marker
        self.settles = settles
        self.polls = 0
        self.destroyed = False

    def evaluate_js(self, expression: str) -> bool:
        # The renderer remains unready during its first two probes. This test
        # exercises launcher callbacks and marker ordering, rather than JS text.
        self.polls += 1
        assert not self.marker.exists()
        return self.settles and self.polls >= 3

    def destroy(self) -> None:
        self.destroyed = True


@pytest.mark.parametrize("settles", [True, False])
def test_native_ready_marker_follows_renderer_without_bridge_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settles: bool
) -> None:
    paths = PortablePaths(ROOT, writable_root=tmp_path / "state")
    marker = tmp_path / "ready"
    bridge = _NativeBridge()
    window = _Window(marker, settles=settles)
    monitored: list[bool] = []
    fake_webview = SimpleNamespace(
        settings={},
        create_window=lambda *args, **kwargs: window,
        start=lambda callback, **kwargs: callback(),
    )
    monkeypatch.setattr(launcher, "SecureDesktopBridge", lambda *args, **kwargs: bridge)
    monkeypatch.setattr(importlib, "import_module", lambda name: fake_webview)
    monkeypatch.setattr(launcher, "_initial_window_geometry", lambda: {})
    monkeypatch.setattr("time.sleep", lambda duration: None)

    def monitor(*args: object, **kwargs: object) -> None:
        assert marker.read_text(encoding="utf-8") == "opaque-ready-token"
        monitored.append(True)

    monkeypatch.setattr(launcher, "monitor_window", monitor)
    if settles:
        launcher._run_window(paths, ready_file=marker, ready_token="opaque-ready-token")
        assert window.polls == 3 and monitored == [True]
        assert not window.destroyed
    else:
        with pytest.raises(RuntimeError, match="Не открылся интерфейс"):
            launcher._run_window(paths, ready_file=marker, ready_token="opaque-ready-token")
        assert window.polls == 900 and window.destroyed
        assert not marker.exists() and not monitored
    assert bridge.locked


class _AccountProtector:
    """In-memory test account cipher; this is not a production DPAPI fallback."""

    def __init__(self) -> None:
        self.cipher = ChaCha20Poly1305(os.urandom(32))

    def protect(self, value: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.cipher.encrypt(nonce, value, b"independent-startup-account")

    def unprotect(self, value: bytes) -> bytes:
        return self.cipher.decrypt(value[:12], value[12:], b"independent-startup-account")


def _data(response: dict[str, Any]) -> dict[str, Any]:
    assert response["ok"], response
    return cast(dict[str, Any], response["data"])


def _make(
    share: Path, local: Path, protector: DeviceProtector | None
) -> tuple[SecureDesktopBridge, SnapshotCoordinator]:
    coordinator = SnapshotCoordinator(share, local / "reporting.sqlite3", 0.5)
    coordinator.prepare()
    bridge = SecureDesktopBridge(
        coordinator.database_path,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=local / "backups",
        device_protector=protector,
        snapshot_coordinator=coordinator,
    )
    return bridge, coordinator


def _setup(bridge: SecureDesktopBridge) -> dict[str, str]:
    status = _data(
        bridge.setup_access(
            {
                "display_name": "Independent startup acceptance",
                "pin": PIN,
                "operation_id": uuid4().hex,
                "base_global_revision": None,
            }
        )
    )
    return {"signer_id": status["current_user"]["id"], "pin": PIN}


def _packet(bridge: SecureDesktopBridge, auth: dict[str, str]) -> dict[str, Any]:
    matrix = _data(bridge.get_report_matrix(QUERY))
    cell = next(
        cell
        for row in matrix["rows"]
        for cell in row["cells"]
        if cell["state"]["access"] == "editable"
        and str(cell["coordinate"].get("operation_date", "")).startswith("2026-09")
    )
    return {
        **QUERY,
        "authorization": auth,
        "confirmation": {"year": 2026, "month": 9},
        "base_revision": matrix["matrix_revision"],
        "base_global_revision": matrix["global_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": "27"}}
        ],
    }


def test_warm_network_startup_opens_anonymous_reading_without_publishing(
    tmp_path: Path,
) -> None:
    protector = _AccountProtector()
    local, share = tmp_path / "client", tmp_path / "share"
    first, _ = _make(share, local, protector)
    auth = _setup(first)
    first._lock()
    reopened, coordinator = _make(share, local, protector)
    forget_database_key(reopened._database)
    baseline = (share / "CURRENT").read_bytes()
    device = reopened._vault.device_path.read_bytes()

    status = _data(reopened.get_access_status())
    assert status["state"] == "ready" and status["current_user"] is None
    assert status["automatic_open_available"]
    assert reopened._automatic_open_attempted and reopened._application is not None
    assert reopened._current_user is None and reopened._administrator is None
    assert _data(reopened.get_report_matrix(QUERY))["rows"]
    assert coordinator.pending_operation_id is None
    assert _data(reopened.get_network_status())["state"] == "ready"
    request = _packet(reopened, {**auth, "pin": "wrong-fresh-access-code"})
    denied = reopened.save_report_cells(request)
    assert not denied["ok"] and denied["error"]["code"] == "ACCESS_DENIED"
    assert (share / "CURRENT").read_bytes() == baseline
    assert reopened._vault.device_path.read_bytes() == device


def test_genuine_interrupted_startup_remains_unknown_despite_cached_device_key(
    tmp_path: Path,
) -> None:
    protector = _AccountProtector()
    local, share = tmp_path / "client", tmp_path / "share"
    first, coordinator = _make(share, local, protector)
    auth = _setup(first)
    request = _packet(first, auth)

    def lose_ack(stage: str) -> None:
        if stage == "before_ack":
            raise OSError("Independent acknowledgement-loss fixture")

    coordinator._fault = lose_ack
    failed = first.save_report_cells(request)
    assert not failed["ok"] and failed["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    first._lock()
    reopened, restarted = _make(share, local, protector)
    forget_database_key(reopened._database)
    baseline = (share / "CURRENT").read_bytes()
    journal = restarted._pending_path.read_bytes()

    status = _data(reopened.get_access_status())
    network = _data(reopened.get_network_status())
    assert status["state"] == "locked" and status["automatic_open_available"]
    assert network["state"] == "outcome_unknown" and not network["can_write"]
    assert network["pending_operation_id"] == request["idempotency_key"]
    assert not reopened._automatic_open_attempted
    assert reopened._application is None and reopened._vault._key is None
    rejected = reopened.save_report_cells({**request, "idempotency_key": uuid4().hex})
    assert not rejected["ok"] and rejected["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    assert restarted._pending_path.read_bytes() == journal
    assert (share / "CURRENT").read_bytes() == baseline


def test_status_waits_for_live_startup_maintenance_instead_of_phantom_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share = tmp_path / "share"
    first, _ = _make(share, tmp_path / "first", None)
    _setup(first)
    bridge, coordinator = _make(share, tmp_path / "fresh", None)
    baseline = (share / "CURRENT").read_bytes()
    maintenance_paused = threading.Event()
    release_maintenance = threading.Event()
    query_started = threading.Event()
    query_completed = threading.Event()
    original_dirty = coordinator._dirty
    access_results: list[dict[str, Any]] = []
    status_results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def paused_dirty(files: dict[Path, str]) -> bool:
        maintenance_paused.set()
        assert release_maintenance.wait(5)
        return original_dirty(files)

    def run(function: Callable[[], dict[str, Any]], results: list[dict[str, Any]]) -> None:
        try:
            results.append(function())
        except BaseException as error:
            errors.append(error)

    def query() -> dict[str, Any]:
        query_started.set()
        try:
            return bridge.get_network_status()
        finally:
            query_completed.set()

    monkeypatch.setattr(coordinator, "_dirty", paused_dirty)
    worker = threading.Thread(target=run, args=(bridge.get_access_status, access_results))
    reader = threading.Thread(target=run, args=(query, status_results))
    worker.start()
    try:
        assert maintenance_paused.wait(5)
        assert coordinator.pending_operation_id is not None
        reader.start()
        assert query_started.wait(5)
        assert not query_completed.wait(0.1)
    finally:
        release_maintenance.set()
        worker.join(5)
        if reader.ident is not None:
            reader.join(5)
    assert not errors and not worker.is_alive() and not reader.is_alive()
    assert _data(access_results[0])["state"] == "locked"
    final = _data(status_results[0])
    assert final["state"] == "ready" and final["pending_operation_id"] is None
    assert bridge._automatic_open_attempted
    assert (share / "CURRENT").read_bytes() == baseline
