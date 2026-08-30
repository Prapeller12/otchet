"""Dependency-free checks for the repository contract skeleton."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "AGENTS.md",
    "README.md",
    "VERSION",
    "pyproject.toml",
    "package.json",
    "config/app.defaults.toml",
    "config/app.local.example.toml",
    "config/reporting_rules.yaml",
    "config/roles.yaml",
    "config/logging.yaml",
    "docs/project-status.md",
    "docs/ui-contracts/reference-form-register.md",
    "docs/decisions/ADR-template.md",
)

REQUIRED_DIRECTORIES = (
    "backend/api",
    "backend/application",
    "backend/domain",
    "backend/infrastructure",
    "backend/repositories",
    "backend/migrations",
    "backend/tests",
    "frontend/src/app",
    "frontend/src/pages",
    "frontend/src/widgets",
    "frontend/src/features",
    "frontend/src/entities",
    "frontend/src/shared/ui",
    "frontend/src/shared/api",
    "frontend/src/shared/config",
    "frontend/src/styles",
    "frontend/tests",
    "resources/templates",
    "resources/schemas",
    "resources/dictionaries",
    "resources/icons",
)

FORBIDDEN_TRACKED_PATTERNS = (
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.db",
    "*.log",
    "*.pem",
    "*.key",
)


def check_required_paths(errors: list[str]) -> None:
    for relative_path in REQUIRED_FILES:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing required file: {relative_path}")

    for relative_path in REQUIRED_DIRECTORIES:
        if not (ROOT / relative_path).is_dir():
            errors.append(f"missing required directory: {relative_path}")


def check_machine_readable_files(errors: list[str]) -> None:
    try:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"invalid pyproject.toml: {exc}")

    try:
        with (ROOT / "config/app.defaults.toml").open("rb") as stream:
            defaults = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"invalid config/app.defaults.toml: {exc}")
    else:
        app = defaults.get("app", {})
        logging = defaults.get("logging", {})
        if app.get("telemetry_enabled") is not False:
            errors.append("telemetry must be disabled by default")
        if app.get("external_network_access_enabled") is not False:
            errors.append("external network access must be disabled by default")
        if logging.get("technical_file_logs_enabled") is not False:
            errors.append("technical file logs must be disabled by default")

    try:
        with (ROOT / "package.json").open(encoding="utf-8") as stream:
            json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid package.json: {exc}")


def check_forbidden_files(errors: list[str]) -> None:
    for pattern in FORBIDDEN_TRACKED_PATTERNS:
        for path in ROOT.rglob(pattern):
            if ".git" not in path.parts:
                errors.append(f"forbidden runtime or secret-like file: {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    check_required_paths(errors)
    check_machine_readable_files(errors)
    check_forbidden_files(errors)

    if errors:
        print("Repository verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Repository verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
