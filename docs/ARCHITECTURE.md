# Proxion Architecture — Technical Reference

Proxion is a **sovereign, federated messenger for small trusted groups (2–6
people)**. Each user runs their own **gateway** (shipped as a `.exe`/desktop
app); there is no central server and no account. The product thesis: *download
it, share a link, message and voice-call your friends — self-hosting is a backend
detail the user never has to think about.*

This document describes the **as-built** system. It is **relay-first**: messages
flow client → your gateway → (directly or via a sealed relay) → your friend's
gateway. A Solid Pod is an **optional durable backbone**, not the transport.

> Historical note: earlier drafts described a "Pod-first" design where clients
> wrote messages directly into each other's Solid Pods. That is no longer how
> Proxion works — the Pod is now optional write-through for durability.

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

A user's **address** is `did:key:<ed25519>@https://<their-gateway>` — the
`did:key` is the gateway's own identity (`_proxion_address`). Sharing is done via
an **invite link** (`/invite?from=…`, short `/i/<token>`, or `proxion://invite`
deep link) that resolves to that address.

### Code map
- `web/` — vanilla-JS SPA (ES modules; `main.js` is the composition root, with
  feature modules: `connection.js`, `rendering.js`, `view.js`, `voice.js`,
  `e2e.js`, `pod.js`, …). Served by the gateway over HTTPS.
- `proxion-messenger-core/src/proxion_messenger_core/`
  - `gateway.py` — `ProxionGateway`: the WS command `switch` + connection state;
    composed from mixins.
  - `_gateway_*.py` mixins — `_gateway_http.py` (all HTTP serving + endpoints),
    `_gateway_voice.py`, `_gateway_rooms.py`, `_gateway_dm.py`, `_gateway_pod.py`
    (PodSyncMixin), `_gateway_mailbox.py` (relay/mailbox), `_gateway_auth.py`,
    `_gateway_misc.py`.
  - `local_store.py` + `_store/` — SQLite persistence split by domain
    (`messages`, `rooms`, `federation`, `devices`, `security`, `identity`).
  - `relay.py`, `persist.py` (`AgentState`), `solid_client.py` (DPoP Pod I/O).
- `run_gateway.py` — process entry point (loads/generates keys + self-signed
  TLS, starts the gateway, attempts UPnP).
- `tauri-app/` — Rust/Tauri desktop shell bundling the gateway as a sidecar.

---

## 2. Identity

- **`did:key` (Ed25519)** is the root identity. Generated on first run; stable
  per install. The user never sees a WebID unless they opt into a Pod.
- **X25519** keys back the E2E ratchet; published so peers can start a session.
- **AgentState** (`persist.py`) holds the Ed25519 identity key + X25519 store
  key. Losing it = losing identity (see Phase E recovery work).
- **RelationshipCertificate** — issued during a mutual opt-in *friend request*
  handshake; authorizes DM capability between two identities. Federation invites
  (`/invite` + `/invite/accept`) exchange these across gateways.
- **Device registry** — multiple devices can register under one identity
  (multi-device UX is still maturing).

---

## 3. Transport & federation (relay-first)

1. **Client ↔ own gateway:** JSON commands over a WebSocket (`wss://…:7474` by
   default). The client only ever talks to *its own* gateway.
2. **Gateway ↔ gateway:** cross-gateway delivery is an HTTP `POST /relay` to the
   peer gateway (resolved from the recipient's address). Payloads are
   **sealed-sender** — the relay/transit gateway learns routing only, never
   plaintext or full metadata. `_validate_relay_target` guards against SSRF /
   unsafe targets.
3. **Reachability (the "it just works" layer):**
   - TLS auto-cert on first run (HTTPS UI ⇒ `wss` WS; the meta `x-gateway-url`
     scheme is reconciled to match).
   - **UPnP** auto-port-mapping (R33) so most home gateways are directly
     reachable.
   - **Managed sealed-relay fallback** (R38) for the ~40% of networks where UPnP
     can't help — routes through a community relay node without exposing
     plaintext/metadata.
   - **TURN** credentials for WebRTC when symmetric NAT blocks direct media.
4. **Mailbox** — messages to an offline peer gateway are queued and delivered on
   reconnect (`_gateway_mailbox.py`).

---

## 4. Messaging

DMs, rooms, reactions, edits, threads, pins, disappearing messages, scheduling,
search, read receipts, presence, and typing are all implemented. The flow:

1. Client sends `{"cmd": "send_dm"/"chat_room_send", …}` to its gateway.
2. Gateway E2E-encrypts (below), persists to `local_store`, and delivers:
   local recipients over their WS; remote recipients via `/relay` to their
   gateway (which delivers over *their* WS).
3. Optional: the sender's (and/or recipient's) gateway writes the message
   through to the Solid Pod (`PodSyncMixin`) for durable, cross-device history.

