"""Review structure on an isolated encrypted copy; replay a checked delta atomically.

A preview never adds business rows to the live database. Numeric local identifiers
are reserved only in the copy; the full business stamp is checked under the commit
write lock before those identifiers are used in the live database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from backend.application.report_cells import RevisionConflictError
from backend.application.report_header import apply_header_patch
from backend.application.subsidiary_report import quantity
from backend.application.workspace_fields import validate_configuration
from backend.infrastructure.database.encrypted_sqlite import (
    create_encrypted_database,
    forget_database_key,
    has_database_key,
)
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_workspace import _insert_group, _name
from backend.repositories.report_workspace import WorkspaceGroupTemplate

_TABLES = (
    "products",
    "components",
    "report_workspace_groups",
    "report_presentation",
    "exchange_entity_bindings",
    "head_report_links",
    "report_calendars",
)
_KEYS = {
    "products": ("id",),
    "components": ("id",),
    "report_workspace_groups": ("id",),
    "report_presentation": ("organization_id", "report_type"),
    "exchange_entity_bindings": ("organization_id", "report_type", "semantic_key"),
    "head_report_links": ("subsidiary_organization_id", "head_group_id"),
    "report_calendars": ("organization_id", "report_type", "year"),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f"SELECT * FROM {table}")
    names = [column[0] for column in cursor.description]
    return sorted((dict(zip(names, row, strict=True)) for row in cursor.fetchall()), key=_json)


def workspace_stamp(connection: sqlite3.Connection) -> str:
    """Include facts, metadata, identities and configuration; exclude staging writes."""
    state = {table: _rows(connection, table) for table in _TABLES}
    state["organizations"] = _rows(connection, "organizations")
    state["dataset_identity"] = _rows(connection, "exchange_dataset_identity")
    state["audit"] = connection.execute(
        "SELECT coalesce(max(id),0) FROM audit_events WHERE action != 'STAGE_EXCEL_IMPORT'"
    ).fetchone()[0]
    state["facts"] = connection.execute(
        "SELECT coalesce(max(id),0) FROM report_fact_revisions"
    ).fetchone()[0]
    return hashlib.sha256(_json(state).encode()).hexdigest()


def exchange_identity(connection: sqlite3.Connection, organization_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT dataset_id FROM exchange_dataset_identity WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise ValueError("Идентификатор набора данных не настроен")
    dataset = str(row[0])
    return {
        "dataset_id": dataset,
        "organization_id": uuid5(UUID(dataset), f"organization:{organization_id}").hex,
    }


def export_exchange(database: Path, organization_id: int, report_type: str) -> dict[str, Any]:
    with closing(connect_sqlite(database)) as connection:
        identity = exchange_identity(connection, organization_id)
        namespace = UUID(identity["dataset_id"])
        groups = []
        for group in _rows(connection, "report_workspace_groups"):
            if (
                group["organization_id"] != organization_id
                or group["report_type"] != report_type
                or not group["is_active"]
            ):
                continue
            config = json.loads(group["configuration_json"])
            bindings = connection.execute(
                "SELECT identity_json FROM exchange_entity_bindings WHERE group_id=?",
                (group["id"],),
            ).fetchall()
            groups.append(
                {
                    "group_id": str(group["id"]),
                    "entity_id": uuid5(namespace, f"group:{group['id']}").hex,
                    "subject_id": str(group["product_id"] or group["component_id"]),
                    "subject_kind": group["subject_kind"],
                    "template_group_id": group["template_group_id"],
                    "party_name": group["party_name"],
                    "position_name": group["position_name"],
                    "configuration": config,
                    "semantic_identities": [json.loads(r[0]) for r in bindings],
                    "suppliers": [
                        {
                            **s,
                            "entity_id": uuid5(
                                namespace, f"group:{group['id']}:supplier:{s['id']}"
                            ).hex,
                        }
                        for s in config.get("subsidiary", {}).get("suppliers", [])
                    ],
                }
            )
        row = connection.execute(
            "SELECT settings_json FROM report_presentation WHERE organization_id=? "
            "AND report_type=?",
            (organization_id, report_type),
        ).fetchone()
        return {
            "organization_id": str(organization_id),
            "exchange_identity": identity,
            "exchange_structure": {
                "version": 1,
                "groups": groups,
                "presentation": {} if row is None else json.loads(row[0]),
            },
        }


def _semantic(action: Mapping[str, Any]) -> dict[str, str]:
    position = action["position"]
    code = str(position.get("code", "")).strip()
    if not code:
        raise ValueError("Для создания позиции требуется текстовое обозначение")
    return {
        "product_context": str(action.get("product_context", "")).strip(),
        "organization_context": str(action.get("organization_context", "")).strip(),
        "code": code,
        "parent_code": str(position.get("parent_code", "")).strip(),
    }


def _apply_position(
    connection: sqlite3.Connection,
    organization: int,
    report: str,
    templates: Sequence[WorkspaceGroupTemplate],
    action: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    identity = _semantic(action)
    key = _json(identity)
    match = connection.execute(
        "SELECT group_id FROM exchange_entity_bindings WHERE organization_id=? AND "
        "report_type=? AND semantic_key=?",
        (organization, report, key),
    ).fetchone()
    groups = [
        g
        for g in _rows(connection, "report_workspace_groups")
        if g["organization_id"] == organization and g["report_type"] == report and g["is_active"]
    ]
    if match is not None:
        found = [g for g in groups if g["id"] == match[0]]
        if not found:
            raise ValueError("Ранее связанная позиция архивирована; выберите назначение явно")
    else:
        # Existing manually entered rows may match by explicit code and product context,
        # never by caption or a database-local component id alone.
        presentation = connection.execute(
            "SELECT settings_json FROM report_presentation WHERE organization_id=? "
            "AND report_type=?",
            (organization, report),
        ).fetchone()
        header = json.loads(presentation[0]).get("header", {}) if presentation else {}
        product = identity["product_context"]
        scope_matches = not product or product in {
            header.get("product_name"),
            header.get("product_designation"),
        }
        found = [
            g
            for g in groups
            if scope_matches
            and not identity["parent_code"]
            and json.loads(g["configuration_json"]).get("subsidiary", {}).get("designation")
            == identity["code"]
        ]
        if len(found) > 1:
            raise ValueError("Неоднозначное обозначение позиции; требуется выбор назначения")
    if found and mode == "CREATE":
        raise ValueError("Позиция уже существует; выберите дополнение или обновление")
    position = action["position"]
    name = _name(str(position.get("name", "")))
    group = found[0] if found else None
    config = json.loads(group["configuration_json"]) if group else {}
    if isinstance(action.get("configuration"), dict):
        config = {**config, **action["configuration"]}
    detail = dict(config.get("subsidiary", {}))
    suppliers = [dict(item) for item in detail.get("suppliers", [])]
    source_suppliers = action.get("suppliers") or [
        {"name": position.get("manufacturer", ""), "contract": position.get("contract", "")}
    ]
    suppliers_added = 0
    for source in source_suppliers:
        supplier_name = _name(str(source.get("name", "")))
        matches = [s for s in suppliers if s["name"].casefold() == supplier_name.casefold()]
        if len(matches) > 1:
            raise ValueError("Изготовитель неоднозначен; выберите существующую связь")
        contract = quantity(str(source.get("contract", "")))
        if matches:
            if contract and mode != "APPEND":
                matches[0]["contract"] = contract
        else:
            supplier_id = (
                hashlib.sha256((key + "\0" + supplier_name).encode()).hexdigest()[:24].upper()
            )
            suppliers.append(
                {"id": supplier_id, "name": supplier_name, "contract": contract, "archived": False}
            )
            suppliers_added += 1
    if group is None or mode != "APPEND":
        detail.update(
            number=str(position.get("structure_number", "")), designation=identity["code"]
        )
        if str(position.get("norm", "")):
            config["norm"] = str(position["norm"])
    detail["suppliers"] = suppliers
    if action.get("weekly_supply") is True:
        detail["weekly_supply"] = True
    config["subsidiary"] = detail
    config = validate_configuration(config)
    if group is None:
        if len(groups) >= 500:
            raise ValueError("В одной форме допускается не более 500 позиций")
        template_id = action.get("template_group_id")
        options = [
            t
            for t in templates
            if t.repeatable and (not template_id or t.template_group_id == template_id)
        ]
        if not options:
            raise ValueError("Нет подходящего шаблона для создания позиции")
        # Monthly forms each have one repeatable position template. Daily imports
        # must supply a template explicitly when there are multiple choices.
        if len(options) != 1:
            raise ValueError("Выберите тип создаваемой позиции")
        group_id = _insert_group(
            connection,
            organization_id=organization,
            report_type=report,
            template=options[0],
            party_name=suppliers[0]["name"],
            position_name=name,
            sort_order=max((g["sort_order"] for g in groups), default=-1) + 1,
            configuration_json=_json(config),
        )
    else:
        group_id = group["id"]
        connection.execute(
            "UPDATE report_workspace_groups SET configuration_json=?, position_name=? WHERE id=?",
            (_json(config), group["position_name"] if mode == "APPEND" else name, group_id),
        )
    connection.execute(
        "INSERT INTO exchange_entity_bindings VALUES(?,?,?,?,?) ON "
        "CONFLICT(organization_id,report_type,semantic_key) DO NOTHING",
        (organization, report, key, group_id, _json(identity)),
    )
    return {
        "kind": "CREATE_POSITION" if group is None else "UPDATE_POSITION",
        "source_key": action.get("source_key"),
        "group_id": str(group_id),
        "position": name,
        "designation": identity["code"],
        "suppliers_added": suppliers_added,
        "suppliers": suppliers,
        "semantic_identity": identity,
    }


def _apply_presentation(
    connection: sqlite3.Connection,
    organization: int,
    report: str,
    patch: Mapping[str, Any],
    mode: str,
) -> None:
    row = connection.execute(
        "SELECT settings_json FROM report_presentation WHERE organization_id=? AND report_type=?",
        (organization, report),
    ).fetchone()
    before = {} if row is None else json.loads(row[0])
    allowed = {
        "header",
        "title",
        "plans",
        "actuals",
        "production_codes",
        "production_code_actuals",
        "confirm_production_totals",
    }
    if patch.keys() - allowed:
        raise ValueError("Неизвестные реквизиты импорта")
    patch = dict(patch)
    if "production_codes" in patch:
        combined = [dict(code) for code in before.get("production_codes", [])]
        for incoming_code in patch["production_codes"]:
            matches = [
                code
                for code in combined
                if code["label"].casefold() == incoming_code["label"].casefold()
            ]
            if len(matches) > 1:
                raise ValueError("Неоднозначный код готового изделия")
            if matches:
                target = matches[0]
                for field in ("plans", "actuals"):
                    target[field] = {
                        **target.get(field, {}),
                        **{
                            month: value
                            for month, value in incoming_code.get(field, {}).items()
                            if value != ""
                            and (mode != "APPEND" or not target.get(field, {}).get(month))
                        },
                    }
            else:
                combined.append(dict(incoming_code))
        patch["production_codes"] = combined
    if mode == "APPEND":
        if "header" in patch:
            patch["header"] = {
                key: value
                for key, value in patch["header"].items()
                if not before.get("header", {}).get(key)
            }
        if before.get("title"):
            patch.pop("title", None)
    normalized = apply_header_patch(before, patch, report)
    for field in ("plans", "actuals"):
        if field in patch:
            values = patch[field]
            if not isinstance(values, dict) or any(
                not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", k) for k in values
            ):
                raise ValueError("Для планов и выпуска требуется точный месяц")
            normalized[field] = {
                **before.get(field, {}),
                **{
                    k: "" if v == "#CLEAR" else quantity(v)
                    for k, v in values.items()
                    if v != "" and (mode != "APPEND" or not before.get(field, {}).get(k))
                },
            }
    if "title" in patch:
        normalized["title"] = _name(patch["title"])
    after = {**before, **normalized}
    connection.execute(
        "INSERT INTO report_presentation VALUES(?,?,?) ON "
        "CONFLICT(organization_id,report_type) DO UPDATE SET "
        "settings_json=excluded.settings_json",
        (organization, report, _json(after)),
    )


def project_structure(
    database: Path,
    organization_id: int,
    report_type: str,
    templates: Sequence[WorkspaceGroupTemplate],
    actions: Sequence[Mapping[str, Any]],
    build_matrix: Callable[[Path], dict[str, Any]],
    *,
    mode: str = "UPDATE",
) -> dict[str, Any]:
    if mode not in {"CREATE", "APPEND", "UPDATE"}:
        raise ValueError("Неизвестный режим импорта")
    if len(actions) > 1000:
        raise ValueError("Слишком много изменений структуры")
    temporary_root = database.parent.parent / "temp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="otchet-import-", dir=temporary_root) as folder:
        projection = Path(folder) / "projection.sqlite3"
        encrypted = has_database_key(database)
        if encrypted:
            create_encrypted_database(projection, os.urandom(32))
        try:
            with (
                closing(connect_sqlite(database)) as source,
                closing(connect_sqlite(projection)) as target,
            ):
                source.backup(target)
                stamp = workspace_stamp(target)
                before = {table: _rows(target, table) for table in _TABLES}
                target.execute("BEGIN IMMEDIATE")
                reviewed = []
                for action in actions:
                    if action.get("report_type", report_type) != report_type:
                        raise ValueError("Тип исходной структуры не совпадает с отчётом")
                    if action.get("kind") == "CANONICAL_GROUP":
                        reviewed.append(
                            _canonical_group(target, organization_id, report_type, action, mode)
                        )
                    elif action.get("kind") == "FOREIGN_CANONICAL_GROUP":
                        reviewed.append(
                            _foreign_group(
                                target, organization_id, report_type, templates, action, mode
                            )
                        )
                    elif action.get("kind") == "UPSERT_POSITION":
                        reviewed.append(
                            _apply_position(
                                target, organization_id, report_type, templates, action, mode
                            )
                        )
                    elif action.get("kind") == "UPSERT_PRESENTATION":
                        _apply_presentation(
                            target, organization_id, report_type, action["patch"], mode
                        )
                        reviewed.append(dict(action))
                    else:
                        raise ValueError("Неизвестное изменение структуры")
                target.commit()
                after = {table: _rows(target, table) for table in _TABLES}
            matrix = build_matrix(projection)
            with closing(connect_sqlite(projection)) as projected_connection:
                after = {table: _rows(projected_connection, table) for table in _TABLES}
            identities: dict[str, list[dict[str, Any]]] = {}
            for binding in after["exchange_entity_bindings"]:
                identities.setdefault(str(binding["group_id"]), []).append(
                    json.loads(binding["identity_json"])
                )
            for row in matrix.get("rows", []):
                group_id = str(
                    row.get("workspace_id")
                    or str(row.get("group_id", "")).removeprefix("workspace-group-")
                )
                variants = identities.get(group_id, [])
                if len(variants) == 1:
                    row.setdefault("left_values", {}).update(
                        {
                            key: variants[0].get(key, "")
                            for key in ("parent_code", "product_context")
                        }
                    )
            delta = []
            for table in _TABLES:
                old = {_json([r.get(k) for k in _KEYS[table]]): r for r in before[table]}
                for row in after[table]:
                    previous = old.get(_json([row.get(k) for k in _KEYS[table]]))
                    if previous != row:
                        delta.append({"table": table, "before": previous, "after": row})
            return {
                "matrix": matrix,
                "structural_actions": reviewed,
                "issues": [],
                "plan": {
                    "version": 1,
                    "stamp": stamp,
                    "organization_id": organization_id,
                    "report_type": report_type,
                    "mode": mode,
                    "delta": delta,
                },
            }
        finally:
            if encrypted:
                forget_database_key(projection)


def apply_structure(connection: sqlite3.Connection, plan: Mapping[str, Any]) -> None:
    """Caller owns BEGIN IMMEDIATE, fact writes and the single final commit."""
    if not connection.in_transaction:
        raise ValueError("Импорт структуры требует общей транзакции")
    if plan.get("version") != 1 or workspace_stamp(connection) != plan.get("stamp"):
        raise RevisionConflictError(
            "База изменилась после проверки импорта; выполните проверку заново"
        )
    for item in plan["delta"]:
        table = item["table"]
        if table not in _TABLES:
            raise ValueError("Неизвестная таблица структурного импорта")
        row = item["after"]
        columns = [column[1] for column in connection.execute(f"PRAGMA table_info({table})")]
        if set(row) != set(columns):
            raise ValueError("Схема пакета импорта устарела")
        if item["before"] is None:
            connection.execute(
                f"INSERT INTO {table} ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(row[c] for c in columns),
            )
        else:
            keys = _KEYS[table]
            fields = [c for c in columns if c not in keys]
            connection.execute(
                f"UPDATE {table} SET {','.join(c + '=?' for c in fields)} "
                f"WHERE {' AND '.join(c + '=?' for c in keys)}",
                tuple(row[c] for c in fields) + tuple(row[c] for c in keys),
            )
    if plan["delta"]:
        connection.execute(
            "INSERT INTO "
            "audit_events(actor_ref,entity_type,entity_id,action,after_json) "
            "VALUES(?,?,?,?,?)",
            (
                "excel-import",
                "report_workspace",
                f"{plan['organization_id']}:{plan['report_type']}",
                "IMPORT_STRUCTURE",
                _json(plan),
            ),
        )


def stage_package(
    database: Path,
    package_id: str,
    organization_id: int,
    report_type: str,
    source_sha256: str,
    package: Mapping[str, Any],
) -> None:
    with closing(connect_sqlite(database)) as connection, connection:
        existing = connection.execute(
            "SELECT status,organization_id,report_type,source_sha256 "
            "FROM exchange_import_packages WHERE id=?",
            (package_id,),
        ).fetchone()
        if existing and tuple(existing[1:]) != (organization_id, report_type, source_sha256):
            raise ValueError("Пакет принадлежит другому источнику или назначению")
        if existing and existing[0] == "COMMITTED":
            raise ValueError("Этот пакет уже импортирован")
        connection.execute(
            "INSERT INTO "
            "exchange_import_packages(id,organization_id,report_type,"
            "source_sha256,status,package_json) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET package_json=excluded.package_json",
            (package_id, organization_id, report_type, source_sha256, "STAGED", _json(package)),
        )


def get_package(database: Path, package_id: str, organization_id: int) -> dict[str, Any]:
    with closing(connect_sqlite(database)) as connection:
        row = connection.execute(
            "SELECT status,package_json FROM exchange_import_packages WHERE id=? "
            "AND organization_id=?",
            (package_id, organization_id),
        ).fetchone()
        if row is None:
            raise ValueError("Проверенный пакет импорта не найден")
        return {**json.loads(row[1]), "package_status": row[0]}


def complete_package(connection: sqlite3.Connection, package_id: str) -> None:
    connection.execute(
        "UPDATE exchange_import_packages SET "
        "status='COMMITTED',committed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id=? AND status='STAGED'",
        (package_id,),
    )


def exchange_actions(
    snapshot: Mapping[str, Any],
    changes: Sequence[Mapping[str, Any]],
    target_identity: Mapping[str, Any],
    *,
    target_structure: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Translate reviewed v2 metadata; never reuse a foreign numeric identity."""
    import copy

    incoming = copy.deepcopy(dict(snapshot))
    source_identity = incoming.get("exchange_identity", {})
    own = bool(source_identity.get("dataset_id")) and all(
        source_identity.get(k) == target_identity.get(k) for k in ("dataset_id", "organization_id")
    )
    if own and not changes:
        return []
    if own:
        if target_structure is None:
            raise ValueError("Для изменения реквизитов требуется актуальная структура рабочей базы")
        incoming["structure"] = copy.deepcopy(dict(target_structure))
        incoming["presentation"] = copy.deepcopy(dict(target_structure.get("presentation", {})))
    touched_groups: set[str] = set()
    presentation_paths: list[list[Any]] = []
    for change in changes:
        path = list(change["path"])
        if own:
            if (
                path[0] == "presentation"
                and len(path) == 3
                and path[1] in {"header", "plans", "actuals"}
            ):
                presentation_paths.append(path)
            elif not (len(path) == 4 and path[0] == "rows" and path[2] == "left_values"):
                raise ValueError("Неизвестное редактируемое поле обмена")
        container: Any = incoming
        for key in path[:-1]:
            container = (
                container[int(key)]
                if isinstance(container, list)
                else container.setdefault(key, {})
            )
        value = change["after"]
        if change.get("type") == "quantity" and value not in ("", "#CLEAR", None):
            from backend.application.import_recognition import decimal_text

            value = decimal_text(value)
        elif value == "#CLEAR" and change.get("type") != "quantity":
            value = ""
        last = path[-1]
        if isinstance(container, list):
            container[int(last)] = value
        else:
            container[last] = value
    structure = incoming.get("structure", {})
    if not isinstance(structure, dict) or not isinstance(structure.get("groups"), list):
        raise ValueError("В книге отсутствует переносимая структура позиций")
    # The visible identifier cells are editable metadata controls. Translate them
    # back to their structural group/supplier instead of discarding row edits.
    for change in changes:
        path = list(change["path"])
        if len(path) != 4 or path[0] != "rows" or path[2] != "left_values":
            continue
        row = incoming["rows"][int(path[1])]
        group_id = str(
            row.get("workspace_id") or str(row.get("group_id", "")).removeprefix("workspace-group-")
        )
        touched_groups.add(group_id)
        groups = [g for g in structure["groups"] if str(g["group_id"]) == group_id]
        if own:
            source_groups = [
                g
                for g in snapshot.get("structure", {}).get("groups", [])
                if str(g["group_id"]) == group_id
            ]
            if (
                len(source_groups) != 1
                or len(groups) != 1
                or groups[0].get("entity_id") != source_groups[0].get("entity_id")
            ):
                raise ValueError("Идентичность редактируемой позиции изменилась после экспорта")
        if len(groups) != 1:
            raise ValueError("Реквизит строки не связан с единственной позицией")
        group = groups[0]
        value = row["left_values"][path[3]]
        field = path[3]
        if field in {"norm", "contract"} and value not in ("", None):
            from backend.application.import_recognition import decimal_text

            value = decimal_text(value)
        elif field not in {"norm", "contract"}:
            value = str(value)
        configuration = group.setdefault("configuration", {})
        if field == "position":
            group["position_name"] = value
        elif field == "norm":
            configuration["norm"] = value
        elif field in {"number", "designation", "party", "contract"}:
            from backend.application.subsidiary_report import default_detail

            detail = configuration.setdefault("subsidiary", default_detail(group["party_name"]))
            if field in {"number", "designation"}:
                detail[field] = str(value)
            else:
                suppliers = [
                    item
                    for item in detail["suppliers"]
                    if item["id"] == row.get("supplier_id", "PRIMARY")
                ]
                if len(suppliers) != 1:
                    raise ValueError("Реквизит изготовителя не связан с единственной строкой")
                suppliers[0]["name" if field == "party" else "contract"] = value
        else:
            raise ValueError("Этот реквизит не поддерживает обратный перенос: " + str(field))
    presentation = dict(incoming.get("presentation") or structure.get("presentation", {}))
    actions: list[dict[str, Any]] = []
    for group in structure["groups"]:
        config = group.get("configuration", {})
        if own:
            if str(group["group_id"]) not in touched_groups:
                continue
            actions.append(
                {
                    "kind": "CANONICAL_GROUP",
                    "group": group,
                    "source_identity": source_identity,
                    "report_type": incoming["report_type"],
                }
            )
            continue
        if config.get("head_links"):
            raise ValueError(
                "Связи с отчётами другой базы требуют явного сопоставления организаций"
            )
        actions.append(
            {
                "kind": "FOREIGN_CANONICAL_GROUP",
                "group": group,
                "source_identity": source_identity,
                "report_type": incoming["report_type"],
            }
        )
    # Export snapshots may contain derived annual/completion fields. Only primary
    # presentation fields participate; formulas are always recomputed by backend.
    allowed = {"header", "title", "plans", "actuals", "production_codes"}
    patch: dict[str, Any] = {key: value for key, value in presentation.items() if key in allowed}
    if own:
        patch = {}
        for path in presentation_paths:
            patch.setdefault(path[1], {})[path[2]] = presentation[path[1]][path[2]]
    if patch:
        actions.insert(
            0,
            {"kind": "UPSERT_PRESENTATION", "report_type": incoming["report_type"], "patch": patch},
        )
    return actions


