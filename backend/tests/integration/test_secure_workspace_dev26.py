"""Independent acceptance of the native security boundary, not UI visibility."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.api.secure_desktop_bridge import SecureDesktopBridge

ROOT = Path(__file__).resolve().parents[3]
PIN = "independent-admin-9152"
REVIEWER_PIN = "independent-reviewer-7218"
MANAGER_PIN = "independent-manager-3128"
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026}


def data(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


def make_app(directory: Path) -> SecureDesktopBridge:
    return SecureDesktopBridge(
        directory / "data" / "reports.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=directory / "backups",
        inbox_directory=directory / "inbox",
    )


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[SecureDesktopBridge, dict[str, str]]:
    app = make_app(tmp_path)
    assert data(app.get_access_status())["state"] == "setup"
    data(app.setup_access({"display_name": "Администратор QA", "pin": PIN}))
    status = data(app.get_access_status())
    assert status["state"] == "ready"
    admin = next(user for user in status["users"] if user["role"] == "admin")
    return app, {"signer_id": admin["id"], "pin": PIN}


def account(
    app: SecureDesktopBridge, authorization: dict[str, str], role: str, pin: str
) -> dict[str, Any]:
    return dict(
        data(
            app.create_report_signer(
                {
                    "display_name": f"QA {role} {uuid4().hex[:6]}",
                    "pin": pin,
                    "role": role,
                    "authorization": authorization,
                }
            )
        )
    )


def cell_request(app: SecureDesktopBridge, quantity: str = "17") -> dict[str, Any]:
    matrix = data(app.get_report_matrix(QUERY))
    cell = next(
        cell
        for row in matrix["rows"]
        for cell in row["cells"]
        if cell["state"]["access"] == "editable"
    )
    return {
        **QUERY,
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": quantity}}
        ],
    }


def test_first_setup_encrypts_and_cannot_create_second_administrator(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, authorization = workspace
    assert not app.setup_access({"display_name": "Второй", "pin": PIN})["ok"]
    assert not app.create_report_signer(
        {
            "display_name": "Другой администратор",
            "pin": PIN,
            "role": "admin",
            "authorization": authorization,
        }
    )["ok"]
    status = data(app.get_access_status())
    assert sum(user["role"] == "admin" for user in status["users"]) == 1
    path = tmp_path / "data/reports.sqlite3"
    assert not path.read_bytes().startswith(b"SQLite format 3")
    assert "Администратор QA".encode() not in path.read_bytes()
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.DatabaseError):
        connection.execute("SELECT name FROM sqlite_master").fetchall()
    assert PIN not in json.dumps(status)


def test_each_changed_save_requires_fresh_reviewer_or_admin_code(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, authorization = workspace
    reviewer = account(app, authorization, "reviewer", REVIEWER_PIN)
    manager = account(app, authorization, "project_manager", MANAGER_PIN)
    request = cell_request(app, "123456789012345.123456789")
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    for credentials in (
        None,
        {"signer_id": reviewer["id"], "pin": "wrong-code"},
        {"signer_id": manager["id"], "pin": MANAGER_PIN},
    ):
        rejected = app.save_report_cells(
            {**request, **({} if credentials is None else {"authorization": credentials})}
        )
        assert not rejected["ok"], rejected
        assert (tmp_path / "data/reports.sqlite3").read_bytes() == before
    data(
        app.save_report_cells(
            {**request, "authorization": {"signer_id": reviewer["id"], "pin": REVIEWER_PIN}}
        )
    )
    # Successful authorization is not a session-wide write grant.
    request2 = cell_request(app, "0")
    assert not app.save_report_cells(request2)["ok"]
    data(app.save_report_cells({**request2, "authorization": authorization}))


@pytest.mark.parametrize(
    "method,payload",
    [
        ("create_organization", {"name": "Неавторизованная организация"}),
        ("rename_organization", {"organization_id": "1", "name": "Подмена"}),
        ("archive_organization", {"organization_id": "1"}),
        ("save_report_layout", {**QUERY, "rows": []}),
        ("save_report_presentation", {**QUERY, "title": "Подмена"}),
        ("validate_import", QUERY),
        ("commit_import", {"batch_id": "nonexistent"}),
        (
            "reference_report",
            {
                "action": "save",
                "organization_id": "1",
                "id": "missing",
                "revision": 0,
                "changes": [],
            },
        ),
        ("reference_report", {"action": "transfer", **QUERY, "id": "missing", "mappings": []}),
        ("create_report_signer", {"display_name": "Подмена", "pin": PIN, "role": "reviewer"}),
    ],
)
def test_native_mutation_paths_do_not_write_without_authorization(
    workspace: tuple[SecureDesktopBridge, dict[str, str]],
    tmp_path: Path,
    method: str,
    payload: dict[str, Any],
) -> None:
    app, _authorization = workspace
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    result = getattr(app, method)(payload)
    assert not result["ok"], result
    assert (tmp_path / "data/reports.sqlite3").read_bytes() == before


def test_reviewer_cannot_change_administrator_configuration(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, authorization = workspace
    reviewer = account(app, authorization, "reviewer", REVIEWER_PIN)
    credentials = {"signer_id": reviewer["id"], "pin": REVIEWER_PIN}
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    for method, payload in (
        ("create_organization", {"name": "Недоступно"}),
        ("save_report_presentation", {**QUERY, "title": "Недоступно"}),
        ("create_report_signer", {"display_name": "Недоступно", "pin": PIN, "role": "reviewer"}),
    ):
        result = getattr(app, method)({**payload, "authorization": credentials})
        assert not result["ok"], result
        assert (tmp_path / "data/reports.sqlite3").read_bytes() == before


def test_locked_startup_does_not_create_or_expose_database(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert data(app.get_access_status())["state"] == "setup"
    assert not app.get_report_matrix(QUERY)["ok"]
    assert not app.save_report_cells({})["ok"]
    assert not (tmp_path / "data/reports.sqlite3").exists()


def test_lock_and_reopen_requires_correct_code_and_preserves_quantities(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, authorization = workspace
    quantity = "123456789012345.123456789"
    request = cell_request(app, quantity)
    data(app.save_report_cells({**request, "authorization": authorization}))
    app._lock()
    reopened = make_app(tmp_path)
    assert data(reopened.get_access_status())["state"] == "locked"
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    assert not reopened.get_report_matrix(QUERY)["ok"]
    assert not reopened.unlock_access({**authorization, "pin": "incorrect-code"})["ok"]
    assert (tmp_path / "data/reports.sqlite3").read_bytes() == before
    data(reopened.unlock_access(authorization))
    matrix = data(reopened.get_report_matrix(QUERY))
    original = request["changes"][0]["coordinate"]
    cell = next(
        cell for row in matrix["rows"] for cell in row["cells"] if cell["coordinate"] == original
    )
    assert cell["value"]["quantity"] == quantity
    assert not reopened.save_report_cells(cell_request(reopened))["ok"]


def test_interrupted_initial_setup_resumes_with_same_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path)
    monkeypatch.setattr(
        app, "_open", lambda **_kwargs: (_ for _ in ()).throw(OSError("interrupted"))
    )
    assert not app.setup_access({"display_name": "Владелец", "pin": PIN})["ok"]
    resumed = make_app(tmp_path)
    assert data(resumed.get_access_status())["state"] == "setup"
    assert not resumed.setup_access({"display_name": "Подмена", "pin": "wrong-code"})["ok"]
    data(resumed.setup_access({"display_name": "Владелец", "pin": PIN}))
    assert len(data(resumed.get_access_status())["users"]) == 1


def test_interrupted_legacy_conversion_resumes_before_access_role_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
    from backend.infrastructure.report_crypto import protect_key

    directory = tmp_path / "old-migrations"
    directory.mkdir()
    for migration in (ROOT / "backend/migrations").glob("*.sql"):
        if migration.name < "0013":
            shutil.copyfile(migration, directory / migration.name)
    database = tmp_path / "data/reports.sqlite3"
    database.parent.mkdir()
    profile = protect_key(
        {
            "id": "legacy-admin",
            "display_name": "Прежний владелец",
            "role": "admin",
            "created_at": "2026-09-01T00:00:00+00:00",
        },
        PIN,
    )
    with connect_sqlite(database) as connection:
        apply_migrations(connection, directory)
        connection.execute(
            "INSERT INTO report_signers(id,display_name,role,public_key,key_fingerprint,"
            "encrypted_private_key,salt,nonce,created_at) VALUES "
            "(:id,:display_name,:role,:public_key,:key_fingerprint,"
            ":encrypted_private_key,:salt,:nonce,:created_at)",
            profile,
        )
    app = make_app(tmp_path)
    monkeypatch.setattr(
        app, "_open", lambda **_kwargs: (_ for _ in ()).throw(OSError("interrupted"))
    )
    assert not app.setup_access({"signer_id": "legacy-admin", "pin": PIN})["ok"]
    assert not database.read_bytes().startswith(b"SQLite format 3")
    resumed = make_app(tmp_path)
    state = data(resumed.get_access_status())["state"]
    credentials = {"signer_id": "legacy-admin", "pin": PIN}
    if state == "legacy":
        data(resumed.setup_access(credentials))
    else:
        assert state == "locked"
        data(resumed.unlock_access(credentials))
    assert data(resumed.get_access_status())["state"] == "ready"
    assert data(resumed.get_report_matrix(QUERY))["rows"]


def test_failed_account_enrollment_recovers_without_second_profile(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, authorization = workspace
    original = app._vault.enroll
    monkeypatch.setattr(app._vault, "enroll", lambda *_args: (_ for _ in ()).throw(OSError("full")))
    response = app.create_report_signer(
        {
            "display_name": "Восстановление доступа",
            "role": "reviewer",
            "pin": REVIEWER_PIN,
            "authorization": authorization,
        }
    )
    assert not response["ok"]
    users = data(app.list_report_signers({}))
    user = next(user for user in users if user["display_name"] == "Восстановление доступа")
    assert user["can_unlock"] is False
    monkeypatch.setattr(app._vault, "enroll", original)
    request = {"signer_id": user["id"], "pin": REVIEWER_PIN}
    assert not app.enroll_access(request)["ok"]
    assert not app.enroll_access({**request, "pin": "wrong-code", "authorization": authorization})[
        "ok"
    ]
    data(app.enroll_access({**request, "authorization": authorization}))
    assert len(data(app.list_report_signers({}))) == 2
    app._lock()
    data(app.unlock_access(request))
    assert data(app.get_access_status())["current_user"]["role"] == "reviewer"


def test_missing_established_database_fails_closed_without_silent_recreation(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, authorization = workspace
    app._lock()
    database = tmp_path / "data/reports.sqlite3"
    preserved = database.with_suffix(".preserved")
    database.rename(preserved)
    response = make_app(tmp_path).unlock_access(authorization)
    assert not response["ok"]
    assert not database.exists()
    assert preserved.exists()


@pytest.mark.parametrize("report_type", ["DAILY_MOVEMENT", "HEAD_SITE"])
def test_plan_matrix_values_require_administrator_even_with_valid_reviewer_code(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path, report_type: str
) -> None:
    app, authorization = workspace
    reviewer = account(app, authorization, "reviewer", REVIEWER_PIN)
    query = {**QUERY, "report_type": report_type}
    matrix = data(app.get_report_matrix(query))
    cell = next(
        cell
        for row in matrix["rows"]
        for cell in row["cells"]
        if cell["state"]["access"] == "editable"
        and "PLAN" in cell["coordinate"]["metric_code"].split("_")
    )
    request = {
        **query,
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": "125"}}
        ],
    }
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    assert not app.save_report_cells(
        {**request, "authorization": {"signer_id": reviewer["id"], "pin": REVIEWER_PIN}}
    )["ok"]
    assert (tmp_path / "data/reports.sqlite3").read_bytes() == before
    data(app.save_report_cells({**request, "authorization": authorization}))


def test_project_manager_never_receives_database_key_and_cannot_unlock_file(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    from backend.infrastructure.access_vault import AccessVault

    app, authorization = workspace
    manager = account(app, authorization, "project_manager", MANAGER_PIN)
    credentials = {"signer_id": manager["id"], "pin": MANAGER_PIN}
    database = tmp_path / "data/reports.sqlite3"
    envelope = database.with_suffix(database.suffix + ".keys.json")
    before_database = database.read_bytes()
    before_envelope = envelope.read_bytes()
    users = data(app.get_access_status())["users"]
    manager_status = next(user for user in users if user["id"] == manager["id"])
    assert manager_status["can_unlock"] is False
    assert all(entry["id"] != manager["id"] for entry in json.loads(before_envelope)["users"])
    # Their identity can be checked while a responsible person has opened the app.
    assert data(app.authenticate_access(credentials))["role"] == "project_manager"
    assert data(app.get_report_matrix(QUERY))["rows"]
    assert not app.save_report_cells({**cell_request(app), "authorization": credentials})["ok"]
    # Even an administrator cannot accidentally grant a manager the master-key envelope.
    assert not app.enroll_access({**credentials, "authorization": authorization})["ok"]
    assert database.read_bytes() == before_database
    assert envelope.read_bytes() == before_envelope
    app._lock()
    standalone_vault = AccessVault(database, tmp_path / "backups")
    with pytest.raises(ValueError):
        standalone_vault.unlock(manager["id"], MANAGER_PIN)
    reopened = make_app(tmp_path)
    assert all(user["id"] != manager["id"] for user in data(reopened.get_access_status())["users"])
    assert not reopened.unlock_access(credentials)["ok"]
    assert database.read_bytes() == before_database
    assert envelope.read_bytes() == before_envelope
    data(reopened.unlock_access(authorization))


def test_reviewer_code_facts_preserve_plan_identity_and_other_months(
    workspace: tuple[SecureDesktopBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, authorization = workspace
    reviewer = account(app, authorization, "reviewer", REVIEWER_PIN)
    query = {"report_type": "HEAD_SITE", "organization_id": "1"}
    matrix = data(app.get_report_matrix({**query, "year": 2026}))
    codes: list[dict[str, Any]] = [
        {
            "id": "A",
            "label": "Изделие А",
            "plans": {"2026-09": "3"},
            "actuals": {"2026-01": "11", "2026-09": "1"},
        },
        {
            "id": "B",
            "label": "Изделие Б",
            "plans": {"2026-09": "4"},
            "actuals": {"2026-01": "12", "2026-09": ""},
        },
    ]
    data(
        app.save_report_presentation(
            {
                **query,
                "expected_revision": matrix["matrix_revision"],
                "production_codes": codes,
                "authorization": authorization,
            }
        )
    )
    matrix = data(app.get_report_matrix({**query, "year": 2026}))
    credentials = {"signer_id": reviewer["id"], "pin": REVIEWER_PIN}
    result = data(
        app.save_report_presentation(
            {
                **query,
                "expected_revision": matrix["matrix_revision"],
                "authorization": credentials,
                "production_code_actuals": {"A": {"2026-09": "0"}, "B": {"2026-09": "2"}},
                "confirm_production_totals": False,
            }
        )
    )
    assert result["actuals"]["2026-09"] == "2"
    for original, updated in zip(codes, result["production_codes"], strict=True):
        assert updated["id"] == original["id"] and updated["label"] == original["label"]
        assert updated["plans"] == original["plans"]
        assert updated["actuals"]["2026-01"] == original["actuals"]["2026-01"]
    assert result["production_codes"][0]["actuals"]["2026-09"] == "0"
    matrix = data(app.get_report_matrix({**query, "year": 2026}))
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    assert not app.save_report_presentation(
        {
            **query,
            "expected_revision": matrix["matrix_revision"],
            "authorization": credentials,
            "production_code_actuals": {"A": {"2026-09": "5"}},
            "plans": {"2026-09": "100"},
        }
    )["ok"]
    assert (tmp_path / "data/reports.sqlite3").read_bytes() == before
