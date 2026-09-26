from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.import_workspace import (
    apply_structure,
    coordinate_remap,
    exchange_actions,
    export_exchange,
    project_structure,
    workspace_stamp,
)
from backend.application.report_cells import RevisionConflictError
from backend.infrastructure.database.encrypted_sqlite import create_encrypted_database
from backend.infrastructure.database.migrator import connect_sqlite

ROOT = Path(__file__).resolve().parents[3]


def application(database: Path, *, initialize: bool = True) -> WorkingReferenceApplicationBridge:
    return WorkingReferenceApplicationBridge(
        database,
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
        initialize_workspace=initialize,
    )


def action(code: str = "001.02", name: str = "Деталь") -> dict[str, Any]:
    return {
        "kind": "UPSERT_POSITION",
        "report_type": "SUBSIDIARY",
        "source_key": "0:C9",
        "position": {
            "code": code,
            "name": name,
            "structure_number": "01.02",
            "parent_code": "001",
            "norm": "2",
            "manufacturer": "Завод А",
        },
        "suppliers": [{"name": "Завод А", "contract": "10"}, {"name": "Завод Б", "contract": "20"}],
    }


def preview(
    app: WorkingReferenceApplicationBridge, actions: list[dict[str, Any]]
) -> dict[str, Any]:
    def build(path: Path) -> dict[str, Any]:
        projected = application(path, initialize=False)
        matrix = dict(projected._build_matrix("SUBSIDIARY", 1, 2026))
        matrix.update(export_exchange(path, 1, "SUBSIDIARY"))
        return matrix

    return project_structure(
        app._database_path, 1, "SUBSIDIARY", app._group_templates("SUBSIDIARY"), actions, build
    )


@pytest.mark.parametrize("encrypted", [False, True])
def test_projection_does_not_write_business_data_and_atomic_commit_reopens(
    tmp_path: Path, encrypted: bool
) -> None:
    database = tmp_path / "data" / "report.db"
    if encrypted:
        create_encrypted_database(database, b"\x12" * 32)
    app = application(database)
    with closing(connect_sqlite(database)) as connection:
        before = workspace_stamp(connection)
    result = preview(app, [action()])
    rows = [r for r in result["matrix"]["rows"] if r["left_values"]["designation"] == "001.02"]
    assert len(rows) == 2
    assert {r["left_values"]["party"] for r in rows} == {"Завод А", "Завод Б"}
    with closing(connect_sqlite(database)) as connection:
        assert workspace_stamp(connection) == before
        connection.execute("BEGIN IMMEDIATE")
        apply_structure(connection, result["plan"])
        connection.commit()
    reopened = application(database, initialize=False)
    assert (
        len(
            [
                r
                for r in cast(dict[str, Any], reopened._build_matrix("SUBSIDIARY", 1, 2026))["rows"]
                if r["left_values"]["designation"] == "001.02"
            ]
        )
        == 2
    )
    repeated = preview(reopened, [action()])
    assert not repeated["plan"]["delta"]
    assert not list((tmp_path / "temp").glob("otchet-import-*"))


def test_structure_and_last_fact_failure_roll_back_together(tmp_path: Path) -> None:
    app = application(tmp_path / "data" / "report.db")
    result = preview(app, [action()])
    with closing(connect_sqlite(app._database_path)) as connection:
        before = workspace_stamp(connection)
        connection.execute("BEGIN IMMEDIATE")
        apply_structure(connection, result["plan"])
        connection.rollback()  # Same rollback used by failing report-fact UoW.
        assert workspace_stamp(connection) == before
        connection.execute("UPDATE organizations SET name='Другой' WHERE id=1")
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(RevisionConflictError):
            apply_structure(connection, result["plan"])
        connection.rollback()


