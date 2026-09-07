"""Append-only local attestations. Names are self-declared, not authenticated."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from backend.application.monthly_report import snapshot_hash, snapshot_json
from backend.infrastructure.database.migrator import connect_sqlite


class SqliteReportVerificationRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def status(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with closing(connect_sqlite(self.database_path)) as connection:
            row = connection.execute(
                "SELECT id, snapshot_sha256, signer_name, signed_at FROM report_verifications "
                "WHERE organization_id=? AND report_type=? AND period=? ORDER BY id DESC LIMIT 1",
                (snapshot["organization_id"], snapshot["report_type"], snapshot["period"]),
            ).fetchone()
        digest = snapshot_hash(snapshot)
        if row is None:
            return {"status": "UNVERIFIED", "snapshot_sha256": digest}
        return {
            "status": "VERIFIED" if row[1] == digest else "STALE",
            "id": row[0],
            "snapshot_sha256": digest,
            "verified_sha256": row[1],
            "signer_name": row[2],
            "signed_at": row[3],
        }

    def verify(self, snapshot: dict[str, Any], signer: str, expected_hash: str) -> dict[str, Any]:
        signer = signer.strip()
        if not 1 <= len(signer) <= 120 or any(ord(c) < 32 for c in signer):
            raise ValueError("Укажите ФИО руководителя, не более 120 символов")
        if snapshot_hash(snapshot) != expected_hash:
            raise ValueError("Данные изменились. Повторно откройте подтверждение и проверьте отчёт")
        if any(row["errors"] for row in snapshot["rows"]):
            raise ValueError("Сначала исправьте ошибки расчёта")
        connection = connect_sqlite(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if snapshot.get("_stamp") != database_stamp(connection):
                raise ValueError("Данные изменились во время подтверждения. Повторите проверку")
            previous = self.status(snapshot)
            if previous["status"] == "VERIFIED" and previous["signer_name"] == signer:
                return previous
            connection.execute(
                "INSERT INTO report_verifications(organization_id,report_type,period,"
                "snapshot_sha256,snapshot_json,signer_name) VALUES (?,?,?,?,?,?)",
                (
                    snapshot["organization_id"],
                    snapshot["report_type"],
                    snapshot["period"],
                    snapshot_hash(snapshot),
                    snapshot_json(snapshot),
                    signer,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.status(snapshot)


def database_stamp(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT (SELECT coalesce(max(id),0) FROM audit_events), "
        "(SELECT coalesce(max(id),0) FROM report_fact_revisions)"
    ).fetchone()
    return f"{row[0]}:{row[1]}"
