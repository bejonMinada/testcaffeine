# Code Signing and Verification Guide

This document explains how to sign TestCaffeine and validate authenticity in enterprise environments.

## 1. Certificate requirements

Use an Authenticode code-signing certificate from a trusted CA, for example:

- DigiCert
- Sectigo
- GlobalSign

For best SmartScreen reputation results over time, use an EV Code Signing certificate when available.

## 2. Sign the executable

Use `signtool.exe` from the Windows SDK.

```powershell
signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /a dist\TestCaffeine.exe
```

Notes:

- `/tr` adds RFC3161 timestamping so signatures remain valid after cert expiry.
- `/a` chooses the best certificate in the current store.

If using an HSM/token provider, include CSP/KSP options per your CA instructions.

## 3. Verify signature locally

```powershell
signtool verify /pa /v dist\TestCaffeine.exe
```

Expected result: successful chain build to a trusted root and valid timestamp.

## 4. Publish checksums for IT

```powershell
Get-FileHash dist\TestCaffeine.exe -Algorithm SHA256
```

Provide hash, signing certificate subject, thumbprint, timestamp URL, and build date to IT security.

## 5. SmartScreen guidance

- Unsigned binaries typically trigger `Unknown Publisher`.
- Signed binaries gain trust reputation over time.
- EV certificates generally reduce friction for first-run warnings.
- Distribute from trusted enterprise channels to improve trust outcomes.

## 6. Compliance mapping (high-level)

For SOC 2 / ISO 27001 style controls, keep evidence of:

- Build pipeline integrity and access controls.
- Signed artifact provenance (who signed, when, with what cert).
- Vulnerability scanning and dependency inventory.
- Change approvals and release records.

## 7. IT verification checklist

- Verify Authenticode signature and certificate chain.
- Validate SHA256 hash against release manifest.
- Confirm publisher name and certificate validity window.
- Confirm timestamp authority response is present.
- Run malware scan in enterprise security tooling before broad rollout.
