import ctypes
import platform
import sys
import tkinter.messagebox as messagebox

from app.ui import run_app


ERROR_ALREADY_EXISTS = 183
_SINGLE_INSTANCE_MUTEX_NAME = "Local\\TestCaffeine.SingleInstance"
_single_instance_handle: int | None = None


def _acquire_single_instance_lock() -> bool:
    global _single_instance_handle

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p

    handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        return False

    last_error = ctypes.get_last_error()
    if last_error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(handle)
        return False

    _single_instance_handle = int(handle)
    return True


def _release_single_instance_lock() -> None:
    global _single_instance_handle
    if not _single_instance_handle:
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle(ctypes.c_void_p(_single_instance_handle))
    _single_instance_handle = None


def main() -> int:
    if platform.system() != "Windows":
        messagebox.showerror(
            "Unsupported platform",
            "TestCaffeine is designed for Windows laptops only.",
        )
        return 1

    if not _acquire_single_instance_lock():
        messagebox.showerror(
            "TestCaffeine already running",
            "Another TestCaffeine instance is already running. Please use the existing app window or tray icon.",
        )
        return 1

    try:
        run_app()
    finally:
        _release_single_instance_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
