import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .config import AppSettings
from .security_events import SecurityEventSink
from .windows_power import WindowsPowerManager


class MonitorState(str, Enum):
    STOPPED = "Stopped"
    ACTIVE = "Active"
    KEEPING_AWAKE = "Idle / Keeping Awake"
    POLICY_BLOCKED = "Idle / Policy Blocked"


@dataclass
class MonitorCallbacks:
    on_state_change: Optional[Callable[[MonitorState], None]] = None
    on_log: Optional[Callable[[str], None]] = None


class IdleMonitor:
    def __init__(
        self,
        settings: AppSettings,
        logger: logging.Logger,
        security_events: Optional[SecurityEventSink] = None,
        callbacks: Optional[MonitorCallbacks] = None,
    ) -> None:
        self._settings = settings
        self._logger = logger
        self._security_events = security_events or SecurityEventSink(
            enabled=settings.security_events_enabled
        )
        self._callbacks = callbacks or MonitorCallbacks()

        self._power = WindowsPowerManager()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = MonitorState.STOPPED
        self._awake_started_at: Optional[float] = None

    @property
    def state(self) -> MonitorState:
        return self._state

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._set_state(MonitorState.ACTIVE)

        self._thread = threading.Thread(
            target=self._run,
            name="IdleMonitorThread",
            daemon=True,
        )
        self._thread.start()
        self._emit_log("Monitoring started")
        self._emit_security_event(
            "monitoring_started",
            idle_timeout_seconds=self._settings.idle_timeout_seconds,
            max_awake_session_hours=self._settings.max_awake_session_hours,
            allow_indefinite_awake=self._settings.allow_indefinite_awake,
            disable_on_battery=self._settings.disable_on_battery,
            pause_on_low_battery=self._settings.pause_on_low_battery,
            low_battery_threshold_percent=self._settings.low_battery_threshold_percent,
        )

    def stop(self) -> None:
        was_running = self._state != MonitorState.STOPPED or (
            self._thread is not None and self._thread.is_alive()
        )
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        wake_released = False
        try:
            wake_released = self._power.release_awake()
            if wake_released:
                self._emit_log("Monitoring stopped, releasing wake assertion")
                self._emit_security_event("awake_released", reason="monitor_stopped")
        except Exception as exc:  # pragma: no cover
            self._logger.exception("Failed to release wake assertion: %s", exc)

        self._set_state(MonitorState.STOPPED)
        self._awake_started_at = None
        if was_running and not wake_released:
            self._emit_log("Monitoring stopped")
        self._emit_security_event("monitoring_stopped")

    def _run(self) -> None:
        is_idle = False
        self._awake_started_at = None
        last_policy_reason = ""

        while not self._stop_event.is_set():
            try:
                idle_seconds = self._power.get_idle_seconds()
                threshold = self._settings.idle_timeout_seconds
                now = time.monotonic()

                on_ac_power = True
                if self._settings.disable_on_battery:
                    on_ac_power = self._power.is_on_ac_power()

                battery_percent = -1
                if self._settings.pause_on_low_battery:
                    battery_percent = self._power.get_battery_percent()

                policy_reason = ""
                if self._settings.disable_on_battery and not on_ac_power:
                    policy_reason = "on_battery"
                elif (
                    self._settings.pause_on_low_battery
                    and battery_percent >= 0
                    and battery_percent <= self._settings.low_battery_threshold_percent
                ):
                    policy_reason = "low_battery"
                elif (
                    not self._settings.allow_indefinite_awake
                    and
                    self._awake_started_at is not None
                    and self._settings.max_awake_session_hours > 0
                    and (now - self._awake_started_at)
                    >= (self._settings.max_awake_session_hours * 3600)
                ):
                    policy_reason = "max_awake_session_reached"

                if idle_seconds >= threshold and not is_idle:
                    is_idle = True
                    self._emit_security_event("idle_detected", idle_seconds=round(idle_seconds, 3))

                    if policy_reason:
                        self._set_state(MonitorState.POLICY_BLOCKED)
                        if policy_reason != last_policy_reason:
                            self._emit_log(
                                "Idle detected but policy blocked wake assertion "
                                f"({policy_reason})"
                            )
                            self._emit_security_event(
                                "policy_blocked",
                                reason=policy_reason,
                                idle_seconds=round(idle_seconds, 3),
                                battery_percent=battery_percent,
                            )
                    else:
                        if self._power.assert_awake():
                            self._awake_started_at = now
                            self._emit_log("Idle detected, keeping system awake")
                            self._emit_security_event(
                                "awake_asserted",
                                idle_seconds=round(idle_seconds, 3),
                            )
                        self._set_state(MonitorState.KEEPING_AWAKE)

                elif idle_seconds >= threshold and is_idle:
                    if policy_reason:
                        if self._power.release_awake():
                            self._emit_log(
                                "Policy triggered, releasing wake assertion "
                                f"({policy_reason})"
                            )
                            self._emit_security_event(
                                "awake_released",
                                reason=policy_reason,
                            )
                        self._awake_started_at = None
                        self._set_state(MonitorState.POLICY_BLOCKED)
                        if policy_reason != last_policy_reason:
                            self._emit_security_event(
                                "policy_blocked",
                                reason=policy_reason,
                                idle_seconds=round(idle_seconds, 3),
                                battery_percent=battery_percent,
                            )
                    else:
                        if self._state == MonitorState.POLICY_BLOCKED:
                            self._emit_log("Policy conditions cleared, resuming awake control")
                            self._emit_security_event("policy_cleared")
                        if not self._power.is_asserted() and self._power.assert_awake():
                            self._awake_started_at = now
                            self._emit_log("Idle ongoing, keeping system awake")
                            self._emit_security_event(
                                "awake_asserted",
                                idle_seconds=round(idle_seconds, 3),
                            )
                        self._set_state(MonitorState.KEEPING_AWAKE)
                        last_policy_reason = ""

                elif idle_seconds < threshold and is_idle:
                    is_idle = False
                    if self._power.release_awake():
                        self._emit_log("Activity resumed, releasing control")
                        self._emit_security_event(
                            "awake_released",
                            reason="activity_resumed",
                        )
                    self._awake_started_at = None
                    last_policy_reason = ""
                    self._set_state(MonitorState.ACTIVE)

                elif idle_seconds < threshold and self._state != MonitorState.ACTIVE:
                    self._set_state(MonitorState.ACTIVE)

                if policy_reason:
                    last_policy_reason = policy_reason

            except Exception as exc:  # pragma: no cover
                self._logger.exception("Monitor loop error: %s", exc)
                self._emit_log(f"Monitor error: {exc}")
                self._emit_security_event("monitor_error", severity="error", error=str(exc))

            self._stop_event.wait(timeout=self._settings.poll_interval_seconds)

    def _set_state(self, state: MonitorState) -> None:
        self._state = state
        if self._callbacks.on_state_change:
            self._callbacks.on_state_change(state)

    def _emit_log(self, message: str) -> None:
        self._logger.info(message)
        if self._callbacks.on_log:
            self._callbacks.on_log(message)

    def _emit_security_event(self, event_type: str, severity: str = "info", **details: object) -> None:
        try:
            self._security_events.emit(event_type=event_type, severity=severity, **details)
        except Exception as exc:  # pragma: no cover
            self._logger.exception("Security event emission failed: %s", exc)
