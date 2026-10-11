from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.blank_report_profiles import (
    create_blank_report_profiles,
    get_organization_start_period,
    list_blank_report_profiles,
)
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_blank_profiles import SqliteBlankProfilesRepository
from backend.infrastructure.database.sqlite_report_workspace import SqliteReportWorkspaceRepository

ROOT = Path(__file__).resolve().parents[3]
DEFINITIONS = ROOT / "resources/report-definitions"
EXPECTED = {
    "kge": (8, 8, 9),
    "molot": (154, 33, 202),
    "kulakova": (69, 7, 69),
    "srz": (82, 17, 188),
    "ravenstvo": (62, 17, 194),
}


@pytest.fixture
def database(tmp_path: Path) -> str:
    database_path = str(tmp_path / "reporting.db")
    with connect_sqlite(database_path) as connection:
        apply_migrations(connection, ROOT / "backend/migrations")
    return database_path


def test_atomic_scaffolds_keep_source_dimensions_and_no_business_quantities(database: str) -> None:
    preview = list_blank_report_profiles(DEFINITIONS)
    result = create_blank_report_profiles(
        SqliteBlankProfilesRepository(database), DEFINITIONS, preview["profile_set_id"]
    )
    assert result["created"] is True
    with connect_sqlite(database) as connection:
        head = int(result["organizations"][0]["organization_id"])
        for org in result["organizations"]:
            positions, manufacturers, _ = EXPECTED[org["template_id"]]
            oid = int(org["organization_id"])
            assert (
                get_organization_start_period(SqliteBlankProfilesRepository(database), oid)
                == "2027-09-01"
            )
            rows = connection.execute(
                "SELECT configuration_json FROM report_workspace_groups WHERE organization_id = ?",
                (oid,),
            ).fetchall()
            assert len(rows) == positions
            unique = set()
            for row in rows:
                config = json.loads(row[0])
                assert config["norm"] == config["opening"] == config["image"] == ""
                assert config["category"] == "UNSPECIFIED"
                assert config["subsidiary"]["designation"] == ""
                for supplier in config["subsidiary"]["suppliers"]:
                    assert supplier["contract"] == ""
                    unique.add(supplier["id"])
            assert len(unique) == manufacturers
            parent = connection.execute(
                "SELECT parent_id FROM organizations WHERE id = ?", (oid,)
            ).fetchone()[0]
            assert parent == (None if org["template_id"] == "kge" else head)


def test_repeat_does_not_rewrite_user_names_or_configuration(database: str) -> None:
    repo = SqliteReportWorkspaceRepository(database)
    original = repo.ensure_default_organization()
    result = create_blank_report_profiles(
        SqliteBlankProfilesRepository(database), DEFINITIONS, "blank-2027-v1"
    )
    oid = int(result["organizations"][0]["organization_id"])
    repo.rename_organization(oid, "Моя площадка")
    with connect_sqlite(database) as connection:
        connection.execute(
            "UPDATE report_workspace_groups SET position_name='Моя позиция' "
            "WHERE organization_id=?",
            (oid,),
        )
    replay = create_blank_report_profiles(
        SqliteBlankProfilesRepository(database), DEFINITIONS, "blank-2027-v1"
    )
    assert not replay["created"]
    assert replay["organizations"] == result["organizations"]
    with connect_sqlite(database) as connection:
        assert (
            connection.execute("SELECT name FROM organizations WHERE id=?", (oid,)).fetchone()[0]
            == "Моя площадка"
        )
        assert (
            connection.execute(
                "SELECT name FROM organizations WHERE id=?", (original.id,)
            ).fetchone()[0]
            == original.name
        )
        assert connection.execute(
            "SELECT DISTINCT position_name FROM report_workspace_groups WHERE organization_id=?",
            (oid,),
        ).fetchall() == [("Моя позиция",)]


def test_mid_install_failure_rolls_back_entire_five_site_set(database: str) -> None:
    with connect_sqlite(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_third BEFORE INSERT ON organizations "
            "WHEN NEW.code = 'BLANK-2027-KULAKOVA' BEGIN "
            "SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        create_blank_report_profiles(
            SqliteBlankProfilesRepository(database), DEFINITIONS, "blank-2027-v1"
        )
    with connect_sqlite(database) as connection:
        for table in (
            "organizations",
            "products",
            "components",
            "report_workspace_groups",
            "report_presentation",
            "audit_events",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_all_source_position_rows_including_unassigned_are_visible(
    database: str, tmp_path: Path
) -> None:
    created = create_blank_report_profiles(
        SqliteBlankProfilesRepository(database), DEFINITIONS, "blank-2027-v1"
    )
    app = WorkingReferenceApplicationBridge(
        Path(database),
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=DEFINITIONS,
        backups_directory=tmp_path / "backups",
        inbox_directory=tmp_path / "inbox",
    )
    for org in created["organizations"]:
        response: dict[str, Any] = app.get_report_matrix(
            {
                "report_type": org["report_type"],
                "organization_id": org["organization_id"],
                "year": 2027,
            }
        )
        assert response["ok"], response
        rows = response["data"]["rows"]
        positions, _, display_rows = EXPECTED[org["template_id"]]
        assert len(rows) == display_rows
        assert len({row["workspace_id"] for row in rows}) == positions
        for row in rows:
            assert all(c["value"]["kind"] == "DATA_NOT_PROVIDED" for c in row["cells"])
            if row["left_values"]["party"] == "Не задан":
                assert all(
                    c["state"]["access"] != "editable"
                    for c in row["cells"]
                    if "BLANKROW" in c["coordinate"]["metric_code"]
                )
