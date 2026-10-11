"""Author verification of the snapshot facade; physical SMB is a separate gate."""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

import backend.infrastructure.network_snapshots as snapshots
from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.network_snapshots import NetworkSnapshotError, SnapshotCoordinator
from backend.tests.integration.test_automatic_open_dev30 import TestAccountProtector

ROOT = Path(__file__).resolve().parents[3]
PIN = "synthetic-network-test-code"


def client(root: Path, share: Path) -> tuple[SecureDesktopBridge, SnapshotCoordinator]:
    coordinator = SnapshotCoordinator(share, root / "data/reporting.sqlite3", 0.2)
    coordinator.prepare()
    facade = SecureDesktopBridge(
        coordinator.database_path,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        snapshot_coordinator=coordinator,
        device_protector=None,
    )
    return facade, coordinator


def setup(app: SecureDesktopBridge) -> dict[str, str]:
    result = app.setup_access(
        {
            "display_name": "Synthetic network administrator",
            "pin": PIN,
            "operation_id": "initial-setup",
            "base_global_revision": None,
        }
    )
    assert result["ok"], result
    return {"signer_id": result["data"]["current_user"]["id"], "pin": PIN}


def write_request(
    coordinator: SnapshotCoordinator, auth: dict[str, str], operation: str
) -> dict[str, Any]:
    return {
        "name": "Synthetic subsidiary",
        "authorization": auth,
        "operation_id": operation,
        "base_global_revision": coordinator.loaded_global_revision,
    }


def image(coordinator: SnapshotCoordinator) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in coordinator._state_files() if p.exists()}


def fail_at(coordinator: SnapshotCoordinator, target: str) -> None:
    def fault(stage: str) -> None:
        if stage == target:
            raise OSError("Synthetic fault")

    coordinator._fault = fault


@pytest.mark.parametrize(
    "stage",
    [
        "before_generation_copy",
        "after_generation_copy",
        "before_current_replace",
    ],
)
def test_first_setup_known_failure_is_empty_and_retryable(tmp_path: Path, stage: str) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    fail_at(coordinator, stage)
    result = app.setup_access(
        {
            "display_name": "Synthetic network administrator",
            "pin": PIN,
            "operation_id": "initial-setup",
            "base_global_revision": None,
        }
    )
    assert not result["ok"] and result["error"]["code"] == "NETWORK_UNAVAILABLE"
    assert not coordinator.database_path.exists()
    assert not (tmp_path / "shared/CURRENT").exists()
    coordinator._fault = lambda _stage: None
    setup(app)
    assert coordinator.loaded_global_revision is not None


def test_stable_reads_cas_and_current_role_recheck(tmp_path: Path) -> None:
    first, a = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(first)
    second, b = client(tmp_path / "b", tmp_path / "shared")
    assert second.unlock_access(auth)["ok"]
    base = b.loaded_global_revision
    request = write_request(a, auth, "create")
    assert first.create_organization(request)["ok"]
    matrix = second.get_report_matrix(
        {"report_type": "HEAD_SITE", "organization_id": "1", "year": 2027}
    )
    assert matrix["ok"] and matrix["data"]["global_revision"] == base
    assert second.get_network_status({})["data"]["state"] == "stale"
    before = image(b)
    conflict = second.create_organization(write_request(b, auth, "stale-create"))
    assert conflict["error"]["code"] == "GLOBAL_REVISION_CONFLICT"
    assert image(b) == before
    replay = first.create_organization(request)
    assert replay["ok"] and replay["data"]["idempotent_replay"]
    assert second.refresh_network_data(
        {"has_unsaved_changes": False, "has_unknown_outcome": False}
    )["ok"]
    assert b.loaded_global_revision == a.loaded_global_revision


def test_failed_authorized_business_command_publishes_only_audit(tmp_path: Path) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)
    old_revision = coordinator.loaded_global_revision
    with closing(connect_sqlite(coordinator.database_path)) as connection:
        old_orgs = connection.execute("SELECT * FROM organizations").fetchall()
        old_audits = connection.execute("SELECT * FROM report_access_events").fetchall()
    request = {**write_request(coordinator, auth, "invalid-create"), "name": ""}
    result = app.create_organization(request)
    assert not result["ok"]
    assert coordinator.loaded_global_revision != old_revision
    with closing(connect_sqlite(coordinator.database_path)) as connection:
        assert connection.execute("SELECT * FROM organizations").fetchall() == old_orgs
        assert (
            len(connection.execute("SELECT * FROM report_access_events").fetchall())
            == len(old_audits) + 1
        )
    recovered = app.get_network_operation_result(
        {"operation_id": "invalid-create", "authorization": auth}
    )
    assert recovered["data"]["found"] and recovered["data"]["data_state"] == "not_committed"
    replay = app.create_organization(request)
    assert not replay["ok"]
    assert replay["error"]["global_revision"] == result["error"]["global_revision"]


