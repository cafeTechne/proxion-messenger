# Planned rounds 136–140

Status: Rounds 136–137 complete; Rounds 138–140 remain planned.
Rounds 130–135 remain closed; the limitations recorded in their closeouts are inputs to new work,
not additions to those completed rounds.

This tracked document is the shared source for the five plans. Root-level
`PLAN_ROUND_136.md` through `PLAN_ROUND_140.md` are local IDE copies, consistent
with the repository's existing ignored planning-file convention.

| Round | Focus | Depends on |
| --- | --- | --- |
| 136 | Gateway offline-device queue restart durability | 135 |
| 137 | Complete E2E account/session lifecycle isolation | 136 |
| 138 | Pending-send visibility and safe controls | 137 |
| 139 | Independent, reliable sender self-sync | 136–138 |
| 140 | Durable encrypted DM media delivery | 136–139 |

## Rules for these rounds

- Start with the listed failure and define the acceptance boundary before editing.
  Audit findings may narrow a round; do not absorb unrelated feature work.
- Add failure-injection and real journey coverage for the changed boundaries.
  Distinguish unit checks, isolated live browser checks, optional integrations
  and unverified external services when reporting results.
- Preserve encryption/privacy guarantees, bounded storage/work and supported
  transport differences. Keep unsupported controls gated.
- Each closeout records the final scope, tests, remaining limits and migration
  behavior. A later round may need a brief adjustment if an earlier result changes
  its assumptions; any new protocol or material scope change gets a separate plan.
- Validate with isolated services and leave a reviewable commit and closeout
  for each completed round.

## Round 136: durable gateway offline-device queues

Status: complete. See [Round 136 closeout](ROUND_136_CLOSEOUT.md). Rounds 130–135 remain closed.

### Problem and result

The gateway can acknowledge an offline-device fanout after putting it in
`_fanout_queue`, which is currently process-local. A gateway restart can lose
that accepted copy after the sender has cleared its local outbox. Persist this
queue before acknowledging acceptance and resume delivery after restart.

### Bounded work

- [x] Add versioned durable storage for local and relayed device-addressed fanout
  envelopes, keyed by authenticated sender, logical message id and recipient
  account/device. Keep payloads opaque ciphertext and scope reads/deletes to the
  owning recipient/device.
- [x] Commit queue acceptance before including a target in `delivered` or
  returning relay success. Storage failure/full capacity must preserve sender
  retries; never evict an accepted pending entry to admit another.
- [x] Resume bounded, ordered draining on device registration and gateway
  restart. Define the lease/retry/removal boundary explicitly; reclaim interrupted
  drains without changing ciphertext or suppressing an unaccepted relay retry.
- [x] Deduplicate exact queued retries. Define handling of the same identity/id
  with different payloads; do not silently replace accepted ciphertext.
- [x] Apply existing block, revocation, account deletion, retention and capacity
  rules to stored queues, including cleanup and authorization on delivery.
- [x] Document acknowledgement as gateway/queue acceptance. A successful socket
  write is not a read receipt or proof of durable client storage.

### Acceptance and validation

- An acknowledged message for an offline device survives a hard gateway restart
  and arrives when that device registers; repeat for a relayed envelope and a
  paired offline sibling while its primary remains online.
- Crashes before commit, before ack and during draining retain a safe retry or
  replay. Failed socket writes retain the queued copy. Exact replays produce one
  readable receiver message through the existing receive journal/receipts.
- Queue-full and database-failure tests emit no false positive acceptance.
  Blocked/revoked/foreign accounts cannot drain another device's queue.
- Run focused store/gateway tests, backend regression checks, Ruff/cold-path
  checks, and real multi-device restart/reconnect browser journeys.

### Likely touchpoints

`_gateway_dm.py`, `_gateway_auth.py`, store schema/migrations and queue methods,
`test_fanout_offline_queue.py`, `test_dm_fanout_socket_delivery.py`, and the
multi-device gateway smoke harness.

### Exclusions

No new end-to-end client acknowledgement protocol, plaintext message archive,
push-provider guarantee, or general replacement of every gateway queue. Audit
shared regular-DM queue code only where needed for the fanout acceptance path.
The gap after a successful socket write but before a client commit must remain
explicit unless an existing protocol can close it safely.


---

## Round 137: E2E identity and session lifecycle isolation

Status: complete. See [Round 137 closeout](ROUND_137_CLOSEOUT.md). Rounds 130–136 remain closed.

### Problem and result

Delayed discovery and storage work now have context guards, but Round 133 left
global identity initialization and peer-only in-memory ratchet caches outside
scope. Bind the entire E2E runtime to its initiating account/device so a switch,
expiry, reauthentication or tab takeover cannot reuse another identity's state.

### Bounded work

