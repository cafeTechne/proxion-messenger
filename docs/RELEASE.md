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
updater key. The OS prompt is a one-time "are you sure?" — see the landing
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

## Recommended: sovereign auto-update (free, no vendor)

```
cargo install tauri-cli --version "^1"
tauri signer generate -w ~/.proxion-updater.key
```
- Put the **public** key in `tauri-app/src-tauri/tauri.conf.json` →
  `tauri.updater.pubkey`; set `updater.active = true`; replace `ORG` in
  `endpoints` with your `owner/repo`.
- Add the **private** key + password as repo secrets `TAURI_PRIVATE_KEY` /
  `TAURI_KEY_PASSWORD`.

Running apps then check the endpoint, see the new version, verify it against
your public key, and update — with no Apple/Microsoft involvement.

The app shows a custom in-app banner ("A new version is ready — Restart &
update") instead of Tauri's native dialog (`updater.dialog` is `false`). The
banner is wired in `web/main.js` (`_checkForUpdates`) and stays dormant in
the browser and until the updater is active.

## Optional: remove the OS prompt (paid, only if you want to)

- **Windows** — Authenticode cert (a CA, or Azure Trusted Signing ~$10/mo):
  secrets `WINDOWS_CERTIFICATE` (base64 .pfx), `WINDOWS_CERTIFICATE_PASSWORD`.
- **macOS** — Apple Developer ID + notarization: `APPLE_CERTIFICATE`,
  `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
  `APPLE_PASSWORD`, `APPLE_TEAM_ID`.

Absent secrets are skipped — the release still builds.

## Cutting a release

```
git tag vX.Y.Z
git push origin vX.Y.Z      # (requires a configured GitHub remote)
```
`.github/workflows/release.yml` builds Windows/macOS/Linux installers (after
building the PyInstaller gateway sidecar), generates `latest.json` if the
updater key is set, and creates a **draft** Release with the assets. Review
and publish; the landing page picks it up automatically.

## Verifiable builds (E4)

After the three OS builds upload their assets, the `verify` job in
`release.yml` publishes a `SHA256SUMS.txt` to the release and signs a
[build-provenance attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations)
for every asset. End-user verification steps live in
[`docs/VERIFYING.md`](VERIFYING.md). Nothing to provision — attestations use
GitHub's OIDC identity, no secrets involved.

## Verifying the updater manifest

`latest.json` must carry a `version` and per-platform `url`+`signature`
entries. `proxion-messenger-core/tests/test_updater_manifest.py` validates the shape.

## Status

- ✅ Wired: landing page + Pages deploy, release CI (unsigned builds succeed),
  updater config (default-off), manifest validation test.
- ✅ `REPO` set to `cafeTechne/proxion-messenger` in the landing page and the
  updater endpoint; repo pushed; Pages enabled.
- ⏳ Optional: generate the updater key (`tauri signer generate`) and flip
  `updater.active` to true to enable auto-update.
