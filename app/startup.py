import sys
import winreg
from pathlib import Path

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "TestCaffeine"


def _build_start_command() -> str:
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable)
        return f'"{exe_path}"'

    python_exe = Path(sys.executable)
    main_script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{python_exe}" "{main_script}"'


def set_auto_start(enabled: bool) -> None:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)
    try:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, _build_start_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def is_auto_start_enabled() -> bool:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ)
    try:
        value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
    except FileNotFoundError:
        return False
    finally:
        winreg.CloseKey(key)

    return bool(value and str(value).strip())
