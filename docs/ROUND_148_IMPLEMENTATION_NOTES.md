# Round 148 implementation checkpoint

Status: complete. The controls and receiver are connected, and real acceptance
passed. See `ROUND_148_CLOSEOUT.md` for final evidence. This note elaborates the
existing bounded plan without adding scope.

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
- The source controller freezes delete/pin intent before local state and any
  header. The outbox binds body, actor and conversation; every action frame and
  full proof carries the separate version. Recovery does not require an original
  that its own durable deletion already removed. Existing ordering holds and
  cancellation restrictions remain in force.
- Native controls use the authenticated action callback and advertise support
  only with that complete receiver connected. File presentation and history
  merging inspect deletion state; late quote reads cannot overwrite tombstones.
  Delete-for-me awaits atomic local removal before changing the visible feed.
- Native pins merge with ordinary server pins. Local previews read metadata,
  and Jump loads the actual retained Blob only when needed. A missing original
  stays unavailable; a retained authenticated pin can be unpinned after signed
  parent preflight without inventing a file. Linked readers use this path.
- A pending caption can finish after its original is deleted: its previously
  validated protected intent must still match a retained author/thread tombstone.
  Exact headers remain unchanged, and delayed caption application consumes the
  tombstone without restoring the file. This adds no cancellation or reset.

Safety constraints retained: no prepared transfer cancellation, source/gateway
retirement, reset protocol, larger storage/ratchet caps or attachment-byte fanout.
Round 144 remains design-only, and ordinary receive commits preserve round 145.

Checkpoint evidence (2026-11-25):

- Full frontend with connected controls and mixed-panel refresh: 2,820 passed,
  64 skipped, including the final native-context guard (shell cache v326).
- Focused history/native-file/reference/receive checks: 67 passed, including
  atomic rollback, concurrent Blob save/delete, v2 migration, both-author UUID
  collisions, delayed captions, bounded state and disabled-history local delete.
- Final focused gateway/reference and relay schema/key checks: 39 passed,
  including signed cross-gateway action preflight/custody/full proof, reply-only
  reader exclusion, exact version/key checks and ordinary-ingress bypass denial.
- Full backend against the complete protocol gates: 4,927 passed, 15 skipped,
  221 warnings (`r148-connected-full-core.log`).
- Focused connected source/outbox/pin/quote/caption checks: 205 passed; mixed
  ordinary-pin refresh has an additional passing regression case.
- Real three-browser/two-gateway acceptance passed
  (`r148-action-focused-browser-4.log`): both participants' pins, ordinary pin
  coexistence, legacy-mirror restart/upgrade, retained-reference linked unpin,
  reload and offline previews, local-only and author-only deletion, atomic Blob
  cleanup, both gateway restarts, exact pending caption/delete headers, delayed
  original replay rejection, and readable later text. The helper now waits for
  asynchronously hydrated file rows before invoking their controls.
- Earlier preserved caption/reply/reaction journeys also passed while this UI
  integration was under test; final combined regression will run in round 150.
