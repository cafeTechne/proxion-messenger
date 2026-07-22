<div align="center">

# Proxion

**The messenger that keeps your data yours.**

End-to-end encrypted messaging and voice, built on the [Solid Protocol](https://solidproject.org).
Your messages live in your own Solid pod, on hardware you control —
no account, no phone number, no company in the middle.

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion desktop: a verified end-to-end encrypted conversation, with rooms and contacts in the sidebar" width="800">

</div>

## Why Proxion

Every mainstream messenger stores your conversations in someone else's data center, under
someone else's terms. Proxion inverts that:

- **Your data, in your pod, in the open** — room history is written to a
  [Solid](https://solidproject.org) pod *you* choose ([Inrupt PodSpaces](https://www.inrupt.com)
  free tier, [solidcommunity.net](https://solidcommunity.net), or a self-hosted Community Solid
  Server) as standard, typed RDF that any Solid app you authorize can read — **not an opaque
  encrypted blob**. The storage format is a documented, open contract
  ([docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md)), so it's a Solid app any other app can
  interoperate with, not a silo with an export button. Pod-less local-only mode works too.
- **Actually private** — end-to-end encrypted DMs with per-contact safety numbers you can verify
  out loud. The encryption is *on the wire*, between you and your contact, so no relay or gateway
  in the middle can read your messages — it is **not** a lock-box that hides your own data from
  your own apps. DM history stays on-device by default; an **opt-in** archive (off by default)
  can mirror it to your own pod as open RDF for cross-device sync. Your identity is an Ed25519
  key generated on your machine; no signup, nothing to leak.
- **No lock-in** — open source, open protocol, standard data. Gateways federate
  peer-to-peer by Proxion address, with no central registry to shut down.

## Don't trust me — check

Claims about privacy software are worth exactly what you can verify. So:

**Every installer is cryptographically traceable to public source.** Releases ship
`SHA256SUMS.txt` plus signed [SLSA build-provenance](https://slsa.dev) attestations, so you can
prove the binary you downloaded was built by CI from this repository and not tampered with
afterwards:

```sh
sha256sum -c SHA256SUMS.txt --ignore-missing        # checksums match
gh attestation verify Proxion_0.1.5_x64-setup.exe \
   --repo cafeTechne/proxion-messenger                # built by CI, from this source
```

**The code is checked.** 3,400+ backend tests and 400+ frontend tests run on every push across
Linux, macOS and Windows, alongside accessibility (axe-core, WCAG 2.2 AA), i18n and contrast
gates. Full detail in [TESTING.md](TESTING.md) and [docs/VERIFYING.md](docs/VERIFYING.md).

**Your data is readable without us.** Pod contents are documented, typed RDF, not an opaque
blob — the format is a written contract in [docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md),
so any Solid app you authorize can read it and you can walk away at any time.

## Honest status

Proxion is **beta software built primarily by one person, with AI assistance**, and it has
**not had a third-party security audit**. The Double Ratchet implementation is our own, not
libsignal. It is a serious attempt at a sovereign messenger and the security work is real
(see [SECURITY.md](SECURITY.md) and [docs/security/](docs/security/)) — but if your safety
depends on your messenger, use [Signal](https://signal.org). Use Proxion because you want to
own your data, and help us find what's broken.

Bug reports and scoped pull requests are genuinely wanted — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Features

<img src="landing/assets/screenshot-mobile.png" alt="Proxion on mobile" width="210" align="right">

Rooms and DMs · P2P WebRTC voice calls · file attachments and media previews · reactions,
edits, pins, mentions · disappearing and scheduled messages · mute and block · cross-gateway
federation · multi-device with encrypted fanout · opt-in cross-device sync of DM history,
bookmarks, settings, saved GIFs, mutes, and blocks via your pod · offline-capable PWA with push ·
six languages including RTL Arabic · WCAG 2.2 AA accessible.

## Download

Grab the latest native build for **Windows (x64/ARM64), macOS (Intel/Apple Silicon), or
Linux (x64/ARM64)** from the [install page](https://cafetechne.github.io/proxion-messenger/)
or the [releases page](../../releases/latest).

**Just download and open it.** The gateway that powers Proxion is bundled *inside* the app —
there's no Python to install and no server to run. Running a standalone gateway is optional and
only for people who want to self-host or connect a phone to their own desktop (see
[Run from source](#run-from-source-developers--self-hosters)).

The executables are intentionally not vendor-signed (no Apple/Microsoft gatekeeping;
updates are verified against Proxion's own signing key), so the OS shows a one-time
caution prompt — on Windows: *More info → Run anyway*; on macOS: *right-click → Open*.
Linux has no prompt.

On macOS you can also install via [Homebrew](https://brew.sh):

```sh
brew install cafeTechne/proxion/proxion
```

Every release ships `SHA256SUMS.txt` and signed build-provenance attestations
proving each installer was built by CI from this repository's public source —
see [docs/VERIFYING.md](docs/VERIFYING.md).

## Run from source (developers & self-hosters)

Most people should just use the installer above — this section is for hacking on Proxion or
running your own always-on gateway (e.g. to point a phone at it).

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # optional: pod credentials; leave blank for local-only
python run_gateway.py
# open http://localhost:8080
```

## Build a native executable

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # PyInstaller sidecar for your platform
cd tauri-app && cargo tauri build # native installer
```

## Architecture

- `web/` — frontend (vanilla JS, no framework), served by the gateway
- `proxion-messenger-core/` — Python backend library + WebSocket/HTTP gateway
- `tauri-app/` — Rust/Tauri desktop wrapper bundling the gateway as a sidecar
- `landing/` — the GitHub Pages install page

The **gateway** is Proxion's real-time transport layer: it holds your identity keys, talks Solid
to your pod, and federates directly with your contacts' gateways by Proxion address. Real-time
federated messaging needs a component like this — the same job a homeserver does for Matrix or an
SMTP server does for email — because the Solid Protocol covers data and identity, not live
delivery, presence, or WebRTC signaling. For desktop users the gateway is **bundled inside the
installer as a sidecar and starts with the app** — you never see it or touch Python. Self-hosters
can instead run it standalone and point phones or browsers at it. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Testing

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend units
```

Browser-level gates live in `web/` (`smoke:a11y`, `smoke:keyboard`, `smoke:pseudo`,
`smoke:federation`, …) — see [TESTING.md](TESTING.md).

## License

[AGPL-3.0](LICENSE) — free to use, self-host, fork, and contribute to. If you run a
modified Proxion as a service for others, you must publish your changes. That's the
point: nobody gets to turn this back into a silo.
