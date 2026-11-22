# Planned rounds 141–145

Status: Rounds 141 and 145 complete; Round 144 reviewed (design-only); Rounds 142–143 in progress after the audited completion
of Rounds 136–140. Earlier rounds
remain closed; retained limitations become new bounded work, not additions to
closed rounds. This tracked file is authoritative; ignored root-level plan
files are local IDE copies.

Work from observed failure boundaries, preserve encryption and storage limits,
and leave a reviewable implementation/validation record for each round. A plan
may narrow after an audit, but exclusions and unsupported cases must stay
explicit. New crypto/control protocols need a concrete safety and compatibility
review before activation. Local commits are separate from pushing/deployment.

| Round | Focus | Depends on |
| --- | --- | --- |
| 141 | Bind bridge text sends to their originating pod account | 140 |
| 142 | Durable native gateway inline DM media | 140–141 |
| 143 | Resumable encrypted gateway chunk transfers | 142 |
| 144 | Safe retirement of blocked prepared sends | 139–143 |
| 145 | Device fanout acknowledgement after durable receive commit | 136, 139, 142–144 |


---

## Round 141: Bind bridge text sends to their originating pod account

Status: complete. See [Round 141 closeout](ROUND_141_CLOSEOUT.md).

### Problem and result

A gateway device keeps its local DID while the gateway’s connected pod WebID
changes. Media intents now record and enforce that actor, but older text intents
only record the local DID. A pending text envelope must never be signed/published
under a replacement pod account merely because it uses the same browser/device.

### Bounded work

- [x] Capture the bridge WebID when freezing new WebID text intents. Retain it
  across disconnect/reload without putting transient session epochs in persisted
  job scopes. Device-only gateway conversations retain their existing identity.
- [x] Check actor and bridge generation before preparation, dispatch, confirmation
  and recovery. Switching away and back cancels old work; reconnecting the same
  account resumes the original ciphertext.
- [x] Define conservative treatment of pre-141 prepared bridge jobs that lack
  actor evidence. Do not infer ownership from the active pod or silently rewrite
  an already transmitted envelope. Keep unsupported legacy recovery visible.
- [x] Make pending status distinguish original-account reauthentication from a
  changed recipient. Do not present account mismatch as delivery failure when
  acceptance was already saved.
- [x] Add deterministic account-switch and live bridge recovery coverage.

### Acceptance and validation

New intents for pod A pause while B is connected, including a rapid A→B→A
switch during preparation or drop. A restored job sends its original payload
only through A; local DID-only DMs still work. Legacy ambiguous jobs cannot
silently acquire B as their sender. Run outbox/main/bridge tests, lint/i18n and
a live desktop-to-browser exact-retry journey with credential replacement.

### Likely touchpoints

`web/main.js`, `web/dm-outbox.js`, `web/crossmode.js`, pending status and bridge smokes.

### Exclusions

No migration of local gateway identity keys, cross-device key distribution,
new cryptography or automatic reauthoring of old prepared messages.


---

## Round 142: Durable native gateway inline DM media

Status: in progress. Depends on 140–141.

Preparation checkpoint (2026-11-21): `native-media-drafts.js` now provides
transactional IndexedDB source/prepared/header stages, ten bounded inline drafts
per account/device scope, stable ratchet send-journal ids, and generation guards.
Secondary file preparation validates and freezes metadata before encryption;
reopening a prepared file reuses the exact file ciphertext and ratchet header.
This internal component is not connected to the live native send path yet.
Native keyed DM recordings now route through sealed attachments rather than the
room-oriented voice command; this does not yet make their sender drafts durable.

The gateway audit found that native inline sends replace client message ids,
echo before delivery acceptance, and save only a summary in message history.
Do not use that echo to release a durable draft. Next: stable ids and conflicting
retry rejection, durable gateway acceptance/replay, receiver/history recovery,
then attachment/voice routing and pending UI. Existing gateway socket delivery
is not evidence of a durable recipient commit (Round 145).

Validation at this checkpoint: 2,589 frontend checks verified; 64 optional
integration checks skipped. Two subprocess checks blocked by sandbox restrictions
passed when rerun with subprocess access. Focused preparation/secondary/chunk
coverage passes (57 checks), and ESLint/whitespace checks pass. No server schema
or cryptographic protocol changes; shell cache v305. Round 142 remains open.

