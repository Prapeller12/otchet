"""Atomic draft, recovery and post-commit status acceptance on real SQLite."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from backend.api.report_confirmation import confirm_saved_report
from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.report_draft import DRAFT_COMMAND
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_cells import SqliteReportCellUnitOfWork
from backend.infrastructure.database.sqlite_report_draft import SqliteReportDraftUnitOfWork

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "HEAD_SITE", "organization_id": "1", "year": 2027}
PERIOD = {"year": 2027, "month": 9}


def data(result: dict[str, Any]) -> Any:
    assert result["ok"], result
    return result["data"]


@pytest.fixture
def application(tmp_path: Path) -> WorkingReferenceApplicationBridge:
    return WorkingReferenceApplicationBridge(
        tmp_path / "data" / "reports.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
    )


def request(app: Any, report_type: str = "HEAD_SITE") -> dict[str, Any]:
    matrix = data(app.get_report_matrix({**QUERY, "report_type": report_type}))
    cells = [
        c
        for r in matrix["rows"]
        for c in r["cells"]
        if c["state"]["access"] == "editable"
        and (
            c["coordinate"].get("operation_date") or c["coordinate"].get("period_start")
        ).startswith("2027-09")
    ]
    assert len(cells) >= 2
    return {
        **QUERY,
        "report_type": report_type,
        "confirmation": PERIOD,
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "presentation": {
            "header": {"product_name": "Новое изделие"},
            "title": "Новая форма",
            **(
                {"plans": {"2027-09": "250"}, "actuals": {"2027-09": "0"}}
                if report_type != "DAILY_MOVEMENT"
                else {}
            ),
        },
        "changes": [
            {"coordinate": cells[0]["coordinate"], "value": {"kind": "QUANTITY", "quantity": "0"}},
            {"coordinate": cells[1]["coordinate"], "value": {"kind": "DATA_NOT_PROVIDED"}},
        ],
    }


def counts(app: WorkingReferenceApplicationBridge) -> tuple[int, int, int]:
    with closing(connect_sqlite(app._database_path)) as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("report_fact_revisions", "audit_events", "idempotency_records")
        )


def recovery(save: dict[str, Any]) -> dict[str, Any]:
    return {
        k: save[k]
        for k in ("report_type", "organization_id", "year", "confirmation", "idempotency_key")
    }


@pytest.mark.parametrize("report_type", ["DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"])
def test_complete_packet_is_saved_and_reopens(
    application: WorkingReferenceApplicationBridge,
    report_type: str,
) -> None:
    app = application
    save = request(app, report_type)
    result = data(app.save_report_draft(save))
    assert result["data_state"] == "committed"
    assert result["operation_revision"] != save["base_revision"]
    assert result["current_revision"] == result["operation_revision"]
    assert result["verification_state"] == result["backup_state"] == "pending"
    assert [c["value"] for c in result["cells"]] == [
        {"kind": "QUANTITY", "quantity": "0"},
        {"kind": "DATA_NOT_PROVIDED"},
    ]
    reopened = WorkingReferenceApplicationBridge(
        app._database_path,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        initialize_workspace=False,
    )
    matrix = data(reopened.get_report_matrix({**QUERY, "report_type": report_type}))
    assert matrix["presentation"]["header"]["product_name"] == "Новое изделие"
    assert matrix["title"] == "Новая форма"
    if report_type != "DAILY_MOVEMENT":
        assert matrix["presentation"]["plans"]["2027-09"] == "250"
        assert matrix["presentation"]["actuals"]["2027-09"] == "0"


def test_last_statement_failure_rolls_back_facts_header_plan_audit_and_key(
    application: WorkingReferenceApplicationBridge,
) -> None:
    app = application
    save = request(app)
    before_counts = counts(app)
    before_presentation = app._workspace.get_presentation(1, "HEAD_SITE")
    with closing(connect_sqlite(app._database_path)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER fail_draft_final_record BEFORE INSERT ON idempotency_records "
            f"WHEN NEW.command_name = '{DRAFT_COMMAND}' "
            "BEGIN SELECT RAISE(ABORT, 'test final packet failure'); END;"
        )
    failure = app.save_report_draft(save)
    assert (
        not failure["ok"]
        and cast(dict[str, Any], failure["error"])["code"] == "DATA_INTEGRITY_ERROR"
    )
    assert counts(app) == before_counts
    assert app._workspace.get_presentation(1, "HEAD_SITE") == before_presentation
    assert data(app.get_report_matrix(QUERY))["matrix_revision"] == save["base_revision"]


def test_same_key_replay_returns_original_revision_without_duplicate_audit(
    application: WorkingReferenceApplicationBridge,
) -> None:
    app = application
    save = request(app)
    original = data(app.save_report_draft(save))
    original_counts = counts(app)
    repeated = data(app.save_report_draft(save))
    assert repeated["idempotent_replay"]
    assert repeated["operation_revision"] == original["operation_revision"]
    assert counts(app) == original_counts
    other = request(app)
    other["presentation"] = {"header": {"product_name": "Более новая форма"}}
    data(app.save_report_draft(other))
    current = counts(app)
    old = data(app.save_report_draft(save))
    assert old["operation_revision"] != old["current_revision"]
    assert old["verification_state"] == "stale"
    assert counts(app) == current
    conflicting = app.save_report_draft({**save, "presentation": {"title": "Подмена"}})
    assert cast(dict[str, Any], conflicting["error"])["code"] == "IDEMPOTENCY_CONFLICT"
    assert counts(app) == current


def test_revision_conflict_precedes_mutation_and_title_also_advances_revision(
    application: WorkingReferenceApplicationBridge,
) -> None:
    app = application
    save = request(app)
    changed = data(app.save_report_draft({**save, "changes": [], "presentation": {"title": "Имя"}}))
    assert changed["operation_revision"] != save["base_revision"]
    before = counts(app)
    result = app.save_report_draft({**save, "idempotency_key": uuid4().hex})
    assert cast(dict[str, Any], result["error"])["code"] == "REVISION_CONFLICT"
    assert counts(app) == before


@pytest.mark.parametrize(
    "invalid", ["other_month", "computed", "duplicate", "structure", "plan_month"]
)
def test_invalid_packet_leaves_everything_unchanged(
    application: WorkingReferenceApplicationBridge,
    invalid: str,
) -> None:
    app = application
    save = request(app)
    before = counts(app)
    if invalid == "other_month":
        save["changes"][0]["coordinate"]["operation_date"] = "2027-08-01"
    elif invalid == "computed":
        save["changes"][0]["coordinate"]["metric_code"] = "NOT_EDITABLE"
    elif invalid == "duplicate":
        save["changes"].append(save["changes"][0])
    elif invalid == "structure":
        save["presentation"]["production_codes"] = []
    else:
        save["presentation"]["plans"] = {"2027-08": "9"}
    result = app.save_report_draft(save)
    assert cast(dict[str, Any], result["error"])["code"] == "VALIDATION_ERROR"
    assert counts(app) == before


def test_recovery_is_read_only_author_scoped_and_absence_stays_unknown(
    application: WorkingReferenceApplicationBridge,
) -> None:
    app = application
    save = request(app)
    original = data(app.save_report_draft(save, actor_ref="owner"))
    before = counts(app)
    with closing(connect_sqlite(app._database_path)) as connection:
        before_changes = connection.execute("SELECT total_changes()").fetchone()[0]
    saved = data(app.get_report_draft_save_result(recovery(save), actor_ref="owner"))
    assert saved["operation_revision"] == original["operation_revision"]
    assert saved["verification_state"] == saved["backup_state"] == "pending"
    assert not app.get_report_draft_save_result(recovery(save), actor_ref="stranger")["ok"]
    absent = data(
        app.get_report_draft_save_result({**recovery(save), "idempotency_key": "missing"})
    )
    assert absent["data_state"] == "unknown"
    assert counts(app) == before
    with closing(connect_sqlite(app._database_path)) as connection:
        assert connection.execute("SELECT total_changes()").fetchone()[0] == before_changes


def test_commit_acknowledgement_failure_is_unknown_and_recovery_finds_saved_packet(
    application: WorkingReferenceApplicationBridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = application
    save = request(app)
    real_exit = SqliteReportCellUnitOfWork.__exit__

    def lost_ack(unit: Any, *args: Any) -> Any:
        result = real_exit(unit, *args)
        if isinstance(unit, SqliteReportDraftUnitOfWork):
            raise sqlite3.OperationalError("Simulated lost commit acknowledgement")
        return result

    monkeypatch.setattr(SqliteReportCellUnitOfWork, "__exit__", lost_ack)
    result = app.save_report_draft(save)
    assert cast(dict[str, Any], result["error"])["code"] == "SAVE_OUTCOME_UNKNOWN"
    recovered = data(app.get_report_draft_save_result(recovery(save)))
    assert recovered["data_state"] == "committed"
    assert recovered["verification_state"] == recovered["backup_state"] == "pending"


def test_later_writer_prevents_signing_a_different_revision(
    application: WorkingReferenceApplicationBridge,
) -> None:
    app = application
    signer = data(app.create_report_signer({"display_name": "Автор", "pin": "sign-test-code"}))
    save = request(app)
    result = app.save_report_draft(save)
    data(result)
    app._workspace.save_presentation(1, "HEAD_SITE", {"title": "Позднее изменение"})
    confirmed = data(
        confirm_saved_report(
            app,
            cast(dict[str, Any], result),
            {**QUERY, "month": 9},
            {"signer_id": signer["id"], "pin": "sign-test-code"},
        )
    )
    assert confirmed["data_state"] == "committed"
    assert confirmed["verification_state"] == "verification_error"
    assert "Отчёт изменился" in confirmed["verification_error"]["message"]
    assert "verification" not in confirmed


def test_request_json_schema_agrees_with_current_coordinate_dto(
    application: WorkingReferenceApplicationBridge,
) -> None:
    schema = json.loads(
        (ROOT / "resources/schemas/report-draft/save-report-draft.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    save = {**request(application), "authorization": {"signer_id": "demo", "pin": "demo-code"}}
    validator.validate(save)
    invalid = {**save, "presentation": {"production_codes": []}}
    assert list(validator.iter_errors(invalid))


def test_blank_profile_routes_publish_initial_period_and_reject_earlier_writes(
    application: WorkingReferenceApplicationBridge,
) -> None:
    app = application
    catalog = data(app.list_blank_report_profiles({}))
    assert catalog["initial_period"] == "2027-09-01"
    made = data(app.create_blank_report_profiles({"profile_set_id": catalog["profile_set_id"]}))
    head = next(org for org in made["organizations"] if org["report_type"] == "HEAD_SITE")
    query = {**QUERY, "organization_id": head["organization_id"]}
    orgs = data(app.list_organizations({}))["organizations"]
    selected = next(org for org in orgs if org["id"] == head["organization_id"])
    assert selected["start_period"] == "2027-09-01"
    assert data(app.bootstrap())["default_period"] == {"year": 2027, "month": 9}
    matrix = data(app.get_report_matrix(query))
    early_cell = next(
        c
        for r in matrix["rows"]
        for c in r["cells"]
        if c["state"]["access"] == "editable"
        and c["coordinate"]["operation_date"].startswith("2027-08")
    )
    legacy = app.save_report_cells(
        {
            **query,
            "base_revision": matrix["matrix_revision"],
            "idempotency_key": uuid4().hex,
            "changes": [
                {
                    "coordinate": early_cell["coordinate"],
                    "value": {"kind": "QUANTITY", "quantity": "0"},
                }
            ],
        }
    )
    assert not legacy["ok"]
    header = app.save_report_presentation(
        {
            "report_type": "HEAD_SITE",
            "organization_id": head["organization_id"],
            "plans": {"2027-08": "0"},
            "expected_revision": matrix["matrix_revision"],
        }
    )
    assert not header["ok"]
    early_draft = app.save_report_draft(
        {
            **query,
            "confirmation": {"year": 2027, "month": 8},
            "changes": [],
            "base_revision": matrix["matrix_revision"],
            "idempotency_key": uuid4().hex,
            "presentation": {"title": "Слишком ранний период"},
        }
    )
    assert not early_draft["ok"]
    assert data(app.get_report_matrix(query))["matrix_revision"] == matrix["matrix_revision"]


@pytest.fixture
def secure(tmp_path: Path) -> tuple[SecureDesktopBridge, dict[str, str]]:
    app = SecureDesktopBridge(
        tmp_path / "data" / "reports.sqlite3",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        backups_directory=tmp_path / "backups",
        device_protector=None,
    )
    data(app.setup_access({"display_name": "Администратор", "pin": "admin-test-code"}))
    profile = data(app.get_access_status())["users"][0]
    auth = {"signer_id": profile["id"], "pin": "admin-test-code"}
    data(app.authenticate_access(auth))
    return app, auth


def test_wrong_code_and_preliminary_backup_failure_prevent_packet_write(
    secure: tuple[SecureDesktopBridge, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, auth = secure
    assert app._application is not None
    save = request(app)
    before = counts(app._application)
    assert not app.save_report_draft({**save, "authorization": {**auth, "pin": "wrong"}})["ok"]
    assert counts(app._application) == before

    def fail_backup() -> None:
        raise ValueError("Нет согласованной копии")

    monkeypatch.setattr(app, "_backup_before_write", fail_backup)
    assert not app.save_report_draft({**save, "authorization": auth})["ok"]
    assert counts(app._application) == before


def test_manager_can_save_own_draft_but_cannot_create_scaffolds_or_read_others(
    secure: tuple[SecureDesktopBridge, dict[str, str]],
) -> None:
    app, auth = secure
    manager = data(
        app.create_report_signer(
            {
                "display_name": "Руководитель",
                "role": "project_manager",
                "pin": "manager-test-code",
            }
        )
    )
    manager_auth = {"signer_id": manager["id"], "pin": "manager-test-code"}
    save = request(app, "DAILY_MOVEMENT")
    data(app.save_report_draft({**save, "authorization": manager_auth}))
    assert (
        data(app.get_report_draft_save_result({**recovery(save), "authorization": manager_auth}))[
            "data_state"
        ]
        == "committed"
    )
    assert not app.get_report_draft_save_result({**recovery(save), "authorization": auth})["ok"]
    assert not app.create_blank_report_profiles(
        {
            "profile_set_id": "blank-2027-v1",
            "authorization": manager_auth,
        }
    )["ok"]
    created = data(
        app.create_blank_report_profiles(
            {
                "profile_set_id": "blank-2027-v1",
                "authorization": auth,
            }
        )
    )
    assert created["created"] and created["backup_complete"]


@pytest.mark.parametrize("failure", ["signature", "post_backup"])
def test_post_commit_failure_keeps_data_and_durable_truthful_status(
    secure: tuple[SecureDesktopBridge, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    app, auth = secure
    assert app._application is not None
    save = request(app, "DAILY_MOVEMENT")

    def fail_signature(*_args: object) -> dict[str, Any]:
        raise ValueError("Контрольное расхождение")

    if failure == "signature":
        monkeypatch.setattr(app._application._verification, "verify", fail_signature)
    else:
        import backend.api.secure_desktop_bridge as module

        real_backup = cast(Any, module).backup_database
        call_count = 0

        def backup(*args: Any, **kwargs: Any) -> Path:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Контрольный отказ после записи")
            return cast(Path, real_backup(*args, **kwargs))

        monkeypatch.setattr(module, "backup_database", backup)
    saved = data(app.save_report_draft({**save, "authorization": auth}))
    assert saved["data_state"] == "committed"
    if failure == "signature":
        assert saved["verification_state"] == "verification_error"
        assert saved["verification_error"]["message"] == "Контрольное расхождение"
    else:
        assert saved["verification_state"] == "verified"
        assert saved["backup_state"] == "post_backup_warning"
        assert not saved["backup_complete"]
    before = counts(app._application)

    def no_write_backup() -> None:
        raise AssertionError("Recovery cannot execute backup/write")

    monkeypatch.setattr(app, "_backup_before_write", no_write_backup)
    read = data(app.get_report_draft_save_result({**recovery(save), "authorization": auth}))
    assert read["verification_state"] == saved["verification_state"]
    assert read["backup_state"] == saved["backup_state"]
    assert counts(app._application) == before
    assert auth["pin"] not in json.dumps(saved, ensure_ascii=False)
    assert not app.get_report_draft_save_result(recovery(save))["ok"]
