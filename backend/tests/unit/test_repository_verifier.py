from pathlib import Path

import pytest

from scripts import verify_repository


def verify_candidates(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *relative_paths: str,
) -> list[str]:
    candidates = tuple(root / relative_path for relative_path in relative_paths)
    monkeypatch.setattr(verify_repository, "ROOT", root)
    monkeypatch.setattr(
        verify_repository,
        "_repository_candidates",
        lambda errors: candidates,
    )
    errors: list[str] = []
    verify_repository.check_forbidden_files(errors)
    return errors


@pytest.mark.parametrize(
    ("relative_path", "expected_error"),
    [
        ("data/reporting.sqlite", "runtime, backup, or secret-like"),
        ("source.docx", "raw source archive/document"),
        ("attachment.zip", "raw source archive/document"),
        ("reference.xlsx", "outside its reviewed location"),
        (".env.local", "environment file is forbidden"),
    ],
)
def test_rejects_sensitive_repository_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    expected_error: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test-only", encoding="utf-8")

    errors = verify_candidates(monkeypatch, tmp_path, relative_path)

    assert any(expected_error in error for error in errors)


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env.example",
        "resources/templates/approved.xlsx",
        "resources/icons/icon.png",
        "docs/specification.md",
    ],
)
def test_allows_only_reviewed_candidate_locations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test-only", encoding="utf-8")

    assert verify_candidates(monkeypatch, tmp_path, relative_path) == []


def test_rejects_symbolic_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("test-only", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    errors = verify_candidates(monkeypatch, tmp_path, "link.txt")

    assert errors == ["symbolic links are not allowed in the repository: link.txt"]