Gateway custody checkpoint: schema 68 now has a bounded opt-in inline-media
queue with stable ids, exact-payload conflict checks, authenticated primary
receive commits, policy cleanup and signed cross-gateway custody proofs. At that
checkpoint the browser did not opt in and the gateway did not advertise it.
Validation: 4,766 backend checks and the final 39-check conformance subset pass.
See [the native custody contract](ROUND_142_NATIVE_CUSTODY.md). Browser
sender/receiver integration, pending controls and live fault journeys remain
open; this does not complete 142 or native chunk resume in 143.

Header/adapter checkpoint (2026-11-22): schema 69 separates signed gateway
custody, durable header consumption and full file commit. Atomic shared-ratchet
send holds preserve ordering without increasing MAX_SKIP. Tested sender/receiver
adapters retain original drafts, encrypted key recovery and exact receipts across
storage failures and reload; capability checks bind the original primary key.
At that checkpoint the adapters remained staged; live file/voice routing,
pending UI and browser fault journeys were still required. Details and validation are in the custody
contract linked above; the scope and completion status are unchanged.

Live integration checkpoint (2026-11-22): primary browsers now negotiate native
inline file/voice recovery and expose pending/header/history/key/policy states.
A real 32 KiB cross-gateway journey passes prepared-write interruption, keyboard
retry, failed receiver history, exact header release, lost full commit and both
gateway/browser restarts, retaining one exact file per primary. Frontend: 2,641
passed plus the final lifecycle checks; native/event-contract backend: 29 passed.
Recorded voice and the preserved device-fanout fault journeys also pass. Larger
inline HTTP bodies remain retained pending Round 143's chunk transport; neither
142 nor 143 is closed. See [the transfer contract](ROUND_143_TRANSFER_PROTOCOL.md).

### Problem and result

Native gateway inline files/voice use secondary sealed envelopes and live
send_file frames, rather than the pod-asset adapter closed in Round 140.
A reload can lose the prepared file/header or delivery evidence. Preserve the
existing supported inline route without copying file bytes into localStorage.

### Bounded work

- [ ] Audit native inline file, GIF and voice routes, sealed metadata, gateway
  message ids/deduplication and real delivery/echo semantics before editing.
- [ ] Persist a bounded account/device-scoped IndexedDB draft containing original
  or prepared encrypted bytes. Freeze logical id, destination and sealed header;
  keep only small references in outgoing metadata.
- [ ] Journal secondary file-header ratchet preparation with the existing atomic
  send journal. Retries must reuse the header/file ciphertext and never silently
  rotate recipients, keys or ids.
- [ ] Honor stable client ids and exact-payload deduplication at applicable
  gateway boundaries. Refuse conflicting retries and false confirmation.
- [ ] Keep the draft until supported acceptance/history completion is durable.
  Apply block/revocation, account/tab isolation, capacity and explicit reset rules.
- [ ] Expose honest pending/retry status and gate unsupported legacy recovery.

### Acceptance and validation

Reload after sealing, before/after durable preparation, during transmission
and after a lost acknowledgement. Each supported inline file/voice note arrives
once with exact bytes, its original envelope and stable message id. Quota failure
keeps the source and spends no uncommitted key. Test native gateway browser
journeys with files, voice, blocking and reconnect plus focused crypto/server
checks; distinguish gateway acceptance from durable recipient storage.

### Likely touchpoints

`web/e2e-secondary.js`, native attachment/voice send paths, IndexedDB drafts, gateway media/id handling and native media smokes.

### Exclusions

No increase to file/frame size limits, room-file redesign, new codecs or
chunk-transfer resume (Round 143). Do not silently reroute native files to pods.


---

## Round 143: Resumable encrypted gateway chunk transfers

Status: in progress. Depends on 142.

