"""Create the user-requested, name-free September 2027 worksheet scaffolds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.repositories.blank_profiles import BlankProfilesRepository


def _definition(directory: Path) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        (directory / "presets" / "blank-profiles-2027.v1.json").read_text(encoding="utf-8")
    )
    if (
        raw.get("schema_version") != 1
        or raw.get("initial_period") != "2027-09-01"
        or raw.get("profile_set_id") != "blank-2027-v1"
    ):
        raise ValueError("Неверный набор заготовок")
    return raw


def list_blank_report_profiles(definitions_directory: Path) -> dict[str, Any]:
    raw = _definition(definitions_directory)
    keys = (
        "template_id",
        "label",
        "report_type",
        "position_count",
        "distinct_manufacturer_count",
        "supplier_row_count",
        "display_row_count",
    )
    return {
        "profile_set_id": raw["profile_set_id"],
        "initial_period": raw["initial_period"],
        "quantity_policy": raw["quantity_policy"],
        "organizations": [{key: org[key] for key in keys} for org in raw["organizations"]],
    }


def create_blank_report_profiles(
    repository: BlankProfilesRepository, definitions_directory: Path, profile_set_id: str
) -> dict[str, Any]:
    raw = _definition(definitions_directory)
    if profile_set_id != raw["profile_set_id"]:
        raise ValueError("Набор заготовок не найден")
    return repository.create(raw)


def get_organization_start_period(
    repository: BlankProfilesRepository, organization_id: int
) -> str | None:
    return repository.start_period(organization_id)
