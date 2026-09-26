"""Anonymous Windows-account reading never grants report-write authorization."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.infrastructure.database.encrypted_sqlite import has_database_key
from backend.infrastructure.windows_data_protection import DeviceProtector

ROOT = Path(__file__).resolve().parents[3]
PIN = "test-admin-only-30"
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}


class TestAccountProtector:
    """Test-only account key stays in memory; production has no non-Windows fallback."""

    __test__ = False

    def __init__(self) -> None:
        self._cipher = ChaCha20Poly1305(os.urandom(32))

    def protect(self, value: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, value, b"test-current-user")

    def unprotect(self, value: bytes) -> bytes:
        try:
            return self._cipher.decrypt(value[:12], value[12:], b"test-current-user")
        except (InvalidTag, ValueError) as error:
            raise ValueError("Different Windows user or damaged wrapper") from error


def app(directory: Path, protector: DeviceProtector | None) -> SecureDesktopBridge:
    return SecureDesktopBridge(
        directory / "data" / "reports.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=directory / "backups",
        device_protector=protector,
    )


def data(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


def setup(bridge: SecureDesktopBridge) -> dict[str, str]:
    status = data(bridge.setup_access({"display_name": "Администратор", "pin": PIN}))
    return {"signer_id": status["current_user"]["id"], "pin": PIN}


def change(bridge: SecureDesktopBridge, quantity: str = "17") -> dict[str, Any]:
    matrix = data(bridge.get_report_matrix(QUERY))
    cell = next(
        cell
        for row in matrix["rows"]
        for cell in row["cells"]
        if cell["state"]["access"] == "editable"
    )
    return {
        **QUERY,
        "confirmation": {"year": 2026, "month": 9},
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": quantity}}
        ],
    }


def test_restart_reads_without_identity_and_all_writes_still_need_code(tmp_path: Path) -> None:
    protector = TestAccountProtector()
    first = app(tmp_path, protector)
    admin = setup(first)
    data(first.save_report_cells({**change(first), "authorization": admin}))
    data(first.authenticate_access(admin))
    first._lock()
    database = tmp_path / "data/reports.sqlite3"
    before = database.read_bytes()
    reopened = app(tmp_path, protector)
    status = data(reopened.get_access_status())
    assert status["state"] == "ready" and status["current_user"] is None
    assert status["automatic_open_available"] is True
    assert reopened._administrator is None
    assert data(reopened.get_report_matrix(QUERY))["rows"]
    assert database.read_bytes() == before
    request = change(reopened, "19")
    for credentials in (None, {**admin, "pin": "wrong-code"}):
        assert not reopened.save_report_cells(
            {**request, **({"authorization": credentials} if credentials else {})}
        )["ok"]
    assert not reopened.create_organization({"name": "Unauthorized"})["ok"]
    assert not reopened.create_report_signer(
        {"display_name": "Unauthorized", "pin": PIN, "role": "reviewer"}
    )["ok"]
    assert not reopened.export_pdf({**QUERY, "month": 9})["ok"]
    assert database.read_bytes() == before
    data(reopened.save_report_cells({**request, "authorization": admin}))
    assert not reopened.save_report_cells(change(reopened))["ok"]
    reopened._lock()
    assert data(reopened.get_access_status())["state"] == "locked"


def test_old_database_one_code_creates_wrapped_key_without_persisting_pin(tmp_path: Path) -> None:
    old = app(tmp_path, None)
    admin = setup(old)
    old._lock()
    protector = TestAccountProtector()
    upgraded = app(tmp_path, protector)
    assert data(upgraded.get_access_status())["state"] == "locked"
    assert not upgraded.unlock_access({**admin, "pin": "wrong-code"})["ok"]
    assert not upgraded._vault.device_path.exists()
    data(upgraded.unlock_access(admin))
    sidecar = upgraded._vault.device_path.read_bytes()
    assert PIN.encode() not in sidecar
    assert upgraded._vault._key is not None
    assert upgraded._vault._key not in sidecar
    assert admin["signer_id"].encode() not in sidecar
    assert set(json.loads(sidecar)) == {"version", "protection", "key"}
    with closing(sqlite3.connect(upgraded._database)) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("SELECT * FROM sqlite_master").fetchall()
    upgraded._lock()
    assert data(app(tmp_path, protector).get_access_status())["state"] == "ready"


@pytest.mark.parametrize("damage", ["json", "ciphertext", "wrong-user", "other-database"])
def test_corrupt_or_foreign_wrapper_fails_closed_and_can_recover(
    tmp_path: Path, damage: str
) -> None:
    protector = TestAccountProtector()
    first = app(tmp_path, protector)
    admin = setup(first)
    device = first._vault.device_path
    first._lock()
    if damage == "json":
        device.write_text("broken", encoding="utf-8")
    elif damage == "ciphertext":
        payload = json.loads(device.read_bytes())
        payload["key"] = "invalid"
        device.write_text(json.dumps(payload), encoding="utf-8")
    elif damage == "wrong-user":
        protector = TestAccountProtector()
    else:
        other = app(tmp_path / "other", protector)
        setup(other)
        device.write_bytes(other._vault.device_path.read_bytes())
        other._lock()
    database = first._database
    before = database.read_bytes()
    reopened = app(tmp_path, protector)
    status = data(reopened.get_access_status())
    assert status["state"] == "locked" and status["automatic_open_error"]
    assert not status["automatic_open_available"]
    assert not has_database_key(database)
    assert not reopened.get_report_matrix(QUERY)["ok"]
    assert database.read_bytes() == before
    data(reopened.unlock_access(admin))
    reopened._lock()
    assert data(app(tmp_path, protector).get_access_status())["state"] == "ready"


def test_missing_database_not_silently_recreated(tmp_path: Path) -> None:
    protector = TestAccountProtector()
    first = app(tmp_path, protector)
    setup(first)
    first._lock()
    first._database.rename(first._database.with_suffix(".preserved"))
    status = data(app(tmp_path, protector).get_access_status())
    assert status["state"] == "locked" and status["automatic_open_error"]
    assert not first._database.exists()


def test_damaged_database_is_checked_before_application_or_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protector = TestAccountProtector()
    first = app(tmp_path, protector)
    setup(first)
    first._lock()
    corrupted = bytearray(first._database.read_bytes())
    corrupted[-100] ^= 1
    first._database.write_bytes(corrupted)
    reopened = app(tmp_path, protector)

    def must_not_migrate() -> None:
        pytest.fail("A damaged database must not reach migration")

    monkeypatch.setattr(reopened, "_migrate_after_unlock", must_not_migrate)
    status = data(reopened.get_access_status())
    assert status["state"] == "locked" and status["automatic_open_error"]
    assert not has_database_key(first._database)
    assert first._database.read_bytes() == corrupted


def test_partial_setup_does_not_auto_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    protector = TestAccountProtector()
    first = app(tmp_path, protector)
    monkeypatch.setattr(first, "_open", lambda **_kw: (_ for _ in ()).throw(OSError("interrupted")))
    assert not first.setup_access({"display_name": "Владелец", "pin": PIN})["ok"]
    assert not first._vault.device_path.exists()
    assert data(app(tmp_path, protector).get_access_status())["state"] == "setup"


def test_sidecar_write_failure_does_not_undo_successful_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = app(tmp_path, TestAccountProtector())
    monkeypatch.setattr(
        first._vault, "remember_device", lambda: (_ for _ in ()).throw(OSError("readonly"))
    )
    setup(first)
    status = data(first.get_access_status())
    assert status["state"] == "ready" and status["automatic_open_error"]
    assert data(first.get_report_matrix(QUERY))["rows"]


def test_enrolling_reviewer_keeps_automatic_open_key_valid(tmp_path: Path) -> None:
    protector = TestAccountProtector()
    first = app(tmp_path, protector)
    admin = setup(first)
    before = first._vault.device_path.read_bytes()
    data(first.authenticate_access(admin))
    data(
        first.create_report_signer({"display_name": "Проверяющий", "pin": PIN, "role": "reviewer"})
    )
    assert first._vault.device_path.read_bytes() == before
    first._lock()
    assert data(app(tmp_path, protector).get_access_status())["state"] == "ready"
