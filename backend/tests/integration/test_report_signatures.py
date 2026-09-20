"""Security boundaries, legacy migration and two-line printing."""

from __future__ import annotations

import base64
import importlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge
from backend.application.monthly_report import snapshot_hash, snapshot_json
from backend.infrastructure.database.migrator import apply_migrations, connect_sqlite
from backend.infrastructure.database.sqlite_report_verification import (
    SqliteReportVerificationRepository,
)

ROOT = Path(__file__).resolve().parents[3]
QUERY = {"report_type": "DAILY_MOVEMENT", "organization_id": "1", "year": 2026, "month": 9}
PIN = "test-secret-9162"


def data(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["data"]


@pytest.fixture
def app(tmp_path: Path) -> WorkingReferenceApplicationBridge:
    return WorkingReferenceApplicationBridge(
        tmp_path / "report.db",
        migrations_directory=ROOT / "backend/migrations",
        definitions_directory=ROOT / "resources/report-definitions",
    )


def create(app: WorkingReferenceApplicationBridge, name: str = "Проверяющий") -> dict[str, Any]:
    return dict(data(app.create_report_signer({"display_name": name, "pin": PIN})))


def sign_request(
    app: WorkingReferenceApplicationBridge, signer: dict[str, Any], query: dict[str, Any] = QUERY
) -> dict[str, Any]:
    status = data(app.get_report_verification(query))
    return {
        **query,
        "signer_id": signer["id"],
        "pin": PIN,
        "confirmed": True,
        "snapshot_sha256": status["snapshot_sha256"],
    }


def test_profiles_require_admin_and_do_not_return_secrets(
    app: WorkingReferenceApplicationBridge,
) -> None:
    assert not app.create_report_signer({"display_name": "Первый", "pin": "123"})["ok"]
    assert data(app.list_report_signers({})) == []
    admin = create(app)
    assert admin["role"] == "admin"
    request = {"display_name": "Оператор", "pin": PIN, "admin_id": admin["id"]}
    assert not app.create_report_signer(request)["ok"]
    assert not app.create_report_signer({**request, "admin_pin": "wrong-pin"})["ok"]
    signer = data(app.create_report_signer({**request, "admin_pin": PIN}))
    assert signer["role"] == "reviewer"
    assert signer["key_fingerprint"] != admin["key_fingerprint"]
    assert not app.create_report_signer({**request, "admin_pin": PIN})["ok"]
    assert not app.create_report_signer(
        {**request, "display_name": "Третий", "admin_id": signer["id"], "admin_pin": PIN}
    )["ok"]
    users = data(app.list_report_signers({}))
    assert len(users) == 2
    assert all(
        set(user) == {"id", "display_name", "role", "key_fingerprint", "created_at"}
        for user in users
    )
    assert PIN not in json.dumps(users)


def test_wrong_pin_never_creates_or_reuses_signature(
    app: WorkingReferenceApplicationBridge, tmp_path: Path
) -> None:
    request = sign_request(app, create(app))
    assert not app.verify_report({**request, "pin": "wrong-pin"})["ok"]
    assert data(app.get_report_verification(QUERY))["status"] == "UNVERIFIED"
    signed = data(app.verify_report(request))
    assert signed["status"] == "VERIFIED"
    assert not app.verify_report({**request, "pin": "wrong-pin"})["ok"]
    with sqlite3.connect(tmp_path / "report.db") as conn:
        assert conn.execute("SELECT count(*) FROM report_signatures").fetchone()[0] == 1
        public, signature, payload = conn.execute(
            "SELECT public_key,signature,payload_json FROM report_signatures"
        ).fetchone()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
            base64.b64decode(signature), payload.encode("utf-8")
        )
        assert len(base64.b64decode(signature)) == 64
        profile = conn.execute("SELECT encrypted_private_key FROM report_signers").fetchone()[0]
        assert len(base64.b64decode(profile)) == 48
    assert PIN not in json.dumps(signed)
    assert not app.verify_report({**request, "signer_name": "Подмена"})["ok"]


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("report_verifications", "signer_name", "Подмена"),
        ("report_verifications", "signed_at", "2026-01-01"),
        ("report_verifications", "snapshot_json", "{}"),
        ("report_verifications", "snapshot_sha256", "0" * 64),
        ("report_signatures", "signature", "bad-signature"),
        ("report_signatures", "payload_json", "{}"),
        ("report_signatures", "event_id", "different"),
        ("report_signatures", "public_key", "bad-key"),
        ("report_signers", "display_name", "Подмена"),
        ("report_signers", "key_fingerprint", "0" * 16),
    ],
)
def test_tampering_is_invalid(
    app: WorkingReferenceApplicationBridge, tmp_path: Path, table: str, field: str, value: str
) -> None:
    data(app.verify_report(sign_request(app, create(app))))
    with sqlite3.connect(tmp_path / "report.db") as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE {table} SET {field}=?", (value,))
        # Simulate an external database editor bypassing the append-only trigger.
        conn.execute(f"DROP TRIGGER {table}_no_update")
        conn.execute(f"UPDATE {table} SET {field}=?", (value,))
    result = data(app.get_report_verification(QUERY))
    assert result["status"] == "INVALID"
    assert "signature" not in result


