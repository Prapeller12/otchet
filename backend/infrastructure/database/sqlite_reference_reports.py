"""Staging and versioned editing of reports using the supplied workbook layouts."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.excel.reference_workbook import (
    calculate,
    export_reference,
    read_reference,
)


class ReferenceReports:
    def __init__(self, database: Path) -> None:
        self.database = database

    def stage(
        self, source: Path, organization: int, *, allow_errors: bool = False
    ) -> dict[str, Any]:
        if source.suffix.lower() != ".xlsx" or not 0 < source.stat().st_size <= 50 * 1024 * 1024:
            raise ValueError("Нужна книга .xlsx размером до 50 МБ")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with closing(connect_sqlite(self.database)) as conn:
            old = conn.execute(
                "SELECT id,committed FROM reference_workbooks WHERE organization_id=? AND sha256=?",
                (organization, digest),
            ).fetchone()
            if old:
                result = self.get(old[0], organization, staged=True)
                return self.preview(result, already=bool(old[1]))
            doc = read_reference(content)
            if doc["errors"] and not allow_errors:
                raise ValueError("Не удалось проверить формулы: " + "; ".join(doc["errors"][:5]))
            identity = uuid4().hex
            conn.execute(
                "INSERT INTO "
                "reference_workbooks(id,organization_id,report_type,file_name,"
                "sha256,original,document) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    identity,
                    organization,
                    doc["report_type"],
                    source.name,
                    digest,
                    content,
                    json.dumps(doc, ensure_ascii=False),
                ),
            )
            conn.commit()
        return self.preview(self.get(identity, organization, staged=True))

    @staticmethod
    def preview(doc: dict[str, Any], *, already: bool = False) -> dict[str, Any]:
        return {
            "cancelled": False,
            "batch_id": doc["id"],
            "file_name": doc["file_name"],
            "source_sha256": doc["sha256"],
            "status": "COMMITTED" if already else "STAGED",
            "new_count": sum(len(s["cells"]) for s in doc["sheets"]),
            "changed_count": 0,
            "same_count": 0,
            "error_count": 0,
            "issues": [],
            "already_imported": already,
            "reference_workbook": doc,
        }

    def owns(self, identity: str) -> bool:
        with closing(connect_sqlite(self.database)) as conn:
            return (
                conn.execute("SELECT 1 FROM reference_workbooks WHERE id=?", (identity,)).fetchone()
                is not None
            )

    def commit(self, identity: str) -> dict[str, Any]:
        with closing(connect_sqlite(self.database)) as conn:
            row = conn.execute(
                "SELECT committed FROM reference_workbooks WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise ValueError("Предпросмотр не найден")
            conn.execute("UPDATE reference_workbooks SET committed=1 WHERE id=?", (identity,))
            conn.commit()
        return {
            "batch_id": identity,
            "status": "COMMITTED",
            "imported_count": 1,
            "same_count": 0,
            "backup_file": None,
            "already_committed": bool(row[0]),
            "reference_workbook_id": identity,
        }

    def list_reports(self, organization: int) -> list[dict[str, Any]]:
        with closing(connect_sqlite(self.database)) as conn:
            return [
                dict(zip(("id", "file_name", "report_type"), r, strict=True))
                for r in conn.execute(
                    "SELECT id,file_name,report_type FROM reference_workbooks WHERE "
                    "organization_id=? AND committed=1 ORDER BY created_at,id",
                    (organization,),
                )
            ]

    def get(self, identity: str, organization: int, *, staged: bool = False) -> dict[str, Any]:
        with closing(connect_sqlite(self.database)) as conn:
            row = conn.execute(
                "SELECT file_name,sha256,document,committed FROM "
                "reference_workbooks WHERE id=? AND organization_id=?",
                (identity, organization),
            ).fetchone()
            if row is None or not staged and not row[3]:
                raise ValueError("Импортированный отчёт не найден")
            doc = json.loads(row[2])
            revision = 0
            for saved_revision, changes in conn.execute(
                "SELECT revision,changes FROM reference_workbook_revisions WHERE "
                "workbook_id=? ORDER BY revision",
                (identity,),
            ):
                revision = saved_revision
                for change in json.loads(changes):
                    doc["sheets"][change["sheet"]]["cells"][change["address"]] = change["cell"]
        doc.update(id=identity, file_name=row[0], sha256=row[1], revision=revision)
        return calculate(doc)

    def save(
        self, identity: str, organization: int, revision: int, changes: list[Any]
    ) -> dict[str, Any]:
        doc = self.get(identity, organization)
        if doc["revision"] != revision:
            raise ValueError("Отчёт изменён; откройте его заново")
        if not 0 < len(changes) <= 10000:
            raise ValueError("Недопустимое число изменений")
        normalized = []
        seen = set()
        from openpyxl.utils.cell import coordinate_to_tuple

        for change in changes:
            sheet_index = change["sheet"]
            address = change["address"]
            value = change["value"]
            if not isinstance(sheet_index, int) or not 0 <= sheet_index < len(doc["sheets"]):
                raise ValueError("Неизвестный лист")
            if not isinstance(address, str) or not re.fullmatch(r"[A-Z]{1,3}[1-9]\d*", address):
                raise ValueError("Некорректный адрес")
            sheet = doc["sheets"][sheet_index]
            y, x = coordinate_to_tuple(address)
            if y < 9 or y > sheet["rows"] or x > sheet["columns"]:
                raise ValueError("Заголовки и даты защищены от изменения")
            if (sheet_index, address) in seen:
                raise ValueError("Повтор ячейки")
            seen.add((sheet_index, address))
            old = sheet["cells"].get(address, {})
            if old.get("kind") in ("f", "d"):
                raise ValueError("Расчётные ячейки недоступны для ввода")
            # A merged range can only be edited through its top-left cell.
            from openpyxl.utils.cell import range_boundaries

            for merge in sheet["merges"]:
                x1, y1, x2, y2 = cast(tuple[int, int, int, int], range_boundaries(merge))
                if x1 <= x <= x2 and y1 <= y <= y2 and (x, y) != (x1, y1):
                    raise ValueError("Выбрана часть объединённой ячейки")
            if value is not None and (not isinstance(value, str) or len(value) > 1000):
                raise ValueError("Некорректное значение")
            value = None if value is None or not value.strip() else value.strip()
            numeric = x >= 5 and x != 6
            if numeric and value is not None:
                value = value.replace(",", ".")
                if not re.fullmatch(r"[+-]?\d{1,15}(?:\.\d{1,10})?", value):
                    raise ValueError("В этой ячейке нужно число или пустое значение")
            cell = {
                "value": value,
                "kind": "n" if numeric and value is not None else "s",
                "edited": True,
            }
            sheet["cells"][address] = cell
            normalized.append({"sheet": sheet_index, "address": address, "cell": cell})
        calculate(doc)
        if doc["errors"]:
            raise ValueError("Изменение вызывает ошибку формулы: " + doc["errors"][0])
        with closing(connect_sqlite(self.database)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM reference_workbook_revisions "
                "WHERE workbook_id=?",
                (identity,),
            ).fetchone()[0]
            if current != revision:
                raise ValueError("Отчёт изменён; откройте его заново")
            conn.execute(
                "INSERT INTO "
                "reference_workbook_revisions(workbook_id,revision,changes) "
                "VALUES(?,?,?)",
                (identity, revision + 1, json.dumps(normalized, ensure_ascii=False)),
            )
            conn.commit()
        return self.get(identity, organization)

    def export(self, identity: str, organization: int, destination: Path) -> None:
        doc = self.get(identity, organization)
        with closing(connect_sqlite(self.database)) as conn:
            content = conn.execute(
                "SELECT original FROM reference_workbooks WHERE id=?", (identity,)
            ).fetchone()[0]
        data = export_reference(content, doc)
        pending = destination.with_name(f".{uuid4().hex}.xlsx")
        try:
            pending.write_bytes(data)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
