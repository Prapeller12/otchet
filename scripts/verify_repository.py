"""Dependency-free checks for the repository contract skeleton."""

from __future__ import annotations

import json
import subprocess
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
    "docs/imports/README.md",
    "docs/development/agents-team.md",
    "docs/development/requirements-traceability.md",
    "docs/ui-contracts/reference-form-register.md",
    "docs/decisions/ADR-template.md",
    "backend/domain/calculations.py",
    "backend/infrastructure/database/migrator.py",
    "backend/migrations/0001_initial_schema.sql",
    "resources/schemas/report-cell/report-cell.schema.json",
    "frontend/src/shared/api/report-cell-contract.ts",
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
    "docs/api",
    "docs/architecture",
    "docs/database",
    "docs/imports",
    "docs/test-cases",
    "docs/ui-contracts",
)

FORBIDDEN_SUFFIXES = {
    ".backup",
    ".bak",
    ".db",
    ".dump",
    ".key",
    ".kdbx",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".sqlitedb",
}

FORBIDDEN_EXACT_NAMES = {
    "id_ed25519",
    "id_rsa",
}

REVIEWED_BINARY_LOCATIONS = {
    ".xls": ("resources/templates/",),
    ".xlsx": ("resources/templates/",),
    ".ico": ("resources/icons/",),
    ".jpeg": ("resources/icons/",),
    ".jpg": ("resources/icons/",),
    ".png": ("resources/icons/",),
}

FORBIDDEN_SOURCE_SUFFIXES = {
    ".7z",
    ".doc",
    ".docx",
    ".gz",
    ".ppt",
    ".pptx",
    ".rar",
    ".tar",
    ".tgz",
    ".xlsm",
    ".zip",
}


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

    try:
        with (ROOT / "resources/schemas/report-cell/report-cell.schema.json").open(
            encoding="utf-8"
        ) as stream:
            report_cell_schema = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid report-cell JSON Schema: {exc}")
    else:
        if report_cell_schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append("report-cell contract must use JSON Schema draft 2020-12")


def _repository_candidates(errors: list[str]) -> tuple[Path, ...]:
    """Return tracked and non-ignored untracked files considered for a commit."""

    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"cannot enumerate repository files with Git: {exc}")
        return ()

    try:
        relative_paths = result.stdout.decode("utf-8", errors="strict").split("\0")
    except UnicodeDecodeError as exc:
        errors.append(f"repository path is not valid UTF-8: {exc}")
        return ()
    return tuple(ROOT / relative_path for relative_path in relative_paths if relative_path)


def check_forbidden_files(errors: list[str]) -> None:
    for path in _repository_candidates(errors):
        relative_path = path.relative_to(ROOT).as_posix()
        name = path.name.lower()
        suffix = path.suffix.lower()

        if path.is_symlink():
            errors.append(f"symbolic links are not allowed in the repository: {relative_path}")
            continue
        if not path.is_file():
            errors.append(f"repository entry is not a regular file: {relative_path}")
            continue

        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            errors.append(f"environment file is forbidden: {relative_path}")
        if name in FORBIDDEN_EXACT_NAMES or suffix in FORBIDDEN_SUFFIXES:
            errors.append(f"runtime, backup, or secret-like file is forbidden: {relative_path}")
        if name.endswith((".sqlite-wal", ".sqlite-shm")):
            errors.append(f"SQLite runtime sidecar is forbidden: {relative_path}")
        if suffix in FORBIDDEN_SOURCE_SUFFIXES:
            errors.append(f"raw source archive/document is forbidden: {relative_path}")
        if suffix in REVIEWED_BINARY_LOCATIONS and not any(
            relative_path.startswith(prefix) for prefix in REVIEWED_BINARY_LOCATIONS[suffix]
        ):
            errors.append(f"binary file is outside its reviewed location: {relative_path}")


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
