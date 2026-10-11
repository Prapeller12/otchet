"""Atomic installation of five blank workspaces; never rewrite existing reports."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

from backend.application.workspace_fields import configuration_json
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_workspace import _audit, _insert_group
from backend.repositories.report_workspace import WorkspaceGroupTemplate


class SqliteBlankProfilesRepository:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create(self, definition: dict[str, Any]) -> dict[str, Any]:
        return create_profiles(self._database_path, definition)

    def start_period(self, organization_id: int) -> str | None:
        return organization_start_period(self._database_path, organization_id)


def organization_start_period(database_path: str, organization_id: int) -> str | None:
    with closing(connect_sqlite(database_path)) as connection:
        rows = connection.execute(
            "SELECT settings_json FROM report_presentation WHERE organization_id = ?",
            (organization_id,),
        ).fetchall()
        periods = [json.loads(row[0]).get("start_period") for row in rows]
        return next((v for v in periods if isinstance(v, str)), None)


def create_profiles(database_path: str, definition: dict[str, Any]) -> dict[str, Any]:
    connection = connect_sqlite(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        codes = ["BLANK-2027-" + o["template_id"].upper() for o in definition["organizations"]]
        existing = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT code, id FROM organizations WHERE code LIKE 'BLANK-2027-%'"
            ).fetchall()
        }
        present = [code in existing for code in codes]
        if any(present) and not all(present):
            raise ValueError("Набор площадок неполон. Восстановите базу перед повторным созданием")
        result = []
        if all(present):
            for code, org in zip(codes, definition["organizations"], strict=True):
                row = connection.execute(
                    "SELECT p.settings_json FROM report_presentation p "
                    "WHERE p.organization_id = ? AND p.report_type = ?",
                    (existing[code], org["report_type"]),
                ).fetchone()
                if (
                    row is None
                    or json.loads(row[0]).get("profile_set_id") != definition["profile_set_id"]
                ):
                    raise ValueError("Код площадки уже занят другим набором")
                result.append(_result(org, existing[code]))
            connection.commit()
            return {
                "created": False,
                "initial_period": definition["initial_period"],
                "organizations": result,
            }
        order = connection.execute(
            "SELECT coalesce(max(sort_order), -1) + 1 FROM report_workspace_profiles"
        ).fetchone()[0]
        head_id = None
        for index, (code, org) in enumerate(zip(codes, definition["organizations"], strict=True)):
            head = org["report_type"] == "HEAD_SITE"
            if not head and head_id is None:
                raise ValueError("Головная площадка должна быть первой")
            cursor = connection.execute(
                "INSERT INTO organizations (code, name, parent_id) VALUES (?, ?, ?)",
                (code, org["label"], None if head else head_id),
            )
            if cursor.lastrowid is None:
                raise sqlite3.DatabaseError("No organization identifier")
            org_id = int(cursor.lastrowid)
            if head:
                head_id = org_id
            connection.execute(
                "INSERT INTO report_workspace_profiles "
                "(organization_id, workspace_kind, sort_order) VALUES (?, ?, ?)",
                (org_id, "HEAD" if head else "SUBSIDIARY", order + index),
            )
            template = WorkspaceGroupTemplate(
                "wrk-head-assembly-set-group" if head else "wrk-subsidiary-supplier-item-group",
                "ASSEMBLY_SET" if head else "SUPPLIER_ITEM",
                "Производитель · заполните",
                "Позиция · заполните",
                "product" if head else "component",
                True,
            )
            for position in org["positions"]:
                suppliers = [
                    {
                        "id": row["manufacturer_id"].replace("-", ""),
                        "name": "Производитель " + row["manufacturer_id"] + " · заполните",
                        "contract": "",
                        "archived": False,
                    }
                    for row in position["manufacturer_rows"]
                ]
                span = position["source_end_row"] - position["source_anchor_row"] + 1
                spacers = sum(
                    position["source_anchor_row"] <= row <= position["source_end_row"]
                    for row in org["source_spacer_rows"]
                )
                empty_slots = span - spacers - len(suppliers)
                config = configuration_json(
                    {
                        "category": "UNSPECIFIED",
                        "norm": "",
                        "opening": "",
                        "opening_date": definition["initial_period"],
                        "image": "",
                        "indicators": [],
                        "subsidiary": {
                            "number": str(position["ordinal"]),
                            "designation": "",
                            "suppliers": suppliers,
                            "empty_supplier_slots": empty_slots,
                        },
                    }
                )
                _insert_group(
                    connection,
                    organization_id=org_id,
                    report_type=org["report_type"],
                    template=template,
                    party_name="Производитель · заполните",
                    position_name=f"Позиция {position['ordinal']:03d} · заполните",
                    sort_order=position["ordinal"] - 1,
                    configuration_json=config,
                )
            settings = {
                "profile_set_id": definition["profile_set_id"],
                "start_period": definition["initial_period"],
                "default_period": {"year": 2027, "month": 9},
                "title": org["label"] + " · заготовка с сентября 2027",
            }
            connection.execute(
                "INSERT INTO report_presentation VALUES (?, ?, ?)",
                (org_id, org["report_type"], json.dumps(settings, ensure_ascii=False)),
            )
            _audit(
                connection,
                entity_type="blank_report_profiles",
                entity_id=str(org_id),
                action="CREATE",
                after={"template_id": org["template_id"], **settings},
            )
            result.append(_result(org, org_id))
        connection.commit()
        return {
            "created": True,
            "initial_period": definition["initial_period"],
            "organizations": result,
        }
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _result(org: dict[str, Any], organization_id: int) -> dict[str, str]:
    return {
        "template_id": org["template_id"],
        "organization_id": str(organization_id),
        "report_type": org["report_type"],
        "label": org["label"],
    }
