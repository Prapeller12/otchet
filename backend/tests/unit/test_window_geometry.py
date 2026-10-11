import ctypes
import os
from unittest.mock import Mock

import pytest

from backend.desktop import launcher


@pytest.mark.parametrize(
    "work_area, scale",
    [
        ((0, 0, 1920, 1040), 1),
        ((0, 0, 1366, 728), 1),
        ((0, 0, 1366, 728), 1.25),
        ((0, 0, 1920, 1040), 1.5),
        ((0, 0, 1366, 728), 2),
        ((48, 0, 1920, 1080), 1.25),
        ((-1920, -1080, 0, -40), 1.5),
    ],
)
def test_initial_window_and_minimum_fit_work_area_at_each_scale(
    work_area: tuple[int, int, int, int], scale: float
) -> None:
    options = launcher._fit_window_geometry(work_area, scale)
    left, top, right, bottom = work_area
    assert options["x"] * scale >= left
    assert options["y"] * scale >= top
    assert (options["x"] + options["width"]) * scale <= right
    assert (options["y"] + options["height"]) * scale <= bottom
    assert 0 < options["min_size"][0] <= options["width"]
    assert 0 < options["min_size"][1] <= options["height"]


def test_unknown_windows_work_area_uses_maximized_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = Mock()
    native.user32.SystemParametersInfoW.return_value = False
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(ctypes, "windll", native, raising=False)
    options = launcher._initial_window_geometry()
    assert options["maximized"] is True
    assert options["min_size"] == (480, 320)


def test_native_measurement_uses_work_area_and_device_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = Mock()

    def work_area(action: int, unused: int, pointer: object, flags: int) -> bool:
        assert action == 0x0030 and unused == 0 and flags == 0
        area = pointer._obj  # type: ignore[attr-defined]
        area.left, area.top, area.right, area.bottom = (0, 0, 1366, 728)
        return True

    native.user32.SystemParametersInfoW.side_effect = work_area
    native.shcore.GetScaleFactorForDevice.return_value = 200
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(ctypes, "windll", native, raising=False)
    options = launcher._initial_window_geometry()
    assert options == launcher._fit_window_geometry((0, 0, 1366, 728), 2)
    native.user32.SetProcessDPIAware.assert_called_once()