Rooms are gateway-hosted (membership in `room_members`, rehydrated from the
store on restart); history catch-up is available over a REST endpoint.

---

## 5. End-to-end encryption

- **Double Ratchet** sessions per DM (forward secrecy + post-compromise
  recovery), bootstrapped from the peers' X25519 keys.
- **Sealed sender** on the relay path so transit gateways don't see who is
  talking to whom in the clear.
- **Group sender keys** for room messages (efficient fan-out with E2E).
- **Ed25519 signatures** for message integrity/authenticity.
- Identity verification: safety-number / fingerprint comparison
  (`/fingerprint/<did>`), surfaced in the DM E2E badge + verify modal.

What a transit/relay node can still infer is routing/timing metadata (the
classic metadata problem); content, reactions, and edits are encrypted.

---

## 6. Voice (WebRTC, mesh)

- 1:1 and small-group (2–6) calls use **WebRTC**; signaling
  (`voice_invite` / `voice_answer` / `ice_candidate`) rides the gateway/relay
  channel — there is **no SFU** (out of scope by design; mesh is correct at this
  scale).
- ICE uses host/STUN candidates; **TURN** (ephemeral credentials) is the
  fallback under symmetric NAT.
- The browser media path (getUserMedia + RTCPeerConnection + ICE + media) is
  smoke-tested headlessly via `web/smoke_webrtc.mjs`.

---

## 7. Security properties (summary)

| Data                    | Protection                 | Visible to a transit/relay node |
| :---------------------- | :------------------------- | :------------------------------ |
| Message / reaction text | E2E (Double Ratchet/AEAD)  | No                              |
| Routing (who→who, when) | Sealed sender mitigates    | Limited routing/timing metadata |
| Identity key (Ed25519)  | Local AgentState           | No (never transmitted)          |
| Pod-stored history      | E2E at rest + Pod ACLs     | Only the user's own Pod host    |

Hardening present elsewhere in the stack: SSRF/relay-target validation, trust
pinning + revocation, per-endpoint HTTP rate limits and body-size caps, audit
logging. The heavyweight assurance subsystem is opt-in (R35).

---

## 8. Persistence & the optional Pod

- **Primary store:** SQLite via `local_store.py`, decomposed into `_store/`
  domain stores. Rooms, messages, relationships, devices, security events,
  read-state.
- **Optional Solid Pod backbone:** when connected (DPoP-authenticated CSS/ESS),
  the gateway write-through-mirrors history so the user can *never lose anything*
  and read from anywhere. This is positioned as an upgrade, **not a requirement**
  — Proxion is fully functional with no Pod.
- **Open, documented storage format:** what lands on the pod is plain typed
  JSON-LD under the `px:` (`https://proxion.dev/vocab/v1#`) vocabulary, readable
  by any authorized Solid app — the end-to-end encryption is a transport property,
  not an at-rest lock-box. The full contract is
  [`docs/POD_DATA_MODEL.md`](POD_DATA_MODEL.md).

---

## 9. Distribution

Native executables via PyInstaller (`build_sidecar.py` → per-triple
`proxion-gateway`) + Tauri packaging. Signed installers + auto-update (R37). No
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
| Friends can't reach you | UPnP off / CGNAT | sealed managed-relay fallback (R38); or set `PROXION_PUBLIC_URL` |
| Voice connects but no audio | ICE failed (symmetric NAT) | ensure TURN credentials; check `iceServers` |
| 403 on Pod PUT/GET | Pod ACL not granted | re-run the relationship/room ACL setup |
| `DPoP token expired` | clock skew | sync system clock |

---

*Proxion: sovereign messaging for small trusted groups.*
