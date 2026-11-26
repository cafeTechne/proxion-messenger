# Round 148 implementation checkpoint

Status: in progress. Persistence and protocol gates are implemented and tested;
delete/pin controls and real acceptance are not connected yet. This elaborates
the existing bounded plan, without adding scope.

Implemented:

- The native action codec binds action id, author-relative original, operation
  and bounded clock. Delete accepts only the original action actor's file;
  pin/unpin can reference either participant's original. It sends no private
  history key, filename, caption or file bytes.
- History v3 adds a bounded `native_state` store in the same account database.
  Deletion evidence, history removal and Blob removal commit atomically. Failed
  transactions leave all three unchanged. Original/history/Blob writes inspect
  this evidence in the same transaction and do not resurrect deleted data.
  Delayed authenticated captions consume safely without restoring the original.
- Delete-for-me has a separate local-only API and remains available for existing
  local files when history saving is disabled. It sets no remote deletion flag.
  Ordinary history cleanup does not evict replay evidence. The state bound is
  2,000 entries per account database; capacity fails closed rather than evicting
  tombstones. Existing rows can still advance.
- Pin state uses a bounded clock followed by opaque action id for concurrent
  ordering. This produces the same winner across gateway/browser actor aliases.
  Everyone-deletion dominates subsequent pin replay. A missing original can
  retain pin metadata without creating a message or attachment.
- Delete/pin has a distinct `native_action_v:1` capability inside the reference
  protocol. Authentication retains strict versions through the nonce response.
  Signed preflight/relay proofs echo the new version; exact device capability,
  queued recovery and full consumption commits enforce it. Ordinary fanout
  rejects action markers, including markers without a reference version.
- The client reference/receive adapters understand the new codec and proof, but
  the application deliberately does **not advertise native_action_v yet**.
  Its action-state callback and UI remain unconnected, so advertising support
  would be inaccurate. Reply/reaction capability remains unchanged.

Remaining implementation before closing 148:

- Bind native actions into the durable outbox: frozen actor/context/body, new
  wire flag, source state before any header, exact retry/hold, new-version full
  ACK evidence and existing cancellation restrictions.
- Connect authenticated incoming action persistence and local presentation.
  Before presenting native files, inspect durable deletion evidence so a stale
  callback cannot restore an in-memory file after its atomic history removal.
  Remove cached quote/pin previews when an original is deleted. Preserve the
  ordinary 145 receive protocol and 144's design-only safety outcome.
- Connect author-only delete-for-everyone and strictly local delete-for-me to
  existing controls. No prepared transfer cancellation or gateway/source
  retirement is inferred from a history tombstone. Check pending prepared
  caption/reply actions against deletion so their exact recovery cannot be
  stranded by removal of the original history row.
- Add native pins alongside ordinary server pins, without hiding ordinary text
  pins. Read retained native references locally for offline previews; resolve
  only real originals, with unavailable/deleted guards and scoped navigation.
  Preserve existing Pod room adapters. Enable capability advertisement only
  once the complete action receiver is connected.
- Exercise both authors' pin/unpin, author-only deletion, local-only removal,
  Blob cleanup, delayed replay, reload and failure/restart recovery in real
  browsers. Rerun final gates and write an evidence-based closeout.

Checkpoint evidence (2026-11-25):

- Full frontend after v3 fixture updates: 2,807 passed, 64 skipped.
- Focused history/native-file/reference/receive checks: 67 passed, including
  atomic rollback, concurrent Blob save/delete, v2 migration, both-author UUID
  collisions, delayed captions, bounded state and disabled-history local delete.
- Final focused gateway/reference and relay schema/key checks: 39 passed,
  including signed cross-gateway action preflight/custody/full proof, reply-only
  reader exclusion, exact version/key checks and ordinary-ingress bypass denial.
- Full backend regression: 4,925 passed, 15 skipped, 220 warnings. Its
  collection predates the final two relay/bypass tests and the allowlist/ordinary
  ingress correction; the focused 39-test run covers those final changes. Final
  closeout must rerun the full backend after complete UI/receiver integration.
- No real delete/pin acceptance case is claimed complete at this checkpoint.
