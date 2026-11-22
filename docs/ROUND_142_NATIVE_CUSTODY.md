# Round 142: staged native inline-media custody protocol

Status: backend checkpoint, not a completed Round 142. The current web client
neither requests this protocol nor receives its optional responses. The gateway
does not advertise `native_media_v` in `registered` yet. Browser draft/receive
integration and pending controls are the next work; chunk resume remains 143.

## Custody and identity

A normal legacy `send_file` echo is not delivery evidence. The opt-in path writes
opaque ciphertext and its sealed file header to SQLite before reporting any
acceptance. It preserves the original client id, ciphertext, route, header and
first acceptance timestamp. An exact retry returns the original record; changing
any bound payload, destination, thread or media digest is refused. Changes to a
profile display name cannot replace a previously accepted event.

The protocol uses the existing native base-peer file keys and targets a primary
DID account. Linked devices have independent keys and cannot drain or acknowledge
these envelopes. This does not implement media fanout/key distribution. Source
and recipient come from authenticated registration and owner-scoped DM threads,
not client-supplied sender fields. A remote send must come from the gateway's
owner account because the peer identifies the signing gateway as the sender.

Local delivery requires an explicitly opted-in primary socket, current block/
revocation checks, and the applicable relationship gate. Background delivery
rechecks the persisted record so an old in-memory row cannot outlive policy
cleanup. An incoming remote file retains its original relationship owner;
reassigning that relationship does not retarget the ciphertext to a new account.

## Versioned messages

A supporting client opts in on register with integer `native_media_v: 1`.
The option survives authentication challenges; linked devices remain ineligible.
A sealed `send_file` adds `native_media_v: 1`, `message_id` and `media_digest`.
The message id is one ASCII segment (1–128 letters, digits, underscores or
hyphens). The media digest is the existing 43-character base64url preparation
digest, not a plaintext file name or key. An unknown/invalid version fails
closed rather than entering the legacy echo path.

The wire file metadata stays `file` / `application/octet-stream`. Canonical
base64 must decode to 17–524,288 bytes, matching the declared ciphertext size.
The complete existing v1 sealed header is required. The store independently
validates the shape and refuses extra fields that could override sender/event
identity. File names, real MIME types and file keys stay inside the encrypted
header.

`native_media_status` contains version, original message id, preparation digest
and a status. `pending` means gateway custody only. `committed` means the
registered primary declared its receive commit. `forwarded` means a supported
peer gateway proved custody; it is not a device commit/read receipt. Policy
blocks do not become successful commits or reveal themselves in status replies.

Delivery includes `native_media_v: 1` and `native_commit: {v, queue_id,
envelope_digest}`. A supporting receiver must journal header consumption, commit
file history under its selected local retention policy, and persist exact replay
evidence before sending `native_media_commit`. That command echoes the queue id,
exact sender/message identity, envelope digest and client receive fingerprint.
The socket determines the recipient. Only an exact owned row can be released;
foreign/sibling acknowledgements and wrong digests cannot remove it. The
idempotent `native_media_committed` reply echoes the same identity, allowing the
browser to protect its receipt until server confirmation as in Round 145.

`native_media_recover` optionally queries at most ten original message ids owned
by the source account, then schedules bounded incoming/outgoing replay. A
missing source row is reported explicitly so the browser can resend its saved
original bytes. Recovery is throttled per socket to four seconds, batches are
capped at ten, and at most sixteen delivery attempts run globally. A slow peer
does not hold the websocket command loop or discard custody.

## Cross-gateway handoff

`native_inline` is a full-envelope-signed relay: the verified signing DID must
match `from_webid`, and the recipient gateway resolves the relationship owner.
The peer commits its own opaque queue before returning a signed
`native_media_custody` proof. This binds the exact canonical request hash,
message id, preparation digest and both gateway identities. The source checks
that proof against the expected peer key. A generic HTTP 200, unsigned reply,
foreign signature, changed request or unavailable peer never releases source
custody. Requests retain the existing 128 KiB HTTP body cap; proof responses are
streamed and capped at 4 KiB. Larger remote inline envelopes remain saved, not
silently re-encrypted or moved to a pod. Their transport is part of Round 143.

A lost response causes a fresh signed request with the same inner envelope.
The peer returns custody for that existing exact record, without creating a new
message or depending on a browser being online. Incoming records are marked as
such so an offline owner cannot cause the file to bounce back to another
location.

## Storage and cleanup

Schema 68 adds `native_media` with immutable source/id identity, route/digest,
opaque event, policy status and incoming/outgoing custody. Pending sender and
recipient scans use partial indexes; committed receipts do not burden replay
scans. The limits are ten pending envelopes per source, 32 MiB of pending wire
bytes globally, and 100,000 total custody/receipt records. Capacity refuses new
work rather than evicting accepted bytes or conflict evidence. Committed or
forwarded rows release file bytes but keep small exact-retry receipts.

