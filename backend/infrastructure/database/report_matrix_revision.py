"""Revision token reads shared by matrix rendering and held draft transactions."""

from __future__ import annotations

import sqlite3


def matrix_revision(connection: sqlite3.Connection, report_type: str, organization_id: int) -> str:
    row = connection.execute(
        """
        SELECT coalesce(max(id), 0)
        FROM report_fact_revisions
        WHERE report_type = ? AND organization_id = ?
        """,
        (report_type, organization_id),
    ).fetchone()
    layout_revision = connection.execute(
        "SELECT coalesce(max(id), 0) FROM audit_events "
        "WHERE entity_type = 'report_workspace' AND entity_id = ?",
        (f"{organization_id}:{report_type}",),
    ).fetchone()[0]
    layout_revision = max(
        layout_revision,
        connection.execute(
            "SELECT coalesce(max(id), 0) FROM audit_events "
            "WHERE entity_type = 'report_presentation' AND entity_id = ? "
            "AND (json_extract(before_json, '$.title') IS NOT "
            "json_extract(after_json, '$.title') OR "
            "json_extract(before_json, '$.plans') IS NOT "
            "json_extract(after_json, '$.plans') OR "
            "json_extract(before_json, '$.actuals') IS NOT "
            "json_extract(after_json, '$.actuals') OR "
            "json_extract(before_json, '$.header') IS NOT "
            "json_extract(after_json, '$.header') OR "
            "json_extract(before_json, '$.production_codes') IS NOT "
            "json_extract(after_json, '$.production_codes'))",
            (f"{organization_id}:{report_type}",),
        ).fetchone()[0],
    )
    if report_type == "HEAD_SITE":
        layout_revision = max(
            layout_revision,
            connection.execute("SELECT coalesce(max(id), 0) FROM audit_events").fetchone()[0],
        )
    revision = f"db-{int(row[0]) if row is not None else 0}"
    return revision if not layout_revision else f"{revision}-layout-{layout_revision}"