def _canonical_group(
    connection: sqlite3.Connection,
    organization: int,
    report: str,
    action: Mapping[str, Any],
    mode: str = "UPDATE",
) -> dict[str, Any]:
    expected = exchange_identity(connection, organization)
    if action.get("source_identity") != expected:
        raise ValueError("Идентификатор источника структуры не совпадает с выбранной базой")
    group = action["group"]
    row = connection.execute(
        "SELECT product_id,component_id,template_group_id FROM report_workspace_groups "
        "WHERE id=? AND organization_id=? AND report_type=? AND is_active=1",
        (group["group_id"], organization, report),
    ).fetchone()
    if (
        row is None
        or str(row[0] or row[1]) != str(group["subject_id"])
        or row[2] != group["template_group_id"]
    ):
        raise ValueError("Исходная позиция отсутствует или её идентичность изменилась")
    if mode == "CREATE":
        raise ValueError("Позиция уже существует; выберите дополнение или обновление")
    if mode == "APPEND":
        return {
            "kind": "SAME_POSITION",
            "group_id": str(group["group_id"]),
            "position": group["position_name"],
        }
    config = validate_configuration(group["configuration"])
    before = json.loads(
        connection.execute(
            "SELECT configuration_json FROM report_workspace_groups WHERE id=?",
            (group["group_id"],),
        ).fetchone()[0]
    )
    if group["configuration"] == before:
        config = before
    old_suppliers = {s["id"] for s in before.get("subsidiary", {}).get("suppliers", [])}
    new_suppliers = {s["id"] for s in config.get("subsidiary", {}).get("suppliers", [])}
    if not old_suppliers <= new_suppliers:
        raise ValueError(
            "Изготовителей с историей нельзя удалить импортом; используйте архивирование"
        )
    if config.get("head_links", []) != before.get("head_links", []):
        raise ValueError(
            "Изменение связей головной площадки требует отдельного подтверждения в настройках"
        )
    connection.execute(
        "UPDATE report_workspace_groups SET party_name=?,position_name=?,configuration_json=? "
        "WHERE id=?",
        (
            _name(group["party_name"]),
            _name(group["position_name"]),
            _json(config),
            group["group_id"],
        ),
    )
    return {
        "kind": "UPDATE_POSITION",
        "group_id": str(group["group_id"]),
        "position": group["position_name"],
    }