Block and primary-device revocation remove pending bytes and prevent requeueing
that old id, without manufacturing a delivery receipt. A later block does not
erase evidence of a prior completed commit. Account deletion purges owned and
authored records. Safe mode, failed database integrity and in-memory stores
cannot issue custody proofs; recovery is a mutating command because it can forward files. There is no
automatic expiry or silent recipient migration. Shutdown cancels delivery
attempts before closing the store and leaves accepted rows for restart.
This does not authorize deleting a browser's spent-key draft; the Round 144
safety outcome still applies. In particular, forwarding to a peer gateway is
not evidence that its browser consumed the file header. The browser must not
retire that pending ratchet header merely because custody transferred.

## Remaining browser work

### Header ordering and adapter checkpoint (2026-11-22)

Schema 69 adds an exact sealed-header digest and a separate durable
`header_committed` bit. A forwarded gateway receipt does not set that bit.
Authenticated header-only commits retain the attachment ciphertext, while full
client commits can release it. Signed, request-bound peer receipt queries
propagate these two boundaries across gateway restarts. An inconsistent declared
header digest is rejected before accepting bytes into custody. Schema 68
forwarded receipts without original header evidence cannot manufacture it.

Before spending a native file header, capability preflight requires an online,
opted-in primary whose published X25519 key matches the source's frozen key.
Registration challenge recovery retains that key. A negative or lost preflight
leaves the source unprepared. Capability tasks share the bounded delivery task
pool and discard replies after account/session changes.

The existing shared Double Ratchet now supports an atomic send hold on a stable
prepared header id. Later encryption on that peer chain waits for exact durable
header-consumption evidence; incoming traffic, unrelated peers and exact pending
replays remain possible. The hold and ciphertext survive encrypted-state reload;
custody alone never clears either. MAX_SKIP remains 20. Releasing the hold retains
the header send journal until full completion. Prepared cancellation and ratchet
reset remain unavailable under Round 144's reviewed outcome.

The staged `native-media-send.js` and `native-media-receive.js` adapters connect
these boundaries without activating browser registration. Source progress is
saved transactionally before hold release, and full peer commit plus sender
history persistence precede journal/source cleanup. The receiver saves file-key
metadata in the encrypted ratchet journal before its header commit, then saves
file history and a protected exact-envelope receipt before the full commit.
Header recovery identity excludes attachment bytes so corrected ciphertext can
reuse an already-consumed header after a damaged-body retry. Full-file receipts
still bind the exact ciphertext. File bytes stay outside localStorage; recovery
requests gateway replay. Native receive journals and receipts are isolated from
ordinary gateway DM recovery.

These adapters have unit and real WebCrypto ratchet coverage, including damaged
file bytes followed by reload and a DH turn, held sends, exact replay, lost ACK,
quota failure, stale account continuations and source-history retry. They are
still **staged**: live UI dispatch, pending controls, capability request wiring
and real browser fault journeys remain open. No native file/voice delivery or
Round 142 completion is claimed by this checkpoint. Round 143 remains open.

Wire the staged adapters into primary-account registration, authenticated socket
dispatch, capability request correlation, own/incoming history and file/voice
entry points. Expose pending/retry, header ordering and unsupported-peer states
honestly. Remove staged module/event-contract exclusions when live imports and
dispatch exist, then advertise the version after validation. Validate reload,
lost acknowledgement, quota, key/account changes and policy boundaries in real
browser journeys. Preserve the existing HTTP limit; larger remote files need
Round 143's durable chunk transport rather than a false inline acceptance claim.

## Validation at the schema 68 custody checkpoint

A complete backend non-integration run passed 4,766 checks, with one skipped
and 14 integration checks deselected. The final focused native suite passes
20 checks covering external-connection commit visibility before acknowledgements,
restart, conflicting retries, concurrent capacity, account cleanup, policy changes,
primary/sibling separation, dispatcher identity binding, strict version refusal,
signed exact-request custody, rejected legacy/foreign proofs, migration from 67,
safe mode, corruption, in-memory stores, bounded attempts and clean shutdown.
The final 39-check conformance subset also passes, including 2 MiB and 25 MiB
active transfers through two real gateways, native custody, fanout and event
contracts. Ruff, the undefined-name cold-path gate and whitespace checks pass. No browser
recovery journey is claimed for this still-staged protocol.

## Validation at the schema 69 header/adapter checkpoint

The complete frontend suite passed 2,628 checks with 64 skipped. Subsequent
focused verification passed 88 checks covering the final source-cleanup retry,
absent-account refusal, real ratchet adapter recovery and service-worker cache
307. The native backend suite passes 26 checks, the preserved fanout commit
suite passes six, and the final event contract passes four, including a guard
that staged adapters are not imported by the live browser. ESLint reports no
errors; Ruff, the undefined-name cold-path gate and whitespace checks pass.
This is adapter/protocol validation, not a live browser native-media closeout.