- [x] Inventory ownership of `_initDone`, initialization promises, identity/state
  keys, ratchet caches, per-peer serialization and queued backup jobs. Define one
  consistent account/device context for these resources.
- [x] Namespace or replace live caches/queues on context changes. Old async work
  must finish only in its own context or cancel before committing/rendering;
  new account initialization must not await or adopt an old account's promise.
- [x] Preserve the bound account during temporary session expiry: expiry must
  not silently fall back to anonymous keys or unscoped storage. Resume only when
  the same account is authenticated again.
- [x] Coordinate identity initialization, outbox/receive recovery and tab
  leadership. Preserve per-peer operation ordering and existing immutable send
  payloads during a legitimate same-account recovery.
- [x] Preserve intentional key migration and same-account re-login identity;
  explicit logout/reset must keep their documented purge semantics. Do not
  regenerate identities merely to bypass a lifecycle failure.

### Acceptance and validation

- Two Solid accounts, including accounts on one pod origin, retain distinct
  identities, peer trust marks, ratchets, pending receives and outgoing jobs.
- Switch while initialization, decrypt/encrypt, state load/save and queue work
  are delayed. No old result touches the replacement context; returning to the
  original account recovers its valid pending work.
- Expiry/reauthentication does not create anonymous state, reset message numbers
  or lose a pending job. Cover rapid switch-back and session replacement.
- Real two-account browser journeys and tab handoff preserve readable messages,
  stable outgoing ciphertext and existing safety-number verification behavior.
- Run crypto/account/tab/outbox regression tests, frontend checks and relevant
  browser, gateway and bridge journeys; report unsupported cases explicitly.

### Likely touchpoints

`web/auth.js`, `web/e2e.js`, main initialization/tab lifecycle, gateway receive
and outgoing recovery modules, account namespace/keystore/state tests, and
account/tab browser smokes.

### Exclusions

No new encryption protocol, new device pairing capability, identity reset UX
redesign or cross-device private-key synchronization. This round isolates the
existing identities rather than changing how keys are distributed.


---

## Round 138: pending-send visibility and safe user controls

Status: planned. Depends on Round 137's stable account context. Rounds 130–135
remain closed.

### Problem and result

The durable text outbox retries safely, but frozen plans can remain pending when
recipients or devices disappear. Give users a clear view of their own pending
sends, accurate status and safe actions without silently changing recipients or
misrepresenting cancellation as recall.

### Bounded work

- [ ] Present pending text sends for the current account/device, with destination,
  age and an understandable state: waiting for connection/storage, awaiting
  confirmation, confirmed with cleanup pending, or requiring user attention.
  Keep crypto/storage implementation details out of normal product wording.
- [ ] Retry existing prepared payloads verbatim; prevent concurrent retries from
  duplicating jobs. Surface queue/storage limits while keeping unsaved composer
  text and already persisted jobs intact.
- [ ] Detect stale/revoked recipient device plans. Explain the condition and
  require a deliberate new send when recipients must change; never mutate an
  already prepared send to include new devices silently.
- [ ] Allow removal of an intent that has not spent a ratchet key. Before offering
  cancellation of a prepared send, prove that stopping retries cannot break
  later decryption: never roll back ratchets, reuse keys or enlarge skip bounds.
- [ ] Define safe treatment of legacy history-only pending rows that have no
  saved ciphertext. Do not label a reconstructed encryption as an exact retry;
  a fresh send must have a fresh id and disclose possible prior delivery.
- [ ] Include keyboard access, translated status wording and account-safe list
  cleanup. Explicit logout/reset retain their documented destructive meaning.

### Safety checkpoint and acceptance

Prepared cancellation is a design checkpoint, not an assumed capability. If the
existing protocol cannot safely handle the resulting skipped-key gaps, limit
Cancel to pre-encryption intents and clearly gate the prepared state. An
authenticated ratchet-reset protocol would require a separate planned round.

- A user can find an unresolved send after reload, retry it, and understand that
  unconfirmed delivery may already have reached the recipient.
- Local cancellation never claims to recall a received message. Cancellation,
  revocation and cleanup tests prove subsequent legitimate messages still
  decrypt within existing bounds.
- New accounts/devices cannot see or act on another device's jobs. Confirmed jobs
  do not revert to pending during refresh or stale asynchronous scans.
- Test queue-full/storage failure, device removal, reconnect, accessible controls
  and legacy rows; run real browser pending/retry and multi-device journeys.

### Likely touchpoints

`web/dm-outbox.js`, send status/history integration, a small pending-send UI,
main conversation/settings entry points, locale files and accessibility smokes.

### Exclusions

No message recall, automatic recipient retargeting, weakened key/skip limits,
bulk private-history management or new scheduling features. Do not add prepared
cancellation before its cryptographic safety is demonstrated.


---

## Round 139: reliable sender self-sync completion

Status: planned. Depends on durable queue acceptance from Round 136, account
isolation from 137 and recipient-change policy from 138. Rounds 130–135 stay closed.

