"""Windows CurrentUser DPAPI; no machine-wide key and no plaintext fallback."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Protocol


class DeviceProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsDataProtector:
    """Protect under the signed-in Windows account without prompting or elevation."""

    _ENTROPY = b"ReportingSystem.Database.AutoOpen.v1"

    def _transform(self, value: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise OSError("Автоматическое открытие поддерживается только в Windows")
        if not value or len(value) > 65536:
            raise ValueError("Некорректная обёртка автоматического открытия")
        # Load only on Windows. DPAPI CurrentUser is the default: intentionally no
        # CRYPTPROTECT_LOCAL_MACHINE flag. UI_FORBIDDEN prevents hidden OS prompts.
        windows_library = getattr(ctypes, "WinDLL", None)
        if windows_library is None:
            raise OSError("Windows DPAPI недоступен в этой среде")
        crypt32 = windows_library("crypt32", use_last_error=True)
        kernel32 = windows_library("kernel32", use_last_error=True)
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(_Blob),
            ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
            ctypes.POINTER(_Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_Blob),
        ]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        source = ctypes.create_string_buffer(value, len(value))
        entropy = ctypes.create_string_buffer(self._ENTROPY, len(self._ENTROPY))
        input_blob = _Blob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        entropy_blob = _Blob(
            len(self._ENTROPY), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte))
        )
        output = _Blob()
        try:
            if not function(
                ctypes.byref(input_blob),
                None if decrypt else "ReportingSystem automatic database opening",
                ctypes.byref(entropy_blob),
                None,
                None,
                0x1,  # CRYPTPROTECT_UI_FORBIDDEN
                ctypes.byref(output),
            ):
                raise OSError(
                    "Windows не смогла открыть защищённый ключ этой учётной записи"
                    if decrypt
                    else "Windows не смогла защитить ключ автоматического открытия"
                )
            return ctypes.string_at(output.data, output.size)
        finally:
            ctypes.memset(source, 0, len(value))
            if output.data:
                ctypes.memset(output.data, 0, output.size)
                kernel32.LocalFree(output.data)

    def protect(self, value: bytes) -> bytes:
        return self._transform(value, decrypt=False)

    def unprotect(self, value: bytes) -> bytes:
        return self._transform(value, decrypt=True)


def default_device_protector() -> DeviceProtector | None:
    return WindowsDataProtector() if os.name == "nt" else None
