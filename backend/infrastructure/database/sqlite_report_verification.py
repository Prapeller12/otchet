"""Append-only cryptographic attestations of canonical monthly snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.application.monthly_report import snapshot_hash, snapshot_json
from backend.infrastructure.database.migrator import connect_sqlite
from backend.infrastructure.database.sqlite_report_signers import load_signer
from backend.infrastructure.report_crypto import (
    canonical,
    encode,
    fingerprint,
    signature_valid,
    unlock_key,
)


def _payload(row: dict[str, Any], profile: dict[str, Any]) -> str:
    return canonical(
        {
            "schema": "otchet.report-signature.v1",
            "event_id": row["event_id"],
            "signer_id": row["signer_id"],
            "signer_name": row["signer_name"],
            "public_key": row["public_key"],
            "key_fingerprint": profile["key_fingerprint"],
            "signed_at": row["signed_at"],
            "organization_id": int(row["organization_id"]),
            "report_type": row["report_type"],
            "period": row["period"],
            "snapshot_sha256": row["snapshot_sha256"],
        }
    )


class SqliteReportVerificationRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def status(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with closing(connect_sqlite(self.database_path)) as connection:
            return self._status(connection, snapshot)

    def _status(self, connection: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, Any]:
        cursor = connection.execute(
            "SELECT v.*,s.signer_id,s.event_id,s.payload_json,s.public_key,s.signature "
            "FROM report_verifications v LEFT JOIN report_signatures s ON s.verification_id=v.id "
            "WHERE v.organization_id=? AND v.report_type=? AND v.period=? "
            "ORDER BY v.id DESC LIMIT 1",
            (snapshot["organization_id"], snapshot["report_type"], snapshot["period"]),
        )
        values = cursor.fetchone()
        digest = snapshot_hash(snapshot)
        if values is None:
            return {"status": "UNVERIFIED", "snapshot_sha256": digest}
        row = dict(zip((c[0] for c in cursor.description), values, strict=True))
        result = {
            "status": "LEGACY" if row["signature_version"] == 0 else "INVALID",
            "id": row["id"],
            "snapshot_sha256": digest,
            "verified_sha256": row["snapshot_sha256"],
            "signer_name": row["signer_name"],
            "signed_at": row["signed_at"],
        }
        if row["signature_version"] == 0:
            return result
        if not row["signer_id"]:
            return result
        try:
            profile = load_signer(connection, row["signer_id"])
            stored = json.loads(row["snapshot_json"])
            valid = (
                profile["display_name"] == row["signer_name"]
                and profile["public_key"] == row["public_key"]
                and fingerprint(row["public_key"]) == profile["key_fingerprint"]
                and _payload(row, profile) == row["payload_json"]
                and hashlib.sha256(row["snapshot_json"].encode("utf-8")).hexdigest()
                == row["snapshot_sha256"]
                and all(
                    str(stored[k]) == str(row[k])
                    for k in ("organization_id", "report_type", "period")
                )
                and signature_valid(row["public_key"], row["signature"], row["payload_json"])
            )
        except (ValueError, KeyError, TypeError):
            valid = False
        if valid:
            result.update(
                {
                    "status": "VERIFIED" if row["snapshot_sha256"] == digest else "STALE",
                    "signer_id": row["signer_id"],
                    "key_fingerprint": profile["key_fingerprint"],
                    "public_key": row["public_key"],
                    "signature": row["signature"],
                    "algorithm": "Ed25519",
                }
            )
        return result

    def verify(
        self, snapshot: dict[str, Any], signer_id: str, pin: str, expected_hash: str
    ) -> dict[str, Any]:
        if snapshot_hash(snapshot) != expected_hash:
            raise ValueError("Данные изменились. Повторно откройте подтверждение и проверьте отчёт")
        if any(row["errors"] for row in snapshot["rows"]):
            raise ValueError(
                "Подтверждение невозможно. Исправьте расхождения:\n"
                + "\n".join(
                    dict.fromkeys(error for row in snapshot["rows"] for error in row["errors"])
                )
            )
        with closing(connect_sqlite(self.database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if snapshot.get("_stamp") != database_stamp(connection):
                raise ValueError("Данные изменились во время подтверждения. Повторите проверку")
            profile = load_signer(connection, signer_id)
            private = unlock_key(profile, pin)
            previous = self._status(connection, snapshot)
            if previous["status"] == "VERIFIED" and previous["signer_id"] == signer_id:
                return previous
            row = {
                **{k: snapshot[k] for k in ("organization_id", "report_type", "period")},
                "event_id": uuid4().hex,
                "signer_id": signer_id,
                "signer_name": profile["display_name"],
                "public_key": profile["public_key"],
                "signed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "snapshot_sha256": snapshot_hash(snapshot),
                "snapshot_json": snapshot_json(snapshot),
            }
            payload = _payload(row, profile)
            signature = encode(private.sign(payload.encode("utf-8")))
            cursor = connection.execute(
                "INSERT INTO report_verifications(organization_id,report_type,period,"
                "snapshot_sha256,snapshot_json,signer_name,signed_at,signature_version) "
                "VALUES (:organization_id,:report_type,:period,:snapshot_sha256,"
                ":snapshot_json,:signer_name,:signed_at,1)",
                row,
            )
            connection.execute(
                "INSERT INTO report_signatures(verification_id,signer_id,event_id,payload_json,"
                "public_key,signature) VALUES (?,?,?,?,?,?)",
                (
                    cursor.lastrowid,
                    signer_id,
                    row["event_id"],
                    payload,
                    row["public_key"],
                    signature,
                ),
            )
            return self._status(connection, snapshot)


def database_stamp(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT (SELECT coalesce(max(id),0) FROM audit_events), "
        "(SELECT coalesce(max(id),0) FROM report_fact_revisions)"
    ).fetchone()
    return f"{row[0]}:{row[1]}"
