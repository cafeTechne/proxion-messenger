# Round 143 implementation contract

Status: primary browser chunk negotiation, sender/receiver resume, Blob history
and accessible progress/retry/recipient decline are integrated. Round 143
remains open for the final policy/route acceptance audit. This implements
the existing Round 143 scope, rather than adding another round.

## Preserve the existing boundaries

Keep the existing AES-GCM file ciphertext and shared Double Ratchet header.
Preparation, stable ids, original peer/public key and receive holds from Round
142 apply to chunked files too. A retry cannot generate another key/header,
change recipients, reset the ratchet or cancel a potentially spent header.
Do not increase MAX_SKIP, the 25 MiB file cap, 64 KiB chunk size, WebSocket frame
cap or 128 KiB signed HTTP relay cap. Legacy transfer signals retain their
explicitly weaker active-transfer behavior.

An ordinary socket/chunk write is neither durable custody nor a client receive
commit. Preserve the separation between source/gateway custody, signed peer
gateway custody, durable client header consumption and full client file/history
commit. A header commit can release the ordering hold while the file remains
pending; it cannot release the file source or receive journal.

## Durable transport

Extend the opt-in native media protocol with explicit chunk capability, bound
to the online primary's original X25519 key. Do not advertise support until
handlers, browser dispatch and fault journeys actually work. Unsupported peers
must not consume a new prepared header under a recovery guarantee they lack.

Persist a small immutable manifest and bounded ciphertext parts. The manifest
binds sender/recipient, stable message id, exact sealed header, preparation
digest, ciphertext SHA-256, size and chunk count. Store original prepared bytes
before any header spend. Deduplicate parts by transfer and sequence, reject
conflicting bytes, and verify the complete ciphertext digest before declaring
complete custody. Preserve opaque filename/type placeholders at gateway
boundaries. Chunk boundaries slice the original ciphertext, including its
existing final authentication tag; they do not re-encrypt it.

Use transactional gateway storage for accepted parts and progress, with the
existing 32 MiB pending-custody budget shared with inline media. Keep per-source
pending/total receipt bounds, reject admission on full storage and clean up
parts on block, revocation and account deletion without claiming delivery.
Cross-gateway manifests, parts, progress queries and receipts require full signed
envelopes with exact request correlation and authenticated original identities.
Only complete, verified peer custody can release source gateway bytes. Browser
sources still require exact header/full receive evidence before their cleanup.

Retain an accepted inline envelope's original identity when its existing signed
HTTP body is too large. Transport its exact ciphertext/header in bounded parts
under the negotiated chunk protocol; do not re-seal or silently reroute to pods.
This closes the remaining large-inline cross-gateway gap as well as the larger
native file route. An already accepted identity cannot acquire conflicting
manifest/route/bytes during this adaptation.

## Browser recovery and file history

Persist bounded recipient ciphertext parts and progress in IndexedDB before
acknowledging them. Journal the decrypted file-key metadata in encrypted ratchet
state before header acknowledgement. Header recovery identity must remain
independent of damaged attachment bytes, as in Round 142. A complete-file receipt
must bind the exact original ciphertext and header. Duplicate offers, unordered
parts, missing-part queries and finalization retry cannot consume another header
or overwrite another peer's transfer.

On reconnect/reload, reconcile bounded missing-part batches against durable
progress. Apply account/device/tab/generation guards across every await. Keep
protected full receipts through lost acknowledgement and interrupted cleanup.
Never put attachment bytes in localStorage or the generic offline command queue.
Retain prepared sources when storage, policy, original keys or peers prevent
progress. Only an unspent source can offer safe cancellation.

The existing message-history record intentionally omits base64 above 8 MiB.
Consequently 25 MiB completion needs a bounded account-scoped local file cache
and a durable history reference, with lookup/download after reload and explicit
retention/deletion behavior. A metadata-only history row is insufficient proof
that a large file is durably saved. Respect the local-history preference rather
than treating its no-op return as a successful file commit.

## Validation required for closeout

Exercise both browser and gateway restart at offer, header commit, middle part,
complete ciphertext, history/cache commit and final receipt. Cover quota failure,
foreign/conflicting transfers, duplicate/unordered parts, policy changes,
decline, original key/account/tab changes, legacy negotiation, exact-header
reuse and subsequent text decryptability. Validate real cross-gateway small
inline, oversized-HTTP inline, 2 MiB and 25 MiB transfers with byte hashes and
one readable history/download result after reload. Close 142–143 only when the
supported paths and their actual limits have this evidence.

