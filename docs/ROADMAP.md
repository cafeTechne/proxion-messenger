# Proxion Messenger — Master Roadmap

Strategic north star for architecture and development. Written June 2026
after a full codebase review. This sits above the thematic roadmaps:
- `docs/E2E_ENCRYPTION_ROADMAP.md` — crypto evolution
- `docs/security/post-baseline-roadmap.md` — security-governance
- `docs/ARCHITECTURE.md` — protocol reference (Pod-centric; see Phase B.4)

---

## The product thesis (what we are actually building)

> A sovereign Discord/Signal for small trusted groups (2–6 people). Download
> a `.exe`, run it, share your Proxion address, and message + voice-call your
> friends — with no company in the middle, no account, and no central server.
> Self-hosting is a backend detail the user should never have to think about.

Every roadmap decision is judged against that thesis. The two implications
that matter most:

1. **The target is small friend groups, not enterprises or large communities.**
   This means mesh voice is correct (no SFU), and it means the enterprise-grade
   security-governance subsystem is over-built relative to user value.
2. **The user is a non-technical "noobie."** This means reachability,
   onboarding, updates, and error recovery must be automatic and invisible.
   This is the single highest-leverage area and is currently the weakest.

---

## Current state — honest assessment

**Strengths (genuinely strong):**
- Mature messaging: rooms, DMs, reactions, edits, threads, pins, disappearing
  messages, scheduling, search, receipts, presence, typing — all working.
- Real E2E: Double Ratchet, sealed sender, group sender keys, device registry.
- Federation that actually works cross-gateway: messages, reactions, edits,
  presence, typing, 1:1 voice, group voice (2–6), push.
- Reachability automation: TLS auto-cert, UPnP auto-map, TURN credentials,
  connectivity guidance.
- 3,230 passing tests, 3-OS CI, sidecar build pipeline, Tauri packaging.

**Weaknesses (where the thesis is not yet met):**
- **Distribution is not "download-and-go".** No signed installers, no
  auto-update, onboarding still surfaces gateway/`.env` concepts to the user.
- **Reachability is automatic only when UPnP works.** The fallback (manual
  port-forward / Cloudflare Tunnel) is a wall for a non-technical user.
- **Three monoliths**: `gateway.py` (4.7K), `local_store.py` (5.9K),
  `main.js` (5.9K) — increasingly hard to evolve safely.
- **Over-built governance subsystem** with dead modules — large maintenance
  surface for near-zero user value at this scale.
- **No mobile story.** PWA exists (`sw.js`) but no packaged mobile app.
- **Hard 512 KB file limit** — below user expectations for photo sharing.
- **Frontend has no test coverage** (5.9K LOC `main.js`, only a vitest stub).
- **Architecture docs have drifted** from Pod-first to relay-first reality.

---

## Phase A — "Download and it just works" (HIGHEST PRIORITY)

The thesis lives or dies here. Goal: a non-technical person installs Proxion
and reaches their friends with zero configuration.

**A1. Signed, auto-updating installers.**
- Code-sign Windows (Authenticode) and macOS (notarized) builds — unsigned
  binaries trigger scary OS warnings that kill noobie adoption.
- Tauri auto-updater wired to GitHub Releases so users get fixes silently.
- Linux: AppImage + optionally Flatpak.

**A2. Reachability that survives UPnP failure — without user effort.**
- Built-in managed relay/TURN fallback: when a gateway can't be reached
  directly, route through a community/relay node (sealed, so the relay never
  sees plaintext or metadata beyond routing). This is the missing piece that
  makes "it just works" true for the ~40% of routers where UPnP is off.
- Optional one-click Cloudflare Tunnel / Tailscale Funnel integration that
  the app drives (today it only prints a command).
- Decision needed: run a small Anthropic-of-Proxion-style default relay, or
  ship a "bring your own relay" model. Lean toward an optional default relay
  with sealed-sender so privacy is preserved.

