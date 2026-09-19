from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_manifest import ManifestError, verify_manifest, write_manifest
from scripts.verify_release import (
    ReleaseVerificationError,
    verify_distribution_hygiene,
    verify_frontend_network_policy,
)


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


@pytest.mark.parametrize(
    "relative,content",
    [
        ("data/reporting.sqlite3", b"SQLite user data"),
        ("backups/snapshot.json", b"private backup"),
        ("exports/report.pdf", b"private report"),
        ("temp/.portable-dir", b"private data disguised as marker"),
        ("resources/forgotten.db", b"SQLite user data"),
        ("config/.env", b"TOKEN=private"),
        ("config/signing.key", b"private key"),
        ("config/id_ed25519", b"private key"),
        ("config/cert.pem", b"-----BEGIN ENCRYPTED PRIVATE KEY-----"),
        ("config/app.local.toml", b'[database]\npath="private-location"'),
    ],
)
def test_pristine_distribution_rejects_user_data_and_keys(
    tmp_path: Path, relative: str, content: bytes
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    # A newly generated manifest does not make private data safe to publish.
    write_manifest(tmp_path, "test")
    with pytest.raises(ReleaseVerificationError):
        verify_distribution_hygiene(tmp_path)


def test_pristine_distribution_allows_public_ca_and_empty_data_directories(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / ".portable-dir").write_bytes(b"managed by ReportingSystem\r\n")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "cacert.pem").write_bytes(b"-----BEGIN CERTIFICATE-----\npublic CA\n")
    config = tmp_path / "config"
    config.mkdir()
    (config / "app.local.example.toml").write_bytes(b"# Example only\n")
    verify_distribution_hygiene(tmp_path)


def test_pristine_distribution_allows_only_exact_generated_evergreen_override(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    local = config / "app.local.toml"
    local.write_text('[webview2]\nruntime_mode="evergreen"\n', encoding="utf-8-sig")
    verify_distribution_hygiene(tmp_path)
    with local.open("a", encoding="utf-8") as stream:
        stream.write('[database]\npath="private-location"\n')
    with pytest.raises(ReleaseVerificationError, match="local configuration"):
        verify_distribution_hygiene(tmp_path)
