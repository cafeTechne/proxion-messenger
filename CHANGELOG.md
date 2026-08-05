# Changelog

## 0.2.1

Calls that connect and verify. This release makes a first call work on real networks,
proves who you are talking to, and smooths the first five minutes.

### Calls

- **Calls connect out of the box, even on restrictive networks.** A call between two
  people behind strict or mobile (CGNAT) networks needs a relay to get through. Proxion
  now uses a free public relay by default, the same way it already uses public STUN, so
  most of these calls just connect with nothing to configure. The media stays end-to-end
  encrypted; a relay only ever forwards ciphertext, and only for calls with no direct path.
- **Know before you call.** Settings, Calls has a connectivity self-test that reports
  whether your network is reachable and whether a relay is available, so a restrictive
  network is diagnosed up front instead of a call failing mysteriously.
- **Add your own relay in the app.** Paste a TURN relay (your provider's or your own) in
  Settings, Calls, used on your next call with no config files or restart. You can also
  turn the default public relay off.
- **Verified means verified, across gateways.** A call signs its media fingerprint with
  your identity, and the other side binds it to the contact they know, so a call reads
  Verified even between two different gateways. A tampered media channel is now refused,
  not just flagged, and a call from a contact known to sign that arrives stripped of its
  proof is refused as a downgrade. See [docs/CALLS.md](docs/CALLS.md).

### First run

- **A plainer, shorter start.** The welcome screen drops the jargon, the premature
  presence step is gone, and starting with no account is a first-class choice rather than
  hidden advanced text. The finish screen and empty state explain what to do next.

### Privacy and correctness

- **Blocks are per account.** On a gateway shared by more than one person, one account's
  block no longer affects another. Blocking also matches a contact regardless of which
  identity form a check uses, so a block can no longer silently miss.

### Under the hood

- One identity model with a single place that answers who a message, call, or contact
  belongs to, documented in [docs/IDENTITY.md](docs/IDENTITY.md), replacing the scattered
  per-surface logic behind several past bugs.
- App-visible text follows the project writing rules, checked automatically.

## 0.2.0

Cross-app reach and real calls. This release makes Proxion interoperate across the
Solid ecosystem end to end, and turns the WebRTC stack into voice and video calls that
are verified to work between two people.

### Cross-app interoperability

- **Discover chats by WebID.** Point Proxion at a contact's WebID to list the chats
  they host (from their public type index) and join one through the access-checked join.
- **Invitations over the Solid inbox.** Hosting a conversation with a participant drops
  an ActivityStreams `Invite` in their `ldp:inbox` (Linked Data Notifications), which any
  Solid app can produce or consume. Pick a `foaf:knows` contact by name instead of
  pasting a WebID.
- **Invitations arrive live, and even when the app is closed.** New invitations show up
  in real time over the Solid Notifications channel, with a toast and an unread badge.
  When the app is closed, a reachable gateway relays a Web Push, or an always-on gateway
  behind NAT can poll your inbox over an outbound connection instead. The settings panel
  states honestly whether closed-app push is active for your setup. See
  [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md).

### Calls

- **1:1 and group video calls with screen sharing**, on top of the existing voice.
  Group calls are a peer-to-peer mesh with no media server.
- **End-to-end secure.** Media is DTLS-SRTP encrypted between the peers; the gateway
  relays only signaling and a TURN relay sees only ciphertext. Each side signs its DTLS
  fingerprint with its identity key, surfaced as a Verified or Unverified status. See
  [docs/CALLS.md](docs/CALLS.md).
- **Adaptive quality** with an Auto / High / Standard / Data-saver control; group calls
  divide the bitrate budget across the mesh.
- **A real call surface:** a pre-join camera and microphone preview with device pickers,
  fullscreen video, an active-speaker highlight, a live connection-quality indicator, and
  a responsive layout.
- **Missed and offline calls.** A call to an offline contact now sends a privacy-
  preserving missed-call push and tells the caller the contact is unavailable, instead of
  ringing into the void. Cross-gateway 1:1 calls route their answer and ICE by WebID.

### Security and quality

- A security review of the new interop and call surface, written up in
  [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md), with fixes for an inbox read bound, a
  gateway inbox-poll SSRF, an unbounded poll state set, and divergent-fingerprint SDP.
- A real two-party call test (two gateways, two browsers) that established bidirectional
  video and, in doing so, caught and fixed four call bugs.
- The Solid Chats interop UI is now localized across all six languages.
