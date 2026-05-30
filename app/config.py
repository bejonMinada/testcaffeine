import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "TestCaffeine"
DEFAULT_IDLE_TIMEOUT_SECONDS = 30
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_AWAKE_SESSION_HOURS = 2
DEFAULT_ALLOW_INDEFINITE_AWAKE = False
DEFAULT_DISABLE_ON_BATTERY = True
DEFAULT_PAUSE_ON_LOW_BATTERY = True
DEFAULT_LOW_BATTERY_THRESHOLD_PERCENT = 20
DEFAULT_SECURITY_EVENTS_ENABLED = True
DEFAULT_AUTO_START_WITH_WINDOWS = False
DEFAULT_MINIMIZE_TO_TRAY = True
DEFAULT_AUTO_FOCUS_OVERLAY_ON_IDLE = False
DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS = 30
DEFAULT_ONBOARDING_COMPLETED = False

MIN_IDLE_TIMEOUT_SECONDS = 5
MAX_IDLE_TIMEOUT_SECONDS = 3600
MIN_POLL_INTERVAL_SECONDS = 0.2
MAX_POLL_INTERVAL_SECONDS = 5.0
MIN_MAX_AWAKE_SESSION_HOURS = 1
MAX_MAX_AWAKE_SESSION_HOURS = 168
MIN_LOW_BATTERY_THRESHOLD_PERCENT = 5
MAX_LOW_BATTERY_THRESHOLD_PERCENT = 95
MIN_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS = 5
MAX_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS = 600


@dataclass
class AppSettings:
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    max_awake_session_hours: int = DEFAULT_MAX_AWAKE_SESSION_HOURS
    allow_indefinite_awake: bool = DEFAULT_ALLOW_INDEFINITE_AWAKE
    disable_on_battery: bool = DEFAULT_DISABLE_ON_BATTERY
    pause_on_low_battery: bool = DEFAULT_PAUSE_ON_LOW_BATTERY
    low_battery_threshold_percent: int = DEFAULT_LOW_BATTERY_THRESHOLD_PERCENT
    security_events_enabled: bool = DEFAULT_SECURITY_EVENTS_ENABLED
    auto_start_with_windows: bool = DEFAULT_AUTO_START_WITH_WINDOWS
    minimize_to_tray: bool = DEFAULT_MINIMIZE_TO_TRAY
    auto_focus_overlay_on_idle: bool = DEFAULT_AUTO_FOCUS_OVERLAY_ON_IDLE
    focus_overlay_blackout_delay_seconds: int = DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS
    onboarding_completed: bool = DEFAULT_ONBOARDING_COMPLETED


def get_app_data_dir() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        base_path = Path(local_app_data)
    else:
        base_path = Path.home() / "AppData" / "Local"

    app_dir = base_path / APP_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_settings_path() -> Path:
    return get_app_data_dir() / "settings.json"


