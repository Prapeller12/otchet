"""SQLite repository for user-configurable WORKING_REFERENCE layouts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import cast
from uuid import uuid4

from backend.infrastructure.database.migrator import connect_sqlite
from backend.repositories.report_workspace import (
    SubjectKind,
    WorkspaceGroup,
    WorkspaceGroupDraft,
    WorkspaceGroupTemplate,
    WorkspaceKind,
    WorkspaceOrganization,
)

_DEFAULT_ORGANIZATION_CODE = "WRK-REFERENCE-PREVIEW"


class SqliteReportWorkspaceRepository:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def ensure_default_organization(self) -> WorkspaceOrganization:
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, name FROM organizations WHERE code = ?",
                (_DEFAULT_ORGANIZATION_CODE,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO organizations (code, name) VALUES (?, ?)",
                    (_DEFAULT_ORGANIZATION_CODE, "Головная площадка"),
                )
                if cursor.lastrowid is None:
                    raise sqlite3.DatabaseError("organization insert returned no identifier")
                organization_id = int(cursor.lastrowid)
                name = "Головная площадка"
            else:
                organization_id = int(row[0])
                name = str(row[1])
            connection.execute(
                """
                INSERT INTO report_workspace_profiles (organization_id, workspace_kind, sort_order)
                VALUES (?, 'HEAD', 0)
                ON CONFLICT (organization_id) DO NOTHING
                """,
                (organization_id,),
            )
            connection.commit()
            return WorkspaceOrganization(organization_id, name, "HEAD")
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_organizations(self) -> tuple[WorkspaceOrganization, ...]:
        connection = connect_sqlite(self._database_path)
        try:
            rows = connection.execute(
                """
                SELECT organizations.id, organizations.name, profiles.workspace_kind
                FROM organizations
                JOIN report_workspace_profiles AS profiles
                  ON profiles.organization_id = organizations.id
                WHERE organizations.is_active = 1
                ORDER BY profiles.sort_order, organizations.name, organizations.id
                """
            ).fetchall()
            return tuple(
                WorkspaceOrganization(int(row[0]), str(row[1]), cast(WorkspaceKind, row[2]))
                for row in rows
            )
        finally:
            connection.close()

    def create_organization(self, name: str) -> WorkspaceOrganization:
        normalized = _name(name)
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            parent = connection.execute(
                """
                SELECT profiles.organization_id
                FROM report_workspace_profiles AS profiles
                JOIN organizations ON organizations.id = profiles.organization_id
                WHERE profiles.workspace_kind = 'HEAD' AND organizations.is_active = 1
                ORDER BY profiles.sort_order, profiles.organization_id
                LIMIT 1
                """
            ).fetchone()
            if parent is None:
                raise ValueError("Головная площадка не настроена")
            code = f"WRK-SUB-{uuid4().hex.upper()}"
            cursor = connection.execute(
                "INSERT INTO organizations (code, name, parent_id) VALUES (?, ?, ?)",
                (code, normalized, int(parent[0])),
            )
            if cursor.lastrowid is None:
                raise sqlite3.DatabaseError("organization insert returned no identifier")
            organization_id = int(cursor.lastrowid)
            sort_order = int(
                connection.execute(
                    "SELECT coalesce(max(sort_order), 0) + 1 FROM report_workspace_profiles"
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO report_workspace_profiles (organization_id, workspace_kind, sort_order)
                VALUES (?, 'SUBSIDIARY', ?)
                """,
                (organization_id, sort_order),
            )
            _audit(
                connection,
                entity_type="organization",
                entity_id=str(organization_id),
                action="CREATE_WORKSPACE_ORGANIZATION",
                after={"name": normalized, "workspace_kind": "SUBSIDIARY"},
            )
            connection.commit()
            return WorkspaceOrganization(organization_id, normalized, "SUBSIDIARY")
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rename_organization(self, organization_id: int, name: str) -> WorkspaceOrganization:
        normalized = _name(name)
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT organizations.name, profiles.workspace_kind
                FROM organizations
                JOIN report_workspace_profiles AS profiles
                  ON profiles.organization_id = organizations.id
                WHERE organizations.id = ? AND organizations.is_active = 1
                """,
                (organization_id,),
            ).fetchone()
            if current is None:
                raise ValueError("Организация не найдена")
            connection.execute(
                "UPDATE organizations SET name = ? WHERE id = ?",
                (normalized, organization_id),
            )
            _audit(
                connection,
                entity_type="organization",
                entity_id=str(organization_id),
                action="RENAME_WORKSPACE_ORGANIZATION",
                before={"name": str(current[0])},
                after={"name": normalized},
            )
            connection.commit()
            return WorkspaceOrganization(
                organization_id, normalized, cast(WorkspaceKind, current[1])
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def archive_organization(self, organization_id: int) -> None:
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT organizations.name, profiles.workspace_kind
                FROM organizations
                JOIN report_workspace_profiles AS profiles
                  ON profiles.organization_id = organizations.id
                WHERE organizations.id = ? AND organizations.is_active = 1
                """,
                (organization_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Организация не найдена")
            if row[1] == "HEAD":
                raise ValueError("Головную площадку нельзя архивировать")
            connection.execute(
                "UPDATE organizations SET is_active = 0 WHERE id = ?", (organization_id,)
            )
            connection.execute(
                "UPDATE report_workspace_groups SET is_active = 0 WHERE organization_id = ?",
                (organization_id,),
            )
            _audit(
                connection,
                entity_type="organization",
                entity_id=str(organization_id),
                action="ARCHIVE_WORKSPACE_ORGANIZATION",
                before={"name": str(row[0]), "active": True},
                after={"name": str(row[0]), "active": False},
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_groups(
        self,
        organization_id: int,
        report_type: str,
        templates: tuple[WorkspaceGroupTemplate, ...],
    ) -> tuple[WorkspaceGroup, ...]:
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _require_active_organization(connection, organization_id)
            count = int(
                connection.execute(
                    """
                    SELECT count(*) FROM report_workspace_groups
                    WHERE organization_id = ? AND report_type = ?
                    """,
                    (organization_id, report_type),
                ).fetchone()[0]
            )
            if count == 0:
                legacy_subjects = _legacy_preview_subjects(connection, organization_id)
                for sort_order, template in enumerate(templates):
                    _insert_group(
                        connection,
                        organization_id=organization_id,
                        report_type=report_type,
                        template=template,
                        party_name=(
                            "Головная площадка"
                            if template.group_kind == "WAREHOUSE_READINESS"
                            else "Изготовитель/поставщик"
                        ),
                        position_name=template.default_position_name,
                        sort_order=sort_order,
                        subject_id=legacy_subjects.get(template.subject_kind),
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self._list_groups(organization_id, report_type)

    def save_groups(
        self,
        organization_id: int,
        report_type: str,
        templates: tuple[WorkspaceGroupTemplate, ...],
        drafts: tuple[WorkspaceGroupDraft, ...],
    ) -> tuple[WorkspaceGroup, ...]:
        template_map = {template.template_group_id: template for template in templates}
        if len(drafts) > 500:
            raise ValueError("В одной форме допускается не более 500 позиций")
        connection = connect_sqlite(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _require_active_organization(connection, organization_id)
            existing_rows = connection.execute(
                """
                SELECT id, template_group_id FROM report_workspace_groups
                WHERE organization_id = ? AND report_type = ?
                """,
                (organization_id, report_type),
            ).fetchall()
            existing = {int(row[0]): str(row[1]) for row in existing_rows}
            retained: set[int] = set()
            for sort_order, draft in enumerate(drafts):
                template = template_map.get(draft.template_group_id)
                if template is None or not template.repeatable:
                    raise ValueError("Неизвестный тип настраиваемой строки")
                party_name = _name(draft.party_name)
                position_name = _name(draft.position_name)
                if draft.id is None:
                    group_id = _insert_group(
                        connection,
                        organization_id=organization_id,
                        report_type=report_type,
                        template=template,
                        party_name=party_name,
                        position_name=position_name,
                        sort_order=sort_order,
                    )
                    retained.add(group_id)
                    continue
                if existing.get(draft.id) != draft.template_group_id:
                    raise ValueError("Строка не принадлежит выбранной форме")
                connection.execute(
                    """
                    UPDATE report_workspace_groups
                    SET party_name = ?, position_name = ?, sort_order = ?, is_active = 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND organization_id = ? AND report_type = ?
                    """,
                    (
                        party_name,
                        position_name,
                        sort_order,
                        draft.id,
                        organization_id,
                        report_type,
                    ),
                )
                retained.add(draft.id)
            repeatable_ids = [
                template.template_group_id for template in templates if template.repeatable
            ]
            if repeatable_ids:
                placeholders = ", ".join("?" for _ in repeatable_ids)
                parameters: list[object] = [organization_id, report_type, *repeatable_ids]
                sql = (
                    "UPDATE report_workspace_groups SET is_active = 0, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE organization_id = ? AND report_type = ? "
                    f"AND template_group_id IN ({placeholders})"
                )
                if retained:
                    retained_placeholders = ", ".join("?" for _ in retained)
                    sql += f" AND id NOT IN ({retained_placeholders})"
                    parameters.extend(sorted(retained))
                connection.execute(sql, parameters)
            _audit(
                connection,
                entity_type="report_workspace",
                entity_id=f"{organization_id}:{report_type}",
                action="SAVE_WORKSPACE_LAYOUT",
                after={
                    "organization_id": organization_id,
                    "report_type": report_type,
                    "active_group_ids": sorted(retained),
                },
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self._list_groups(organization_id, report_type)

    def _list_groups(self, organization_id: int, report_type: str) -> tuple[WorkspaceGroup, ...]:
        connection = connect_sqlite(self._database_path)
        try:
            rows = connection.execute(
                """
                SELECT id, template_group_id, party_name, position_name, subject_kind,
                       product_id, component_id, sort_order
                FROM report_workspace_groups
                WHERE organization_id = ? AND report_type = ? AND is_active = 1
                ORDER BY sort_order, id
                """,
                (organization_id, report_type),
            ).fetchall()
            return tuple(
                WorkspaceGroup(
                    id=int(row[0]),
                    organization_id=organization_id,
                    report_type=report_type,
                    template_group_id=str(row[1]),
                    party_name=str(row[2]),
                    position_name=str(row[3]),
                    subject_kind=cast(SubjectKind, row[4]),
                    subject_id=int(row[5] if row[4] == "product" else row[6]),
                    sort_order=int(row[7]),
                )
                for row in rows
            )
        finally:
            connection.close()


def _insert_group(
    connection: sqlite3.Connection,
    *,
    organization_id: int,
    report_type: str,
    template: WorkspaceGroupTemplate,
    party_name: str,
    position_name: str,
    sort_order: int,
    subject_id: int | None = None,
) -> int:
    if subject_id is not None:
        product_id = subject_id if template.subject_kind == "product" else None
        component_id = subject_id if template.subject_kind == "component" else None
    else:
        subject_code = f"WRK-LAYOUT-{uuid4().hex.upper()}"
        if template.subject_kind == "product":
            subject_cursor = connection.execute(
                """
                INSERT INTO products (organization_id, code, name)
                VALUES (?, ?, ?)
                """,
                (organization_id, subject_code, position_name),
            )
            product_id = subject_cursor.lastrowid
            component_id = None
        else:
            subject_cursor = connection.execute(
                """
                INSERT INTO components (organization_id, code, name, kind)
                VALUES (?, ?, ?, 'WORKING_REFERENCE')
                """,
                (organization_id, subject_code, position_name),
            )
            product_id = None
            component_id = subject_cursor.lastrowid
        if subject_cursor.lastrowid is None:
            raise sqlite3.DatabaseError("workspace subject insert returned no identifier")
    cursor = connection.execute(
        """
        INSERT INTO report_workspace_groups (
            organization_id, report_type, template_group_id, party_name, position_name,
            subject_kind, product_id, component_id, sort_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            report_type,
            template.template_group_id,
            party_name,
            position_name,
            template.subject_kind,
            product_id,
            component_id,
            sort_order,
        ),
    )
    if cursor.lastrowid is None:
        raise sqlite3.DatabaseError("workspace group insert returned no identifier")
    return int(cursor.lastrowid)


def _legacy_preview_subjects(
    connection: sqlite3.Connection, organization_id: int
) -> dict[SubjectKind, int]:
    """Reuse subjects from preview v0.1.0 so an upgrade keeps saved cells visible."""
    result: dict[SubjectKind, int] = {}
    product = connection.execute(
        "SELECT id FROM products WHERE organization_id = ? AND code = 'WRK-PREVIEW-PRODUCT'",
        (organization_id,),
    ).fetchone()
    if product is not None:
        result["product"] = int(product[0])
    component = connection.execute(
        "SELECT id FROM components WHERE organization_id = ? AND code = 'WRK-PREVIEW-COMPONENT'",
        (organization_id,),
    ).fetchone()
    if component is not None:
        result["component"] = int(component[0])
    return result


def _require_active_organization(connection: sqlite3.Connection, organization_id: int) -> None:
    row = connection.execute(
        """
        SELECT 1 FROM organizations
        JOIN report_workspace_profiles AS profiles
          ON profiles.organization_id = organizations.id
        WHERE organizations.id = ? AND organizations.is_active = 1
        """,
        (organization_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Организация не найдена")


def _name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Наименование не может быть пустым")
    if len(normalized) > 200:
        raise ValueError("Наименование не должно превышать 200 символов")
    return normalized


def _audit(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    after: Mapping[str, object],
    before: Mapping[str, object] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events (
            actor_ref, entity_type, entity_id, action, before_json, after_json
        ) VALUES ('local-workspace-settings', ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            action,
            None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
            json.dumps(after, ensure_ascii=False, sort_keys=True),
        ),
    )


__all__ = ["SqliteReportWorkspaceRepository"]