@pytest.mark.parametrize("stage", ["after_current_replace", "before_ack"])
def test_lost_ack_restores_local_pair_and_recovers_authoritative_receipt(
    tmp_path: Path, stage: str
) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)
    before = image(coordinator)
    base = coordinator.loaded_global_revision
    fail_at(coordinator, stage)
    request = write_request(coordinator, auth, "lost-ack")
    result = app.create_organization(request)
    assert result["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    assert image(coordinator) == before and coordinator.loaded_global_revision == base
    assert coordinator.pending_operation_id == "lost-ack"
    blocked = app.create_organization(write_request(coordinator, auth, "other"))
    assert blocked["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    coordinator._fault = lambda _stage: None
    outcome = app.get_network_operation_result({"operation_id": "lost-ack", "authorization": auth})
    assert outcome["data"]["found"] and outcome["data"]["data_state"] == "committed"
    assert coordinator.pending_operation_id is None
    assert coordinator.loaded_global_revision == base
    assert app.refresh_network_data({"has_unsaved_changes": False, "has_unknown_outcome": False})[
        "ok"
    ]
    replay = app.create_organization(request)
    assert replay["ok"] and replay["data"]["idempotent_replay"]
    with closing(connect_sqlite(coordinator.database_path)) as connection:
        assert connection.execute("SELECT count(*) FROM organizations").fetchone() == (2,)


@pytest.mark.parametrize(
    "stage",
    [
        "after_database_activation",
        "after_keys_activation",
        "before_local_state_replace",
    ],
)
def test_failed_explicit_refresh_restores_every_local_file(tmp_path: Path, stage: str) -> None:
    first, a = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(first)
    second, b = client(tmp_path / "b", tmp_path / "shared")
    assert second.unlock_access(auth)["ok"]
    before, base = image(b), b.loaded_global_revision
    assert first.create_organization(write_request(a, auth, "create"))["ok"]
    fail_at(b, stage)
    result = second.refresh_network_data(
        {"has_unsaved_changes": False, "has_unknown_outcome": False}
    )
    assert not result["ok"]
    assert image(b) == before and b.loaded_global_revision == base
    with closing(connect_sqlite(b.database_path)) as connection:
        assert connection.execute("SELECT count(*) FROM organizations").fetchone() == (1,)


def test_missing_current_existing_store_never_resurrects_local_copy(tmp_path: Path) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    setup(app)
    (tmp_path / "shared/CURRENT").unlink()
    with pytest.raises(NetworkSnapshotError, match="CURRENT"):
        coordinator.prepare()


def test_wrong_code_has_no_effect_and_no_plaintext_secrets_are_shared(tmp_path: Path) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)
    before = image(coordinator)
    request = write_request(coordinator, {**auth, "pin": "wrong-code"}, "wrong-code")
    assert not app.create_organization(request)["ok"]
    assert image(coordinator) == before
    for file in (tmp_path / "shared").rglob("*"):
        if file.is_file():
            assert ".device.json" not in file.name
            assert PIN.encode() not in file.read_bytes()


def test_explicit_import_infers_admin_and_preserves_source(tmp_path: Path) -> None:
    source = SecureDesktopBridge(
        tmp_path / "source/data/reporting.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        device_protector=None,
    )
    assert source.setup_access({"display_name": "Synthetic source", "pin": PIN})["ok"]
    original = source._database.read_bytes()
    keys = source._vault.path.read_bytes()
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    app._configure_local_store_dialog(lambda: source._database)
    result = app.import_local_store(
        {
            "authorization": {"pin": PIN},
            "operation_id": "import",
            "base_global_revision": None,
        }
    )
    assert result["ok"], result
    assert source._database.read_bytes() == original and source._vault.path.read_bytes() == keys
    assert coordinator.loaded_global_revision is not None


def test_failed_restoration_reopen_repairs_with_positive_prepublication_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)
    current = (tmp_path / "shared/CURRENT").read_bytes()
    fail_at(coordinator, "before_generation_copy")

    def unavailable(*_args: Any) -> None:
        raise OSError("Synthetic failed local restore")

    monkeypatch.setattr(coordinator, "_restore", unavailable)
    result = app.create_organization(write_request(coordinator, auth, "failed-restore"))
    assert result["error"]["code"] == "NETWORK_LOCAL_RECOVERY_REQUIRED"
    assert result["network"]["can_write"] is False
    journal = json.loads(coordinator._pending_path.read_text())
    checkpoint = coordinator.database_path.parent / journal["checkpoint_directory"]
    assert checkpoint.is_dir() and journal["publication_attempted"] is False
    reopened, repaired = client(tmp_path / "a", tmp_path / "shared")
    assert not checkpoint.exists() and repaired.pending_operation_id is None
    assert (tmp_path / "shared/CURRENT").read_bytes() == current
    outcome = reopened.get_network_operation_result(
        {"operation_id": "failed-restore", "authorization": auth}
    )
    assert outcome["ok"] and outcome["data"]["data_state"] == "not_committed"
    assert outcome["data"]["proof"] == "verified_unpublished_local_journal"
    assert reopened._application is None and reopened._current_user is None


