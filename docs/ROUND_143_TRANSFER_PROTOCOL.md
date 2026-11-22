# Round 143 implementation contract

Status: design checkpoint; implementation and validation remain open. This
implements the existing Round 143 scope, rather than adding another round.

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
