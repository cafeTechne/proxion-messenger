# Round 146: Native attachment references and caption edits

Status: implementation and focused browser acceptance verified; final backend
regression gate running. Keep 146 open until that gate completes.

## Result

Native attachment captions use a negotiated, primary-only `native_edit_v: 1`
action. A private `native:<actor>:<UUID>` history key never becomes an ordinary
edit command. The source validates the retained file, author, conversation,
caption bounds and sequence before ratchet preparation. Preflight binds the
completed original and the peer's capable primary/key. Unsupported peers do not
spend a send position.

The encrypted send-job journal saves the caption plan before encryption. Its
stable action/send identity bridges interrupted prepared writes. Header
consumption releases only the ordering hold; gateway custody/socket writes do
not prove caption delivery. Full delivery additionally requires the receiver's
atomic caption/action-history write and protected receipt. Source cleanup waits
for its own retained caption history too. Failed writes, conflicting retries,
changed accounts/threads/keys and blocked prepared actions retain their original
recovery state. Pending sends offers keyboard retry and no prepared cancellation.

Gateway caption rows use the existing bounded native custody store, with their
own target/sequence and immutable sealed envelope. They do not rewrite the
original file receipt, ciphertext or history Blob. Signed relay requests/proofs
bind the action, original header, recipient key and routing metadata. Legacy,
linked and wrong-key clients cannot drain or acknowledge a caption. A retry of
an already-forwarded action queries the peer's receive receipt even after the
source releases its opaque action bytes.

Validated native references and caption sequence/action markers persist locally.
Existing records without reference metadata resolve from their original key,
file and authenticated participant context. Both sender aliases map back to the
existing local attachment. An edited caption starting with `📎` remains visible.

## Acceptance evidence

The focused real-browser journey in `web/scripts/native-caption-journey.mjs`
runs through the actual editor, context menu and Pending sends keyboard action,
with two gateways, both primary browsers and the existing linked-device setup.
It passed these cases:

- Both accounts edit their own file: a chunk-backed attachment and an inline
  attachment. The peer's attachment offers no non-author edit action.
- A prepared-job write interruption and source reload reuse the same action,
  send identity and ratchet header before transmission.
- Failed recipient history writes acknowledge the protected header only.
- Lost full commits survive both gateway restarts and both browser reloads;
  protected receipts complete delivery without replacing preparation.
- Each primary retains one original attachment with its exact original hash,
  the final caption/sequence, and readable later normal text.

This also exposed a fanout-only conversation defect: a live owned contact can
appear in the sidebar before an ordinary message creates its `dm_threads` row.
Native preflight now resolves the exact account-owned live certificate without
writing state. Actual file admission materializes that owner's thread. Foreign,
ownerless, expired and revoked certificates cannot use this path. The completed
145 device receive-commit protocol is unchanged.

## Validation

- Focused native gateway/storage/policy tests: 76 passed after the owned-contact
  fix, including retry through the real caption-send command.
- Frontend regression: 2,747 passed, 64 skipped; this also includes the
  isolated, not-yet-connected round 147 reply codec. This includes ordinary text/room edit coverage.
- Focused real-browser caption journey: passed (both authors and interruptions).
- ESLint, Ruff, cold-path gate and whitespace checks: passed.
- Backend regression after the owned-contact fix: 4,897 passed, 15 skipped,
  with one owner-removal acknowledgement-order failure. The completion-order
  fix and deterministic check passed the owner/native gateway suite (33 tests);
  a clean full backend rerun is pending.

Rounds 130–145 retain their existing scope and outcomes. In particular, 144
remains design-only: no prepared cancellation, unilateral reset or larger skip
buffer. Native media fanout and file replacement are not added by this round.
