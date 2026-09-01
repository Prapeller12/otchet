from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_manifest import ManifestError, verify_manifest, write_manifest
from scripts.verify_release import ReleaseVerificationError, verify_frontend_network_policy


def test_release_manifest_ignores_mutable_runtime_data(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "immutable.txt").write_text("fixed", encoding="utf-8")
    (tmp_path / "data").mkdir()
    write_manifest(tmp_path, "test")
    (tmp_path / "data" / "reporting.sqlite3").write_bytes(b"user data")

    verify_manifest(tmp_path)


def test_release_manifest_detects_added_immutable_file(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("test", encoding="utf-8")
    write_manifest(tmp_path, "test")
    (tmp_path / "unexpected.exe").write_bytes(b"not allowed")

    with pytest.raises(ManifestError, match="file set differs"):
        verify_manifest(tmp_path)


def test_frontend_network_policy_allows_only_inert_framework_uris(tmp_path: Path) -> None:
    asset = tmp_path / "index.js"
    asset.write_text(
        'const ns="http://www.w3.org/2000/svg";'
        'const help="https://reactjs.org/docs/error-decoder.html?invariant=";',
        encoding="utf-8",
    )

    verify_frontend_network_policy(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        'fetch("/unexpected")',
        'const endpoint="https://example.invalid/api";',
        'new WebSocket("ws:" + endpoint)',
    ],
)
def test_frontend_network_policy_rejects_network_capability(tmp_path: Path, source: str) -> None:
    (tmp_path / "index.js").write_text(source, encoding="utf-8")

    with pytest.raises(ReleaseVerificationError):
        verify_frontend_network_policy(tmp_path)