### Problem and result

Recipient delivery can currently clear an outbox job while sender self-sync
mirrors retain best-effort semantics. Track those mirrors independently so a
conversation continued on another already linked device reliably contains the
sender's own messages, without delaying or falsely confirming peer delivery.

### Bounded work

- [ ] Separate primary recipient acceptance from own-device synchronization
  state. An own-device ack never confirms the peer; a confirmed peer send never
  becomes unconfirmed because its mirror is waiting.
- [ ] Retain exact encrypted mirror payloads until their supported acceptance
  boundary is met, including after sender reload and gateway restart. Reuse
  atomic pending-send results and preserve message ids and thread mapping.
- [ ] Retry unfinished mirror targets independently; do not re-encrypt or replay
  a completed primary send merely to catch up an own device.
- [ ] Apply the reviewed frozen-roster/revocation policy. Do not deliver to a
  revoked device or silently send historical plaintext to a newly linked one.
- [ ] Clean up per-target state only when safe; preserve history-disabled and
  explicit logout behavior and keep queue/journal capacity bounded.

### Acceptance and validation

- Primary acceptance before/after mirror acknowledgement produces the same
  correct UI: peer delivery settled, own-device sync independently pending.
- A previously linked offline sibling receives one readable copy after coming
  online, including sender reload, gateway restart and lost mirror ack cases.
- Partial success, duplicate frames, cleanup interruption and tab handoff reuse
  exact mirror ciphertext and retain one message per device/thread.
- Revocation/deletion cannot leak queued copies to an unauthorized session;
  new devices do not receive history through implicit outbox retargeting.
- Run focused outbox/device/queue tests plus real three-device gateway journeys,
  including self-sync after a successful primary send and later recovery.

### Likely touchpoints

`web/dm-outbox.js`, main acknowledgement routing and device roster handling,
per-device queue/acceptance code, paired-device smoke helpers and outbox tests.

### Exclusions

No browser pairing support, full historical backfill, multi-user group E2E,
new key distribution protocol or read-receipt guarantee. Only existing linked
sender-device mirrors belong to this round.


---

## Round 140: durable encrypted DM media delivery

Status: planned. Execute after the text/account/self-sync foundations above.
Rounds 130–135 stay closed.

### Problem and result

Files, voice notes and forwarded media have upload/draft recovery, but were
explicitly excluded from Round 135's stable encrypted text-send guarantee.
Carry the same stable-intent and confirmation model through existing DM media
flows so retry cannot strand an uploaded asset or spend another delivery key.

### Bounded work

- [ ] Audit the boundary between media draft/upload completion, encrypted
  attachment-key envelope preparation, outbox persistence and delivery evidence
  for files, voice notes and existing forward flows.
- [ ] Persist stable logical ids, encrypted delivery envelopes and recipient
  plans before transmission. Recover partially prepared per-device envelopes
  without ratchet re-encryption, using the established send journal.
- [ ] Keep large bytes in the existing media/draft storage rather than copying
  base64 payloads into localStorage outbox jobs. Define ownership/lifetime of
  references so interrupted cleanup cannot orphan the only usable draft.
- [ ] Distinguish uploaded, awaiting delivery and confirmed states. A failed send
  retains its retriable draft; an uncertain result never triggers destructive
  cleanup of an asset that may already be referenced by a delivered message.
- [ ] Preserve private forwarding's fresh file key/id behavior, exact decrypted
  bytes, spoiler metadata and supported playback. Do not reuse a source
  conversation's attachment key when forwarding.
- [ ] Apply account/tab isolation and recipient/self-sync policies consistently
  in browser pod, desktop bridge and supported gateway media paths.

### Acceptance and validation

- Fail before/after upload, envelope preparation, outbox writes, transmission,
  acknowledgement and cleanup; reload and recover without losing the draft or
  changing the prepared recipient ciphertext.
- Lost acknowledgements and duplicate deliveries leave one message, with exact
  file/audio bytes and stable decryptability after reload.
- Voice playback and forwarded media retain current privacy guarantees. Cleanup
  cannot delete a delivered asset, an active upload or another account's data.
- Verify quota/capacity failure, expired sessions, partial fanout and device
  changes without plaintext fallback or false delivery confirmation.
- Run focused media/outbox/crypto tests and real browser, bridge and supported
  multi-device media journeys. Gate unsupported routes explicitly.

### Likely touchpoints

`web/dm-media.js`, `web/dm-attachments.js`, media draft stores, encrypted secondary
file-envelope integration, outgoing job adapters and media journey scripts.

### Exclusions

No new codecs, file-size expansion, room-media redesign, content scanning service,
WebID-DM edit/reaction/pin parity or general secondary-event protocol rewrite.
Touch sealed file metadata only as required for the existing media delivery flow.
