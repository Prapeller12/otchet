"""Real CurrentUser DPAPI executes in Windows CI, including a fresh process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.infrastructure.windows_data_protection import (
    WindowsDataProtector,
    default_device_protector,
)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows DPAPI")
def test_native_dpapi_roundtrip_and_new_process(tmp_path: Path) -> None:
    protector = WindowsDataProtector()
    value = os.urandom(32)
    wrapped = protector.protect(value)
    assert value not in wrapped
    assert protector.unprotect(wrapped) == value
    path = tmp_path / "windows-account.bin"
    path.write_bytes(wrapped)
    code = (
        "from pathlib import Path; import sys; "
        "from backend.infrastructure.windows_data_protection import WindowsDataProtector; "
        "value=WindowsDataProtector().unprotect(Path(sys.argv[1]).read_bytes()); "
        "sys.stdout.buffer.write(value)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)], check=True, capture_output=True, timeout=30
    )
    assert result.stdout == value
    damaged = bytearray(wrapped)
    damaged[-1] ^= 1
    with pytest.raises(OSError):
        protector.unprotect(bytes(damaged))


@pytest.mark.skipif(os.name == "nt", reason="Non-Windows behavior")
def test_non_windows_has_no_plaintext_fallback() -> None:
    assert default_device_protector() is None
    with pytest.raises(OSError, match="Windows"):
        WindowsDataProtector().protect(os.urandom(32))