def test_missing_signature_is_invalid(
    app: WorkingReferenceApplicationBridge, tmp_path: Path
) -> None:
    data(app.verify_report(sign_request(app, create(app))))
    with sqlite3.connect(tmp_path / "report.db") as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM report_signatures")
        conn.execute("DROP TRIGGER report_signatures_no_delete")
        conn.execute("DELETE FROM report_signatures")
    assert data(app.get_report_verification(QUERY))["status"] == "INVALID"


def test_legacy_migration_preserves_old_attestation(tmp_path: Path) -> None:
    old = tmp_path / "old-migrations"
    old.mkdir()
    for path in (ROOT / "backend/migrations").glob("*.sql"):
        if path.name < "0012":
            shutil.copy(path, old / path.name)
    database = tmp_path / "legacy.db"
    conn = connect_sqlite(database)
    try:
        apply_migrations(conn, old)
        conn.execute("INSERT INTO organizations(id,code,name) VALUES (1,'TEST','Тест')")
        snapshot = {
            "organization_id": "1",
            "report_type": "DAILY_MOVEMENT",
            "period": "2026-09",
            "rows": [],
        }
        conn.execute(
            "INSERT INTO report_verifications(organization_id,report_type,period,"
            "snapshot_sha256,snapshot_json,signer_name) VALUES (1,?,?,?,?,'Старое ФИО')",
            (
                snapshot["report_type"],
                snapshot["period"],
                snapshot_hash(snapshot),
                snapshot_json(snapshot),
            ),
        )
        conn.commit()
        assert apply_migrations(conn, ROOT / "backend/migrations") == ("0012", "0013", "0014")
        assert apply_migrations(conn, ROOT / "backend/migrations") == ()
    finally:
        conn.close()
    result = SqliteReportVerificationRepository(str(database)).status(snapshot)
    assert result["status"] == "LEGACY" and result["signer_name"] == "Старое ФИО"


@pytest.mark.parametrize("report_type", ["DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"])
def test_signed_pdf_has_exactly_two_footer_lines(
    app: WorkingReferenceApplicationBridge,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_type: str,
) -> None:
    query = {**QUERY, "report_type": report_type}
    # Longest accepted name also must fit a single physical line.
    signed = data(app.verify_report(sign_request(app, create(app, "И" * 120), query)))
    assert signed["status"] == "VERIFIED"
    module = importlib.import_module("reportlab.pdfgen.canvas")
    original = module.Canvas.drawString
    drawn: dict[int, list[tuple[float, str]]] = {}

    def record(self: Any, x: float, y: float, text: str, *args: Any, **kwargs: Any) -> None:
        if y in (17, 28) and x == 18:
            drawn.setdefault(self.getPageNumber(), []).append((y, text))
        original(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(module.Canvas, "drawString", record)
    app.configure_pdf_dialog(lambda _: tmp_path / "signed.pdf")
    data(app.export_pdf(query))
    assert drawn
    for lines in drawn.values():
        assert len(lines) == 2
        assert lines[0][1].startswith("Подтверждено: ")
        assert signed["key_fingerprint"] in lines[1][1]
        assert signed["signature"] in lines[1][1]
        assert all("\n" not in text and PIN not in text for _, text in lines)
