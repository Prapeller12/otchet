"""Real snapshot/signature integration for the automatic save/print workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.api.report_confirmation import (
    confirm_print_report,
    confirm_saved_report,
    prepare_save_confirmation,
)
from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2024}


def data(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[WorkingReferenceApplicationBridge, dict[str, str]]:
    app = WorkingReferenceApplicationBridge(
        tmp_path / "report.db",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
    )
    signer = data(app.create_report_signer({"display_name": "Проверка", "pin": "test-code"}))
    return app, {"signer_id": signer["id"], "pin": "test-code"}


def save_request(app: WorkingReferenceApplicationBridge) -> dict[str, Any]:
    matrix = data(app.get_report_matrix(QUERY))
    cell = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == "2024-02-29")
    return {
        **QUERY,
        "confirmation": {"month": 2},
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": "17"}}
        ],
    }


def test_saved_data_gets_real_current_snapshot_signature(
    workspace: tuple[WorkingReferenceApplicationBridge, dict[str, str]],
) -> None:
    app, auth = workspace
    clean, context = prepare_save_confirmation(app, "save_report_cells", save_request(app))
    result = confirm_saved_report(app, app.save_report_cells(clean), context, auth)
    verified = data(result)["verification"]
    assert verified["status"] == "VERIFIED"
    assert verified["algorithm"] == "Ed25519"
    assert verified["signature"]
    assert verified["signer_id"] == auth["signer_id"]
    assert verified == data(app.get_report_verification({**QUERY, "month": 2}))
    assert data(result)["matrix_revision"] != clean["base_revision"]
    assert auth["pin"] not in str(result)


def test_committed_save_remains_successful_when_signing_fails(
    workspace: tuple[WorkingReferenceApplicationBridge, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, auth = workspace
    clean, context = prepare_save_confirmation(app, "save_report_cells", save_request(app))
    saved = app.save_report_cells(clean)

    def fail(*_args: object) -> dict[str, Any]:
        raise ValueError("Исправьте расхождения отчёта")

    monkeypatch.setattr(app._verification, "verify", fail)
    result = confirm_saved_report(app, saved, context, auth)
    assert result["ok"]
    assert data(result)["cells"] == data(saved)["cells"]
    assert data(result)["verification_error"]["message"] == "Исправьте расхождения отчёта"
    assert "verification" not in data(result)
    matrix = data(app.get_report_matrix(QUERY))
    cell = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == "2024-02-29")
    assert cell["value"] == {"kind": "QUANTITY", "quantity": "17"}


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"month": True},
        {"month": 13},
        {"month": 2, "unexpected": "value"},
        {"month": 2, "year": 2025},
        {"month": 2, "week_start": False},
        {"month": 2, "week_start": "2024-03-01"},
    ],
)
def test_invalid_context_rejected_before_save(
    workspace: tuple[WorkingReferenceApplicationBridge, dict[str, str]], context: object
) -> None:
    app, _auth = workspace
    request = {**save_request(app), "confirmation": context}
    before = data(app.get_report_matrix(QUERY))["matrix_revision"]
    with pytest.raises(ValueError):
        prepare_save_confirmation(app, "save_report_cells", request)
    assert data(app.get_report_matrix(QUERY))["matrix_revision"] == before


def test_failed_save_is_never_signed(
    workspace: tuple[WorkingReferenceApplicationBridge, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, auth = workspace
    clean, context = prepare_save_confirmation(app, "save_report_cells", save_request(app))
    clean["base_revision"] = "outdated"
    failed = app.save_report_cells(clean)
    assert not failed["ok"]

    def unexpected(*_args: object) -> dict[str, Any]:
        raise AssertionError("Failed save cannot produce an attestation")

    monkeypatch.setattr(app._verification, "verify", unexpected)
    assert confirm_saved_report(app, failed, context, auth) is failed


def test_print_signs_expected_snapshot_and_rejects_stale_or_wrong_code(
    workspace: tuple[WorkingReferenceApplicationBridge, dict[str, str]], tmp_path: Path
) -> None:
    app, auth = workspace
    matrix = data(app.get_report_matrix(QUERY))
    query = {**QUERY, "month": 2, "expected_revision": matrix["matrix_revision"]}
    with pytest.raises(ValueError):
        confirm_print_report(app, {**query, "expected_revision": "outdated"}, auth)
    with pytest.raises(ValueError):
        confirm_print_report(app, query, {**auth, "pin": "wrong-code"})
    assert data(app.get_report_verification({**QUERY, "month": 2}))["status"] == "UNVERIFIED"
    verified = confirm_print_report(app, query, auth)
    assert verified["status"] == "VERIFIED"
    assert data(app.get_report_matrix(QUERY))["matrix_revision"] == matrix["matrix_revision"]
    destination = tmp_path / "confirmed.pdf"
    app.configure_pdf_dialog(lambda _: destination)
    assert not data(app.export_pdf(query))["cancelled"]
    assert destination.read_bytes().startswith(b"%PDF-")
    with pytest.raises(ValueError):
        confirm_print_report(app, {**query, "unexpected": "value"}, auth)


def test_presentation_period_and_width_exception(
    workspace: tuple[WorkingReferenceApplicationBridge, dict[str, str]],
) -> None:
    app, auth = workspace
    clean, context = prepare_save_confirmation(
        app,
        "save_report_presentation",
        {**QUERY, "title": "Новое имя", "confirmation": {"year": 2024, "month": 2}},
    )
    assert "year" not in clean
    result = confirm_saved_report(app, app.save_report_presentation(clean), context, auth)
    assert data(result)["title"] == "Новое имя"
    assert data(result)["verification"]["status"] == "VERIFIED"
    _clean, context = prepare_save_confirmation(
        app,
        "save_report_presentation",
        {**QUERY, "widths": {"position": 180}},
    )
    assert context is None
    with pytest.raises(ValueError):
        prepare_save_confirmation(app, "save_report_presentation", {**QUERY, "title": "X"})


def test_secure_manager_save_and_print_need_one_fresh_code_and_produce_signatures(
    tmp_path: Path,
) -> None:
    app = SecureDesktopBridge(
        tmp_path / "data/reports.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=tmp_path / "backups",
        inbox_directory=tmp_path / "inbox",
    )
    data(app.setup_access({"display_name": "Администратор", "pin": "admin-code"}))
    admin = data(app.get_access_status())["users"][0]
    data(app.authenticate_access({"signer_id": admin["id"], "pin": "admin-code"}))
    manager = data(
        app.create_report_signer(
            {
                "display_name": "Руководитель",
                "pin": "manager-code",
                "role": "project_manager",
            }
        )
    )
    data(app.end_administration())
    auth = {"signer_id": manager["id"], "pin": "manager-code"}
    matrix = data(app.get_report_matrix(QUERY))
    cell = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == "2024-02-29")
    request = {
        **QUERY,
        "confirmation": {"month": 2},
        "authorization": auth,
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": "9"}}
        ],
    }
    before = (tmp_path / "data/reports.sqlite3").read_bytes()
    assert not app.save_report_cells({**request, "authorization": {**auth, "pin": "wrong"}})["ok"]
    assert (tmp_path / "data/reports.sqlite3").read_bytes() == before
    saved = data(app.save_report_cells(request))
    assert saved["verification"]["status"] == "VERIFIED"
    assert saved["verification"]["signer_id"] == manager["id"]
    destination = tmp_path / "manager.pdf"
    opened = []

    def choose_file(name: str) -> Path:
        opened.append(name)
        return destination

    app._configure_pdf_dialog(choose_file)
    # Dialogs are configured before the real desktop unlock; this fixture has
    # already initialized the application, so attach its native callback too.
    assert app._application is not None
    app._application.configure_pdf_dialog(choose_file)
    print_request = {**QUERY, "month": 2, "expected_revision": saved["matrix_revision"]}
    assert not app.export_pdf(print_request)["ok"]
    assert not app.export_pdf({**print_request, "authorization": {**auth, "pin": "wrong"}})["ok"]
    assert not opened
    assert not destination.exists()
    result = data(app.export_pdf({**print_request, "authorization": auth}))
    assert result["verification"]["status"] == "VERIFIED"
    assert result["verification"]["signer_id"] == manager["id"]
    assert len(opened) == 1
    assert destination.read_bytes().startswith(b"%PDF-")
