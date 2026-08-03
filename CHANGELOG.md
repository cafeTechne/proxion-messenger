# Changelog

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