@pytest.mark.parametrize("device_key", [False, True])
def test_pending_manager_restart_requires_keyholder_then_exact_owner(
    tmp_path: Path, device_key: bool
) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    protector = TestAccountProtector() if device_key else None
    app._vault._device_protector = protector
    auth = setup(app)
    assert app.authenticate_access(
        {
            **auth,
            "operation_id": "admin-open",
            "base_global_revision": coordinator.loaded_global_revision,
        }
    )["ok"]
    manager_pin = "synthetic-manager-network-pin"
    manager = app.create_report_signer(
        {
            "display_name": "Synthetic manager",
            "role": "project_manager",
            "pin": manager_pin,
            "operation_id": "manager-create",
            "base_global_revision": coordinator.loaded_global_revision,
        }
    )
    assert manager["ok"], manager
    owner = {"signer_id": manager["data"]["id"], "pin": manager_pin}
    query = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}
    matrix = app.get_report_matrix(query)["data"]
    cell = next(
        c
        for r in matrix["rows"]
        for c in r["cells"]
        if c["state"]["access"] == "editable"
        and c["coordinate"].get("operation_date", "").startswith("2026-09")
    )
    request = {
        **query,
        "authorization": owner,
        "operation_id": "manager-save",
        "idempotency_key": "manager-save",
        "base_revision": matrix["matrix_revision"],
        "base_global_revision": coordinator.loaded_global_revision,
        "confirmation": {"year": 2026, "month": 9},
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": "8"}}
        ],
    }
    fail_at(coordinator, "before_ack")
    assert app.save_report_cells(request)["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    current = (tmp_path / "shared/CURRENT").read_bytes()
    reopened, recovered = client(tmp_path / "a", tmp_path / "shared")
    reopened._vault._device_protector = protector
    users = reopened.get_network_recovery_users({})["data"]["users"]
    assert {p["id"] for p in users} == {owner["signer_id"], auth["signer_id"]}
    assert next(p for p in users if p["id"] == owner["signer_id"])["role"] == "project_manager"
    query_result = {"operation_id": "manager-save", "authorization": owner}
    if not device_key:
        blocked = reopened.get_network_operation_result(query_result)
        assert blocked["error"]["code"] == "NETWORK_RECOVERY_UNLOCK_REQUIRED"
        assert reopened.unlock_network_recovery({"authorization": auth})["ok"]
    denied = reopened.get_network_operation_result(
        {"operation_id": "manager-save", "authorization": auth}
    )
    assert denied["error"]["code"] == "ACCESS_DENIED"
    outcome = reopened.get_network_operation_result(query_result)
    assert outcome["ok"] and outcome["data"]["data_state"] == "committed"
    assert recovered.pending_operation_id is None
    assert (
        reopened._application is None
        and reopened._current_user is None
        and reopened._administrator is None
    )
    assert (tmp_path / "shared/CURRENT").read_bytes() == current


def test_maintenance_unlock_receipt_replay_checks_fresh_pin(tmp_path: Path) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)

    def interrupted_lifecycle(_request: dict[str, Any]) -> dict[str, Any]:
        app._vault.prepare_lifecycle("0" * 32, app._vault._read())
        return {"ok": True, "data": {"synthetic": True}}

    coordinator.execute(
        "synthetic-lifecycle",
        {
            "operation_id": "fixture-lifecycle",
            "base_global_revision": coordinator.loaded_global_revision,
        },
        interrupted_lifecycle,
        key=lambda: app._vault._key,
        restore_memory=lambda: None,
        authorize=lambda _db: auth["signer_id"],
    )
    second, copy = client(tmp_path / "b", tmp_path / "shared")
    request = {
        **auth,
        "operation_id": "recover-lifecycle",
        "base_global_revision": copy.loaded_global_revision,
    }
    assert second.unlock_access(request)["ok"]
    current = (tmp_path / "shared/CURRENT").read_bytes()
    denied = second.unlock_access({**request, "pin": "incorrect-pin"})
    assert not denied["ok"] and denied["error"]["code"] == "ACCESS_DENIED"
    assert (tmp_path / "shared/CURRENT").read_bytes() == current
    replay = second.unlock_access(request)
    assert replay["ok"] and replay["data"]["idempotent_replay"]


