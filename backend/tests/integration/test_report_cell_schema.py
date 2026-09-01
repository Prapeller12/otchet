import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "resources/schemas/report-cell/report-cell.schema.json"
VALID_FIXTURES_PATH = ROOT / "frontend/tests/contracts/fixtures/report-cell.valid.json"
INVALID_FIXTURES_PATH = ROOT / "frontend/tests/contracts/fixtures/report-cell.invalid.json"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize("fixture", load_json(VALID_FIXTURES_PATH))
def test_valid_report_cell_fixture_passes_standard_validator(
    validator: Draft202012Validator,
    fixture: dict[str, Any],
) -> None:
    errors = sorted(validator.iter_errors(fixture["cell"]), key=lambda error: list(error.path))
    assert errors == [], f"{fixture['name']}: {[error.message for error in errors]}"


@pytest.mark.parametrize("fixture", load_json(INVALID_FIXTURES_PATH))
def test_invalid_report_cell_fixture_fails_standard_validator(
    validator: Draft202012Validator,
    fixture: dict[str, Any],
) -> None:
    assert list(validator.iter_errors(fixture["cell"])), fixture["name"]
