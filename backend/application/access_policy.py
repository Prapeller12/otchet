"""Deny-by-default desktop command policy; independent of the visible interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

Permission = Literal["admin", "reviewer"]
READ_COMMANDS = frozenset(
    {
        "health",
        "bootstrap",
        "get_report_matrix",
        "get_report_layout",
        "get_report_verification",
        "list_report_signers",
        "list_organizations",
        "export_report",
        "export_pdf",
    }
)
ADMIN_COMMANDS = frozenset(
    {
        "create_organization",
        "rename_organization",
        "archive_organization",
        "save_report_layout",
        "create_report_signer",
        "validate_import",
        "commit_import",
    }
)


def is_plan_coordinate(coordinate: object) -> bool:
    """Protect canonical plan rows including their per-code variants."""
    if not isinstance(coordinate, Mapping):
        return False
    metric = coordinate.get("metric_code", "")
    return isinstance(metric, str) and "PLAN" in metric.split("_")


def classify_permission(method: str, payload: object) -> Permission | None:
    """Every persisted change requires a freshly entered role-appropriate code.

    Excel uploads/transfer affect staging tables, and source workbooks can include
    arbitrary plan cells, so all import/source changes belong to administrator.
    A UI-only resize is also a write when persisted and therefore needs a code.
    """
    if method in READ_COMMANDS:
        return None
    if method in ADMIN_COMMANDS:
        return "admin"
    request = payload if isinstance(payload, Mapping) else {}
    if method == "save_report_cells":
        changes = request.get("changes", [])
        if (
            isinstance(changes, Sequence)
            and not isinstance(changes, (str, bytes))
            and any(
                isinstance(change, Mapping) and is_plan_coordinate(change.get("coordinate"))
                for change in changes
            )
        ):
            return "admin"
        return "reviewer"
    if method == "save_report_presentation":
        return (
            "admin"
            if request.keys() & {"plans", "header", "title", "production_codes"}
            else "reviewer"
        )
    if method == "verify_report":
        return "reviewer"
    if method == "reference_report":
        action = request.get("action")
        if action in {"list", "get", "export"}:
            return None
        if action in {"transfer", "save"}:
            return "admin"
    raise ValueError("Неизвестная команда доступа")
