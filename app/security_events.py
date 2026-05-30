import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_app_data_dir


class SecurityEventSink:
    """Writes newline-delimited JSON security events for SIEM ingestion."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._lock = threading.Lock()
        self._path: Path = get_app_data_dir() / "security_events.jsonl"

    def emit(self, event_type: str, severity: str = "info", **details: Any) -> None:
        if not self._enabled:
            return

        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "severity": severity,
            "process_id": os.getpid(),
            "details": details,
        }

        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    @property
    def path(self) -> Path:
        return self._path
