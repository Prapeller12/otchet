"""Create and verify the immutable-file manifest for a portable release."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "release-manifest.json"
MUTABLE_PREFIXES = (
    "attachments/",
    "backups/",
    "data/",
    "exports/",
    "imports/",
    "temp/",
)


class ManifestError(RuntimeError):
    """Raised when release contents do not match their manifest."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_mutable(relative_path: str) -> bool:
    return any(relative_path.startswith(prefix) for prefix in MUTABLE_PREFIXES)


def _immutable_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ManifestError(f"Symbolic links are forbidden in releases: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST_NAME or _is_mutable(relative):
            continue
        files[relative] = path
    return files


def create_manifest(root: Path, version: str) -> dict[str, object]:
    root = root.resolve()
    if not root.is_dir():
        raise ManifestError(f"Release directory does not exist: {root}")
    entries = [
        {"path": relative, "sha256": _sha256(path), "size": path.stat().st_size}
        for relative, path in _immutable_files(root).items()
    ]
    return {
        "application": "reporting-system",
        "files": entries,
        "format_version": 1,
        "target": "windows-x64",
        "version": version,
    }


def write_manifest(root: Path, version: str) -> Path:
    manifest_path = root / MANIFEST_NAME
    payload = create_manifest(root, version)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _safe_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError("Manifest file path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ManifestError(f"Unsafe manifest file path: {value!r}")
    return value


def verify_manifest(root: Path) -> None:
    root = root.resolve()
    manifest_path = root / MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read release manifest: {exc}") from exc
    if payload.get("format_version") != 1 or payload.get("target") != "windows-x64":
        raise ManifestError("Unsupported release manifest format or target")
    raw_entries = payload.get("files")
    if not isinstance(raw_entries, list):
        raise ManifestError("Manifest files must be a list")

    expected: dict[str, tuple[str, int]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise ManifestError("Manifest file entry must be an object")
        relative = _safe_manifest_path(entry.get("path"))
        checksum = entry.get("sha256")
        size = entry.get("size")
        if relative in expected:
            raise ManifestError(f"Duplicate manifest entry: {relative}")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise ManifestError(f"Invalid SHA-256 for {relative}")
        if not isinstance(size, int) or size < 0:
            raise ManifestError(f"Invalid size for {relative}")
        expected[relative] = (checksum, size)

    actual = _immutable_files(root)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise ManifestError(f"Release file set differs (missing={missing}, extra={extra})")
    for relative, path in actual.items():
        expected_hash, expected_size = expected[relative]
        if path.stat().st_size != expected_size or _sha256(path) != expected_hash:
            raise ManifestError(f"Release file differs from manifest: {relative}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("root", type=Path)
    create.add_argument("--version", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "create":
            write_manifest(arguments.root, arguments.version)
        else:
            verify_manifest(arguments.root)
    except ManifestError as exc:
        print(f"Release manifest verification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
