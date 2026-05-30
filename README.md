# TestCaffeine

TestCaffeine is a Windows desktop utility that monitors real user inactivity and uses the Windows `SetThreadExecutionState` API to keep a laptop awake safely for long automation runs.

## Download and run (no Python required)

- Download the latest release asset from GitHub Releases: https://github.com/bejonMinada/testcaffeine/releases/latest
- Run on a 64-bit Windows machine.
- Python is not required on end-user machines because the release executable is built with PyInstaller.

## Release verification

- Release: https://github.com/bejonMinada/testcaffeine/releases/tag/v1.0.0
- Asset: `TestCaffeine.exe`
- SHA-256: `1BDDA2EF14300CC857BE95830732FE423C2BC27D0B34C0079C08C2D8980833D0`

To verify on Windows:

```powershell
Get-FileHash .\TestCaffeine.exe -Algorithm SHA256
```

## Why this is compliant

- Uses Windows power-management API only.
- Does not simulate keyboard or mouse input.
- Releases control immediately when activity resumes.
- Logs all key state changes for transparency.

## Features

- Configurable inactivity timeout (default: 30 seconds).
- Configurable max awake session guardrail (default: 2 hours).
- Optional indefinite awake mode for long unattended runs.
- Optional policy to block wake assertions when device is on battery.
- Optional low-battery safeguard with configurable threshold percent.
- Optional auto-start with Windows (current user).
- Optional minimize-to-tray behavior with tray menu controls.
- Diagnostics export to ZIP (settings + logs + security events).
- First-run setup wizard for auto-start, tray mode, and battery safeguards.
- Minimal Tkinter UI to start/stop monitoring.
- Single-instance protection (prevents launching multiple independent app instances).
- Live status: `Active`, `Idle / Keeping Awake`, `Idle / Policy Blocked`, `Stopped`.
- Persistent settings in `%LOCALAPPDATA%/TestCaffeine/settings.json`.
- Rolling logs in `%LOCALAPPDATA%/TestCaffeine/test_caffeine.log`.
- Structured security events in `%LOCALAPPDATA%/TestCaffeine/security_events.jsonl`.

## Runtime behavior

1. Reads the system idle time from `GetLastInputInfo`.
2. If idle time >= timeout, calls:
   - `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)`
3. If activity resumes, calls:
   - `SetThreadExecutionState(ES_CONTINUOUS)`
4. On application exit, always releases the wake assertion.

## Quick start (developer)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python main.py
```

## Build standalone executable

```powershell
./build.ps1 -Clean
```

Output:

- `dist/TestCaffeine.exe`

If the executable is in use while rebuilding, close TestCaffeine first, then rerun the build command.

## Security and IT notes

- Sign the executable with an Authenticode certificate before distribution.
- Publish hash and signature metadata for internal verification.
- Use controlled distribution channels (Intune, SCCM, approved software portal).
- Ingest `security_events.jsonl` into SIEM for policy/audit visibility.

Detailed signing steps are in `SIGNING.md`.

## License

Apache-2.0 (recommended for business-friendly open-source distribution).

## Performance profile

- Poll interval defaults to `0.5s` with a single daemon thread.
- No high-frequency input hooks.
- Very low CPU usage in idle state due to event-driven wait.

## Limitations

- This tool is Windows-only.
- Some organization lock policies may still enforce lock behavior regardless of power assertion.
