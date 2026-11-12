# Round 130 completion and capability record

Status: **closed locally, 2026-11-12**. Final verification passed.
This record supersedes historical “remaining” lists in Round 130's continuation
notes and the older web-build proposals. It describes supported behavior and
explicitly gated features; it does not declare the entire project feature-complete
or assert that this checkout has been deployed.

## Scope and outcome

The original approval and reload defects are repaired: an owner decision must
complete the required membership writes before success; incomplete work remains
retryable. Owned/joined rooms restore from durable records after reload, reconnect,
and gateway restart. Legacy records with discarded RDF fields can require reopening
an invite; the application does not invent missing room identifiers.

The round also repairs exposed partial implementations within its original WS-B–E
scope: room lifecycle reconciliation, conversation capability checks, signed pod-room
message operations, encrypted media, browser contacts/friendship/DM entry points,
device handoff and revocation, and notification recovery. Unsupported protocols stay
gated. New protocol parity is not silently advertised as working.

## Workstream closure

| Workstream | Closure |
| --- | --- |
| WS-A: approval and persistence | Completed; durable membership and invite/reload journeys verified |
| WS-B: room lifecycle | Supported operations verified; unsupported administration gated in the lifecycle matrix |
| WS-C: exposed actions | Supported handlers and capability refusals verified |
| WS-D: messaging | Implemented pod-native operations verified; remaining protocol differences explicitly gated |
| WS-E: contacts, devices, calls, notifications | Audits and reproduced fixes completed within the documented support boundaries |
| WS-F: representative verification/docs | Full suites and all 40 required browser runs passed; roadmaps reconciled |

## Conversation capability matrix

“Yes” means an implemented path with permission checks and relevant regression
coverage. It is not a promise about every Solid server. “Gated” means the action is
hidden/refused for that conversation. Operations remain subject to ownership and roles.

| Capability | Gateway room | Gateway DM | Browser pod room | Desktop pod room | Browser / cross-mode WebID DM |
| --- | --- | --- | --- | --- | --- |
| Send/read/history/reload | Yes | Yes | Yes | Yes | Yes; local plaintext history |
| Signed edits and deletion | Yes | Yes | Yes | Yes | Gated |
| Reactions and pins | Yes | Yes | Yes | Yes | Gated |
| Files and voice notes | Yes | Yes | Yes | Yes | Yes; encrypted pod assets |
| Private media forwarding | Gateway path | Gateway path | No public forwarding advertised | No public forwarding advertised | Yes; fresh asset ID/key |
| Polls | Yes | Gateway conversation path | Yes | Yes | Gated |
| Scheduling | Yes | Gateway conversation path | Gated | Gated | Gated |
| Disappearing messages | Gateway path | Gateway path | Gated | Gated | Gated |
| Group voice | Yes | Not applicable | Gated | Gated | Not applicable |
| One-to-one voice/video | Not applicable | Yes | Not applicable | Not applicable | Yes; signed peer connection |
| Search | Gateway history | Gateway history | Loaded accessible snapshots | Loaded accessible snapshots | This device's saved plaintext |

Conversation gating is implemented in `web/transport.js`, guarded again in the
handlers, and covered by `transport.test.js`, `podtransport.test.js`, and the live
room/media/call journeys. A browser-only DM and a desktop WebID DM use the pod
protocol; they do not inherit ordinary gateway-DM capabilities.

## Room lifecycle matrix

| Operation | Gateway rooms | Browser and desktop pod rooms |
| --- | --- | --- |
| Create, invite, approve, reopen after reload | Implemented | Implemented; durable request/approval state |
| Ordinary member leave/rejoin | Implemented | Implemented; membership and durable index reconciliation |
| Owner remove participant | Implemented | Implemented; idle access monitoring and cleanup retry |
| Ban/unban | Implemented | Gated |
| Ownership transfer | Implemented | Gated; owner closes instead of abandoning ownership |
| Invite renewal/expiry controls | Gateway invite protocol | Gated; no equivalent token-renewal protocol advertised |
| Delete/close | Implemented | Implemented; deny departed participants, retain owner history |

Gateway lifecycle is exercised by `smoke_rooms.mjs`; pod ownership and membership
are exercised by `smoke_web_rooms.mjs`, `smoke_cross_mode_rooms.mjs`,
`smoke_web_join.mjs`, and `smoke_web_join_link.mjs`. Failure, account-switch,
conditional-write, unauthorized actor, and restart cases have focused regressions.

## Devices, recovery, and notifications

- Browser linked-device pairing and fanout are not supported; the controls are gated.
  Gateway devices use account-signed delegation. A WebID inbox has one receiving
  signer; another device's publication takes over. The old receiver loses inbox
  reads/deletes/watches. An explicit reconnect can reclaim that role.
- Revocation atomically removes the registry entry and fanout key and persists the
  delegation revocation. Self-revocation closes that session too. A failed close
  cannot authorize more commands, and restart cannot revive the delegation.
- Gateway backup, verify, restore, and account-scoped export have a real isolated
  browser journey. Browser account switching, session recovery, DM history and media
  are exercised by the signed-in pod account/session journeys. Gateway recovery kits
  are not advertised as a Solid-provider account recovery mechanism.
