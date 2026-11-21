# Round 134: gateway DM receive recovery

Gateway socket encrypted text and per-device fanout now save message metadata
inside the same encrypted state record as plaintext and ratchet advancement.
After an interrupted history or receipt write, a new reader restores the message
without needing another socket delivery. Temporary metadata and plaintext are
removed after history and a durable exact-envelope receipt are saved.

Exact duplicate events serialize and are suppressed using account/device-scoped
receipts. Different ciphertext never shares a receipt merely because its sender
reused a message id. Fanout includes peer-device and self-sync copies. Regular
message metadata and routing normalization are preserved. Local history disabled
in Settings still skips permanent history.

The active tab retries pending work after registration and periodically. Account,
device, namespace and tab-generation checks discard stale results. A bounded
live buffer also retries transient storage failures before decryption; invalid
ciphertext cannot occupy that storage retry buffer indefinitely. Pod receives
continue to use their existing reader. Shell v297 includes the new module.

## Validation

- Frontend: 2,480 passed; 64 existing gated/skipped.
- Gateway browser DM history journey passed with an injected history write
  failure, encrypted recovery retained before reload, plaintext restored after
  reload, recovery cleanup, disabled history and reconnect behavior.
- Paired multi-device gateway journey passed with injected fanout history failure
  and reload on the receiving device, plus cross-gateway delivery to both devices,
  primary reply, sender self-sync, history reload and revocation.
- Browser pod DM journey passed, including its interrupted-storage recovery,
  durable receipts, encrypted media, reload and blocking.
- Unit coverage includes real ratchet/journal recovery for normal socket and
  fanout delivery, cross-account scan isolation, storage/receipt/cleanup failure,
  duplicate events, changed ciphertext, capacity backpressure and stale receives.

Changed production modules pass ESLint; final whitespace checks pass.

## Limits

This round covers gateway text and device fanout, not files or sealed secondary
events. Receipts keep their existing bounded retention; the journal holds at
most 64 pending receives per peer and the live encrypted-event buffer at most
1,024 entries. Events lost before a durable receive write, cleared browser storage
and lost device keys remain outside this recovery guarantee. Pod backup remains
best effort. Sender outbox transactions are planned for Round 135.
