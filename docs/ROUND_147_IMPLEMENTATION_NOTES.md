# Round 147 implementation handoff

Status: staged primitives only. The compose/reaction paths are not enabled.
This note elaborates the existing 147 scope; it does not add acceptance work.

Implemented and checked:

- `native-reply-codec.js` validates an original attachment in its authenticated
  conversation and encodes an author-relative UUID reference bound to the new
  reply message id. It resolves colliding UUIDs by author; a missing/ambiguous
  original leaves a reference/unavailable state, never a new file.
- `dm-outbox.js` can retain validated `wireText` separately from display text.
  The plan binds text, actor, reference, conversation and recipient before
  encryption. Primary and mirror frames omit private `reply_to_id` metadata.
- `gateway-dm-receive.js` has an optional asynchronous transform before history
  retention and full ACK. It cannot change authenticated message/thread/actor
  routing; failed/stale transforms keep the protected receive journal.

Not connected: compose, native-reference capability negotiation/delivery,
reference persistence on reply history, quote navigation and native reactions.
The normal UI still uses its existing reply/reaction paths.

Compatibility/safety constraints established by source inspection:

- The current legacy gateway receiver consumes sealed control plaintext without
  displaying it. A typed reply must not enter that path on an old primary or
  linked device: it could acknowledge/drop the reply's text. New reference
  frames need explicit version/capability gates at ingress, relay and each
  device's delivery/recovery, not only a source-side optimistic preflight.
- Preserve normal text and the completed 145 receive-commit behavior. Do not
  silently omit existing text mirrors to make native references easier. An
  upgraded linked reader can retain reply text with an unavailable quote when
  it lacks the primary's file; this does not add attachment-byte fanout.
- Versioned native-reference commands/relay envelopes can reuse the bounded
  device custody/journal machinery while ensuring an old gateway rejects a new
  frame rather than forwarding it to an old reader. Existing ordinary commands
  must keep their current behavior.
- A prepared native-reference header cannot be abandoned or replaced. If a
  reference frame waits for an upgraded device while later ordinary frames
  advance that same ratchet, the existing skip bound can be exceeded. Keep the
  affected device's ordering hold until protected consumption is proved; gateway
  custody alone must not release it. Other recipients can progress independently.
- Reactions require durable add/remove tokens scoped to the authenticated actor
  and original reference, including both authors choosing the same file UUID.
  Source/receiver storage and restart behavior still need implementation and
  real acceptance coverage. No emoji vocabulary, larger caps or reset workflow
  is added here.

Checkpoint validation (2026-11-22): full frontend regression 2,754 passed,
64 skipped; focused reply/outbox/receive checks 69 passed; ESLint and the
cold-path gate passed. No real reply/reaction acceptance case is complete yet.
