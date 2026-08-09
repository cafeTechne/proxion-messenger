<div align="center">

# Proxion

**Private messaging that you actually own.**

Chat, voice, and video with real end-to-end encryption, where your conversations live in
storage you control instead of a company's servers. Built on the open
[Solid](https://solidproject.org) standard. No phone number, no signup, no company in the middle.

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion desktop: an end-to-end encrypted conversation, with rooms and contacts in the sidebar" width="800">

</div>

## What is Proxion?

Proxion is a messenger like the ones you already use, with one difference that changes
everything: your data belongs to you.

Your messages, files, and call history live in your own **Solid pod**, a personal storage
space you control, instead of being locked inside a company's app. Pick a free pod
provider, bring your own, or run one yourself, and move any time. Your identity is created
on your device, so there is no account to sign up for and nothing to leak.

It is a real, everyday messenger: rooms and direct messages, voice and video calls with
screen sharing, files, reactions, replies, and more, on Windows, macOS, Linux, and the web.

## Get Proxion

**Download and open it.** There is nothing to configure and no server to run.

- **Windows, macOS, or Linux:** grab the [install page](https://cafetechne.github.io/proxion-messenger/)
  or the [latest release](../../releases/latest).
- **macOS with [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **In your browser:** Proxion also runs as an installable web app.

Because Proxion is not signed by Apple or Microsoft (on purpose, so no gatekeeper sits
between you and your own software), your OS shows a one-time prompt the first time you open
it. On Windows choose *More info then Run anyway*; on macOS *right-click then Open*; Linux
has no prompt.

## What you can do

- **Message and call.** Group rooms and private one-to-one chats, plus peer-to-peer voice
  and video calls with screen sharing.
- **Keep your history.** Everything lives in your pod, in an open format, so it is yours to
  keep, read with other tools, and take with you.
- **Truly private conversations.** Direct messages are end-to-end encrypted, and you can
  confirm you are really talking to your contact with a short safety phrase you read aloud
  together. Calls are encrypted the same way.
- **Reach anyone on Solid.** Find and invite people across the wider Solid ecosystem, not
  just other Proxion users.
- **Use it anywhere.** Desktop, browser, and mobile, offline-capable, in six languages
  including right-to-left Arabic, and built to work with a screen reader and keyboard alone.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion running on a phone" width="240">
</p>

## Part of the Solid ecosystem

Proxion is a good Solid citizen, not a walled garden that happens to use Solid underneath.
A room you create is written in the standard Solid chat format, so other Solid apps can
read and join it.

<img src="landing/assets/interop-sidebyside.png" alt="The same room shown side by side in Proxion and the SolidOS databrowser, with the same messages" width="900">

- **Open a Proxion room in [SolidOS](https://solidos.org)** and every message is there.
  This is checked against the real SolidOS in our tests, not just claimed.
- **Find and invite people by their WebID.** Discover the chats someone hosts, or drop an
  invitation in their Solid inbox that any Solid app can read.
- **See new messages and invitations in real time,** even reaching you when Proxion is
  closed.
- **Your rooms outlive any one server.** A room's structure lives in your pod, so it can be
  rebuilt from your pod alone.

Shared rooms are open by design so other apps can read them; private direct messages are
end-to-end encrypted and deliberately readable only by the people in them. The full data
format is documented in [docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), the
app-by-app compatibility picture in [docs/INTEROP.md](docs/INTEROP.md), and a
per-requirement audit against the Solid spec suite in
[docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Private by design

- **End-to-end encrypted** direct messages and calls, so no relay or server in the middle
  can read them.
- **Your data in your pod, in the open.** It is documented, standard data, not a locked
  blob, so any app you allow can read it and you can walk away whenever you like.
- **Verifiable, not just promised.** Every download can be checked back to this public
  source code, and thousands of automated tests run on every change.

For the details, including the call security model, the threat model, and how to verify a
download, see [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md),
[docs/CALLS.md](docs/CALLS.md), [SECURITY.md](SECURITY.md), and
[docs/VERIFYING.md](docs/VERIFYING.md).

## Contributing

Proxion is open source and contributions are genuinely welcome, from bug reports to code.
Start with [CONTRIBUTING.md](CONTRIBUTING.md). If you are here from the Solid community and
something does not interoperate the way you expect, that is exactly the kind of issue we
want to hear about.

## For developers and self-hosters

Most people should just use the installer above. To hack on Proxion or run your own
always-on gateway (for example to point a phone at your desktop):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # optional pod credentials; leave blank for local-only
python run_gateway.py
# open http://localhost:8080
```

Build a native installer:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # bundle the gateway for your platform
cd tauri-app && cargo tauri build # native installer
```

Run the tests:

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**How it fits together.** The frontend (in `web/`) is served by a small **gateway** (in
`proxion-messenger-core/`) that holds your keys, talks to your pod, and connects directly
to your contacts' gateways. On desktop the gateway is bundled inside the app and starts
with it, so you never see it or install Python. The gateway exists because Solid covers
data and identity but not live delivery, presence, or call setup, the same role a
homeserver plays for Matrix or an SMTP server plays for email. Details in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## License

[AGPL-3.0](LICENSE). Free to use, self-host, fork, and contribute to. If you run a modified
Proxion as a service for others, you have to publish your changes. That is the point:
nobody gets to turn this back into a silo.
