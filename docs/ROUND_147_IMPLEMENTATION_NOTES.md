# Round 147 implementation checkpoint

Status: native replies are connected and passed the browser journey. Native
reactions remain incomplete; their wire codec, durable state store and guarded
receive hook are staged. Round 147 stays open. This note records implementation
of the existing scope and adds no acceptance work.

Implemented:

- Author-relative native references bind replies to an authenticated actor,
  conversation and transfer UUID. The two authors can reuse a UUID without
  selecting each other's original. Private UI ids, filenames and attachment
  bytes do not become wire reference fields.
- Versioned native-reference capability, WebSocket and signed gateway relay
  paths reuse bounded device custody. Authentication, recovery and receive
  commits enforce the version and exact device key. Ordinary fanout cannot
  bypass these gates. Custody and socket writes do not prove consumption.
- Compose retains validated encrypted wire text separately from display text.
  All existing peer and own-device targets remain in the frozen send plan.
  Exact prepared frames survive restart, and each affected device's ordering
  hold remains until its full protected consumption is proved.
- Reply history retains the actor/reference and resolves delayed originals
  atomically. Quotes and jumps use the actual cached original. An upgraded
  linked reader without the primary's attachment displays readable reply text
  and an unavailable quote, without creating a file placeholder.
- The reaction codec binds action id, author-relative original, token, emoji,
  sequence and add/remove operation. The receive hook requires durable state
  before consumption and does not create a message or attachment.
- The staged reaction store separates accounts, threads, authors and actors.
  Stable tokens and monotonically increasing sequence numbers preserve remove
  tombstones against delayed adds. Its 2,000-record per-scope bound fails closed;
  it does not evict anti-replay state to accept more actions. Failed/aborted
  writes permit identical retries.

Still required before closing 147:

- Connect reaction intent, source persistence, outbox recovery and visible pill
  hydration. The store is not yet imported by the application, and the UI's
  native reaction action is not implemented by these staged primitives.
- Exercise native reaction add/remove/re-add, reload and failure recovery in the
  real browser journey, including delayed originals and author collisions.
- Run final regression gates after those changes and write the closeout against
  actual evidence. Keep 144's design-only safety outcome and 145's ordinary
  receive-commit protocol unchanged. No attachment-byte fanout, larger skip or
  custody limits, prepared cancellation, reset workflow or emoji vocabulary is
  added.

Validation checkpoint (2026-11-25):

- Real three-browser/two-gateway native reply journey passed. Both primaries
  retained quotes and jump targets after reload. A legacy mirror retained exact
  pending frames across gateway/browser restart, then an upgraded reader
  consumed readable reply text without a phantom attachment. Later ordinary
  text remained independent. The existing 146 caption/restart/hash journey and
  145 protected-receive restart checks also passed in that run.
- Full frontend suite: 2,784 passed, 64 skipped. Focused reaction codec/store,
  reference boundary and service-worker checks: 39 passed.
- Changed production JavaScript passed ESLint; changed Python passed Ruff;
  whitespace and cold-path checks passed.
- Full backend suite: 4,918 passed, 15 skipped (222 warnings). No real
  reaction acceptance case is complete yet.
