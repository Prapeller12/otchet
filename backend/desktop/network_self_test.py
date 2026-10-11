"""Explicit, marker-protected packaged network acceptance checks.

This module never accepts an ordinary business share as a fixture. The caller
must provide a disposable marker, and all test data lives in a new UUID subtree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.desktop.paths import PortablePaths, _windows_drive_is_remote
from backend.infrastructure.network_snapshots import (
    NetworkSnapshotError,
    SharedWriterLock,
    SnapshotCoordinator,
)

DISPOSABLE_MARKER = ".otchet-network-self-test"
DISPOSABLE_CONTENT = "otchet-disposable-network-self-test-v1"


def _disposable_root(path: Path) -> Path:
    root = Path(path)
    marker = root / DISPOSABLE_MARKER
    if (
        root.is_symlink()
        or not root.is_dir()
        or marker.is_symlink()
        or not marker.is_file()
        or marker.stat().st_size != len(DISPOSABLE_CONTENT)
        or marker.read_text(encoding="ascii") != DISPOSABLE_CONTENT
    ):
        raise RuntimeError("Network self-test requires an exact disposable-share marker")
    return root.resolve()


def _write_report(path: Path, value: dict[str, Any]) -> None:
    path = Path(path).resolve()
    if _windows_drive_is_remote(path):
        raise RuntimeError("Network self-test reports must be local")
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def run_network_lock_probe(store_path: Path, report_path: Path) -> None:
    """Hold the actual OS/SMB lock in another packaged process, bounded to 30s."""
    store = _disposable_root(store_path)
    report = Path(report_path).resolve()
    release = report.with_suffix(".release")
    if report.exists() or release.exists():
        raise RuntimeError("Lock probe requires fresh local report and release paths")
    value: dict[str, Any] = {"ok": True, "pid": os.getpid(), "store": str(store)}
    with SharedWriterLock(store / "writer.lock", timeout_seconds=5):
        _write_report(report, {**value, "state": "locked"})
        deadline = time.monotonic() + 30
        while not release.exists():
            if time.monotonic() >= deadline:
                _write_report(report, {**value, "ok": False, "state": "release_timeout"})
                raise RuntimeError("Lock probe timed out waiting for its parent")
            time.sleep(0.05)
    _write_report(report, {**value, "state": "released"})


def _data(response: dict[str, Any]) -> Any:
    if response.get("ok") is not True:
        error = response.get("error", {})
        raise RuntimeError(f"Network acceptance failed: {error.get('code', 'invalid response')}")
    return response["data"]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current(store: Path) -> bytes:
    return (store / "CURRENT").read_bytes()


def _check_pair(store: Path, pin: str) -> None:
    pointer = json.loads(_current(store))
    directory = store / "generations" / pointer["generation_id"]
    manifest = directory / "manifest.json"
    _require(_sha(manifest) == pointer["manifest_sha256"], "CURRENT manifest digest mismatch")
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    _require(
        {"reporting.sqlite3", "reporting.sqlite3.keys.json"} <= files.keys(),
        "Encrypted database and credential file must publish together",
    )
    for name, digest in files.items():
        _require(_sha(directory / name) == digest, "Published pair failed its digest check")
    _require(
        (directory / "reporting.sqlite3").read_bytes()[:16] != b"SQLite format 3\x00",
        "Shared database is plaintext SQLite",
    )
    for file in store.rglob("*"):
        if file.is_file():
            _require(not file.name.endswith(".device.json"), "Device wrapping leaked to share")
            if file.suffix == ".json":
                _require(
                    pin not in file.read_text(encoding="utf-8"), "Personal code leaked to share"
                )


def _new_client(
    paths: PortablePaths, store: Path, local: Path, timeout: float
) -> tuple[SecureDesktopBridge, SnapshotCoordinator]:
    _require(not _windows_drive_is_remote(local), "Working database is on a network drive")
    coordinator = SnapshotCoordinator(store, local / "data/reporting.sqlite3", timeout)
    coordinator.prepare()
    bridge = SecureDesktopBridge(
        coordinator.database_path,
        migrations_directory=paths.migrations,
        definitions_directory=paths.resources / "report-definitions",
        backups_directory=local / "backups",
        inbox_directory=local / "imports/inbox",
        preferences_path=local / "config/ui-preferences.json",
        application_version="network-acceptance",
        device_protector=None,
        snapshot_coordinator=coordinator,
    )
    return bridge, coordinator


def _save_request(app: SecureDesktopBridge, auth: dict[str, str], quantity: str) -> dict[str, Any]:
    query = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}
    matrix = _data(app.get_report_matrix(query))
    cell = next(
        c
        for r in matrix["rows"]
        for c in r["cells"]
        if c["state"]["access"] == "editable"
        and str(c["coordinate"].get("operation_date", "")).startswith("2026-09")
    )
    return {
        **query,
        "authorization": auth,
        "confirmation": {"year": 2026, "month": 9},
        "base_revision": matrix["matrix_revision"],
        "base_global_revision": matrix["global_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": quantity}}
        ],
    }


def _cell_value(app: SecureDesktopBridge, request: dict[str, Any]) -> Any:
    query = {key: request[key] for key in ("report_type", "organization_id", "year")}
    coordinate = request["changes"][0]["coordinate"]
    matrix = _data(app.get_report_matrix(query))
    return next(
        c["value"] for r in matrix["rows"] for c in r["cells"] if c["coordinate"] == coordinate
    )


def _check_process_lock(store: Path, local: Path, paths: PortablePaths) -> None:
    report = local / "lock-child.json"
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "backend.desktop.launcher"]
    command += ["--network-lock-probe", str(store), "--self-test-report", str(report)]
    child = subprocess.Popen(
        command, cwd=paths.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 20
        while not report.exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Packaged lock child did not acknowledge readiness")
            time.sleep(0.05)
        ready = json.loads(report.read_text(encoding="utf-8"))
        _require(
            ready.get("ok") is True and ready.get("state") == "locked",
            "Child did not hold the shared writer lock",
        )
        lock_path = store / "writer.lock"
        try:
            with SharedWriterLock(lock_path, timeout_seconds=0.25):
                raise RuntimeError("Two processes acquired the same writer lock")
        except NetworkSnapshotError as error:
            _require(error.code == "NETWORK_BUSY", "Competing lock returned an incorrect outcome")
        _require(lock_path.exists(), "Timeout deleted the writer lock marker")
        report.with_suffix(".release").write_text("release\n", encoding="ascii")
        _require(child.wait(timeout=10) == 0, "Lock child did not release normally")
        released = json.loads(report.read_text(encoding="utf-8"))
        _require(released.get("state") == "released", "Child release was not acknowledged")
        with SharedWriterLock(lock_path, timeout_seconds=1) as lock:
            lock.assert_owned()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def run_network_self_test(
    paths: PortablePaths,
    share_root: Path,
    report_path: Path | None,
    *,
    lock_timeout_seconds: float = 15,
) -> dict[str, Any]:
    """Run encrypted two-client acceptance in a fresh disposable namespace."""
    root = _disposable_root(share_root)
    test_id = uuid4().hex
    store = root / f"acceptance-{test_id}"
    local = paths.temp / f"network-acceptance-{test_id}"
    _require(not _windows_drive_is_remote(local), "Acceptance local state must be local")
    store.mkdir()
    (store / DISPOSABLE_MARKER).write_text(DISPOSABLE_CONTENT, encoding="ascii")
    local.mkdir(parents=True)
    result: dict[str, Any] = {
        "ok": False,
        "test_id": test_id,
        "shared_test_directory": str(store),
        "local_test_directory": str(local),
        "os": os.name,
        "checks": [],
        "limitations": [
            "physical Windows 10/11 NOT_RUN",
            "separate Windows users/DPAPI NOT_RUN",
            "ten-client load NOT_RUN",
            "physical remote clients NOT_RUN",
        ],
    }
    checks: list[str] = result["checks"]
    try:
        first, coordinator = _new_client(paths, store, local / "client-a", lock_timeout_seconds)
        pin = f"synthetic-{test_id}"
        status = _data(
            first.setup_access(
                {
                    "display_name": "Network acceptance",
                    "pin": pin,
                    "operation_id": f"setup-{test_id}",
                    "base_global_revision": None,
                }
            )
        )
        auth = {"signer_id": status["current_user"]["id"], "pin": pin}
        _check_pair(store, pin)
        checks.append("encrypted_atomic_pair")
        second, second_coordinator = _new_client(
            paths, store, local / "client-b", lock_timeout_seconds
        )
        _data(second.unlock_access(auth))
        _require(
            coordinator.database_path != second_coordinator.database_path,
            "Clients do not have distinct local working databases",
        )
        stale = _save_request(second, auth, "102")
        request = _save_request(first, auth, "101")
        _data(first.save_report_cells(request))
        committed = _current(store)
        response = second.save_report_cells(stale)
        _require(
            response.get("ok") is False and response["error"]["code"] == "GLOBAL_REVISION_CONFLICT",
            "Stale client write was not rejected",
        )
        _require(_current(store) == committed, "Stale rejection changed authoritative state")
        _require(
            _cell_value(second, stale) != {"kind": "QUANTITY", "quantity": "101"},
            "Background status silently replaced the accepted snapshot",
        )
        checks.append("two_clients_stale_cas")
        replay = _data(first.save_report_cells(request))
        _require(replay.get("idempotent_replay") is True, "Replay lacks durable receipt")
        _require(_current(store) == committed, "Replay published another generation")
        recovered = _data(
            second.get_network_operation_result(
                {
                    "operation_id": request["idempotency_key"],
                    "authorization": auth,
                }
            )
        )
        _require(
            recovered.get("found") is True and recovered["data_state"] == "committed",
            "Another client cannot verify the committed receipt",
        )
        checks.append("idempotent_receipt_replay")
        blocked = second.refresh_network_data(
            {"has_unsaved_changes": True, "has_unknown_outcome": False}
        )
        _require(
            blocked.get("ok") is False and blocked["error"]["code"] == "NETWORK_REFRESH_BLOCKED",
            "Refresh discarded an unsaved draft",
        )
        _data(
            second.refresh_network_data(
                {"has_unsaved_changes": False, "has_unknown_outcome": False}
            )
        )
        _require(
            _cell_value(second, request) == {"kind": "QUANTITY", "quantity": "101"},
            "Explicit refresh did not load the committed encrypted pair",
        )
        checks.append("explicit_refresh_guard")

        def fail(stage: str, target: str) -> None:
            if stage == target:
                raise OSError("Injected acceptance publication interruption")

        before = _save_request(first, auth, "103")
        coordinator._fault = lambda stage: fail(stage, "before_current_replace")
        response = first.save_report_cells(before)
        _require(
            response.get("ok") is False and response["error"]["code"] == "NETWORK_UNAVAILABLE",
            "Known prepublication failure did not report cancellation",
        )
        _require(
            _current(store) == committed and coordinator.pending_operation_id is None,
            "Known failure published data or retained an ambiguous result",
        )
        _require(
            _cell_value(first, request) == {"kind": "QUANTITY", "quantity": "101"},
            "Known failure did not roll back the local domain state",
        )
        checks.append("prepublication_rollback")
        uncertain = _save_request(first, auth, "104")
        coordinator._fault = lambda stage: fail(stage, "before_ack")
        response = first.save_report_cells(uncertain)
        _require(
            response.get("ok") is False and response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN",
            "Lost acknowledgment was represented as known failure or success",
        )
        _require(
            coordinator.pending_operation_id == uncertain["idempotency_key"],
            "Unknown result lost its recoverable operation identity",
        )
        coordinator._fault = lambda _stage: None
        frozen = first.save_report_cells(_save_request(first, auth, "105"))
        _require(
            frozen.get("ok") is False and frozen["error"]["code"] == "SAVE_OUTCOME_UNKNOWN",
            "An unknown result permitted another dependent write",
        )
        recovery = _data(
            first.get_network_operation_result(
                {
                    "operation_id": uncertain["idempotency_key"],
                    "authorization": auth,
                }
            )
        )
        _require(
            recovery.get("found") is True and recovery["data_state"] == "committed",
            "Published operation was not recovered from authoritative storage",
        )
        _data(
            first.refresh_network_data({"has_unsaved_changes": False, "has_unknown_outcome": False})
        )
        _require(
            _cell_value(first, request) == {"kind": "QUANTITY", "quantity": "104"},
            "Recovered committed value could not be read",
        )
        _check_pair(store, pin)
        checks.append("unknown_outcome_recovery")
        draft = _save_request(first, auth, "106")
        draft["presentation"] = {"title": "Acceptance unified draft"}
        coordinator._fault = lambda stage: fail(stage, "before_ack")
        response = first.save_report_draft(draft)
        _require(
            response.get("ok") is False and response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN",
            "Unified draft lost acknowledgment was not reported as unknown",
        )
        draft_pointer = _current(store)
        coordinator._fault = lambda _stage: None
        recovered_draft = _data(
            first.get_report_draft_save_result(
                {
                    **{
                        key: draft[key]
                        for key in (
                            "report_type",
                            "organization_id",
                            "year",
                            "confirmation",
                            "idempotency_key",
                        )
                    },
                    "authorization": auth,
                }
            )
        )
        _require(
            recovered_draft.get("data_state") == "committed"
            and isinstance(recovered_draft.get("operation_revision"), str)
            and isinstance(recovered_draft.get("global_revision"), str)
            and recovered_draft.get("presentation", {}).get("title") == "Acceptance unified draft",
            "Unified draft recovery lost its committed result contract",
        )
        _require(_current(store) == draft_pointer, "Draft recovery repeated a shared write")
        checks.append("unified_draft_unknown_recovery")
        _check_process_lock(store, local, paths)
        checks.append("two_process_writer_lock")
        result["ok"] = True
        if report_path is not None:
            _write_report(report_path, result)
        return result
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        if report_path is not None:
            _write_report(report_path, result)
        raise
