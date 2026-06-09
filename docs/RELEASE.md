# Releasing Proxion (signed installers + auto-update)

The release pipeline (`.github/workflows/release.yml`) builds desktop
installers for Windows, macOS, and Linux on a `v*` tag. To make them
**signed** (no OS warnings) and **auto-updating**, provision the secrets
below. Until then, tagging produces functional but **unsigned** artifacts
with auto-update inactive.

## One-time setup (human actions — these need credentials only you can obtain)

### 1. Updater signing key (required for auto-update)
```
cargo install tauri-cli --version "^1"   # if not installed
tauri signer generate -w ~/.proxion-updater.key
```
- Put the **public** key into `tauri-app/src-tauri/tauri.conf.json` →
  `tauri.updater.pubkey`, set `tauri.updater.active = true`, and replace
  `<ORG>` in `endpoints` with your GitHub `org/repo`.
- Add the **private** key + its password as repo secrets
  `TAURI_PRIVATE_KEY` and `TAURI_KEY_PASSWORD`.

### 2. Windows code signing (removes SmartScreen warning)
Obtain an OV/EV Authenticode certificate (a CA, or Azure Trusted Signing).
- `WINDOWS_CERTIFICATE` — base64 of the `.pfx`
- `WINDOWS_CERTIFICATE_PASSWORD`

### 3. macOS signing + notarization (removes Gatekeeper warning)
Requires an Apple Developer account.
- `APPLE_CERTIFICATE` — base64 of the Developer ID Application `.p12`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_SIGNING_IDENTITY` — e.g. `Developer ID Application: Name (TEAMID)`
- `APPLE_ID`, `APPLE_PASSWORD` (app-specific), `APPLE_TEAM_ID`

### 4. Linux
AppImage is unsigned by convention; publish the SHA-256 (and optionally a
GPG signature) alongside the release.

## Cutting a release
```
git tag vX.Y.Z
git push origin vX.Y.Z
```
The workflow builds, signs, generates `latest.json` (the updater manifest),
and creates a **draft** GitHub Release with the installers + `latest.json`.
Review, then publish. Running apps on the previous version check the
`endpoints` URL, see the new signed version, and update on next launch.

## Verifying the updater manifest
`latest.json` must contain a `version`, per-platform `url` + `signature`
entries, and `notes`. `tests/test_updater_manifest.py` validates the shape
of a manifest dict.

## Status
- ✅ Wired: updater config (default-off), `updater` crate feature, release CI
  with all signing env slots, manifest validation test.
- ⏳ Pending human action: provision the secrets above and flip
  `updater.active = true` with the real pubkey.