## Implementation checkpoint (2026-11-22)

Schema 70 extends the native custody parent with manifest, original recipient
public key, ciphertext digest/size/count and a separate complete-custody bit.
`native_media_parts` stores immutable 64 KiB BLOB parts. Full declared bytes and
bounded part metadata are reserved at manifest admission in the shared 32 MiB
budget. Duplicate parts return the original state; conflicting parts are refused.
The final ordered ciphertext digest must verify before complete custody or a
full/header commit is permitted. Missing-part reads are bounded. Policy cleanup
and parent deletion clear parts transactionally; blocking removes pending
native bytes in both directions without fabricating delivery evidence.

The staged upload handlers are `native_media_begin` and `native_media_part`.
They require an explicitly opted-in primary session, a healthy durable store,
an owned thread and applicable owner/federation authority. Part responses follow
the database commit. The signed `native_media_chunks` relay binds the manifest,
part identity and exact request. It resumes missing parts after a lost response
and gateway restart, uses the existing file count/byte rate budgets and refuses
unrelated signers before allocating per-signer byte accounting. Only a complete
peer custody proof can release source gateway parts. Full/header client commits
also require the original recipient public key for chunk records.

This version is **not advertised or requested by the current browser**. Auth
registration does not yet enable `native_chunks_v`, and the normal native drain
intentionally skips these staged records. The existing inline recovery protocol
remains active. Next: negotiate chunk support, adapt retained oversized inline
envelopes without changing their identity, wire the bounded browser inbox and
source upload/drain, add durable missing-part/finalization recovery and accessible
progress/decline controls, then validate the complete browser journeys above.

Local DM history now upgrades from version 1 to 2 without replacing messages.
Its `native_files` store and message reference commit atomically via
`dmHistorySaveFile`. It accepts files through 25 MiB, with a 128 MiB/64-file local
cache bound, and refuses full storage rather than evicting accepted files. The
ordinary 8 MiB base64 retention limit is unchanged. Reads verify the original
message/sender, byte size and digest, and recheck account/deletion state after
asynchronous Blob reads. Search/pairing exports omit the bytes and local-only
reference. Message/thread/expiry/cap/clear deletions remove associated Blobs in
the same transaction. Exact finalization retry preserves later edits. A history
no-op or a just-written file evicted by retention cannot issue a native full
receive commit.

The large-file history API is tested but is not yet the active chunk receive
path. Current inline file/voice recovery now checks actual retained history
before full acknowledgement. Incoming unfinished files appear in Pending sends
with a retry action and no unsafe cancellation. Native inspection no longer
disables normal pending sends on pod/linked/legacy connections or when one
native storage provider is inaccessible. Shell cache is 309.

Validation: 83 focused backend protocol/fanout/migration checks pass. Two real
HTTP gateways transfer 2 MiB and 25 MiB ciphertext with exact hashes and signed
complete custody, using the existing relay limits. This is gateway-custody
evidence, not browser resume evidence. Frontend: 2,663 passed, 64 skipped; the
new large-file history suite has 17 checks. ESLint, Ruff, cold-path, whitespace
and translation gates pass. The complete multi-device browser journey still
passes inline/voice/history and all preserved fanout crash/restart cases.

The broad backend run passed 4,813 checks with one friend-accept HTTP timeout,
one skipped and 14 deselected. That timeout did not reproduce in nine lifecycle
checks alone or in 49 checks including the same preceding legacy file tests.
It is not marked fixed; recheck the complete suite at final integration.

### Receive recovery checkpoint (still open)

Added a staged ciphertext-only browser inbox with an eight-transfer/32 MiB
account bound, immutable manifest identity, ordered full-ciphertext verification,
and durable missing-part recovery. The receiver requests at most eight parts per
window after protected header journaling; a failed retained-history save leaves
the ciphertext and key journal available for retry. Full commit still requires
the protected receipt. No prepared source cancellation is introduced.

Gateway download offers and bounded part reads now enforce the original
recipient key, account, exact custody token and manifest, and existing delivery
policy. Parts remain in custody after socket writes. Cross-gateway complete
custody can advance forwarding; full delivery still requires the client receipt.
Browser chunk capability registration and main dispatch remain disabled until
sender upload integration is ready. Contract checks explicitly exclude staged
modules, and reject accidentally loading them without removing the exclusions.
The shared protocol utility used by the active inline receiver is cached in
shell version 310.

