from pathlib import Path
from unittest.mock import Mock

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