Audit checkpoint (2026-11-21): the legacy live chunk route now binds incoming
chunks/completion and peer acceptance/rejection/receipts to the original sender
or destination. Colliding offers cannot replace partial state; null-prototype
maps avoid special-id object-property behavior. Receiver chunk and total byte
limits are checked against the offer before acknowledging completion. Enabled
local file history (within its existing byte-retention cap) now commits before a completion receipt; a failed write keeps
the live assembly for retry, and account changes suppress stale receipts. New
adversarial and storage-failure tests cover these boundaries. This is integrity hardening, not
resumability: persistent chunk storage, negotiation and replay-safe header/history
commit remain open. No legacy socket/chunk acknowledgement is relabeled as a
durable file receipt.

Custody/history checkpoint (2026-11-22): schema 70 and a staged signed-part
protocol now preserve bounded manifests/parts across gateway restart, reject
conflicts and reserve complete capacity before admission. Real gateways transfer
2 MiB/25 MiB ciphertext with exact hashes and complete custody proofs. Local
history v2 adds atomic bounded Blob/reference storage and privacy/retention
checks. Frontend: 2,663 passed; focused backend protocol/fanout subset: 83 passed.
The complete backend run had one non-reproduced friend-accept timeout; final
integration must recheck it. Browser chunk opt-in/resume, oversized-inline
adaptation, progress/decline controls and live large-file fault journeys remain
open. See [the implementation contract](ROUND_143_TRANSFER_PROTOCOL.md); no
chunk browser recovery or 142–143 completion is claimed at this checkpoint.

Accepted-inline checkpoint (2026-11-22): schema 71 now preserves original
inline receipt identity during exact-byte chunk adaptation and reconciles signed
old peer completion. Component upload/download recovery and strict preflight
are tested. Browser activation, progress/decline UX and live chunk restart
journeys remain open; this adds no scope to the round.

Browser integration checkpoint (2026-11-22): primary chunk negotiation, upload/
download recovery, Blob history and labelled progress/retry are connected. Real
2 MiB and maximum-size journeys pass partial upload/download gateway/browser
restarts, history failure and lost final receipt with exact hashes. Recipient
decline and final acceptance evidence remain open; see the transfer contract.

Decline/migration checkpoint (2026-11-22): accepted 400 KiB inline migration and
keyboard recipient decline now pass real gateway/browser restart journeys.
Decline is a distinct terminal outcome; the sender retains its exact prepared
source. Full suites: 4,848 backend and 2,702 frontend passed. Final policy/route
acceptance audit remains open. No additional round scope is introduced.

### Problem and result

The existing file_offer/file_chunk/file_complete route buffers live transfer
state in memory. Navigation or a disconnect can discard either side’s progress,
and replaying a sealed header can spend a ratchet key twice. Add explicit,
bounded recovery to this separate transfer protocol.

### Bounded work

- [ ] Inventory sender/receiver states, timeout/decline behavior, authorization,
  chunk and file limits and current file_received semantics.
- [ ] Persist encrypted sender bytes, immutable transfer id/header and bounded
  receiver chunk progress in IndexedDB before relevant acknowledgements.
- [ ] Negotiate recovery capability and resume only authenticated peer/transfer
  identities. Define missing-chunk queries, deduplication/conflict rules and
  interrupted finalization without unbounded retransmission or buffering.
- [ ] Journal receive-header consumption and local history commit so complete/
  acknowledgement replay does not decrypt a spent header or lose the only file.
- [ ] Apply account/tab, block/revocation, cancellation, retention and capacity
  rules. Legacy peers keep explicitly documented active-transfer behavior.
- [ ] Provide accessible progress, reconnect/resume and failure controls.

### Acceptance and validation

Pause and restart both sender and receiver at offer, middle chunk, complete,
history commit and final acknowledgement. Restore exact bytes once without
re-sealing the header, leaking plaintext or accepting a foreign/conflicting
transfer. Test full stores, duplicate/out-of-order chunks, declined/revoked
peers, legacy negotiation and real cross-gateway reload/reconnect journeys.

### Likely touchpoints

`web/filetransfer.js`, secondary header recovery, draft/history stores, gateway file relay and transfer smoke helpers.

### Exclusions

No larger file caps, full history backfill, new storage service or generic
replacement of every gateway queue. Capability negotiation must preserve older
clients and cannot weaken sender authentication or encryption.


---

## Round 144: Safe retirement of blocked prepared sends