def test_current_postreplace_flush_failure_is_unknown_until_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)
    baseline = image(coordinator)
    flush = snapshots._flush

    def fail_current(path: Path) -> None:
        if path == tmp_path / "shared/CURRENT":
            raise OSError("Synthetic SMB FLUSH failed after rename")
        flush(path)

    monkeypatch.setattr(snapshots, "_flush", fail_current)
    result = app.create_organization(write_request(coordinator, auth, "current-flush"))
    assert result["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    assert image(coordinator) == baseline and coordinator.pending_operation_id == "current-flush"
    monkeypatch.setattr(snapshots, "_flush", flush)
    recovered = app.get_network_operation_result(
        {"operation_id": "current-flush", "authorization": auth}
    )
    assert recovered["ok"] and recovered["data"]["data_state"] == "committed"


def test_activation_failed_restore_remains_blocked_until_authority_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, a = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(first)
    second, b = client(tmp_path / "b", tmp_path / "shared")
    assert second.unlock_access(auth)["ok"]
    assert first.create_organization(write_request(a, auth, "publish"))["ok"]
    fail_at(b, "after_database_activation")

    def failed_restore(*_args: Any) -> None:
        raise OSError("Synthetic activation restore failed")

    monkeypatch.setattr(b, "_restore", failed_restore)
    result = second.refresh_network_data(
        {"has_unsaved_changes": False, "has_unknown_outcome": False}
    )
    assert result["error"]["code"] == "NETWORK_LOCAL_RECOVERY_REQUIRED"
    assert result["network"]["can_write"] is False
    operation_id = b.pending_operation_id
    assert operation_id is not None
    reopened, repaired = client(tmp_path / "b", tmp_path / "shared")
    assert repaired.loaded_global_revision == a.loaded_global_revision
    outcome = reopened.get_network_operation_result(
        {"operation_id": operation_id, "authorization": auth}
    )
    assert outcome["ok"] and outcome["data"]["data_state"] == "not_committed"
    assert repaired.pending_operation_id is None


def test_pdf_cleanup_refusal_preserves_confirmed_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, coordinator = client(tmp_path / "a", tmp_path / "shared")
    auth = setup(app)
    destination = tmp_path / "confirmed.pdf"
    app._configure_pdf_dialog(lambda _name: destination)
    assert app._application is not None
    app._application.configure_pdf_dialog(lambda _name: destination)
    query = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}
    matrix = app.get_report_matrix(query)["data"]
    baseline = coordinator.loaded_global_revision
    unlink_path, unlink_os = Path.unlink, os.unlink
    refused = set()

    def refuse_destination_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path.parent == destination.parent and path.name.endswith(".pdf.pending"):
            refused.add("destination")
            raise PermissionError("Synthetic antivirus denied staging cleanup after ACK")
        unlink_path(path, missing_ok=missing_ok)

    def refuse_temporary_cleanup(path: Any, *, dir_fd: int | None = None) -> None:
        if Path(path).name == "report.pdf":
            refused.add("temporary")
            raise PermissionError("Synthetic antivirus denied temporary PDF cleanup after ACK")
        if dir_fd is None:
            unlink_os(path)
        else:
            unlink_os(path, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "unlink", refuse_destination_cleanup)
    monkeypatch.setattr(os, "unlink", refuse_temporary_cleanup)
    result = app.export_pdf(
        {
            **query,
            "month": 9,
            "expected_revision": matrix["matrix_revision"],
            "authorization": auth,
            "operation_id": "pdf-cleanup-refusal",
            "base_global_revision": baseline,
        }
    )
    assert result["ok"], result
    assert refused == {"destination", "temporary"}
    assert result["data"]["export_state"] == "complete"
    assert result["data"]["verification"]["status"] == "VERIFIED"
    assert destination.read_bytes().startswith(b"%PDF-")
    assert result["data"]["global_revision"] == coordinator.loaded_global_revision != baseline
    assert result["network"]["can_write"] and coordinator.pending_operation_id is None
    receipt = app.get_network_operation_result(
        {"operation_id": "pdf-cleanup-refusal", "authorization": auth}
    )
    assert receipt["ok"] and receipt["data"]["data_state"] == "committed"
    assert receipt["data"]["global_revision"] == result["data"]["global_revision"]
