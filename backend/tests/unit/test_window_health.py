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


def test_native_confirmation_stops_if_secret_is_left_in_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = Mock()
    window.evaluate_js.side_effect = [False]
    monkeypatch.setattr(window_health, "_click_button", Mock())
    monkeypatch.setattr(window_health, "_wait_for_script", Mock())
    monkeypatch.setattr(window_health, "_fill_form_input", Mock())
    with pytest.raises(RuntimeError, match="Код не очищен"):
        window_health._confirm_write(window, reject_wrong_code=True)


def test_input_waits_until_asynchronous_access_field_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = Mock()
    # The page shell exists; its input appears after the native status response.
    window.evaluate_js.side_effect = [False, False, True]
    sleeper = Mock()
    monkeypatch.setattr("backend.desktop.window_health.time.sleep", sleeper)
    window_health._fill_form_input(window, ".access-page", "Код доступа", "synthetic-pin")
    assert window.evaluate_js.call_count == 3
    assert sleeper.call_count == 2


def test_input_timeout_names_field_without_disclosing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = Mock()
    window.evaluate_js.side_effect = [False, ""]
    clock = iter((0, 16))
    monkeypatch.setattr("backend.desktop.window_health.time.monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError) as error:
        window_health._fill_form_input(window, ".access-page", "Код доступа", "never-disclose")
    assert "Код доступа" in str(error.value)
    assert ".access-page" in str(error.value)
    assert "never-disclose" not in str(error.value)


def test_failed_native_self_test_captures_screen_before_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = Mock()
    window.events.loaded.wait.return_value = False
    capture = Mock()
    importer = Mock(return_value=capture)
    monkeypatch.setattr("backend.desktop.window_health.importlib.import_module", importer)
    failures: list[str] = []
    monitor_window(window, PortablePaths(tmp_path), ui_self_test=True, failures=failures)
    assert failures
    importer.assert_called_once_with("PIL.ImageGrab")
    capture.grab.return_value.save.assert_called_once_with(tmp_path / "temp/window-error.png")
    window.destroy.assert_called_once()


def test_screen_capture_waits_for_font_paint_and_native_compositor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    window = Mock()
    evaluations = iter((True, False, True))

    def evaluate(expression: str) -> bool:
        events.append("evaluate")
        return next(evaluations)

    window.evaluate_js.side_effect = evaluate
    monkeypatch.setattr(
        "backend.desktop.window_health.time.sleep", lambda delay: events.append(f"sleep:{delay}")
    )
    image = Mock()

    def capture() -> Mock:
        events.append("capture")
        return image

    image.grab.side_effect = capture
    monkeypatch.setattr("backend.desktop.window_health.importlib.import_module", lambda name: image)
    window_health._capture_window(window, PortablePaths(tmp_path), "window-test.png")
    script = window.evaluate_js.call_args_list[0].args[0]
    assert "document.fonts.ready" in script
    assert script.count("requestAnimationFrame") == 2
    assert events == ["evaluate", "evaluate", "sleep:0.1", "evaluate", "sleep:0.25", "capture"]
    image.save.assert_called_once_with(tmp_path / "temp/window-test.png")


def test_screen_capture_does_not_save_stale_frame_if_paint_never_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = Mock()
    window.evaluate_js.side_effect = [True, False, ""]
    clock = iter((0, 16))
    monkeypatch.setattr("backend.desktop.window_health.time.monotonic", lambda: next(clock))
    importer = Mock()
    monkeypatch.setattr("backend.desktop.window_health.importlib.import_module", importer)
    with pytest.raises(RuntimeError, match="отрисовка"):
        window_health._capture_window(window, PortablePaths(tmp_path), "window-test.png")
    importer.assert_not_called()


def test_icon_check_rejects_missing_or_invisible_print_icon() -> None:
    window = Mock()
    window.evaluate_js.return_value = False
    with pytest.raises(RuntimeError, match="видимая SVG"):
        window_health._check_action_icons(window)


def test_icon_accessibility_rejects_unnamed_button() -> None:
    window = Mock()
    window.evaluate_js.return_value = False
    with pytest.raises(RuntimeError, match="доступное имя"):
        window_health._check_icon_accessibility(window)