Status: reviewed, design-only outcome. See [Round 144 safety review](ROUND_144_SAFETY_REVIEW.md). No additional prepared cancellation or reset is enabled. Media implementation remains open in 142–143.

### Problem and result

Rounds 138–140 intentionally keep prepared sends when recipients or devices
change, because deleting spent-key intents can exceed the receiver’s skip bound.
Establish which retirement actions can be proved safe and make blocked queues
manageable without claiming recall or resetting trust implicitly.

### Bounded work

- [ ] Enumerate blocked prepared/partially accepted text and media states,
  including revoked devices, changed keys and legacy unknown sends.
- [ ] Write a concrete safety model for any proposed retirement: peer/chain
  identity, accepted message positions, replay/order rules, crash boundaries and
  compatibility. Keep MAX_SKIP and key non-reuse invariant.
- [ ] Implement only retirement that existing authenticated durable evidence
  proves safe. A new ratchet-reset/control protocol requires a separate reviewed
  design and explicit compatibility gate; do not improvise it inside a button.
- [ ] Retain unsafe cases with actionable original-account/device recovery or
  explicit destructive local-reset wording. Never relabel deletion as recall.
- [ ] Test subsequent decryptability, repeated retirement, partial target
  acceptance, quota recovery, stale account actions and media asset ownership.
- [ ] Document remaining blocked cases and any separately required protocol
  review rather than marking an unsafe action available.

### Acceptance and validation

For every enabled action, later messages decrypt within existing bounds,
keys are never reused, confirmed peer delivery stays confirmed and potentially
delivered assets are retained. Adversarial/replay/crash tests support the safety
argument. Cases without sufficient evidence remain gated with an honest UX.
A design-only result must explicitly distinguish itself from shipped reset or
prepared cancellation.

### Likely touchpoints

Outbox receipts and frozen target policy, crypto state, device lifecycle, pending controls and explicit reset UX.

### Exclusions

No message recall, larger skip buffers, unilateral trust reset, automatic
recipient retargeting or unreviewed cryptographic protocol deployment.


---

## Round 145: Device fanout acknowledgement after durable receive commit

Status: complete for encrypted device fanout. See [Round 145 closeout](ROUND_145_CLOSEOUT.md). This independently closes the fanout socket-write gap; it does not claim completion of media Rounds 142–143.

### Problem and result

Round 136 persists offline fanout but removes a queued row after a successful
socket write. A crash between that write and the recipient’s durable receive
journal can still lose accepted delivery. Close this specific gap using an
explicit client commit acknowledgement and bounded server replay.

### Bounded work

- [ ] Specify a versioned acknowledgement bound to authenticated recipient
  account/device and the exact sender/message/envelope identity; document the
  distinction between gateway acceptance, durable receive commit and reading.
- [ ] Retain durable fanout until a supported client confirms its receive journal/
  history boundary. Define bounded leases/retries and restart recovery; preserve
  opaque ciphertext and reject conflicting identities or foreign acks.
- [ ] Make client receipt/journal recovery send an idempotent acknowledgement
  after commit, including duplicate envelopes, history-disabled behavior and
  interrupted local cleanup. Never acknowledge before the recovery copy exists.
- [ ] Apply block, account deletion and device revocation to pending/leased rows;
  bound retained bytes/targets and fail admission rather than evict accepted work.
- [ ] Negotiate legacy behavior explicitly and report its weaker boundary rather
  than treating a socket write as a durable client receipt.
- [ ] Update schema/protocol metadata, migrations, tests and real multi-device
  crash/restart journeys around write-before-commit and commit-before-ack.

### Acceptance and validation

Kill the gateway and recipient on both sides of the receive commit, lose and
replay acknowledgements, and reload the sender. One readable message survives
with original ciphertext, no foreign device can drain/ack it, and full or failed
storage creates no false acceptance. Run backend migrations/authorization/
capacity checks and live paired-device cross-gateway fault journeys.

### Likely touchpoints

Durable device fanout store/drain, authenticated gateway protocol, client receive receipts/journals and restart smokes.

### Exclusions

No read receipts, guarantee for permanently lost/revoked devices, plaintext
server archive or blanket redesign of room/ordinary-DM delivery protocols.
