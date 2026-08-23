# Proxion Web Roadmap: a gateway-less browser build

Proxion runs today as a desktop app (a Tauri window over a bundled local
gateway) and as a self-hosted gateway. Both require running a process. A
recurring request from Solid users is the opposite: visit a URL, sign in with a
pod, and use the app with nothing to install.

This roadmap scopes that build: **Proxion Web**, a static site (hostable on
GitHub Pages next to the landing page) where the browser talks directly to the
user's pod and no server ever holds a key or sees plaintext.

Written after a full read of the realtime paths in `gateway.py`, `main.js`,
`e2e.js`, `pod.js`, `notify.js`, `voice.js`, and `callsec.js`. The findings
below are grounded in that code, not in the (historically stale) roadmap docs.
Treat the code as the source of truth and verify before assuming any item is
open.

---

## Why this is feasible: what already runs in the browser

The gateway's footprint overstates its role. A trace of every realtime path
shows most of the hard work is already client-side:

| Capability | Where it runs today | Gateway-free ready |
|---|---|---|
| Pod read/write (rooms, history, profiles, invites) | Browser (`pod.js`, `solidSession.fetch`, DPoP) | Yes, already |
| DM encryption (double ratchet, X25519) | Browser (`e2e.js`, WebCrypto, non-extractable key; ratchet state to pod) | Yes, already |
| Realtime pod push (Solid Notifications) | Browser (`notify.js`, WebSocketChannel2023 + StreamingHTTP fallback) | Yes, already |
| Browser OIDC login | Browser (`auth.js`, bundled authn lib) | Yes, already |
| DM delivery (ciphertext transport) | Gateway WS relay + cross-gateway federation | Rework to pod-drop |
| Prekey bundles (X3DH session init) | Gateway SQLite (`local_store`) | Publish to pod |
| Presence | Gateway broadcast | Rework or degrade |
| Call signaling (SDP / ICE) | Gateway WS relay | Rework to pod-drop |
| STUN / TURN for calls | External ICE servers (already) | STUN public; TURN needs a relay |

The load-bearing insight: for DMs the gateway is **pure ciphertext transport**.
The crypto, the storage, and a working realtime push mechanism are all already
in the browser. So "DM-push, presence, and calls without a gateway" is a matter
of re-pointing transport at the pod, not rebuilding the hard parts.

The privacy posture is not just preserved, it is stronger: a static host serves
only HTML and JS and never sees keys or messages, and the browser talks straight
to the user's own pod. This is the opposite of a shared public gateway, which
would reintroduce exactly the key-holding third party the project exists to
avoid. A shared public gateway is explicitly a non-goal.

---

## The backbone: one transport seam

`main.js` today is wired directly to `socket.send({cmd})` and a WebSocket
`onmessage` switch. Supporting both worlds without forking the client requires a
single seam:

```
Transport (interface)
├── GatewayTransport   (current WS gateway, desktop / self-host): full features
└── PodTransport (new): pod + Solid Notifications, gateway-free web build
```

Both expose the same logical operations: `sendDM`, `onIncomingDM`,
`publishPresence`, `subscribePresence`, `sendSignal`, `onSignal`, plus a
`supports(feature)` predicate for UI gating. `main.js` calls the interface; one
boot-time detector picks the implementation from a `PROXION_MODE` capability
flag (`'gateway'` vs `'web'`). This confines all gateway-free logic to
`PodTransport` and keeps the realtime UI code identical across both builds.

Phase 1 introduces the seam. Phases 2 to 4 each ride it, so the Phase 1
abstraction work is what de-risks everything after it.

---

## Phases

Detailed, actionable plans live in the per-round files. This section is the map.

### Phase 1: Static Pages build, pod-only features (`PLAN_ROUND_102.md`)
Visit a URL, sign in with a pod, use shared rooms and history. Zero install.
Introduces the transport seam, a Solid-OIDC client identifier document hosted on
the Pages origin, a web-specific service worker and CSP, and UI gating that
hides realtime-only affordances behind `transport.supports(...)`. Lights up:
OIDC login, shared rooms (Long Chat read/write), history, invites (LDN),
profiles, type index discovery. Shippable on its own.

### Phase 2: DM-push without a gateway (`PLAN_ROUND_103.md`)
Start and hold an E2E DM, delivered near-realtime, no gateway on either side.
Publish prekey bundles to a public-read pod resource for X3DH init; deliver the
ratchet-encrypted envelope (unchanged from `e2e.js`) to the recipient's pod DM
inbox; receive via a `notify.js` subscription to the local inbox. The pod stores
only ciphertext. Wire-compatible with gateway-relayed DMs so web and desktop
interoperate.

### Phase 3: Presence without a gateway (`PLAN_ROUND_104.md`)
Presence as a public-read pod heartbeat resource plus per-contact subscription.
Online / away / offline derived from heartbeat freshness. Honestly coarser than
the desktop's socket presence, with a disclosed "last seen" fallback rather than
a faked green dot.

### Phase 4: Calls without a gateway (`PLAN_ROUND_105.md`)
1:1 voice, video, and screen-share with WebRTC signaling routed over the
callee's pod inbox and delivered via `notify.js`. `callsec.js` already signs the
DTLS fingerprint and refuses a MitM, so the untrusted pod-signaling path is safe
by the same mechanism that protects against an untrusted gateway. STUN covers
many networks with no server; symmetric-NAT peers need a TURN relay, which never
sees plaintext media but is a server (the one honest asterisk on "zero server").

---

## Honest limitations of the finished web build

- Presence is heartbeat-based and coarser than the desktop's socket presence.
- Calls work over public STUN for many networks; restrictive NATs need a TURN
  relay (self-hosted or a shared TURN-only service, far less sensitive than a
  key-holding gateway).
- Delivery latency is pod-notification latency, not direct-socket latency.
- True push requires a pod that implements the Solid Notifications Protocol;
  otherwise the existing polling fallback applies.

None of these compromise the security model. The browser talks only to pods, and
no server holds a key or sees plaintext. State each limitation plainly in the
README and landing page (public-writing rules apply: no em dashes, no
disclaimers, lead with what a reader can check).

---

## Cross-cutting requirements

- **Interop:** the web build and gateway build must stay wire-compatible (same
  DM envelopes, same room format, same signaling shapes) so desktop, web, and
  other Solid apps interoperate. Each phase carries an explicit compat test.
- **Repo rule:** every new web module is added to the eslint lists and the
  `sw.js` SHELL, and the full `pytest` suite runs after any web-file move because
  Python tests assert on web file contents.
- **Gates per item:** `npm test` (vitest), `check:i18n`, `smoke:pseudo` and RTL
  for new UI strings, `smoke:a11y` and `smoke:keyboard` for new controls, plus
  the new gateway-less smokes each round defines. Commit per item.

## Sequencing

Ship Phase 1 alone first: it is the "just visit a URL and use rooms" experience
users asked for, it is low risk, and it stands on its own as a launch asset.
Then 2, 3, 4 in order on the shared `PodTransport` seam.