def load_settings() -> AppSettings:
    path = get_settings_path()

    if not path.exists():
        settings = AppSettings()
        save_settings(settings)
        return settings

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        settings = AppSettings()
        save_settings(settings)
        return settings

    timeout = raw.get("idle_timeout_seconds", DEFAULT_IDLE_TIMEOUT_SECONDS)
    poll_interval = raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    max_awake_hours_raw = raw.get("max_awake_session_hours")
    if max_awake_hours_raw is None:
        # Backward compatibility with older settings files.
        legacy_minutes = raw.get("max_awake_session_minutes", DEFAULT_MAX_AWAKE_SESSION_HOURS * 60)
        try:
            max_awake_hours_raw = max(1, int((float(legacy_minutes) + 59) // 60))
        except (TypeError, ValueError):
            max_awake_hours_raw = DEFAULT_MAX_AWAKE_SESSION_HOURS

    allow_indefinite_awake = raw.get("allow_indefinite_awake", DEFAULT_ALLOW_INDEFINITE_AWAKE)
    disable_on_battery = raw.get("disable_on_battery", DEFAULT_DISABLE_ON_BATTERY)
    pause_on_low_battery = raw.get("pause_on_low_battery", DEFAULT_PAUSE_ON_LOW_BATTERY)
    low_battery_threshold = raw.get(
        "low_battery_threshold_percent", DEFAULT_LOW_BATTERY_THRESHOLD_PERCENT
    )
    security_events_enabled = raw.get(
        "security_events_enabled", DEFAULT_SECURITY_EVENTS_ENABLED
    )
    auto_start_with_windows = raw.get("auto_start_with_windows", DEFAULT_AUTO_START_WITH_WINDOWS)
    minimize_to_tray = raw.get("minimize_to_tray", DEFAULT_MINIMIZE_TO_TRAY)
    auto_focus_overlay_on_idle = raw.get(
        "auto_focus_overlay_on_idle", DEFAULT_AUTO_FOCUS_OVERLAY_ON_IDLE
    )
    focus_overlay_blackout_delay_seconds = raw.get(
        "focus_overlay_blackout_delay_seconds", DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS
    )
    onboarding_completed = raw.get("onboarding_completed", DEFAULT_ONBOARDING_COMPLETED)

    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        timeout_int = DEFAULT_IDLE_TIMEOUT_SECONDS

    try:
        poll_float = float(poll_interval)
    except (TypeError, ValueError):
        poll_float = DEFAULT_POLL_INTERVAL_SECONDS

    needs_save = False
    # Migration: older versions allowed very short polling intervals that can
    # cause unnecessary wakeups. Normalize to 1.0s minimum for efficiency.
    if poll_float < 1.0:
        poll_float = 1.0
        needs_save = True

    try:
        max_awake_int = int(max_awake_hours_raw)
    except (TypeError, ValueError):
        max_awake_int = DEFAULT_MAX_AWAKE_SESSION_HOURS

    try:
        low_battery_threshold_int = int(low_battery_threshold)
    except (TypeError, ValueError):
        low_battery_threshold_int = DEFAULT_LOW_BATTERY_THRESHOLD_PERCENT

    try:
        blackout_delay_int = int(focus_overlay_blackout_delay_seconds)
    except (TypeError, ValueError):
        blackout_delay_int = DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS

    settings = AppSettings(
        idle_timeout_seconds=max(MIN_IDLE_TIMEOUT_SECONDS, min(timeout_int, MAX_IDLE_TIMEOUT_SECONDS)),
        poll_interval_seconds=max(MIN_POLL_INTERVAL_SECONDS, min(poll_float, MAX_POLL_INTERVAL_SECONDS)),
        max_awake_session_hours=max(
            MIN_MAX_AWAKE_SESSION_HOURS,
            min(max_awake_int, MAX_MAX_AWAKE_SESSION_HOURS),
        ),
        allow_indefinite_awake=bool(allow_indefinite_awake),
        disable_on_battery=bool(disable_on_battery),
        pause_on_low_battery=bool(pause_on_low_battery),
        low_battery_threshold_percent=max(
            MIN_LOW_BATTERY_THRESHOLD_PERCENT,
            min(low_battery_threshold_int, MAX_LOW_BATTERY_THRESHOLD_PERCENT),
        ),
        security_events_enabled=bool(security_events_enabled),
        auto_start_with_windows=bool(auto_start_with_windows),
        minimize_to_tray=bool(minimize_to_tray),
        auto_focus_overlay_on_idle=bool(auto_focus_overlay_on_idle),
        focus_overlay_blackout_delay_seconds=max(
            MIN_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS,
            min(blackout_delay_int, MAX_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS),
        ),
        onboarding_completed=bool(onboarding_completed),
    )

    if needs_save:
        save_settings(settings)

    return settings


def save_settings(settings: AppSettings) -> None:
    path = get_settings_path()
    payload = asdict(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)
