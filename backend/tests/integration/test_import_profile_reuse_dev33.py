from pathlib import Path
from typing import Any

from backend.application.import_profiles import apply_reusable_profile, reusable_profile
from backend.application.import_workspace import complete_package, stage_package
from backend.infrastructure.database.migrator import connect_sqlite
from backend.tests.integration.test_reference_reports import bridge


def test_only_committed_exact_context_profiles_reused_with_current_quantities(
    tmp_path: Path,
) -> None:
    bridge(tmp_path)
    database = tmp_path / "data/app.db"
    recognition: dict[str, Any] = {
        "profile_id": "enterprise-monthly-weekly",
        "profile_version": 1,
        "structure_fingerprint": "shape-a",
        "sources": {"0:K8": {"value": "170"}},
    }
    saved: dict[str, Any] = {
        **{k: recognition[k] for k in ("profile_id", "profile_version", "structure_fingerprint")},
        "year": 2026,
        "mappings": [
            {
                "source": "0:K8",
                "coordinate": {"component_id": "1"},
                "quantity": "100",
                "confirmed": True,
            }
        ],
        "period_rules": {
            "0:K": {"start": "2026-09-01", "end": "2026-09-06", "reason": "проверено"}
        },
        "sheet_decisions": {},
    }
    stage_package(database, "test-package", 1, "SUBSIDIARY", "abc", saved)
    assert reusable_profile(database, 1, "SUBSIDIARY", 2026, recognition) is None
    connection = connect_sqlite(database)
    try:
        complete_package(connection, "test-package")
        connection.commit()
    finally:
        connection.close()
    profile = reusable_profile(database, 1, "SUBSIDIARY", 2026, recognition)
    assert profile is not None
    assert profile["mappings"][0]["quantity"] == "170"
    for report, year, rec in [
        ("HEAD_SITE", 2026, recognition),
        ("SUBSIDIARY", 2025, recognition),
        ("SUBSIDIARY", 2026, {**recognition, "structure_fingerprint": "shape-b"}),
        ("SUBSIDIARY", 2026, {**recognition, "profile_version": 2}),
    ]:
        assert reusable_profile(database, 1, report, year, rec) is None
    manual = {"mappings": [{"source": "0:K8", "coordinate": {}}], "period_rules": {}}
    merged = apply_reusable_profile(manual, profile)
    assert merged["mappings"][0]["coordinate"] == {}
    assert merged["period_rules"] == saved["period_rules"]
    assert apply_reusable_profile({**manual, "reset_profile": True}, profile)["period_rules"] == {}
