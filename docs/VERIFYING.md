# Verifying a Proxion download

Every GitHub Release ships with two independent verification layers, so you
can confirm that the installer you downloaded is byte-for-byte the one CI
built from the public source — without trusting Apple, Microsoft, or any
third-party CA.

## 1. Checksums (`SHA256SUMS.txt`)

Each release includes a `SHA256SUMS.txt` asset generated in CI after all
platform builds finish. Verify your download against it:

**Linux**
```sh
sha256sum -c SHA256SUMS.txt --ignore-missing
```

**macOS**
```sh
shasum -a 256 -c SHA256SUMS.txt --ignore-missing
```

**Windows (PowerShell)**
```powershell
(Get-FileHash .\Proxion_0.1.0_x64-setup.exe -Algorithm SHA256).Hash
# compare with the matching line in SHA256SUMS.txt
```

This proves your file matches what CI uploaded — but not, by itself, that CI
built it. That's layer 2.

## 2. Build provenance attestations

CI signs a [build provenance attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations)
for every release asset. The attestation cryptographically binds the file's
SHA-256 digest to the exact workflow run, commit, and repository that
produced it, countersigned by GitHub's Sigstore instance.

With the [GitHub CLI](https://cli.github.com/):

```sh
gh attestation verify Proxion_0.1.0_x64-setup.exe \
  --repo cafeTechne/proxion-messenger
```

A successful verification tells you:

- the file was built by a GitHub Actions run in this repository (not on
  someone's laptop),
- which commit it was built from — so you can audit exactly that source,
- the workflow file that built it (`.github/workflows/release.yml`).

## 3. Auto-update signatures (when enabled)

Independently of the above, the Tauri updater verifies every auto-update
against the maintainer's self-generated Ed25519 key baked into the app at
build time (`tauri.conf.json` → `updater.pubkey`). No certificate authority
is involved. See [`docs/RELEASE.md`](RELEASE.md) for how this key is
provisioned.

## What this does *not* claim

Builds are **verifiable, not yet bit-for-bit reproducible**: PyInstaller and
Tauri bundling embed timestamps and environment details, so building the same
commit yourself will not produce an identical binary today. The provenance
attestation is the compensating control — it proves the published binary came
from the public source at a specific commit via the public workflow. Full
reproducibility remains a roadmap goal (Phase L, E4 follow-up).
