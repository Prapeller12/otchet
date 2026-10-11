"""The native parent observes readiness only after a completed publication."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from backend.desktop.launcher import _publish_network_ready


def test_ready_is_invisible_until_flushed_writer_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    flushed = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    original_fsync = os.fsync

    def pause_flush(descriptor: int) -> None:
        original_fsync(descriptor)
        flushed.set()
        assert release.wait(5)

    def publish() -> None:
        try:
            _publish_network_ready(ready, "exact-token")
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(os, "fsync", pause_flush)
    writer = threading.Thread(target=publish)
    writer.start()
    try:
        assert flushed.wait(5)
        assert not ready.exists()
    finally:
        release.set()
        writer.join(5)
    assert not writer.is_alive() and not errors
    assert ready.read_bytes() == b"exact-token"
    assert list(tmp_path.iterdir()) == [ready]


@pytest.mark.parametrize("stage", ["flush", "replace"])
def test_failed_ready_publication_preserves_previous_signal_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    ready = tmp_path / "ready"
    ready.write_bytes(b"previous-token")

    def fail(*args: object) -> None:
        raise OSError("injected readiness failure")

    monkeypatch.setattr(os, "fsync" if stage == "flush" else "replace", fail)
    with pytest.raises(OSError, match="injected readiness failure"):
        _publish_network_ready(ready, "new-token")
    assert ready.read_bytes() == b"previous-token"
    assert list(tmp_path.iterdir()) == [ready]