**A3. Onboarding that never says "gateway" or ".env".**
- Replace remaining technical language with outcomes ("Your friends can reach
  you ✓" / "Setting up your private connection…").
- First-run: generate identity, attempt UPnP, show address + a single shareable
  link, done. No mention of ports unless something fails.

**A4. Invite-link-first contact flow.**
- A clickable `https://…/invite/#…` link that, when opened, walks the
  recipient through install + auto-adds the inviter. The current Proxion
  address is correct but not noobie-shareable.

---

## Phase B — Architecture hygiene (enables everything after)

Pay down the debt that will otherwise make Phases C–E slow and risky.

**B1. Decompose `local_store.py` (5.9K).** Split into domain stores
(`store/messages.py`, `store/rooms.py`, `store/federation.py`,
`store/devices.py`, `store/security.py`) behind a thin `LocalStore` facade.
The ~80 tables already cluster cleanly by domain. Migrations stay centralized.

**B2. Decompose `gateway.py` (4.7K).** The mixin split helped; finish it by
moving the HTTP endpoint handlers out of the WS command loop into an
`http_endpoints.py` mixin, and extract the relay-dispatch switch into a
`relay_router.py`. Make the mixin `self` contract explicit with a `Protocol`.

**B3. Modularize `main.js` (5.9K).** Move to ES modules (`ws.js`, `voice.js`,
`rooms.js`, `dms.js`, `ui/*.js`) with a tiny build step (esbuild). Add a
real vitest suite — the frontend is currently the least-tested, most-changed
surface. No framework needed; keep it vanilla.

**B4. Right-size the governance subsystem.** Audit
`continuous_assurance`, `security_exit_gates`, `integrity_consensus`,
`policy_state_machine`, `federation_attest`, `solid_oidc_conformance`,
`supply_chain`, `recovery_drill_runner`, `incident_sim`. Delete dead modules
(zero importers), and demote the rest behind an opt-in `PROXION_ASSURANCE=1`
flag so the default build is lean. Preserve the genuinely useful pieces
(SSRF guards, relay validation, trust pinning, audit log).

**B5. Reconcile architecture docs.** `docs/ARCHITECTURE.md` describes the
Pod-first model; the system is now relay-first with optional Pod backing.
Rewrite it to match reality (or supersede with the as-built summary). Move
the root `ARCH.md` (homelab infra) out of this repo entirely.

**B6. Finish the `proxion-messenger-core` → `proxion-messenger-core` rename.**

---

## Phase C — Federation completeness & robustness

Close the cross-gateway gaps so federation is trustworthy end-to-end.

**C1. Ban/mute federation.** Today moderation is local-only; a banned user on
another gateway can still reach the room. Relay ban/mute state to federated
member gateways and enforce on inbound relay.

**C2. Large file transfer (chunked).** Lift the 512 KB limit with a chunked
upload/reassembly protocol (`file_transfer.py` already has `TIER1_MAX_BYTES`
scaffolding). Target 50 MB for photos/clips; chunk over relay, reassemble,
E2E per chunk.

**C3. Room history catch-up & pagination polish.** The REST `/room-history`
endpoint (R34) is the foundation; add cursor pagination in the UI and a
"load older" affordance for federated members.

**C4. did:web identity (optional).** Lets users have a human-readable identity
(`did:web:alice.example.com`) alongside `did:key`. Lower priority — `did:key`
works for the core thesis.

**C5. Federation resilience.** Quarantine misbehaving peer gateways, surface
relay health in the UI, exponential backoff with jitter on relay retries
(partly present), and a "your friend's gateway is offline" UX.

---

## Phase D — Feature parity for the social experience

Make it feel as good as the apps people are leaving.

**D1. Voice quality & UX.** Speaking/level indicators (Web Audio
`AnalyserNode`), per-call quality stats (`getStats`: jitter, RTT, packet
loss) surfaced in the participant panel, push-to-talk, noise suppression
(browser-native `noiseSuppression` constraints).

**D2. Mobile.** Package the PWA properly (installable, push via existing
VAPID bridge) and/or a Tauri-mobile / Capacitor wrapper. The backend is
already mobile-ready (relay + push); the gap is packaging and a
mobile-first layout pass on `main.js`.

**D3. Rich media.** Inline link previews (module exists), image galleries,
voice messages (exists) polish, animated reactions.

**D4. Notifications that work when the app is closed.** The WebPush/VAPID
bridge exists; wire it to a background service so users get messages without
the gateway window open — critical for a real messenger.

---

## Phase E — Trust, recovery & long-term durability

The sovereignty promise requires users never lose their identity or history.

**E1. Identity backup & recovery UX.** Backup exists; make it
foolproof — recovery codes, "export to file", optional encrypted cloud
backup the user controls. Losing the Ed25519 key today = losing identity.

**E2. Multi-device made real.** Device registry exists; finish the UX so a
user can add a second device by scanning a QR from the first, with sessions
and history syncing.

**E3. Solid Pod as optional durable backbone.** The Pod write-through exists
but is secondary. Position it clearly as the "never lose anything + access
from anywhere" upgrade, not a requirement. Make connecting a pod a one-click
optional step.

**E4. Verifiable builds.** Reproducible builds + published hashes so a
security-conscious user can verify the `.exe` matches the source — fits the
sovereignty ethos and leverages the existing `supply_chain` work.

---

## Sequencing & rationale

```
A (download-and-go)  ──►  the thesis; do first, unblocks real users
        │
B (hygiene)          ──►  in parallel/interleaved; makes C–E safe & fast
        │
C (federation)       ──►  trust & completeness once more users exist
        │
D (parity)           ──►  retention; compete on experience
        │
E (durability)       ──►  deliver the full sovereignty promise
```

- **A and B interleave.** Ship A1–A3 for adoption while doing B1–B3 to keep
  velocity. Don't let either fully block the other.
- **Phase A is the highest ROI** — the product is technically excellent but
  undeliverable to its target user until install + reachability are invisible.
- **Phase B4 (governance right-sizing) is the highest *effort-saving* move** —
  it removes maintenance drag from every future round.

## What to explicitly NOT build

- SFU / large-room voice (40-person raids belong on TeamSpeak — confirmed).
- Screen share (out of scope per product direction).
- A central account system or directory (violates the thesis).
- Crypto/token/blockchain anything.

## Suggested next rounds

| Round | Theme | Phase |
|-------|-------|-------|
| R35 | Governance right-sizing: delete dead modules, gate assurance behind a flag | B4 |
| R36 | `local_store.py` decomposition behind a facade | B1 |
| R37 | Signed installers + Tauri auto-update | A1 |
| R38 | Sealed managed-relay fallback for non-UPnP networks | A2 |
| R39 | Chunked large-file transfer | C2 |
| R40 | `main.js` modularization + vitest harness | B3 |