def coordinate_remap(
    snapshot: Mapping[str, Any], projected_exchange: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Map source subject and supplier identities to the reviewed target structure."""
    source_groups = snapshot.get("structure", {}).get("groups", [])
    target_groups = projected_exchange.get("exchange_structure", {}).get("groups", [])
    source_identity = snapshot.get("exchange_identity", {})
    target_identity = projected_exchange.get("exchange_identity", {})
    same = source_identity == target_identity
    mapping: dict[str, dict[str, Any]] = {}
    organization_id = projected_exchange.get("organization_id")
    if organization_id is None:
        raise ValueError("Не указана целевая организация для сопоставления координат")
    for record in snapshot.get("coordinates", []):
        coordinate = record["coordinate"]
        subject_kind = "component" if "component_id" in coordinate else "product"
        key = subject_kind + "_id"
        sources = [
            g
            for g in source_groups
            if g["subject_kind"] == subject_kind
            and str(g["subject_id"]) == str(coordinate.get(key))
        ]
        if len(sources) != 1:
            raise ValueError("Координата книги неоднозначно связана со структурой")
        source = sources[0]
        if same:
            targets = [g for g in target_groups if g["entity_id"] == source["entity_id"]]
        else:
            targets = [
                g
                for g in target_groups
                if any(
                    identity.get("source_dataset_id") == source_identity.get("dataset_id")
                    and identity.get("source_entity_id") == source.get("entity_id")
                    for identity in g.get("semantic_identities", [])
                )
            ]
        if len(targets) != 1:
            raise ValueError("Неоднозначная позиция в целевой структуре; требуется сопоставление")
        target = targets[0]
        resolved = {
            **coordinate,
            key: str(target["subject_id"]),
            "organization_id": str(organization_id),
        }
        metric = str(coordinate.get("metric_code", ""))
        for supplier in source.get("configuration", {}).get("subsidiary", {}).get("suppliers", []):
            if metric.endswith("_" + supplier["id"]):
                candidates = [
                    s
                    for s in target.get("configuration", {})
                    .get("subsidiary", {})
                    .get("suppliers", [])
                    if s["name"].casefold() == supplier["name"].casefold()
                ]
                if len(candidates) != 1:
                    raise ValueError("Неоднозначный изготовитель в целевой структуре")
                resolved["metric_code"] = metric[: -len(supplier["id"])] + candidates[0]["id"]
                break
        mapping[_json(coordinate)] = resolved
    return mapping


def _foreign_group(
    connection: sqlite3.Connection,
    organization: int,
    report: str,
    templates: Sequence[WorkspaceGroupTemplate],
    action: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Import a foreign entity into its own namespace, never merge by local id/name."""
    group = action["group"]
    source = action["source_identity"]
    try:
        dataset_id = UUID(str(source["dataset_id"])).hex
        entity_id = UUID(str(group["entity_id"])).hex
    except (ValueError, KeyError) as error:
        raise ValueError("У исходной позиции нет устойчивого идентификатора обмена") from error
    identity = {"source_dataset_id": dataset_id, "source_entity_id": entity_id}
    key = _json(identity)
    templates_by_id = {t.template_group_id: t for t in templates}
    template = templates_by_id.get(group["template_group_id"])
    if template is None or template.subject_kind != group["subject_kind"]:
        raise ValueError("Шаблон исходной позиции не соответствует выбранной форме")
    config = validate_configuration(group["configuration"])
    if config.get("head_links"):
        raise ValueError("Связи другой базы требуют явного сопоставления организаций")
    binding = connection.execute(
        "SELECT group_id FROM exchange_entity_bindings WHERE organization_id=? "
        "AND report_type=? AND semantic_key=?",
        (organization, report, key),
    ).fetchone()
    target = None
    if binding:
        target = connection.execute(
            "SELECT id,configuration_json FROM report_workspace_groups WHERE id=? AND is_active=1",
            (binding[0],),
        ).fetchone()
        if target is None:
            raise ValueError("Ранее импортированная позиция архивирована")
        if mode == "CREATE":
            raise ValueError("Позиция уже импортирована; выберите дополнение или обновление")
    elif not template.repeatable:
        # Canonical singleton calculation blocks have a semantic template identity.
        # Reuse only an untouched seed with no facts or foreign binding.
        candidates = connection.execute(
            "SELECT "
            "id,configuration_json,subject_kind,coalesce(product_id,component_id) "
            "FROM report_workspace_groups WHERE organization_id=? AND report_type=? "
            "AND template_group_id=? AND is_active=1",
            (organization, report, template.template_group_id),
        ).fetchall()
        if len(candidates) != 1:
            raise ValueError("Итоговый блок не может дублироваться при импорте")
        candidate = candidates[0]
        subject_column = "product_id" if candidate[2] == "product" else "component_id"
        exists = connection.execute(
            f"SELECT 1 FROM report_fact_revisions WHERE organization_id=? "
            f"AND report_type=? AND {subject_column}=? LIMIT 1",
            (organization, report, candidate[3]),
        ).fetchone()
        bound = connection.execute(
            "SELECT 1 FROM exchange_entity_bindings WHERE group_id=?", (candidate[0],)
        ).fetchone()
        if exists or bound or json.loads(candidate[1]):
            raise ValueError("Итоговый блок уже настроен; требуется отдельное сопоставление")
        target = candidate[:2]
    created = target is None
    if target is None:
        count = connection.execute(
            "SELECT count(*) FROM report_workspace_groups WHERE "
            "organization_id=? AND report_type=? AND is_active=1",
            (organization, report),
        ).fetchone()[0]
        if count >= 500:
            raise ValueError("В одной форме допускается не более 500 позиций")
        sort_order = connection.execute(
            "SELECT coalesce(max(sort_order),-1)+1 FROM report_workspace_groups "
            "WHERE organization_id=? AND report_type=?",
            (organization, report),
        ).fetchone()[0]
        group_id = _insert_group(
            connection,
            organization_id=organization,
            report_type=report,
            template=template,
            party_name=_name(group["party_name"]),
            position_name=_name(group["position_name"]),
            sort_order=sort_order,
            configuration_json=_json(config),
        )
    else:
        group_id = target[0]
        if mode != "APPEND":
            previous = json.loads(target[1])
            old_ids = {s["id"] for s in previous.get("subsidiary", {}).get("suppliers", [])}
            new_ids = {s["id"] for s in config.get("subsidiary", {}).get("suppliers", [])}
            if not old_ids <= new_ids:
                raise ValueError("Изготовителя с историей нельзя удалить обратным импортом")
            connection.execute(
                "UPDATE report_workspace_groups SET "
                "party_name=?,position_name=?,configuration_json=? WHERE id=?",
                (
                    _name(group["party_name"]),
                    _name(group["position_name"]),
                    _json(config),
                    group_id,
                ),
            )
    connection.execute(
        "INSERT INTO exchange_entity_bindings VALUES(?,?,?,?,?) ON "
        "CONFLICT(organization_id,report_type,semantic_key) DO NOTHING",
        (organization, report, key, group_id, _json(identity)),
    )
    return {
        "kind": "CREATE_POSITION" if created else "UPDATE_POSITION",
        "group_id": str(group_id),
        "position": group["position_name"],
        "source_entity_id": entity_id,
        "suppliers_added": len(config.get("subsidiary", {}).get("suppliers", [])) if created else 0,
    }
