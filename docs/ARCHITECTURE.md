# Proxion Architecture: Technical Reference

Proxion is a **sovereign, federated messenger for small trusted groups (2-6
people)**. Each user runs their own **gateway** (shipped as a `.exe`/desktop
app); there is no central server and no account. The product thesis: *download
it, share a link, message and voice-call your friends. Self-hosting is a backend
detail the user never has to think about.*

This document describes the **as-built** system. It is **relay-first**: messages
flow client → your gateway → (directly or via a sealed relay) → your friend's
gateway. A Solid Pod is an **optional durable backbone**, not the transport.

> Historical note: earlier drafts described a "Pod-first" design where clients
> wrote messages directly into each other's Solid Pods. That is no longer how
> Proxion works. The Pod is now optional write-through for durability.

---

## 1. Stack

```ascii
   +---------------------------+        +---------------------------+
   |  Client A (web / Tauri)   |        |  Client B (web / Tauri)   |
   +---------------------------+        +---------------------------+
                | WebSocket (wss)                     | WebSocket (wss)
                v                                     v
   +---------------------------+   HTTP   +---------------------------+
   |   Gateway A (run_gateway) |<-------->|   Gateway B               |
   |   gateway.py + mixins     |  /relay  |   (one gateway per user)  |
   |   - WS command loop       | (sealed) |                           |
   |   - HTTP endpoints        |          |                           |
   |   - SQLite local_store    |          |                           |
   +---------------------------+          +---------------------------+
        |  (optional, async)                        |  (optional)
        v                                           v
   +---------------------------+          +---------------------------+
   |  Solid Pod A (durable     |          |  Solid Pod B              |
   |  write-through backup)     |          |                           |
   +---------------------------+          +---------------------------+
```

A user's **address** is `did:key:<ed25519>@https://<their-gateway>`. The
`did:key` is the gateway's own identity (`_proxion_address`). Sharing is done via
an **invite link** (`/invite?from=…`, short `/i/<token>`, or `proxion://invite`
deep link) that resolves to that address.

### Code map
- `web/`: vanilla-JS SPA (ES modules; `main.js` is the composition root, with
  feature modules: `connection.js`, `rendering.js`, `view.js`, `voice.js`,
  `e2e.js`, `pod.js`, …). Served by the gateway (HTTPS when run with
  `run_gateway.py`; plain loopback HTTP inside the desktop app).
- `proxion-messenger-core/src/proxion_messenger_core/`
  - `gateway.py`: `ProxionGateway`, the WS command `switch` + connection state,
    composed from mixins.
  - `_gateway_*.py` mixins: `_gateway_http.py` (all HTTP serving + endpoints),
    `_gateway_voice.py`, `_gateway_rooms.py`, `_gateway_dm.py`, `_gateway_pod.py`
    (PodSyncMixin), `_gateway_mailbox.py` (relay/mailbox), `_gateway_auth.py`,
    `_gateway_misc.py`.
  - `local_store.py` + `_store/`: SQLite persistence split by domain
    (`messages`, `rooms`, `federation`, `devices`, `security`, `identity`).
  - `relay.py`, `persist.py` (`AgentState`), `solid_client.py` (DPoP Pod I/O).
- `run_gateway.py`: process entry point (loads/generates keys + self-signed
  TLS, starts the gateway, attempts UPnP).
- `tauri-app/`: Rust/Tauri desktop shell bundling the gateway as a sidecar.

---

## 2. Identity

- **`did:key` (Ed25519)** is the root identity. Generated on first run; stable
  per install. The user never sees a WebID unless they opt into a Pod.
- **X25519** keys back the E2E ratchet; published so peers can start a session.
- **AgentState** (`persist.py`) holds the Ed25519 identity key + X25519 store
  key. Losing it means losing the identity; the recovery kit and encrypted
  backups exist for that.
- **RelationshipCertificate**: issued during a mutual opt-in *friend request*
  handshake; authorizes DM capability between two identities. Federation invites
  (`/invite` + `/invite/accept`) exchange these across gateways.
- **Multi-device (delegation, not key-copying)**: a secondary device generates
  its own Ed25519 keypair and receives a `DeviceCertificate` signed by the
  primary (`device_cert.py`); the account private key never leaves the primary.
  The gateway keeps a set of live sockets per identity, so several devices can be
  online at once, and inbound messages fan out to each with per-device encryption.