- Successful live calls, forged-signer rejection, media transfer/reload, microphone
  denial, and recoverable recorder failures have browser and focused regression checks.
  TURN and actual hardware permissions remain deployment/device dependent.
- Closed-app notifications are missed-call/message alerts, not live ringing. Push
  renewal, opt-out races, provider expiry, receiving-device changes, and SQLite scan
  recovery have behavioral regressions. Provider status tests simulate responses;
  this audit does not claim delivery through a real third-party push provider.

See [INTEROP.md](INTEROP.md) and [POD_DATA_MODEL.md](POD_DATA_MODEL.md) for exact
notification baseline, storage, and dispatch semantics.

## Retained limits

These are explicit support boundaries, not newly added Round 130 work:

- Pod scheduling, disappearing messages, bans/ownership transfer, browser device
  pairing, and WebID-DM edits/reactions/pins are gated as shown above.
- Solid membership spans multiple resources. Conditional writes and reconciliation
  repair partial work; they are not a multi-resource transaction. Strong ETags are
  required. CSS 7.3.0 WAC and ACP are tested; Inrupt ESS/other providers are not inferred.
- Gateway scan checkpoints and expiry tombstones require the original SQLite store.
  Initial offline baselines suppress old items. Push dispatch is best effort; a crash
  around dispatch may lose or repeat an alert.
- Ratchet advancement, plaintext-history persistence, and durable receive receipts
  do not form one atomic transaction. Supported retries and external inbox deletion
  are covered; a fully transactional receive redesign is not claimed here.
- DM asset cleanup is limited to proven unused drafts with conditional deletion.
  Possibly delivered assets, legacy files, and room assets remain retained and
  inspectable. The round does not implement speculative destructive cleanup.
- Legacy irrecoverable room records require a surviving cache/device or invite.
  Recovery/export preserves supported account data; it does not reconstruct lost
  provider credentials or unavailable historical encryption state.

## Final fixes found by the closeout audit

- Device revocation is transactional. Storage failures preserve the linked device
  for retry; self-revocation and a lost acknowledgement still cut its session.
  A failed socket close cannot leave it in outbound delivery routes.
- Restoring a cached failed DM registers a real resend handler even when its bubble
  was rendered first. Shared-origin Solid accounts have separate health scopes;
  peer failures no longer cause a resend loop and ratchet skip-buffer overflow.
- A failed canonical signer read pauses inbox access instead of declaring a
  takeover. Pending watches recover when the signer is readable again. A confirmed
  replacement signer still removes the old receiver's access.
- Intermittent HTTP transport failures surfaced as `RemoteProtocolError`. Pod
  reads and strongly conditional PUTs retry once with fresh authentication headers.
  POST/PATCH operations are not blindly replayed; write conflicts remain explicit.
- Keyboard and WebRTC probes now handle initial focus/current ICE state correctly.
  Inventory polling uses timers rather than background-tab animation frames.
  Their original functional assertions remain intact.

## Final verification

Environment: Python 3.12, Node 22.16.0, Chromium, CSS 7.3.0. Browser smokes use
throwaway accounts and gateways. `PROXION_SMOKE_REQUIRED=1` makes missing prerequisites
and skipped required journeys fail the run.

| Check | Result |
| --- | --- |
| `python -m pytest tests/ -q --tb=short -m 'not integration'` from the core | 4,715 passed; 14 deselected |
| `npm test` | 2,431 passed; 64 gated/skipped tests reported separately |
| `PROXION_LIVE_POD=1 npm run test:integration` | 16 passed; no skips |
| `npm run smoke:ci` | 20 passed; no skips or failures |
| Required `pod` group | 15 passed; no skips or failures |
| Required `pod-acp` group | 5 passed; no skips or failures |
| Web lint, Ruff, cold-path undefined-name gate, i18n, whitespace | Passed |

The default frontend skips are not counted as proof of live behavior. The live
integration and browser groups are separate checks. The i18n check retains existing
warnings for incomplete draft translations; the Python suite reports existing mock/
shutdown warnings. Neither is represented as a warning-free run.

Initial live runs exposed the failed-DM resend loop, transient signer handling, and
intermittent HTTP protocol errors repaired above. Probe failures in initial keyboard,
WebRTC, and inventory runs were investigated; the repaired probes retain their
original functional assertions. Earlier failed runs are not counted as passes.

Reproduce from the repository's configured Python/Node environment, with
`PROXION_CHROME` pointing to Chromium and `PROXION_CSS_EXECUTABLE` to CSS 7.3.0:

```sh
# web/
NODE_OPTIONS=--no-experimental-global-navigator npm test
npm run lint
npm run check:i18n
PROXION_LIVE_POD=1 npm run test:integration
PROXION_SMOKE_REQUIRED=1 npm run smoke:ci
PROXION_SMOKE_REQUIRED=1 node scripts/run-smokes.mjs pod
PROXION_SMOKE_REQUIRED=1 node scripts/run-smokes.mjs pod-acp
```

The static build and service-worker regressions are covered by the frontend suite;
browser build/deployment behavior is exercised by the required smokes. Round 130 is closed against the supported capabilities and explicit gates above.
Publishing this checkout remains a separate action.
