from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.desktop import window_health
from backend.desktop.paths import PortablePaths
from backend.desktop.window_health import monitor_window


def test_missing_document_is_not_a_success(tmp_path: Path) -> None:
    window = Mock()
    window.events.loaded.wait.return_value = False
    failures: list[str] = []
    monitor_window(window, PortablePaths(tmp_path), ui_self_test=False, failures=failures)
    assert failures and "45" in failures[0]
    window.destroy.assert_called_once()


def test_ready_document_leaves_normal_window_open(tmp_path: Path) -> None:
    window = Mock()
    window.events.loaded.wait.return_value = True
    window.evaluate_js.return_value = True
    failures: list[str] = []
    monitor_window(window, PortablePaths(tmp_path), ui_self_test=False, failures=failures)
    assert not failures
    window.destroy.assert_not_called()


def test_frontend_error_is_reported(tmp_path: Path) -> None:
    window = Mock()
    window.events.loaded.wait.return_value = True
    window.evaluate_js.side_effect = [False, "Нет связи с базой"]
    failures: list[str] = []
    monitor_window(window, PortablePaths(tmp_path), ui_self_test=False, failures=failures)
    assert "Нет связи с базой" in failures[0]
    window.destroy.assert_called_once()


def test_native_action_timeout_reports_visible_error(monkeypatch: pytest.MonkeyPatch) -> None:
    window = Mock()
    window.evaluate_js.side_effect = [False, "Подпись недействительна"]
    clock = iter((0, 16))
    monkeypatch.setattr("backend.desktop.window_health.time.monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="Подпись недействительна"):
        window_health._wait_for_script(window, "false", "Подтверждение не завершилось")


def test_native_signing_stops_if_secret_is_left_in_input(monkeypatch: pytest.MonkeyPatch) -> None:
    window = Mock()
    window.evaluate_js.side_effect = [None, False]
    monkeypatch.setattr(window_health, "_click_button", Mock())
    monkeypatch.setattr(window_health, "_wait_for_script", Mock())
    monkeypatch.setattr(window_health, "_fill_signing_input", Mock())
    with pytest.raises(RuntimeError, match="PIN не очищен"):
        window_health._exercise_signing(window)
