# Releasing & distributing Proxion

Proxion is distributed **off GitHub, free, with no vendor lock-in**. Users
install from a GitHub Pages landing page that points at the latest GitHub
Release. There is intentionally **no required dependency on Apple or
Microsoft**.

## The two independent "signing" layers (don't confuse them)

| Layer | What it does | Vendor? | Cost | Default |
|-------|-------------|---------|------|---------|
| **Updater key** | Verifies auto-updates came from you | None (self-generated) | Free | **Recommended** |
| **OS code signing** | Removes the OS first-launch caution prompt | Apple / Microsoft | Paid | **Optional** |

You can ship a fully working, auto-updating, sovereign app using only the
updater key. The OS prompt is a one-time "are you sure?", see the landing
page copy for the honest per-OS story (Linux: none; Windows: More info → Run
anyway; macOS: right-click → Open).

## Install front door (GitHub Pages)

`landing/index.html` is an OS-detecting page that reads the latest release via
the GitHub API at view time (no rebuild per release) and serves the right
asset with honest first-run instructions.

One-time setup:
1. Edit `landing/index.html`: set `const REPO = "<owner>/<repo>"`.
2. Repo Settings → Pages → Source = **GitHub Actions**.
3. Push; `.github/workflows/pages.yml` deploys it. Share that Pages URL.

## Sovereign auto-update (free, no vendor) is ACTIVE

The updater is turned ON: `tauri.conf.json` has `updater.active = true` and a
committed `updater.pubkey`, so running installs check the endpoint, verify a new
version against that public key, and update, with no Apple/Microsoft involved.

**REQUIRED before the next release, or the build fails.** Because the updater is
active, `tauri build` must SIGN the update artifacts. The signing private key is
not in the repo; it is provided to CI as repo secrets. Set both once:

- `TAURI_PRIVATE_KEY`, the CONTENTS of the private key file from
  `tauri signer generate` (paste the file's text, not its path).
- `TAURI_KEY_PASSWORD`, the password chosen at generation (empty string if none).

Add them under repo Settings > Secrets and variables > Actions. If they are
absent, the Release workflow fails to sign and no `latest.json` is produced.
(`release.yml` already wires these env vars into tauri-action and sets
`includeUpdaterJson: true`, so once the secrets exist, the next tag ships a
signed release plus the update manifest automatically.)

The private key was generated locally with `tauri signer generate`; keep it
OUTSIDE the repo and back it up. If it is ever lost, running installs can no
longer verify updates and users must reinstall once from GitHub with a new key.

The app shows a custom in-app banner (localized "A new version is ready, Restart
& update") instead of Tauri's native dialog (`updater.dialog` is `false`), and
Settings > App > Check for updates runs the same check on demand and reports
"you are on the latest version". Both are wired in `web/main.js`
(`_showUpdateBanner` / `_manualCheckForUpdates`) and stay dormant in the browser.

## Optional: remove the OS prompt (paid, only if you want to)

- **Windows**, Authenticode cert (a CA, or Azure Trusted Signing ~$10/mo):
  secrets `WINDOWS_CERTIFICATE` (base64 .pfx), `WINDOWS_CERTIFICATE_PASSWORD`.
- **macOS**, Apple Developer ID + notarization: `APPLE_CERTIFICATE`,
  `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
  `APPLE_PASSWORD`, `APPLE_TEAM_ID`.

Absent secrets are skipped, the release still builds.

## Cutting a release

```
git tag vX.Y.Z
git push origin vX.Y.Z      # (requires a configured GitHub remote)
```
`.github/workflows/release.yml` builds Windows/macOS/Linux installers (after
building the PyInstaller gateway sidecar), generates `latest.json` if the
updater key is set, and creates a **draft** Release with the assets. Review
and publish; the landing page picks it up automatically.

## Homebrew tap (macOS)

`brew install cafeTechne/proxion/proxion` installs the .dmg from the tap repo
[`cafeTechne/homebrew-proxion`](https://github.com/cafeTechne/homebrew-proxion).
`.github/workflows/homebrew.yml` regenerates the cask (version + per-arch
sha256) whenever a release is **published**. One-time setup: create a
fine-grained PAT with **Contents: write** on the tap repo and save it as the
`TAP_GITHUB_TOKEN` secret here; until then the job no-ops with a notice and
the cask can be bumped by hand. Graduating to the official `homebrew-cask`
repo is possible later once the project meets their notability bar
(★75/30 forks); the cask file carries over as-is.

## Verifiable builds (E4)

After the three OS builds upload their assets, the `verify` job in
`release.yml` publishes a `SHA256SUMS.txt` to the release and signs a
[build-provenance attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations)
for every asset. End-user verification steps live in
[`docs/VERIFYING.md`](VERIFYING.md). Nothing to provision, attestations use
GitHub's OIDC identity, no secrets involved.

## Verifying the updater manifest

`latest.json` must carry a `version` and per-platform `url`+`signature`
entries. `proxion-messenger-core/tests/test_updater_manifest.py` validates the shape.

## Status

- ✅ Wired: landing page + Pages deploy, release CI, manifest validation test,
  in-app update banner + Settings > App > Check for updates.
- ✅ `REPO` set to `cafeTechne/proxion-messenger` in the landing page and the
  updater endpoint; repo pushed; Pages enabled.
- ✅ Auto-update ON: `updater.active = true` with a committed `pubkey`
  (key ID `529EEA89E67E1E6E`).
- ⏳ REQUIRED before the next release: add the `TAURI_PRIVATE_KEY` /
  `TAURI_KEY_PASSWORD` repo secrets, or the signed build fails (see above).
