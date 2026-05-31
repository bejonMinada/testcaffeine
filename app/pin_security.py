import base64
import ctypes
import getpass
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import APP_NAME, get_app_data_dir

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 12
PIN_HASH_ITERATIONS = 320_000
PIN_SALT_BYTES = 16
LOCKOUT_SECONDS_CAP = 120
MAX_BACKOFF_EXPONENT = 7


@dataclass
class PinVerificationResult:
    success: bool
    locked: bool
    retry_after_seconds: int
    failed_attempts: int


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class PinManager:
    def __init__(self) -> None:
        self._path: Path = get_app_data_dir() / "focus_pin.dat"
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_wchar_p,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_DATA_BLOB),
        ]
        self._crypt32.CryptProtectData.restype = ctypes.c_int
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_DATA_BLOB),
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_int
        self._session_boot_marker = self._get_boot_marker()

    @property
    def current_username(self) -> str:
        return getpass.getuser()

    def is_configured(self) -> bool:
        return self._load_record() is not None

    def can_recover_after_reboot(self) -> bool:
        record = self._load_record()
        if record is None:
            return True
        marker = int(record.get("boot_marker_at_pin_change", 0))
        return self._session_boot_marker > marker

    def set_new_pin(self, pin: str) -> None:
        normalized = self._normalize_pin(pin)
        salt = os.urandom(PIN_SALT_BYTES)
        verifier = self._derive_pin_hash(normalized, salt)
        payload = {
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "hash_b64": base64.b64encode(verifier).decode("ascii"),
            "iterations": PIN_HASH_ITERATIONS,
            "failed_attempts": 0,
            "lockout_until_epoch": 0.0,
            "last_failure_epoch": 0.0,
            "last_success_epoch": 0.0,
            "boot_marker_at_pin_change": self._session_boot_marker,
            "created_at_epoch": time.time(),
        }
        self._save_record(payload)

    def change_pin(self, current_pin: str, new_pin: str) -> PinVerificationResult:
        result = self.verify_pin(current_pin)
        if not result.success:
            return result
        self.set_new_pin(new_pin)
        return PinVerificationResult(success=True, locked=False, retry_after_seconds=0, failed_attempts=0)

    def recover_pin_after_reboot(self, windows_username: str, new_pin: str) -> None:
        if not self.can_recover_after_reboot():
            raise ValueError("PIN recovery requires a full device restart and fresh sign-in.")
        expected = self.current_username.strip().lower()
        observed = (windows_username or "").strip().lower()
        if observed != expected:
            raise ValueError("Windows username confirmation failed.")
        self.set_new_pin(new_pin)

    def verify_pin(self, pin: str) -> PinVerificationResult:
        record = self._load_record()
        if record is None:
            raise ValueError("PIN is not configured.")

        now = time.time()
        lockout_until = float(record.get("lockout_until_epoch", 0.0) or 0.0)
        failed_attempts = int(record.get("failed_attempts", 0) or 0)
        if lockout_until > now:
            retry_after = int(max(1, lockout_until - now))
            return PinVerificationResult(
                success=False,
                locked=True,
                retry_after_seconds=retry_after,
                failed_attempts=failed_attempts,
            )

        normalized = self._normalize_pin(pin)
        salt = base64.b64decode(record["salt_b64"])
        expected_hash = base64.b64decode(record["hash_b64"])
        actual_hash = self._derive_pin_hash(normalized, salt)
        if hmac.compare_digest(expected_hash, actual_hash):
            record["failed_attempts"] = 0
            record["lockout_until_epoch"] = 0.0
            record["last_success_epoch"] = now
            self._save_record(record)
            return PinVerificationResult(success=True, locked=False, retry_after_seconds=0, failed_attempts=0)

        failed_attempts += 1
        retry_after = min(LOCKOUT_SECONDS_CAP, 2 ** min(failed_attempts, MAX_BACKOFF_EXPONENT))
        record["failed_attempts"] = failed_attempts
        record["last_failure_epoch"] = now
        record["lockout_until_epoch"] = now + retry_after
        self._save_record(record)
        return PinVerificationResult(
            success=False,
            locked=True,
            retry_after_seconds=retry_after,
            failed_attempts=failed_attempts,
        )

    def _normalize_pin(self, pin: str) -> str:
        text = str(pin or "").strip()
        if not text.isdigit():
            raise ValueError("PIN must contain digits only.")
        if len(text) < PIN_MIN_LENGTH or len(text) > PIN_MAX_LENGTH:
            raise ValueError(f"PIN must be {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH} digits.")
        return text

    def _derive_pin_hash(self, pin: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PIN_HASH_ITERATIONS, dklen=32)

    def _load_record(self) -> dict[str, object] | None:
        if not self._path.exists():
            return None
        raw_ciphertext = self._path.read_bytes()
        plaintext = self._dpapi_unprotect(raw_ciphertext)
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Invalid PIN payload.")
        return payload

    def _save_record(self, record: dict[str, object]) -> None:
        plaintext = json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ciphertext = self._dpapi_protect(plaintext)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_bytes(ciphertext)
        temp_path.replace(self._path)

    def _dpapi_protect(self, data: bytes) -> bytes:
        in_blob, in_buffer = self._to_blob(data)
        out_blob = _DATA_BLOB()
        entropy, entropy_buffer = self._to_blob(APP_NAME.encode("utf-8"))
        keepalive = (in_buffer, entropy_buffer)
        try:
            ok = self._crypt32.CryptProtectData(
                ctypes.byref(in_blob),
                ctypes.c_wchar_p("TestCaffeine Focus PIN"),
                ctypes.byref(entropy),
                None,
                None,
                0,
                ctypes.byref(out_blob),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            return self._blob_to_bytes(out_blob)
        finally:
            _ = keepalive
            self._free_blob(out_blob)

    def _dpapi_unprotect(self, data: bytes) -> bytes:
        in_blob, in_buffer = self._to_blob(data)
        out_blob = _DATA_BLOB()
        entropy, entropy_buffer = self._to_blob(APP_NAME.encode("utf-8"))
        keepalive = (in_buffer, entropy_buffer)
        try:
            ok = self._crypt32.CryptUnprotectData(
                ctypes.byref(in_blob),
                None,
                ctypes.byref(entropy),
                None,
                None,
                0,
                ctypes.byref(out_blob),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            return self._blob_to_bytes(out_blob)
        finally:
            _ = keepalive
            self._free_blob(out_blob)

    def _get_boot_marker(self) -> int:
        uptime_ms = int(self._kernel32.GetTickCount64())
        return int(time.time() - (uptime_ms / 1000.0))

    def _to_blob(self, data: bytes) -> tuple[_DATA_BLOB, object]:
        raw = ctypes.create_string_buffer(data)
        blob = _DATA_BLOB(
            cbData=len(data),
            pbData=ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, raw

    def _blob_to_bytes(self, blob: _DATA_BLOB) -> bytes:
        if blob.cbData == 0:
            return b""
        return ctypes.string_at(blob.pbData, blob.cbData)

    def _free_blob(self, blob: _DATA_BLOB) -> None:
        if blob.pbData:
            self._kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))
