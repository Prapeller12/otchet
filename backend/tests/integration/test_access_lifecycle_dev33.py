"""Credential lifecycle retains signatures and recovers the DB/vault crash boundary."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.application.access_lifecycle import AccessLifecycleService
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_signers import load_signer
from backend.infrastructure.report_crypto import encode, signature_valid, unlock_key

ROOT = Path(__file__).resolve().parents[3]
PIN = "admin-lifecycle-33"
USER_PIN = "reviewer-lifecycle-33"
NEW_PIN = "reviewer-new-lifecycle-33"


def make(directory: Path) -> SecureDesktopBridge:
    return SecureDesktopBridge(
        directory / "data.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=directory / "backups",
        device_protector=None,
    )


def ok(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


def fixture(directory: Path) -> tuple[SecureDesktopBridge, dict[str, str], dict[str, Any]]:
    app = make(directory)
    status = ok(app.setup_access({"display_name": "Администратор", "pin": PIN}))
    admin = next(user for user in status["users"] if user["role"] == "admin")
    auth = {"signer_id": admin["id"], "pin": PIN}
    ok(app.authenticate_access(auth))
    reviewer = ok(
        app.create_report_signer(
            {
                "display_name": "Проверяющий",
                "pin": USER_PIN,
                "role": "reviewer",
            }
        )
    )
    return app, auth, reviewer


@pytest.mark.parametrize("users", [[None], [[]], [{}], "bad", [], [{"id": "a", "role": []}]])
def test_corrupt_vault_returns_structured_access_error(tmp_path: Path, users: object) -> None:
    path = tmp_path / "data.sqlite3.keys.json"
    path.write_text(json.dumps({"version": 1, "users": users}), encoding="utf-8")
    response = make(tmp_path).get_access_status()
    assert response["ok"] is False
    assert response["error"]["code"] == "ACCESS_DENIED"


def test_rotate_transfer_revoke_and_historical_signature(tmp_path: Path) -> None:
    app, auth, user = fixture(tmp_path)
    assert app._application is not None
    repo = app._application._signers
    with closing(connect_sqlite(tmp_path / "data.sqlite3")) as conn:
        original = load_signer(conn, user["id"])
    message = "historical report snapshot"
    signature = encode(unlock_key(original, USER_PIN).sign(message.encode()))
    service = AccessLifecycleService(tmp_path / "data.sqlite3", app._vault)
    service.execute(
        {
            "action": "change_pin",
            "signer_id": user["id"],
            "current_pin": USER_PIN,
            "new_pin": NEW_PIN,
            "authorization": auth,
        }
    )
    with pytest.raises(ValueError):
        repo.authenticate(user["id"], USER_PIN)
    assert repo.authenticate(user["id"], NEW_PIN)["id"] == user["id"]
    with closing(connect_sqlite(tmp_path / "data.sqlite3")) as conn:
        current = load_signer(conn, user["id"])
        assert current["public_key"] == original["public_key"]
        assert signature_valid(current["public_key"], signature, message)
    app._lock()
    assert not app.unlock_access({"signer_id": user["id"], "pin": USER_PIN})["ok"]
    ok(app.unlock_access({"signer_id": user["id"], "pin": NEW_PIN}))
    service.execute(
        {
            "action": "transfer_admin",
            "signer_id": user["id"],
            "current_pin": NEW_PIN,
            "authorization": auth,
        }
    )
    with pytest.raises(ValueError):
        repo.authorize(auth["signer_id"], PIN, admin_only=True)
    new_auth = {"signer_id": user["id"], "pin": NEW_PIN}
    assert repo.authorize(user["id"], NEW_PIN, admin_only=True)["role"] == "admin"
    with pytest.raises(ValueError, match="передайте"):
        service.execute({"action": "revoke", "signer_id": user["id"], "authorization": new_auth})
    service.execute({"action": "revoke", "signer_id": auth["signer_id"], "authorization": new_auth})
    with pytest.raises(ValueError, match="отозван"):
        repo.authorize(auth["signer_id"], PIN)
    assert next(p for p in repo.list() if p["id"] == auth["signer_id"])["revoked"]
    app._lock()
    assert not app.unlock_access(auth)["ok"]
    ok(app.unlock_access(new_auth))
    assert not app.create_report_signer(
        {"display_name": "Stale admin", "pin": PIN, "role": "reviewer"}
    )["ok"]


@pytest.mark.parametrize("commit", [False, True])
def test_pin_rotation_recovers_both_sides_of_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit: bool,
) -> None:
    app, auth, user = fixture(tmp_path)
    service = AccessLifecycleService(tmp_path / "data.sqlite3", app._vault)
    if not commit:
        with closing(connect_sqlite(tmp_path / "data.sqlite3")) as conn, conn:
            conn.execute(
                "CREATE TRIGGER injected_abort BEFORE INSERT ON report_access_lifecycle_commits "
                "BEGIN SELECT RAISE(ABORT, 'injected transaction failure'); END"
            )
    original_write = app._vault._write
    monkeypatch.setattr(
        app._vault, "_write", lambda *_args: (_ for _ in ()).throw(OSError("disk full"))
    )
    request = {
        "action": "change_pin",
        "signer_id": user["id"],
        "current_pin": USER_PIN,
        "new_pin": NEW_PIN,
        "authorization": auth,
    }
    if commit:
        result = service.execute(request)
        assert result["access_warning"]
    else:
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            service.execute(request)
    assert app._vault.lifecycle_path.exists()
    monkeypatch.setattr(app._vault, "_write", original_write)
    app._lock()
    reopened = make(tmp_path)
    rejected_pin, accepted_pin = (USER_PIN, NEW_PIN) if commit else (NEW_PIN, USER_PIN)
    assert not reopened.unlock_access({"signer_id": user["id"], "pin": rejected_pin})["ok"]
    ok(reopened.unlock_access({"signer_id": user["id"], "pin": accepted_pin}))
    assert not reopened._vault.lifecycle_path.exists()
    assert not Path(str(tmp_path / "data.sqlite3") + ".lifecycle.json").exists()