---

## 3. Transport & federation (relay-first)

1. **Client ↔ own gateway:** JSON commands over a WebSocket on port 7474
   (`wss://` from `run_gateway.py` with its self-signed cert, `ws://127.0.0.1`
   inside the desktop app). The client only ever talks to *its own* gateway.
2. **Gateway ↔ gateway:** cross-gateway delivery is an HTTP `POST /relay` to the
   peer gateway (resolved from the recipient's address). Message **content** is
   encrypted end to end, so no gateway on the path reads it in the clear. On the
   direct path the recipient's *own* gateway sees the routing fields
   (`from_webid`/`to_webid`) because it has to deliver to its user; those fields
   are Ed25519-signed and bound to the sending gateway so a peer cannot
   forge them. When delivery instead goes through a **managed middle relay**
   (§3.3), the sender identity is additionally sealed for the recipient gateway
   only (`sealed_relay.py`: X25519 + ChaCha20-Poly1305), so the middle node sees
   the destination but not who sent it. `_validate_relay_target` guards against
   SSRF / unsafe targets.
3. **Reachability (the "it just works" layer):**
   - TLS auto-cert on first run (HTTPS UI ⇒ `wss` WS; the meta `x-gateway-url`
     scheme is reconciled to match).
   - **UPnP** auto-port-mapping when `PROXION_PUBLIC_URL` is unset, so many home
     gateways are directly reachable.
   - **Connect a phone**: the app can open a Cloudflare tunnel to the local
     gateway (needs `cloudflared`); authentication is forced on while it is open.
   - **Sealed-relay fallback**, opt-in: when `PROXION_RELAY_FALLBACK_URL` names a
     gateway running with `PROXION_RELAY_NODE=1`, a relay that cannot be
     delivered directly is left in that node's mailbox for the recipient gateway
     to collect (both sides need the setting). It is sealed, so the node sees the
     destination gateway but not the sender or content. No relay is configured by
     default.
   - **TURN** credentials for WebRTC when symmetric NAT blocks direct media.
4. **Mailbox**: messages to an offline peer gateway are queued and delivered on
   reconnect (`_gateway_mailbox.py`).

---

## 4. Messaging

DMs, rooms, reactions, edits, threads, pins, disappearing messages, scheduling,
search, read receipts, presence, and typing are all implemented. The flow:

1. Client sends `{"cmd": "send_dm"/"send_room", …}` to its gateway.
2. For a DM the browser has already encrypted the text (below). The gateway
   persists the message as received to `local_store` and delivers:
   local recipients over their WS; remote recipients via `/relay` to their
   gateway (which delivers over *their* WS).
3. Optional: the sender's (and/or recipient's) gateway writes the message
   through to the Solid Pod (`PodSyncMixin`) for durable, cross-device history.

Rooms are gateway-hosted (membership in `room_members`, rehydrated from the
store on restart); history catch-up is available over a REST endpoint.

---

## 5. End-to-end encryption

The full protocol, with its limits, is specified in
[E2E_ENCRYPTION.md](E2E_ENCRYPTION.md). In short:

- **Double Ratchet** sessions per DM peer (and per peer device), run in the
  browser (`web/e2e.js`), bootstrapped from the recipient's published X25519 key.
- **Gateway-to-gateway sealing** of the relay envelope (`sealed_relay.py`) when
  the peer gateway's seal key is known, so HTTP intermediaries do not see the
  sender.
- **Ed25519 signatures** on gateway-free DM envelopes and on gateway relays.
- Identity verification: safety-number comparison, surfaced in the DM E2E badge
  and verify modal.

DM edits, reactions and file attachments to a keyed peer are sealed with the
same ratchet session (`web/e2e-secondary.js`): the gateway stores the edit text
and file bytes as ciphertext and a reaction as an opaque token. Rooms are not
end-to-end encrypted, by design, so other Solid apps can read them. Gateways see
routing and timing metadata.

---

## 6. Voice (WebRTC, mesh)

- 1:1 and small-group (2-6) calls use **WebRTC**; signaling
  (`voice_invite` / `voice_answer` / `ice_candidate`) rides the gateway/relay
  channel, with **no SFU** (out of scope by design; mesh is correct at this
  scale).