Validation at this checkpoint: 75 focused receiver/ratchet checks, 46 receiver
and shell checks, and 19 gateway/download/event-contract checks passed. Ruff,
focused ESLint and whitespace checks passed. These are recovery component tests,
not a claim of a live browser chunk journey. Sender upload, existing oversized
inline adaptation, browser activation, accessible progress/decline and full
restart journeys remain required before closing 142–143.

### Upload recovery checkpoint (still staged)

Added a bounded direct-socket uploader that sends an immutable manifest followed
by only gateway-requested 64 KiB ciphertext slices. Begin/part acknowledgements
bind account/connection epoch, message, preparation/header/manifest digests,
original recipient key and requested sequence. Lost replies trigger an exact
manifest retry; the gateway's durable missing list resumes the upload. Concurrent
starts coalesce and reserve the ten-upload limit before hashing. Account,
connection and original-key changes invalidate outstanding work. Custody alone
never deletes the browser source or releases its receive hold.

The native sender now accepts an optional upload adapter and requires explicit
chunk readiness before preparing a larger source. The live wrapper does not
supply that adapter yet. Draft storage supports an explicit 25 MiB ciphertext
policy with an atomic per-account 32 MiB ciphertext reservation; the default
inline admission limit stays in place until integration. The reservation bounds
both retained source and prepared copies to roughly twice that ciphertext
budget, plus metadata. No existing draft is evicted on admission failure.

Authenticated registration preserves strict chunk opt-in through its challenge,
and preflight can require a chunk-capable original primary locally or through
an exactly correlated signed remote query. Registration does not yet advertise
browser chunk support, and the current browser does not request it. Shell cache
is 311 for the active sender/storage changes.

Validation: 84 backend custody/auth/relay/event-contract checks passed, including
real 2 MiB and 25 MiB gateway transfers. The frontend suite passed 2,686 checks
with 64 skipped, followed by seven focused uploader checks covering the final
concurrency guard. Sender/storage integration tests passed 46 checks. Focused
ESLint, Ruff, cold-path and whitespace gates passed. This does not close browser
activation, accepted-inline adaptation, progress/decline UX or live chunk crash
journeys; 142–143 remain open.

### Accepted-inline adaptation checkpoint

Schema 71 adds a separate chunk identity digest so adapting an already accepted
inline envelope preserves its original queue id, envelope/receipt digest,
timestamp, route and header progress. Adaptation checks the supplied manifest
against the actual retained ciphertext and exact original header before slicing
those bytes atomically. A conflicting route, direction, media digest, header,
size/hash or subsequent recipient key cannot replace custody. A failed part
insertion rolls back the conversion and preserves the original inline bytes.
Exact retries through either transport keep the same accepted identity.

When old inline bytes have already been released, the source reports its exact
existing progress instead of inventing chunk custody. For adapted transfers,
signed peer progress can reconcile an original inline receive that completed
before migration. Header-only progress does not free file bytes. Pending peer
inline custody can itself adapt without a second ciphertext upload; old full
receipts retain their original identity and do not acquire an invented manifest.

Validation: 100 custody/migration/fanout/gateway checks passed, followed by 59
focused checks including rollback and schema 70-to-71 preservation. Four real
HTTP gateway cases passed: the existing 2 MiB/25 MiB transport checks plus a
400 KiB accepted-inline transfer to both a fresh peer and one with existing
inline custody. Hash/byte equality and original receipt tokens were checked.
Ruff, cold-path and whitespace checks passed. This closes the backend adaptation
implementation gap; browser activation, progress/decline controls and live chunk
restart journeys are still required before 142–143 closeout.

The complete backend run passed 4,834 checks with 15 skips; two Node-backed
policy/specification checks failed because that invocation omitted the available
Node wrapper from PATH. Both corresponding suites passed on recheck with the
correct Python/Node PATH (seven checks). The previously intermittent friend
acceptance case passed in the broad run. Repeat the complete integration gates
with the correct environment after browser activation; this is not a claim that
that future integration is validated.


### Primary browser integration checkpoint

Primary connections with local history and the original X25519 key now request
and negotiate chunk version 1. The browser dispatches manifest/part responses,
recovers scoped upload/download state and uses bounded Blob history before full
receive acknowledgement. Files above 64 KiB use chunk transport when supported;
inline-only connections keep the existing small-file route and cannot spend a
larger saved source header. The legacy large-file route shows its weaker reload
boundary explicitly. Existing accepted inline files can reuse their exact
ciphertext/header through the new upload adapter. Linked/pod connections do not
claim this primary-only protocol.

