from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema.validators import validator_for  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "resources" / "schemas" / "report-definition" / "matrix-definition.schema.json"
DEFINITIONS_DIRECTORY = ROOT / "resources" / "report-definitions"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_all_working_reference_definitions_match_versioned_schema() -> None:
    schema = _read_json(SCHEMA_PATH)
    validator_type = validator_for(schema)
    validator_type.check_schema(schema)
    validator = validator_type(schema)
    definitions = sorted(DEFINITIONS_DIRECTORY.glob("*.json"))

    assert len(definitions) == 3
    report_types: set[str] = set()
    definition_ids: set[str] = set()
    for path in definitions:
        definition = _read_json(path)
        validator.validate(definition)
        assert definition["status"] == "WORKING_REFERENCE"
        assert definition["production_use"] is False
        report_types.add(str(definition["report_type"]))
        definition_ids.add(str(definition["definition_id"]))

    assert report_types == {"DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"}
    assert len(definition_ids) == len(definitions)