- ICE uses host/STUN candidates; **TURN** (ephemeral credentials) is the
  fallback under symmetric NAT.
- The browser media path (getUserMedia + RTCPeerConnection + ICE + media) is
  smoke-tested headlessly via `web/smoke_webrtc.mjs`.

---

## 7. Security properties (summary)

| Data                    | Protection                 | Visible to a transit/relay node |
| :---------------------- | :------------------------- | :------------------------------ |
| DM text, edits, reactions, files | E2E (Double Ratchet/AEAD; files AES-256-GCM) once a peer key is known | No (sizes, ids and timing only) |
| Room text, reactions, files | Transport only | Yes, to gateways handling them |
| Routing (who→who, when) | Sealed sender mitigates    | Limited routing/timing metadata |
| Identity key (Ed25519)  | Local AgentState           | No (never transmitted)          |
| Pod-stored history      | Owner-only Pod ACLs (open typed RDF, not encrypted at rest) | Only the user's own Pod host |

Hardening present elsewhere in the stack: SSRF/relay-target validation, trust
pinning + revocation, per-endpoint HTTP rate limits and body-size caps, audit
logging. The heavyweight assurance subsystem is opt-in (see
[CONFIGURATION.md](CONFIGURATION.md#hardening-off-by-default)).

---

## 8. Persistence & the optional Pod

- **Primary store:** SQLite via `local_store.py`, decomposed into `_store/`
  domain stores. Rooms, messages, relationships, devices, security events,
  read-state.
- **Optional Solid Pod backbone:** when connected (DPoP-authenticated CSS/ESS),
  the gateway write-through-mirrors history so the user can *never lose anything*
  and read from anywhere. This is positioned as an upgrade, **not a requirement**
. Proxion is fully functional with no Pod.
- **Open, documented storage format:** what lands on the pod is plain typed
  JSON-LD under the `px:` (`https://proxion.dev/vocab/v1#`) vocabulary, readable
  by any authorized Solid app. The end-to-end encryption is a transport property,
  not an at-rest lock-box. The full contract is
  [`docs/POD_DATA_MODEL.md`](POD_DATA_MODEL.md).

---

## 9. Distribution

Native executables via PyInstaller (`build_sidecar.py` → per-triple
`proxion-gateway`) + Tauri packaging. Auto-updates are signed with the updater
key ([RELEASE.md](RELEASE.md)); installers are not OS-code-signed. No
Docker dependency at runtime.

---

## 10. Solid SDK convergence contract

Pod I/O converges on the official Inrupt SDK. Canonical package set:

| Package | Role | Gate |
|---|---|---|
| `@inrupt/solid-client-authn-node` | Server-side OIDC + DPoP | Required |
| `@inrupt/solid-client-authn-browser` | Browser OIDC flows | Required |
| `@inrupt/solid-client` | Resource CRUD, container listing, ETag | Required |
| `@inrupt/solid-client-notifications` | WebSocketChannel2023 push | Required |
| `@inrupt/solid-client-access-grants` | Delegated access | Feature-gated (`PROXION_ENABLE_ACCESS_GRANTS=1`) |
| `@inrupt/vocab-solid`, `@inrupt/vocab-inrupt-core` | Vocabulary IRIs | Required |

**Deprecation blocklist** (fails the `check:solid-sdk` CI gate): `solid-auth-client`,
`solid-auth-fetcher`, and any unsupported `@inrupt/solid-client-authn-*` range.

---

## 11. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Client shows "connecting", never connects | https page + `ws://` meta (mixed content) | gateway must advertise `wss://` when TLS is on (`_ws_public_url`) |
| Rooms don't appear after a gateway restart | membership rebuilt only for live sockets | `get_rooms` falls back to persistent store membership by webid |
| Friends can't reach you | UPnP off / CGNAT | set `PROXION_PUBLIC_URL` behind a reverse proxy ([SELF_HOSTING.md](SELF_HOSTING.md)), use Connect a phone, or configure `PROXION_RELAY_FALLBACK_URL` |
| Voice connects but no audio | ICE failed (symmetric NAT) | ensure TURN credentials; check `iceServers` |
| 403 on Pod PUT/GET | Pod ACL not granted | re-run the relationship/room ACL setup |
| `DPoP token expired` | clock skew | sync system clock |

---

*Proxion: sovereign messaging for small trusted groups.*
