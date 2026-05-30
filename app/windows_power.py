import ctypes
import threading
from ctypes import wintypes

# SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("SystemStatusFlag", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class WindowsPowerManager:
    """Thin wrapper over Windows APIs used by the monitor."""

    def __init__(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._lock = threading.Lock()
        self._is_asserted = False

    def get_idle_seconds(self) -> float:
        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)

        if not self._user32.GetLastInputInfo(ctypes.byref(last_input)):
            raise ctypes.WinError(ctypes.get_last_error())

        tick_count = self._kernel32.GetTickCount64()
        # LASTINPUTINFO.dwTime is a 32-bit millisecond tick value.
        last_tick = int(last_input.dwTime)
        current_32 = int(tick_count & 0xFFFFFFFF)

        if current_32 >= last_tick:
            idle_ms = current_32 - last_tick
        else:
            idle_ms = (0x100000000 - last_tick) + current_32

        return idle_ms / 1000.0

    def assert_awake(self) -> bool:
        with self._lock:
            if self._is_asserted:
                return False

            result = self._kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            if result == 0:
                raise ctypes.WinError(ctypes.get_last_error())

            self._is_asserted = True
            return True

    def release_awake(self) -> bool:
        with self._lock:
            if not self._is_asserted:
                return False

            result = self._kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            if result == 0:
                raise ctypes.WinError(ctypes.get_last_error())

            self._is_asserted = False
            return True

    def is_asserted(self) -> bool:
        with self._lock:
            return self._is_asserted

    def is_on_ac_power(self) -> bool:
        status = SYSTEM_POWER_STATUS()
        if not self._kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())

        # 1 = online, 0 = offline, 255 = unknown.
        return status.ACLineStatus == 1

    def get_battery_percent(self) -> int:
        status = SYSTEM_POWER_STATUS()
        if not self._kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())

        percent = int(status.BatteryLifePercent)
        if percent < 0 or percent > 100:
            return -1
        return percent