def test_same_caption_different_codes_stay_separate_and_plans_persist(tmp_path: Path) -> None:
    app = application(tmp_path / "data" / "report.db")
    result = preview(
        app,
        [
            action("001"),
            action("002"),
            {
                "kind": "UPSERT_PRESENTATION",
                "patch": {
                    "plans": {"2026-09": "100"},
                    "actuals": {"2026-09": "80"},
                    "header": {"product_name": "Изделие"},
                },
            },
        ],
    )
    assert (
        len(
            {
                r["workspace_id"]
                for r in result["matrix"]["rows"]
                if r["left_values"]["designation"] in {"001", "002"}
            }
        )
        == 2
    )
    with closing(connect_sqlite(app._database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        apply_structure(connection, result["plan"])
    assert app._workspace.get_presentation(1, "SUBSIDIARY")["plans"] == {"2026-09": "100"}


def test_canonical_own_identity_and_foreign_local_collision(tmp_path: Path) -> None:
    left = application(tmp_path / "left" / "data" / "report.db")
    right = application(tmp_path / "right" / "data" / "report.db")
    original = export_exchange(left._database_path, 1, "SUBSIDIARY")
    snapshot = {
        "exchange_identity": original["exchange_identity"],
        "structure": original["exchange_structure"],
        "report_type": "SUBSIDIARY",
        "presentation": {},
    }
    own_actions = exchange_actions(snapshot, [], original["exchange_identity"])
    assert own_actions == []
    assert not preview(left, own_actions)["plan"]["delta"]
    foreign = export_exchange(right._database_path, 1, "SUBSIDIARY")
    imported = preview(right, exchange_actions(snapshot, [], foreign["exchange_identity"]))
    assert imported["structural_actions"][0]["kind"] == "CREATE_POSITION"
    assert (
        imported["structural_actions"][0]["group_id"]
        != snapshot["structure"]["groups"][0]["group_id"]
    )
    assert not coordinate_remap({**snapshot, "coordinates": []}, original)


def test_visible_metadata_edits_apply_to_group_and_plans(tmp_path: Path) -> None:
    app = application(tmp_path / "data/report.db")
    original = export_exchange(app._database_path, 1, "SUBSIDIARY")
    matrix = cast(dict[str, Any], app._build_matrix("SUBSIDIARY", 1, 2026))
    snapshot = {
        "exchange_identity": original["exchange_identity"],
        "structure": original["exchange_structure"],
        "report_type": "SUBSIDIARY",
        "presentation": {"plans": {"2026-09": "20"}},
        "rows": matrix["rows"],
    }
    changes: list[dict[str, Any]] = [
        {"path": ["rows", 0, "left_values", "designation"], "after": "001.04", "type": "text"},
        {"path": ["rows", 0, "left_values", "position"], "after": "Новое имя", "type": "text"},
        {"path": ["presentation", "plans", "2026-09"], "after": "1 000,5", "type": "quantity"},
    ]
    changes.extend(
        [
            {"path": ["rows", 0, "left_values", "norm"], "after": 2.5, "type": "text"},
            {"path": ["rows", 0, "left_values", "contract"], "after": "1 200,5", "type": "text"},
        ]
    )
    actions = exchange_actions(
        snapshot,
        changes,
        original["exchange_identity"],
        target_structure=original["exchange_structure"],
    )
    result = preview(app, actions)
    row = result["matrix"]["rows"][0]
    assert row["left_values"]["position"] == "Новое имя"
    assert row["left_values"]["designation"] == "001.04"
    assert row["left_values"]["norm"] == "2.5"
    assert row["left_values"]["contract"] == "1200.5"
    assert result["matrix"]["presentation"]["plans"]["2026-09"] == "1000.5"


def test_foreign_coordinate_remap_uses_semantic_position_and_supplier(tmp_path: Path) -> None:
    source = application(tmp_path / "a/data/report.db")
    result = preview(source, [action()])
    with closing(connect_sqlite(source._database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        apply_structure(connection, result["plan"])
    exported = export_exchange(source._database_path, 1, "SUBSIDIARY")
    exported["exchange_structure"]["groups"] = [
        g
        for g in exported["exchange_structure"]["groups"]
        if g["configuration"].get("subsidiary", {}).get("designation") == "001.02"
    ]
    row = next(r for r in result["matrix"]["rows"] if r["left_values"]["designation"] == "001.02")
    coordinate = row["cells"][1]["coordinate"]
    snapshot = {
        "exchange_identity": exported["exchange_identity"],
        "structure": exported["exchange_structure"],
        "report_type": "SUBSIDIARY",
        "coordinates": [{"source_cell": "J9", "coordinate": coordinate}],
    }
    target = application(tmp_path / "b/data/report.db")
    identity = export_exchange(target._database_path, 1, "SUBSIDIARY")["exchange_identity"]
    projected = preview(target, exchange_actions(snapshot, [], identity))
    mapping = coordinate_remap(snapshot, projected["matrix"])
    assert len(mapping) == 1
    mapped = next(iter(mapping.values()))
    assert mapped["organization_id"] == "1"
    assert mapped["metric_code"].startswith("SUB_RECEIVED_")
    assert (
        mapped["component_id"]
        == projected["matrix"]["exchange_structure"]["groups"][-1]["subject_id"]
    )


def test_canonical_append_preserves_structure_and_create_rejects_collision(tmp_path: Path) -> None:
    app = application(tmp_path / "data/report.db")
    exported = export_exchange(app._database_path, 1, "SUBSIDIARY")
    snapshot = {
        "exchange_identity": exported["exchange_identity"],
        "structure": exported["exchange_structure"],
        "report_type": "SUBSIDIARY",
    }
    snapshot["structure"]["groups"][0]["position_name"] = "Не заменять"
    snapshot["rows"] = cast(dict[str, Any], app._build_matrix("SUBSIDIARY", 1, 2026))["rows"]
    actions = exchange_actions(
        snapshot,
        [{"path": ["rows", 0, "left_values", "position"], "after": "Не заменять", "type": "text"}],
        exported["exchange_identity"],
        target_structure=export_exchange(app._database_path, 1, "SUBSIDIARY")["exchange_structure"],
    )

    def build(path: Path) -> dict[str, Any]:
        return cast(
            dict[str, Any], application(path, initialize=False)._build_matrix("SUBSIDIARY", 1, 2026)
        )

    result = project_structure(
        app._database_path,
        1,
        "SUBSIDIARY",
        app._group_templates("SUBSIDIARY"),
        actions,
        build,
        mode="APPEND",
    )
    assert not result["plan"]["delta"]
    with pytest.raises(ValueError, match="существует"):
        project_structure(
            app._database_path,
            1,
            "SUBSIDIARY",
            app._group_templates("SUBSIDIARY"),
            actions,
            build,
            mode="CREATE",
        )


def test_foreign_daily_uses_stable_entities_and_single_summary(tmp_path: Path) -> None:
    source = application(tmp_path / "a/data/report.db")
    target = application(tmp_path / "b/data/report.db")
    export = export_exchange(source._database_path, 1, "DAILY_MOVEMENT")
    source_matrix = cast(dict[str, Any], source._build_matrix("DAILY_MOVEMENT", 1, 2026))
    snapshot = {
        "exchange_identity": export["exchange_identity"],
        "structure": export["exchange_structure"],
        "report_type": "DAILY_MOVEMENT",
        "coordinates": [
            {"coordinate": c["coordinate"], "source_cell": str(i)}
            for i, row in enumerate(source_matrix["rows"])
            for c in row["cells"]
            if c["state"]["access"] == "editable"
        ],
    }
    target_identity = export_exchange(target._database_path, 1, "DAILY_MOVEMENT")[
        "exchange_identity"
    ]
    actions = exchange_actions(snapshot, [], target_identity)

    def build(path: Path) -> dict[str, Any]:
        matrix = cast(
            dict[str, Any],
            application(path, initialize=False)._build_matrix("DAILY_MOVEMENT", 1, 2026),
        )
        matrix.update(export_exchange(path, 1, "DAILY_MOVEMENT"))
        return matrix

    projected = project_structure(
        target._database_path,
        1,
        "DAILY_MOVEMENT",
        target._group_templates("DAILY_MOVEMENT"),
        actions,
        build,
    )
    mapping = coordinate_remap(snapshot, projected["matrix"])
    assert len(mapping) == len(snapshot["coordinates"])
    with closing(connect_sqlite(target._database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        apply_structure(connection, projected["plan"])
    repeated = project_structure(
        target._database_path,
        1,
        "DAILY_MOVEMENT",
        target._group_templates("DAILY_MOVEMENT"),
        actions,
        build,
    )
    assert not repeated["plan"]["delta"]


def test_old_export_fact_edit_keeps_newer_header_and_structure(tmp_path: Path) -> None:
    """An unchanged metadata snapshot is evidence, never an instruction to roll back."""
    from openpyxl import load_workbook

    from backend.repositories.report_workspace import WorkspaceGroupDraft

    app = application(tmp_path / "data/report.db")
    workbook_path = tmp_path / "old-export.xlsx"
    app.configure_excel_dialogs(open_file=lambda: workbook_path, save_file=lambda _: workbook_path)
    request = {"report_type": "SUBSIDIARY", "organization_id": "1", "year": 2026}
    assert app.export_report(request)["ok"]
    app._workspace.save_presentation(
        1,
        "SUBSIDIARY",
        {"header": {"product_name": "Актуальное изделие"}, "plans": {"2026-09": "900"}},
    )
    groups = app._workspace.list_groups(1, "SUBSIDIARY")
    app._workspace.save_groups(
        1,
        "SUBSIDIARY",
        app._group_templates("SUBSIDIARY"),
        tuple(
            WorkspaceGroupDraft(
                g.id, g.template_group_id, g.party_name, "Актуальная позиция", '{"norm":"7"}'
            )
            for g in groups
        ),
    )
    workbook = load_workbook(workbook_path)
    first_address = workbook["_Системная карта"]["A6"].value
    workbook["Отчёт"][first_address] = 12
    workbook.save(workbook_path)
    workbook.close()
    preview_response = app.validate_import(request)
    assert preview_response["ok"], preview_response
    staged = cast(dict[str, Any], preview_response["data"])
    assert staged["error_count"] == 0, staged
    assert staged["structural_actions"] == []
    committed = app.commit_import({"batch_id": staged["batch_id"], "year": 2026})
    assert committed["ok"], committed
    reopened = application(app._database_path, initialize=False)
    current = reopened._workspace.get_presentation(1, "SUBSIDIARY")
    assert current["header"] == {"product_name": "Актуальное изделие"}
    assert current["plans"] == {"2026-09": "900"}
    current_group = reopened._workspace.list_groups(1, "SUBSIDIARY")[0]
    assert current_group.position_name == "Актуальная позиция"
    import json

    assert json.loads(current_group.configuration_json)["norm"] == "7"
    values = reopened._service.get_cells(report_type="SUBSIDIARY", organization_id="1")
    assert any(value.value.quantity == "12" for value in values)