Pending sends shows labelled progress and retry for both directions, including
an admitted incoming manifest whose header has not yet been consumed. Inspection
keeps durable incoming progress visible while disconnected and deduplicates its
receive-journal row. Only unspent sources offer cancellation. Shell cache 313
includes the active chunk modules; gateway event-contract exclusions have been
removed for the three implemented browser events.

The maximum-size browser experiment exposed two integration issues that are now
fixed: native part/fetch frames use the gateway's existing file budget rather
than the general command pacer, and native retry frames never enter the generic
reconnect queue. A Blob-history transaction that has already aborted can fail
closed without an uncaught second abort. Regression tests cover both cases.

Live browser evidence: 600 KiB file history and voice, preserved inline
prepared-write/history/lost-commit crashes, plus 2 MiB and maximum-size native
file journeys. The latter use 25 MiB ciphertext (25 MiB minus 16 bytes of
plaintext, preserving the existing AES-GCM cap). Each larger journey verifies a
source/gateway restart after three upload parts, recipient/gateway restart after
eight durable download parts, failed Blob history, subsequent text decryptability,
lost full commit, final restart of both browsers/gateways, and one exact readable
history file per primary. Source ciphertext/header hashes remain unchanged and
durable upload prefixes are not resent. This is actual browser recovery evidence,
not solely gateway custody.

Validation: complete backend suite 4,840 passed, 15 skipped; final pacing/native
registration/event-contract subset 34 passed. Complete frontend suite 2,694
passed, 64 skipped. ESLint, Ruff, cold-path, whitespace and translation checks
pass (1,217 canonical keys, 15 existing fallback warnings). Recipient decline,
remaining policy/final acceptance checks and an explicit browser journey for
previously accepted oversized inline custody remain open; neither 142 nor 143
is closed by this checkpoint.


### Recipient decline and accepted-inline browser evidence

The real primary-browser journey now starts a 400 KiB file on an inline-only
connection, verifies original gateway custody cannot cross the HTTP body cap,
then upgrades the connection after a gateway/browser restart. Queue id,
envelope/receipt digest, header digest and ciphertext hash remain unchanged.
Both primaries restore exactly one readable file after reload. This resolves
the explicit accepted-inline browser migration evidence gap recorded above.

Chunk-capable primary registration advertises `native_decline_v: 1` only when
the gateway implements the separate decline command. Pending incoming transfers
expose a labelled keyboard button. The receiver journals header consumption,
then a protected content-free decline marker in its own receipt scope before
removing incoming ciphertext. A decline is never a full receive/history receipt.
Lost acknowledgements replay the marker and original header proof; normal
full/decline/header reconciliation rotates through bounded batches rather than
filling the generic command pacer. Native decline frames stay out of the generic
reconnect queue.

The gateway authenticates the original primary account/key and exact custody
token, and accepts decline only for complete offered chunk custody. `declined`
is a distinct persistent terminal status in the existing native-media table;
no new schema columns are required. Decline and full commit have one atomic
winner; a stale full commit cannot turn a decline into delivery. Signed peer
progress propagates the distinct outcome without revealing a block/revocation.
Decline clears gateway ciphertext and acknowledges header consumption, while
retaining the sender's exact prepared browser source. It neither writes a sent
history file nor finishes/deletes that prepared send. Round 144's prohibition
on arbitrary prepared cancellation/reset remains intact.

The receiver refuses a decline if a file already reached retained local history
but the separate full-receipt write failed. It also suppresses in-flight part
completion after successful decline. Quota/context failures retain ciphertext
and journal state for an exact retry. Unsupported gateways expose no decline
control. Earlier clients may retain an unknown declined source pending until
upgraded; no compatibility path relabels decline as successful file delivery.

Validation: 124 focused native/authorization/policy checks passed; complete
backend suite 4,848 passed, 15 skipped. Complete frontend suite 2,702 passed,
64 skipped. Real keyboard decline passed lost-request recovery, both gateway/
browser restarts, protected receipt confirmation, ciphertext cleanup, no full
history/delivery claim, unchanged retained source and later text decryptability.
The full browser run also passed accepted-inline migration, 2 MiB/maximum-size
fault journeys, inline/voice recovery and every preserved fanout fault case.
ESLint, Ruff, cold-path, whitespace and translation gates pass (1,220 canonical
keys, 15 fallback warnings). Shell cache 315 includes the final race guards.
The final policy/route acceptance audit remains open; no 142–143 closeout or
146–150 plan execution is claimed by this checkpoint.
