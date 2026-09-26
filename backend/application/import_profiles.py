"""Reuse only committed, context- and structure-identical recognition decisions."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from backend.infrastructure.database.migrator import connect_sqlite


def reusable_profile(
    database: Path,
    organization_id: int,
    report_type: str,
    year: int | None,
    recognition: dict[str, Any],
) -> dict[str, Any] | None:
    """No new writes: the atomically committed package is the durable profile version.

    Local target coordinates are reusable only in this database and organization.
    Never reuse saved numeric quantities; each current source is parsed afresh.
    """
    fingerprint = recognition.get("structure_fingerprint")
    if not fingerprint or year is None:
        return None
    with closing(connect_sqlite(database)) as connection:
        rows = connection.execute(
            "SELECT id,package_json FROM exchange_import_packages WHERE organization_id=? "
            "AND report_type=? AND status='COMMITTED' ORDER BY committed_at DESC,id DESC",
            (organization_id, report_type),
        )
        for batch_id, raw in rows:
            package = json.loads(raw)
            if (
                package.get("year") != year
                or package.get("structure_fingerprint") != fingerprint
                or package.get("profile_id") != recognition.get("profile_id")
                or package.get("profile_version") != recognition.get("profile_version")
            ):
                continue
            sources = recognition.get("sources", {})
            mappings = []
            for old in package.get("mappings", []):
                source = old.get("source")
                if source not in sources:
                    continue
                if old.get("skip_reason"):
                    mappings.append({"source": source, "skip_reason": old["skip_reason"]})
                elif old.get("coordinate"):
                    mappings.append(
                        {
                            "source": source,
                            "coordinate": old["coordinate"],
                            "quantity": sources[source]["value"],
                            "confirmed": True,
                            "method": "saved_profile",
                        }
                    )
            return {
                "profile_batch_id": batch_id,
                "mappings": mappings,
                "period_rules": package.get("period_rules", {}),
                "sheet_decisions": package.get("sheet_decisions", {}),
                "structure_overrides": package.get("structure_overrides", {}),
            }
    return None


def apply_reusable_profile(request: dict[str, Any], saved: dict[str, Any] | None) -> dict[str, Any]:
    """A new manual choice replaces the old choice, including intentionally empty targets.

    reset_profile is useful when a saved exclusion needs a fresh review.
    """
    if saved is None or request.get("reset_profile"):
        return dict(request)
    mappings = {m["source"]: m for m in saved["mappings"]}
    for entry in request.get("mappings", []):
        mappings[entry["source"]] = entry
    return {
        **request,
        "mappings": list(mappings.values()),
        "period_rules": {**saved["period_rules"], **request.get("period_rules", {})},
        "sheet_decisions": {**saved["sheet_decisions"], **request.get("sheet_decisions", {})},
        "structure_overrides": {
            **saved.get("structure_overrides", {}),
            **request.get("structure_overrides", {}),
        },
    }
