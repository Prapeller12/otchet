"""Independent acceptance of network authority, using real encrypted databases."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

import backend.infrastructure.network_snapshots as snapshots
from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.desktop.network_self_test import (
    DISPOSABLE_CONTENT,
    DISPOSABLE_MARKER,
    run_network_self_test,
)
from backend.desktop.paths import PortablePaths
from backend.infrastructure.database.encrypted_sqlite import forget_database_key
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.network_snapshots import NetworkSnapshotError, SnapshotCoordinator

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}
PIN = "independent-network-acceptance-pin"


def ok(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


def make(store: Path, local: Path) -> tuple[SecureDesktopBridge, SnapshotCoordinator]:
    coordinator = SnapshotCoordinator(store, local / "reporting.sqlite3", 0.5)
    coordinator.prepare()
    app = SecureDesktopBridge(
        coordinator.database_path,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=local / "backups",
        device_protector=None,
        snapshot_coordinator=coordinator,
    )
    return app, coordinator


@dataclass
class Workspace:
    store: Path
    first: SecureDesktopBridge
    coordinator: SnapshotCoordinator
    second: SecureDesktopBridge
    second_coordinator: SnapshotCoordinator
    auth: dict[str, str]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    store = tmp_path / "share"
    first, coordinator = make(store, tmp_path / "client-a")
    status = ok(
        first.setup_access(
            {
                "display_name": "Independent acceptance",
                "pin": PIN,
                "operation_id": uuid4().hex,
                "base_global_revision": None,
            }
        )
    )
    auth = {"signer_id": status["current_user"]["id"], "pin": PIN}
    second, second_coordinator = make(store, tmp_path / "client-b")
    ok(second.unlock_access(auth))
    return Workspace(store, first, coordinator, second, second_coordinator, auth)


def packet(app: SecureDesktopBridge, auth: dict[str, str], quantity: str = "21") -> dict[str, Any]:
    matrix = ok(app.get_report_matrix(QUERY))
    cell = next(
        c
        for r in matrix["rows"]
        for c in r["cells"]
        if c["state"]["access"] == "editable"
        and str(c["coordinate"].get("operation_date", "")).startswith("2026-09")
    )
    return {
        **QUERY,
        "authorization": auth,
        "confirmation": {"year": 2026, "month": 9},
        "base_revision": matrix["matrix_revision"],
        "base_global_revision": matrix["global_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": quantity}}
        ],
    }


def value(app: SecureDesktopBridge, request: dict[str, Any]) -> Any:
    coordinate = request["changes"][0]["coordinate"]
    return next(
        c["value"]
        for r in ok(app.get_report_matrix(QUERY))["rows"]
        for c in r["cells"]
        if c["coordinate"] == coordinate
    )


def counts(app: SecureDesktopBridge) -> tuple[int, ...]:
    with closing(connect_sqlite(app._database)) as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "report_fact_revisions",
                "audit_events",
                "report_access_events",
                "idempotency_records",
                "report_signatures",
                "network_operation_receipts",
            )
        )


def pointer(store: Path) -> bytes:
    return (store / "CURRENT").read_bytes()


def fail_at(target: str) -> Any:
    def failure(stage: str) -> None:
        if stage == target:
            raise OSError("Independent injected publication fault")

    return failure


def test_real_pair_is_complete_encrypted_and_has_no_local_device_key(workspace: Workspace) -> None:
    current = json.loads(pointer(workspace.store))
    directory = workspace.store / "generations" / current["generation_id"]
    manifest = directory / "manifest.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == current["manifest_sha256"]
    files = json.loads(manifest.read_text())["files"]
    assert {"reporting.sqlite3", "reporting.sqlite3.keys.json"} <= files.keys()
    assert not (directory / "reporting.sqlite3").read_bytes().startswith(b"SQLite format 3\x00")
    for name, digest in files.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
    assert not list(workspace.store.rglob("*.device.json"))
    assert all(PIN not in p.read_text() for p in workspace.store.rglob("*.json"))
    assert workspace.first._database != workspace.second._database
    assert (
        workspace.coordinator.loaded_global_revision
        == workspace.second_coordinator.loaded_global_revision
    )


@pytest.mark.parametrize("method", ["save_report_cells", "save_report_draft"])
def test_stale_packet_never_overwrites_current_and_poll_keeps_snapshot(
    workspace: Workspace, method: str
) -> None:
    stale = packet(workspace.second, workspace.auth, "22")
    before = value(workspace.second, stale)
    before_counts = counts(workspace.second)
    fresh = packet(workspace.first, workspace.auth, "23")
    if method == "save_report_draft":
        stale["presentation"] = {"title": "Stale title"}
        fresh["presentation"] = {"title": "Committed title"}
    ok(getattr(workspace.first, method)(fresh))
    committed = pointer(workspace.store)
    response = getattr(workspace.second, method)(stale)
    assert response["ok"] is False
    assert response["error"]["code"] == "GLOBAL_REVISION_CONFLICT"
    assert pointer(workspace.store) == committed
    assert ok(workspace.second.get_network_status())["state"] == "stale"
    assert value(workspace.second, stale) == before
    assert counts(workspace.second) == before_counts


def test_replay_and_conflicting_reuse_have_no_second_effect(workspace: Workspace) -> None:
    request = packet(workspace.first, workspace.auth)
    ok(workspace.first.save_report_cells(request))
    committed = pointer(workspace.store)
    original_counts = counts(workspace.first)
    assert ok(workspace.first.save_report_cells(request))["idempotent_replay"] is True
    assert counts(workspace.first) == original_counts
    assert pointer(workspace.store) == committed
    changed = {
        **request,
        "changes": [{**request["changes"][0], "value": {"kind": "QUANTITY", "quantity": "99"}}],
    }
    response = workspace.first.save_report_cells(changed)
    assert not response["ok"]
    assert response["error"]["code"] == "NETWORK_OPERATION_CONFLICT"
    assert counts(workspace.first) == original_counts
    assert pointer(workspace.store) == committed


@pytest.mark.parametrize(
    "stage",
    [
        "before_generation_copy",
        "after_generation_copy",
        "before_current_replace",
    ],
)
def test_known_publication_failure_rolls_back_domain_audit_keys_and_memory(
    workspace: Workspace, stage: str
) -> None:
    app = workspace.first
    request = packet(app, workspace.auth)
    original_value = value(app, request)
    original_counts = counts(app)
    original_pointer = pointer(workspace.store)
    key_file = Path(str(app._database) + ".keys.json")
    original_keys = key_file.read_bytes()
    workspace.coordinator._fault = fail_at(stage)
    response = app.save_report_cells(request)
    assert not response["ok"]
    assert response["error"]["code"] == "NETWORK_UNAVAILABLE"
    assert pointer(workspace.store) == original_pointer
    assert counts(app) == original_counts
    assert value(app, request) == original_value
    assert key_file.read_bytes() == original_keys
    assert workspace.coordinator.pending_operation_id is None
    workspace.coordinator._fault = lambda _stage: None
    ok(app.save_report_cells(request))
    assert value(app, request) == {"kind": "QUANTITY", "quantity": "21"}


@pytest.mark.parametrize(
    "stage",
    [
        "after_current_replace",
        "before_ack",
    ],
)
def test_unknown_outcome_survives_reopen_and_is_recovered_without_repeat_write(
    workspace: Workspace, tmp_path: Path, stage: str
) -> None:
    request = packet(workspace.first, workspace.auth, "41")
    workspace.coordinator._fault = fail_at(stage)
    response = workspace.first.save_report_cells(request)
    assert not response["ok"]
    assert response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    committed = pointer(workspace.store)
    blocked = workspace.first.save_report_cells(packet(workspace.first, workspace.auth, "42"))
    assert blocked["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    missing = ok(
        workspace.first.get_network_operation_result(
            {
                "operation_id": "missing-operation",
                "authorization": workspace.auth,
            }
        )
    )
    assert missing["found"] is False and missing["data_state"] == "unknown"
    assert workspace.coordinator.pending_operation_id == request["idempotency_key"]
    reopened, coordinator = make(workspace.store, workspace.first._database.parent)
    assert coordinator.pending_operation_id == request["idempotency_key"]
    # The pending state intentionally blocks normal unlock maintenance. The
    # recovery read uses a fresh PIN to open only its authoritative local copy.
    recovered = ok(
        reopened.get_network_operation_result(
            {
                "operation_id": request["idempotency_key"],
                "authorization": workspace.auth,
            }
        )
    )
    assert recovered["found"] is True and recovered["data_state"] == "committed"
    assert coordinator.pending_operation_id is None
    assert pointer(workspace.store) == committed
    ok(reopened.unlock_access(workspace.auth))
    assert value(reopened, request) == {"kind": "QUANTITY", "quantity": "41"}


def test_missing_or_corrupt_current_never_resurrects_a_local_copy(workspace: Workspace) -> None:
    current = workspace.store / "CURRENT"
    original = current.read_bytes()
    current.unlink()
    with pytest.raises(NetworkSnapshotError, match="CURRENT"):
        workspace.coordinator.prepare()
    assert not current.exists()
    current.write_text("broken", encoding="ascii")
    with pytest.raises(NetworkSnapshotError):
        workspace.second_coordinator.prepare()
    assert current.read_text() == "broken"
    current.write_bytes(original)


def test_acknowledged_save_with_local_activation_failure_reports_committed_warning(
    workspace: Workspace,
) -> None:
    request = packet(workspace.first, workspace.auth, "43")
    old_value = value(workspace.first, request)
    old_revision = workspace.coordinator.loaded_global_revision
    workspace.coordinator._fault = fail_at("before_local_activation")
    saved = ok(workspace.first.save_report_cells(request))
    assert saved["local_activation_warning"]
    assert saved["global_revision"] != old_revision
    assert workspace.coordinator.pending_operation_id is None
    assert ok(workspace.first.get_network_status())["state"] == "stale"
    assert value(workspace.first, request) == old_value
    committed = pointer(workspace.store)
    recovered = ok(
        workspace.second.get_network_operation_result(
            {
                "operation_id": request["idempotency_key"],
                "authorization": workspace.auth,
            }
        )
    )
    assert recovered["data_state"] == "committed"
    assert pointer(workspace.store) == committed
    workspace.coordinator._fault = lambda _stage: None
    ok(
        workspace.first.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    assert value(workspace.first, request) == {"kind": "QUANTITY", "quantity": "43"}


def test_unified_draft_ack_loss_recovers_the_exact_frontend_save_contract(
    workspace: Workspace,
) -> None:
    request = packet(workspace.first, workspace.auth, "44")
    request["presentation"] = {"title": "Recovered unified draft"}
    workspace.coordinator._fault = fail_at("before_ack")
    response = workspace.first.save_report_draft(request)
    assert not response["ok"]
    assert response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    committed = pointer(workspace.store)
    workspace.coordinator._fault = lambda _stage: None
    recovery_request = {
        key: request[key]
        for key in (
            "report_type",
            "organization_id",
            "year",
            "confirmation",
            "idempotency_key",
            "base_global_revision",
            "authorization",
        )
    }
    recovered = ok(workspace.first.get_report_draft_save_result(recovery_request))
    assert recovered["data_state"] == "committed"
    assert isinstance(recovered["operation_revision"], str)
    assert isinstance(recovered["current_revision"], str)
    assert isinstance(recovered["idempotent_replay"], bool)
    assert recovered["presentation"]["title"] == "Recovered unified draft"
    assert recovered["verification_state"] in {"verified", "verification_error", "pending", "stale"}
    assert recovered["backup_state"] in {"complete", "post_backup_warning", "pending"}
    assert recovered["cells"][0]["value"] == {"kind": "QUANTITY", "quantity": "44"}
    assert isinstance(recovered["global_revision"], str)
    assert workspace.coordinator.pending_operation_id is None
    assert pointer(workspace.store) == committed


def test_audit_only_failed_draft_recovery_is_known_not_committed(workspace: Workspace) -> None:
    request = packet(workspace.first, workspace.auth, "45")
    request["presentation"] = {"title": "x" * 201}
    before_value = value(workspace.first, request)
    before_counts = counts(workspace.first)
    workspace.coordinator._fault = fail_at("before_ack")
    response = workspace.first.save_report_draft(request)
    assert not response["ok"]
    assert response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    committed = pointer(workspace.store)
    workspace.coordinator._fault = lambda _stage: None
    recovery = ok(
        workspace.first.get_report_draft_save_result(
            {
                **{
                    key: request[key]
                    for key in (
                        "report_type",
                        "organization_id",
                        "year",
                        "confirmation",
                        "idempotency_key",
                    )
                },
                "authorization": workspace.auth,
            }
        )
    )
    assert recovery["data_state"] == "not_committed"
    assert isinstance(recovery["error"]["code"], str)
    assert isinstance(recovery["error"]["message"], str)
    assert workspace.coordinator.pending_operation_id is None
    assert pointer(workspace.store) == committed
    ok(
        workspace.first.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    after_counts = counts(workspace.first)
    assert after_counts[0:2] == before_counts[0:2]
    assert after_counts[3:5] == before_counts[3:5]
    assert after_counts[2] == before_counts[2] + 1
    assert after_counts[5] == before_counts[5] + 1
    assert value(workspace.first, request) == before_value


def create_profile(workspace: Workspace, role: str, pin: str) -> dict[str, Any]:
    ok(
        workspace.first.authenticate_access(
            {
                **workspace.auth,
                "operation_id": uuid4().hex,
                "base_global_revision": workspace.coordinator.loaded_global_revision,
            }
        )
    )
    return cast(
        dict[str, Any],
        ok(
            workspace.first.create_report_signer(
                {
                    "display_name": f"Independent {role}",
                    "pin": pin,
                    "role": role,
                    "operation_id": uuid4().hex,
                    "base_global_revision": workspace.coordinator.loaded_global_revision,
                }
            )
        ),
    )


def test_revoked_client_cannot_write_from_old_snapshot(workspace: Workspace) -> None:
    reviewer_pin = "independent-reviewer-pin"
    reviewer = create_profile(workspace, "reviewer", reviewer_pin)
    auth = {"signer_id": reviewer["id"], "pin": reviewer_pin}
    ok(
        workspace.second.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    stale_request = packet(workspace.second, auth)
    ok(
        workspace.first.manage_report_signer(
            {
                "action": "revoke",
                "signer_id": reviewer["id"],
                "authorization": workspace.auth,
                "operation_id": uuid4().hex,
                "base_global_revision": workspace.coordinator.loaded_global_revision,
            }
        )
    )
    committed = pointer(workspace.store)
    response = workspace.second.save_report_cells(stale_request)
    assert not response["ok"]
    assert response["error"]["code"] == "ACCESS_DENIED"
    assert pointer(workspace.store) == committed
    assert (
        workspace.second_coordinator.loaded_global_revision == stale_request["base_global_revision"]
    )


def test_receipt_owner_and_admin_role_checked_against_current_authority(
    workspace: Workspace,
) -> None:
    manager_pin = "independent-manager-pin"
    manager = create_profile(workspace, "project_manager", manager_pin)
    manager_auth = {"signer_id": manager["id"], "pin": manager_pin}
    request = packet(workspace.first, manager_auth)
    ok(workspace.first.save_report_cells(request))
    committed = pointer(workspace.store)
    denied = workspace.first.create_organization(
        {
            "name": "Forbidden organization",
            "kind": "SUBSIDIARY",
            "code": "forbidden",
            "authorization": manager_auth,
            "operation_id": uuid4().hex,
            "base_global_revision": workspace.coordinator.loaded_global_revision,
        }
    )
    assert not denied["ok"]
    assert denied["error"]["code"] == "ACCESS_DENIED"
    assert pointer(workspace.store) == committed
    other_owner = workspace.second.get_network_operation_result(
        {
            "operation_id": request["idempotency_key"],
            "authorization": workspace.auth,
        }
    )
    assert not other_owner["ok"]
    assert other_owner["error"]["code"] == "ACCESS_DENIED"
    owner = ok(
        workspace.first.get_network_operation_result(
            {
                "operation_id": request["idempotency_key"],
                "authorization": manager_auth,
            }
        )
    )
    assert owner["data_state"] == "committed"
    assert pointer(workspace.store) == committed


def test_wrong_code_does_not_publish_attempt_audit_or_receipt(workspace: Workspace) -> None:
    request = packet(workspace.first, {**workspace.auth, "pin": "wrong-personal-code"})
    before_pointer = pointer(workspace.store)
    before_counts = counts(workspace.first)
    response = workspace.first.save_report_cells(request)
    assert not response["ok"]
    assert response["error"]["code"] == "ACCESS_DENIED"
    assert counts(workspace.first) == before_counts
    assert pointer(workspace.store) == before_pointer


def test_same_packet_unknown_retry_reads_receipt_without_another_effect(
    workspace: Workspace,
) -> None:
    request = packet(workspace.first, workspace.auth, "46")
    request["presentation"] = {"title": "Single acknowledged draft"}
    workspace.coordinator._fault = fail_at("before_ack")
    response = workspace.first.save_report_draft(request)
    assert response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    committed = pointer(workspace.store)
    workspace.coordinator._fault = lambda _stage: None
    recovery = ok(
        workspace.second.get_network_operation_result(
            {
                "operation_id": request["idempotency_key"],
                "authorization": workspace.auth,
            }
        )
    )
    assert recovery["data_state"] == "committed"
    ok(
        workspace.second.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    after_counts = counts(workspace.second)
    replay = ok(workspace.first.save_report_draft(request))
    assert replay["data_state"] == "committed" and replay["idempotent_replay"] is True
    assert workspace.coordinator.pending_operation_id is None
    assert pointer(workspace.store) == committed
    ok(
        workspace.second.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    assert counts(workspace.second) == after_counts


def test_ambiguous_replace_without_receipt_never_reexecutes_packet(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = snapshots._atomic_json

    def disconnected(path: Path, content: Any) -> None:
        if path == workspace.store / "CURRENT":
            raise OSError("Unknown SMB replace completion")
        original(path, content)

    monkeypatch.setattr(snapshots, "_atomic_json", disconnected)
    request = packet(workspace.first, workspace.auth, "47")
    before_pointer = pointer(workspace.store)
    before_counts = counts(workspace.first)
    response = workspace.first.save_report_cells(request)
    assert response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    monkeypatch.setattr(snapshots, "_atomic_json", original)
    retry = workspace.first.save_report_cells(request)
    assert not retry["ok"]
    assert retry["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    assert pointer(workspace.store) == before_pointer
    assert counts(workspace.first) == before_counts
    assert workspace.coordinator.pending_operation_id == request["idempotency_key"]


def test_pending_reopen_can_list_public_access_profiles_without_unlock_or_write(
    workspace: Workspace,
) -> None:
    request = packet(workspace.first, workspace.auth)
    workspace.coordinator._fault = fail_at("before_ack")
    response = workspace.first.save_report_cells(request)
    assert response["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    committed = pointer(workspace.store)
    reopened, coordinator = make(workspace.store, workspace.first._database.parent)
    status = ok(reopened.get_access_status())
    assert status["state"] == "locked"
    assert workspace.auth["signer_id"] in {profile["id"] for profile in status["users"]}
    assert reopened._application is None and reopened._vault._key is None
    assert coordinator.pending_operation_id == request["idempotency_key"]
    assert pointer(workspace.store) == committed


def test_manager_restart_without_dpapi_uses_keyholder_then_exact_owner_readonly(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewer_pin = "recovery-reviewer-acceptance-pin"
    reviewer = create_profile(workspace, "reviewer", reviewer_pin)
    reviewer_auth = {"signer_id": reviewer["id"], "pin": reviewer_pin}
    manager_pin = "recovery-manager-acceptance-pin"
    manager = create_profile(workspace, "project_manager", manager_pin)
    owner = {"signer_id": manager["id"], "pin": manager_pin}
    request = packet(workspace.first, owner, "51")
    request["presentation"] = {"title": "Manager recovery acceptance"}
    workspace.coordinator._fault = fail_at("before_ack")
    assert workspace.first.save_report_draft(request)["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    committed = pointer(workspace.store)
    ok(
        workspace.second.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    before_counts = counts(workspace.second)
    reopened, coordinator = make(workspace.store, workspace.first._database.parent)

    def forbidden_workspace_change(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Recovery attempted ordinary workspace opening or migration")

    monkeypatch.setattr(reopened, "_migrate_after_unlock", forbidden_workspace_change)
    monkeypatch.setattr(reopened, "_open", forbidden_workspace_change)
    forget_database_key(
        reopened._database
    )  # Simulate a fresh process, not our test's key registry.
    users = ok(reopened.get_network_recovery_users({}))["users"]
    assert {p["id"] for p in users} == {
        workspace.auth["signer_id"],
        reviewer["id"],
        manager["id"],
    }
    assert all(set(p) == {"id", "display_name", "role"} for p in users)
    query = {"operation_id": request["idempotency_key"], "authorization": owner}
    blocked = reopened.get_network_operation_result(query)
    assert blocked["error"]["code"] == "NETWORK_RECOVERY_UNLOCK_REQUIRED"
    wrong_holder = reopened.unlock_network_recovery(
        {
            "authorization": {**reviewer_auth, "pin": "incorrect-keyholder-pin"},
        }
    )
    assert wrong_holder["error"]["code"] == "ACCESS_DENIED"
    assert reopened._network_recovery_key is None
    assert (
        ok(reopened.unlock_network_recovery({"authorization": reviewer_auth}))["unlocked"] is True
    )
    assert reopened._application is None and reopened._vault._key is None
    assert reopened._current_user is None and reopened._administrator is None
    wrong_owner = reopened.get_network_operation_result(
        {
            **query,
            "authorization": {**owner, "pin": "incorrect-owner-pin"},
        }
    )
    assert wrong_owner["error"]["code"] == "ACCESS_DENIED"
    other_owner = reopened.get_network_operation_result({**query, "authorization": workspace.auth})
    assert other_owner["error"]["code"] == "ACCESS_DENIED"
    assert coordinator.pending_operation_id == request["idempotency_key"]
    recovery = ok(reopened.get_network_operation_result(query))
    assert recovery["data_state"] == "committed"
    assert recovery["result"]["data"]["presentation"]["title"] == "Manager recovery acceptance"
    assert coordinator.pending_operation_id is None
    assert reopened._application is None and reopened._current_user is None
    assert reopened._administrator is None and reopened._vault._key is None
    assert pointer(workspace.store) == committed
    assert counts(workspace.second) == before_counts
    assert not list(reopened._database.parent.glob("network-read-*"))


def test_revoked_keyholder_cannot_unlock_pending_manager_recovery(workspace: Workspace) -> None:
    reviewer_pin = "revoked-recovery-reviewer-pin"
    reviewer = create_profile(workspace, "reviewer", reviewer_pin)
    manager_pin = "revoked-recovery-manager-pin"
    manager = create_profile(workspace, "project_manager", manager_pin)
    owner = {"signer_id": manager["id"], "pin": manager_pin}
    request = packet(workspace.first, owner, "52")
    workspace.coordinator._fault = fail_at("before_ack")
    assert workspace.first.save_report_cells(request)["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    ok(
        workspace.second.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    ok(
        workspace.second.manage_report_signer(
            {
                "action": "revoke",
                "signer_id": reviewer["id"],
                "authorization": workspace.auth,
                "operation_id": uuid4().hex,
                "base_global_revision": workspace.second_coordinator.loaded_global_revision,
            }
        )
    )
    committed = pointer(workspace.store)
    before_counts = counts(workspace.second)
    reopened, coordinator = make(workspace.store, workspace.first._database.parent)
    forget_database_key(reopened._database)
    users = ok(reopened.get_network_recovery_users({}))["users"]
    assert reviewer["id"] not in {p["id"] for p in users}
    denied = reopened.unlock_network_recovery(
        {
            "authorization": {"signer_id": reviewer["id"], "pin": reviewer_pin},
        }
    )
    assert denied["error"]["code"] == "ACCESS_DENIED"
    assert reopened._network_recovery_key is None
    assert coordinator.pending_operation_id == request["idempotency_key"]
    assert reopened._application is None and reopened._current_user is None
    assert pointer(workspace.store) == committed
    assert counts(workspace.second) == before_counts


def test_maintenance_unlock_receipt_replay_does_not_accept_wrong_fresh_code(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewer_pin = "maintenance-reviewer-old-pin"
    reviewer = create_profile(workspace, "reviewer", reviewer_pin)

    # A real lifecycle commit with a failed final credential-file write creates
    # a recoverable journal. Opening it legitimately changes the local pair and
    # produces an unlock receipt, unlike an ordinary read-only unlock.
    def full_disk(*_args: Any) -> None:
        raise OSError("Credential finalization interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(workspace.first._vault, "_write", full_disk)
        change = ok(
            workspace.first.manage_report_signer(
                {
                    "action": "change_pin",
                    "signer_id": reviewer["id"],
                    "current_pin": reviewer_pin,
                    "new_pin": "maintenance-reviewer-new-pin",
                    "authorization": workspace.auth,
                    "operation_id": uuid4().hex,
                    "base_global_revision": workspace.coordinator.loaded_global_revision,
                }
            )
        )
    assert change["access_warning"]
    assert workspace.first._vault.lifecycle_path.exists()
    third, coordinator = make(workspace.store, workspace.store.parent / "maintenance-client")
    request = {
        **workspace.auth,
        "operation_id": uuid4().hex,
        "base_global_revision": coordinator.loaded_global_revision,
    }
    ok(third.unlock_access(request))
    committed = pointer(workspace.store)
    before_counts = counts(third)
    denied = third.unlock_access({**request, "pin": "wrong-unlock-code"})
    assert denied["error"]["code"] == "ACCESS_DENIED"
    assert pointer(workspace.store) == committed
    assert counts(third) == before_counts
    replay = ok(third.unlock_access(request))
    assert replay["idempotent_replay"] is True
    assert counts(third) == before_counts
    assert pointer(workspace.store) == committed


def test_failed_local_restoration_freezes_following_writes(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = packet(workspace.first, workspace.auth)
    second_request = {**request, "idempotency_key": uuid4().hex}
    committed = pointer(workspace.store)

    def disk_failure(*_args: Any) -> None:
        raise OSError("Local checkpoint cannot be restored")

    workspace.coordinator._fault = fail_at("before_generation_copy")
    monkeypatch.setattr(workspace.coordinator, "_restore", disk_failure)
    response = workspace.first.save_report_cells(request)
    assert not response["ok"]
    assert response["error"]["code"] == "NETWORK_LOCAL_RECOVERY_REQUIRED"
    assert response["network"]["can_write"] is False
    assert workspace.coordinator.pending_operation_id == request["idempotency_key"]
    blocked = workspace.first.save_report_cells(second_request)
    assert not blocked["ok"]
    assert blocked["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    assert pointer(workspace.store) == committed


@pytest.mark.parametrize(
    "stage",
    [
        "after_database_activation",
        "after_keys_activation",
        "before_local_state_replace",
    ],
)
def test_failed_refresh_preserves_previous_local_database_key_pair(
    workspace: Workspace,
    stage: str,
) -> None:
    request = packet(workspace.first, workspace.auth, "48")
    ok(workspace.first.save_report_cells(request))
    local_db = workspace.second._database
    local_keys = Path(str(local_db) + ".keys.json")
    before_database = local_db.read_bytes()
    before_keys = local_keys.read_bytes()
    before_revision = workspace.second_coordinator.loaded_global_revision
    committed = pointer(workspace.store)
    workspace.second_coordinator._fault = fail_at(stage)
    response = workspace.second.refresh_network_data(
        {
            "has_unsaved_changes": False,
            "has_unknown_outcome": False,
        }
    )
    assert not response["ok"]
    assert local_db.read_bytes() == before_database
    assert local_keys.read_bytes() == before_keys
    assert workspace.second_coordinator.loaded_global_revision == before_revision
    assert pointer(workspace.store) == committed
    workspace.second_coordinator._fault = lambda _stage: None
    ok(
        workspace.second.refresh_network_data(
            {
                "has_unsaved_changes": False,
                "has_unknown_outcome": False,
            }
        )
    )
    assert value(workspace.second, request) == {"kind": "QUANTITY", "quantity": "48"}


def test_mismatched_key_file_rejected_before_activation(
    workspace: Workspace, tmp_path: Path
) -> None:
    current = json.loads(pointer(workspace.store))
    keys = (
        workspace.store / "generations" / current["generation_id"] / "reporting.sqlite3.keys.json"
    )
    original = keys.read_bytes()
    keys.write_bytes(original + b" ")
    database = tmp_path / "unopened-client/reporting.sqlite3"
    with pytest.raises(NetworkSnapshotError):
        SnapshotCoordinator(workspace.store, database).prepare()
    assert not database.exists()
    keys.write_bytes(original)


@pytest.mark.parametrize(
    "state",
    [
        {"has_unsaved_changes": True, "has_unknown_outcome": False},
        {"has_unsaved_changes": False, "has_unknown_outcome": True},
    ],
)
def test_refresh_refuses_dirty_or_unknown_context(
    workspace: Workspace, state: dict[str, bool]
) -> None:
    before = workspace.second_coordinator.loaded_global_revision
    response = workspace.second.refresh_network_data(state)
    assert not response["ok"]
    assert response["error"]["code"] == "NETWORK_REFRESH_BLOCKED"
    assert workspace.second_coordinator.loaded_global_revision == before


@pytest.mark.parametrize("content", [None, "wrong", DISPOSABLE_CONTENT + "\n"])
def test_executable_smoke_rejects_unmarked_business_directory_without_writes(
    tmp_path: Path, content: str | None
) -> None:
    shared = tmp_path / "business"
    shared.mkdir()
    sentinel = shared / "business.keep"
    sentinel.write_bytes(b"unchanged")
    if content is not None:
        (shared / DISPOSABLE_MARKER).write_text(content, encoding="ascii")
    before = {p.relative_to(shared): p.read_bytes() for p in shared.iterdir()}
    paths = PortablePaths.discover(writable_root=tmp_path / "client")
    with pytest.raises(RuntimeError, match="disposable"):
        run_network_self_test(paths, shared, tmp_path / "report.json")
    assert {p.relative_to(shared): p.read_bytes() for p in shared.iterdir()} == before
    assert not paths.temp.exists()


def test_executable_smoke_local_protocol_and_actual_process_lock(tmp_path: Path) -> None:
    shared = tmp_path / "disposable"
    shared.mkdir()
    (shared / DISPOSABLE_MARKER).write_text(DISPOSABLE_CONTENT, encoding="ascii")
    keep = shared / "keep"
    keep.write_text("pre-existing fixture must remain unchanged", encoding="ascii")
    report = tmp_path / "acceptance.json"
    result = run_network_self_test(
        PortablePaths.discover(writable_root=tmp_path / "local"),
        shared,
        report,
        lock_timeout_seconds=1,
    )
    assert result["ok"] is True
    assert set(result["checks"]) == {
        "encrypted_atomic_pair",
        "two_clients_stale_cas",
        "idempotent_receipt_replay",
        "explicit_refresh_guard",
        "prepublication_rollback",
        "unknown_outcome_recovery",
        "unified_draft_unknown_recovery",
        "two_process_writer_lock",
    }
    assert json.loads(report.read_text()) == result
    assert keep.read_text() == "pre-existing fixture must remain unchanged"
