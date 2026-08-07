# Verifying a Proxion download

Every GitHub Release ships with two independent verification layers, so you
can confirm that the installer you downloaded is byte-for-byte the one CI
built from the public source, without trusting Apple, Microsoft, or any
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

This proves your file matches what CI uploaded, but not, by itself, that CI
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
- which commit it was built from, so you can audit exactly that source,
- the workflow file that built it (`.github/workflows/release.yml`).

## 3. Auto-update signatures (when enabled)

Independently of the above, the Tauri updater verifies every auto-update
against the maintainer's self-generated Ed25519 key baked into the app at
build time (`tauri.conf.json` → `updater.pubkey`). No certificate authority
is involved. See [`docs/RELEASE.md`](RELEASE.md) for how this key is
provisioned.

## 4. Reproducible builds

The gateway sidecar (the bundled Python backend) is built deterministically.
`build_sidecar.py` pins the build timestamp to the commit (`SOURCE_DATE_EPOCH`)
and fixes string hashing (`PYTHONHASHSEED`), so the same commit built with the
same toolchain produces a byte-for-byte identical binary. Measure it yourself:

```sh
python scripts/check_reproducible.py
```

It builds the sidecar twice and compares the two binaries. CI runs the same
check on every change and publishes the result to the run summary.

Two builds of the same commit are byte-identical (matching SHA-256 over about
150 MB), and the build path is not embedded in the binary, so the result does not
depend on where it was built. With the toolchain pinned (Python 3.12 and
PyInstaller 6.19.0), a rebuild on a different machine is expected to match. To
check your own build against a published reference digest:

```sh
python scripts/check_reproducible.py --expect <sha256>
```

## What this does *not* claim

Two limits remain, and the provenance attestation (layer 2) is the compensating
control for both:

- **Toolchain sensitivity.** Reproducibility holds for the pinned toolchain
  (Python 3.12, PyInstaller 6.19.0). A different Python or PyInstaller version
  can produce different bytes, so match the pinned versions before comparing
  digests. The pin is bumped deliberately.
- **The OS installer.** The Tauri bundling step embeds signing material and
  timestamps, so the final installer is not bit-for-bit reproducible. Verify it
  through the checksums (layer 1) and the attestation (layer 2), not by
  rebuilding.
