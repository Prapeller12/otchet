"""Fail closed if packaged reopen tests accidentally reuse setup identity or reseed."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.desktop import launcher, window_health
from backend.desktop.paths import PortablePaths


def test_ui_test_modes_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        launcher._build_parser().parse_args(["--ui-self-test", "--ui-reopen-test"])


@pytest.mark.parametrize("reopen", [False, True])
def test_ui_start_uses_fresh_bridge_and_reopen_never_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reopen: bool
) -> None:
    (tmp_path / "VERSION").write_text("0.1.0-dev.30", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/app.defaults.toml").write_text("", encoding="utf-8")
    bridges: list[Mock] = []

    def create(*args: object, **kwargs: object) -> Mock:
        if bridges:
            bridges[0]._lock.assert_called_once()
        bridge = Mock()
        bridge._lock = Mock()
        bridge.setup_access.return_value = {"ok": True}
        bridges.append(bridge)
        return bridge

    monkeypatch.setattr(launcher, "SecureDesktopBridge", create)
    reference = Mock()
    subsidiary = Mock()
    monkeypatch.setattr(launcher, "prepare_reference_window_test", reference)
    monkeypatch.setattr(launcher, "prepare_subsidiary_window_test", subsidiary)
    webview = Mock()
    webview.settings = {}
    monkeypatch.setattr("backend.desktop.launcher.importlib.import_module", lambda name: webview)
    monitor = Mock()
    monkeypatch.setattr(launcher, "monitor_window", monitor)
    webview.start.side_effect = lambda callback, **kwargs: callback()

    paths = PortablePaths(tmp_path)
    launcher._run_window(paths, ui_self_test=not reopen, ui_reopen_test=reopen)

    assert len(bridges) == (1 if reopen else 2)
    assert webview.create_window.call_args.kwargs["js_api"] is bridges[-1]
    bridges[-1].setup_access.assert_not_called()
    if reopen:
        reference.assert_not_called()
        subsidiary.assert_not_called()
    else:
        bridges[0].setup_access.assert_called_once()
        reference.assert_called_once()
        subsidiary.assert_called_once()
    monitor.assert_called_once_with(
        webview.create_window.return_value,
        paths,
        ui_self_test=not reopen,
        ui_reopen_test=reopen,
        failures=[],
    )


def test_anonymous_start_rejects_retained_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(window_health, "_wait_for_script", Mock())
    window = Mock()
    window.evaluate_js.return_value = True
    with pytest.raises(RuntimeError, match="режим администратора"):
        window_health._check_anonymous_start(window)


def test_anonymous_start_rejects_unprotected_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_health, "_wait_for_script", Mock())
    window = Mock()
    window.evaluate_js.side_effect = [False, True, False]
    with pytest.raises(RuntimeError, match="запись без кода"):
        window_health._check_anonymous_start(window)


def test_native_hint_regression_rejects_missing_field_help(tmp_path: Path) -> None:
    window = Mock()
    window.evaluate_js.return_value = False
    with pytest.raises(RuntimeError, match="отсутствует пояснение"):
        window_health._exercise_readonly_hints(window, PortablePaths(tmp_path), 0)


def test_reopen_failure_captures_screen_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = Mock()
    window.events.loaded.wait.return_value = False
    image = Mock()
    monkeypatch.setattr("backend.desktop.window_health.importlib.import_module", lambda name: image)
    failures: list[str] = []
    window_health.monitor_window(
        window,
        PortablePaths(tmp_path),
        ui_self_test=False,
        ui_reopen_test=True,
        failures=failures,
    )
    assert failures
    image.grab.return_value.save.assert_called_once_with(tmp_path / "temp/window-error.png")
    window.destroy.assert_called_once()
