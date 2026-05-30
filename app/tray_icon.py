import ctypes
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Optional

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_USER = 0x0400
WM_APP_NOTIFYCALLBACK = WM_USER + 20
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
ERROR_CLASS_ALREADY_EXISTS = 1410


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class WinTrayIcon:
    def __init__(
        self,
        tooltip: str,
        icon_path: Path,
        on_show_hide: Callable[[], None],
        on_context_menu: Callable[[int, int], None],
    ) -> None:
        self._tooltip = tooltip
        self._icon_path = icon_path
        self._on_show_hide = on_show_hide
        self._on_context_menu = on_context_menu

        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._hicon: Optional[int] = None
        self._window_visible = True
        self._running = False
        self._class_name = "TestCaffeineTrayWindow"
        self._wndproc_ref = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(target=self._run, name="TrayIconThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def set_window_visible(self, is_visible: bool) -> None:
        self._window_visible = is_visible
        self._update_tooltip()

    def set_running(self, is_running: bool) -> None:
        self._running = is_running
        self._update_tooltip()

    def _load_icon(self) -> int:
        hicon = user32.LoadImageW(0, str(self._icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            hicon = user32.LoadIconW(0, IDI_APPLICATION)
        return hicon

    def _notify(self, op: int) -> None:
        if not self._hwnd:
            return

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_NOTIFYCALLBACK
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip[:127]
        shell32.Shell_NotifyIconW(op, ctypes.byref(nid))

    def _update_tooltip(self) -> None:
        state = "Running" if self._running else "Stopped"
        vis = "Hidden" if not self._window_visible else "Visible"
        self._tooltip = f"TestCaffeine - {state} - {vis}"
        self._notify(NIM_MODIFY)

    def _show_menu(self) -> None:
        if not self._hwnd:
            return
        point = POINT()
        user32.GetCursorPos(ctypes.byref(point))
        self._on_context_menu(point.x, point.y)

    def _run(self) -> None:
        WndProcType = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        def wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_APP_NOTIFYCALLBACK:
                if lparam == WM_RBUTTONUP:
                    self._show_menu()
                elif lparam == WM_LBUTTONDBLCLK:
                    self._on_show_hide()
                return 0

            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0

            if msg == WM_DESTROY:
                self._notify(NIM_DELETE)
                user32.PostQuitMessage(0)
                return 0

            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WndProcType(wnd_proc)

        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name
        registered = user32.RegisterClassW(ctypes.byref(wc))
        if not registered:
            last_error = ctypes.get_last_error()
            if last_error != ERROR_CLASS_ALREADY_EXISTS:
                return

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            hinstance,
            0,
        )
        if not hwnd:
            return
        self._hwnd = hwnd
        self._hicon = self._load_icon()
        self._notify(NIM_ADD)

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
