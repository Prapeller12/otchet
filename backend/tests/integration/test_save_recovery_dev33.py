"""Native bridge saves must preserve truthful outcomes and complete recovery points."""

from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.desktop.database_bootstrap import backup_database

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}


def data(result: dict[str, Any]) -> Any:
    assert result["ok"], result
    return result["data"]


@pytest.fixture
def ready(tmp_path: Path) -> tuple[SecureDesktopBridge, dict[str, str]]:
    app = SecureDesktopBridge(
        tmp_path / "data/reporting.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=tmp_path / "backups",
        device_protector=None,
    )
    status = data(app.setup_access({"display_name": "QA", "pin": "recovery-test-pin"}))
    return app, {"signer_id": status["current_user"]["id"], "pin": "recovery-test-pin"}


def request(app: SecureDesktopBridge, auth: dict[str, str], quantity: str) -> dict[str, Any]:
    matrix = data(app.get_report_matrix(QUERY))
    cell = next(c for r in matrix["rows"] for c in r["cells"] if c["state"]["access"] == "editable")
    return {
        **QUERY,
        "authorization": auth,
        "confirmation": {"year": 2026, "month": 9},
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": quantity}}
        ],
    }


def test_every_save_has_before_and_after_snapshot(
    ready: tuple[SecureDesktopBridge, dict[str, str]],
) -> None:
    app, auth = ready
    for quantity in ("11", "12"):
        payload = request(app, auth, quantity)
        with patch(
            "backend.api.secure_desktop_bridge.backup_database", wraps=backup_database
        ) as backup:
            result = data(app.save_report_cells(payload))
            assert backup.call_count == 2
            assert result["backup_complete"] is True
            assert (app._vault.backups / result["backup_file"]).is_file()


def test_failed_pre_save_backup_does_not_write(
    ready: tuple[SecureDesktopBridge, dict[str, str]],
) -> None:
    app, auth = ready
    before = data(app.get_report_matrix(QUERY))
    with patch(
        "backend.api.secure_desktop_bridge.backup_database", side_effect=OSError("full disk")
    ):
        response = app.save_report_cells(request(app, auth, "15"))
    assert response["ok"] is False
    assert "резервную копию" in response["error"]["message"]
    assert data(app.get_report_matrix(QUERY))["matrix_revision"] == before["matrix_revision"]


def test_failed_post_save_backup_retains_success(
    ready: tuple[SecureDesktopBridge, dict[str, str]],
) -> None:
    app, auth = ready
    calls = 0

    def failing_after(*args: Any, **kwargs: Any) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("full disk after commit")
        return backup_database(*args, **kwargs)

    payload = request(app, auth, "17")
    with patch("backend.api.secure_desktop_bridge.backup_database", side_effect=failing_after):
        result = data(app.save_report_cells(payload))
    assert result["backup_complete"] is False
    assert "Данные сохранены" in result["backup_warning"]
    coordinate = payload["changes"][0]["coordinate"]
    actual = next(
        c
        for r in data(app.get_report_matrix(QUERY))["rows"]
        for c in r["cells"]
        if c["coordinate"] == coordinate
    )
    assert actual["value"] == {"kind": "QUANTITY", "quantity": "17"}
