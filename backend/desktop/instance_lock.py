"""Single-instance guard scoped to one portable directory."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType


class AlreadyRunningError(RuntimeError):
    """Raised when the same portable directory is already in use."""


class SingleInstanceLock:
    """Use a Windows named mutex and keep a small managed lock marker."""

    def __init__(self, root: Path, marker: Path) -> None:
        self._root = root.resolve()
        self._marker = marker
        self._handle: int | None = None
        self._fd: int | None = None

    @property
    def mutex_name(self) -> str:
        digest = hashlib.sha256(os.fsencode(str(self._root))).hexdigest()[:32]
        return f"Local\\ReportingSystem-{digest}"

    def acquire(self) -> None:
        if os.name == "nt":
            self._acquire_windows()
        else:
            self._acquire_posix()
        try:
            self._write_marker()
        except OSError:
            self.release()
            raise

    def _acquire_windows(self) -> None:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.GetLastError.restype = ctypes.c_ulong
        handle = kernel32.CreateMutexW(None, True, self.mutex_name)
        if not handle:
            raise OSError("Windows could not create the single-instance mutex")
        if int(kernel32.GetLastError()) == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            raise AlreadyRunningError("This portable directory is already open")
        self._handle = int(handle)

    def _acquire_posix(self) -> None:
        import fcntl

        self._marker.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._marker, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise AlreadyRunningError("This portable directory is already open") from exc
        self._fd = fd

    def _write_marker(self) -> None:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "root": str(self._root),
                "started_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        self._marker.parent.mkdir(parents=True, exist_ok=True)
        if self._fd is not None:
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.ftruncate(self._fd, 0)
            os.write(self._fd, payload)
            os.fsync(self._fd)
        else:
            self._marker.write_bytes(payload)

    def release(self) -> None:
        if self._handle is not None:
            try:
                self._marker.unlink(missing_ok=True)
            finally:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.ReleaseMutex(ctypes.c_void_p(self._handle))
                kernel32.CloseHandle(ctypes.c_void_p(self._handle))
                self._handle = None
        if self._fd is not None:
            import fcntl

            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
                self._marker.unlink(missing_ok=True)

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
