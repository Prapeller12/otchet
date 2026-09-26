"""Preference persistence never opens or modifies the protected report database."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.api.secure_desktop_bridge import SecureDesktopBridge
from backend.infrastructure.ui_preferences import UiPreferences


def bridge(directory: Path) -> SecureDesktopBridge:
    return SecureDesktopBridge(
        directory / "data" / "reports.sqlite3",
        migrations_directory=directory / "migrations",
        definitions_directory=directory / "definitions",
        preferences_path=directory / "config" / "ui-preferences.json",
        device_protector=None,
    )


def test_preference_survives_new_bridge_without_unlock_or_database(tmp_path: Path) -> None:
    first = bridge(tmp_path)
    assert first.get_ui_preferences({}) == {"ok": True, "data": {"field_hints_enabled": True}}
    assert first.save_ui_preferences({"field_hints_enabled": False})["ok"]
    second = bridge(tmp_path)
    assert second.get_ui_preferences({})["data"] == {"field_hints_enabled": False}
    assert second.save_ui_preferences({"field_hints_enabled": True})["ok"]
    assert bridge(tmp_path).get_ui_preferences({})["data"] == {"field_hints_enabled": True}
    assert not (tmp_path / "data").exists()
    assert first._application is None and first._administrator is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"field_hints_enabled": 0},
        {"field_hints_enabled": "false"},
        {"field_hints_enabled": None},
        {"field_hints_enabled": False, "authorization": {}},
        {"field_hints_enabled": False, "path": "other.json"},
    ],
)
def test_invalid_preference_cannot_change_existing_file(tmp_path: Path, payload: object) -> None:
    app = bridge(tmp_path)
    app.save_ui_preferences({"field_hints_enabled": True})
    path = tmp_path / "config" / "ui-preferences.json"
    before = path.read_bytes()
    result = app.save_ui_preferences(payload)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"[]",
        b"null",
        b"{}",
        b'{"field_hints_enabled": 0}',
        b"\xff",
        b'{"field_hints_enabled": false, "extra": true}',
    ],
)
def test_corrupt_preference_defaults_on_without_rewriting(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "preferences.json"
    path.write_bytes(content)
    assert UiPreferences(path).read() == {"field_hints_enabled": True}
    assert path.read_bytes() == content


def test_failed_atomic_replace_keeps_previous_choice_and_reports_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = bridge(tmp_path)
    app.save_ui_preferences({"field_hints_enabled": False})

    def denied(*args: object) -> None:
        raise PermissionError("synthetic failure")

    monkeypatch.setattr("backend.infrastructure.ui_preferences.os.replace", denied)
    result = app.save_ui_preferences({"field_hints_enabled": True})
    assert result["ok"] is False
    assert result["error"]["code"] == "PREFERENCES_SAVE_FAILED"
    assert app.get_ui_preferences({})["data"] == {"field_hints_enabled": False}
    assert list((tmp_path / "config").iterdir()) == [tmp_path / "config/ui-preferences.json"]


def test_concurrent_reads_and_saves_always_have_complete_boolean(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    preferences = UiPreferences(path)

    def update(index: int) -> None:
        result = preferences.save({"field_hints_enabled": bool(index % 2)})
        assert type(result["field_hints_enabled"]) is bool
        assert type(preferences.read()["field_hints_enabled"]) is bool

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(40)))
    assert type(json.loads(path.read_text())["field_hints_enabled"]) is bool
    assert list(tmp_path.iterdir()) == [path]


def test_read_rejects_unexpected_arguments(tmp_path: Path) -> None:
    assert bridge(tmp_path).get_ui_preferences({"path": "elsewhere"})["ok"] is False
