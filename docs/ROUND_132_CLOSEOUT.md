# Round 132: recoverable pod DM receives

Round 131 is complete: its previously blocked cross-mode DM browser smoke passed
after rebuilding an isolated Python 3.12 gateway test environment. Round 130
remains closed and unchanged.

## Result

Regular and per-device fanout encrypted DMs received through browser or gateway
bridge pod inboxes now save temporary recovery plaintext inside the same encrypted
state blob as the ratchet advance. A failed local write leaves the receive key
retryable. A crash after that write can recover plaintext without spending the
key again.

History or receipt failures retain the drop for retry, with one live rendering
per reader. Durable history and exact-envelope receipt precede recovery cleanup
and inbox deletion. A receipt committed before interrupted cleanup also resumes
safely. The bridge now returns the fanout handler's asynchronous persistence
result instead of discarding it. Disabling local history still skips history and
removes temporary recovery after successful receipt persistence. Shell v295 ships
the updated code.

## Validation

- Frontend: 2,449 passed, 64 existing gated/skipped; changed production modules
  pass ESLint; diff whitespace checks pass.
- Real browser DM smoke: passed, including injected history-store failures,
  retained encrypted recovery, reload, one saved/readable message and inbox cleanup.
- Real cross-mode DM smoke: passed with the same interruption journey through
  the desktop gateway bridge, plus existing encrypted media, requests, reload,
  presence, blocking and receiving-role takeover journeys.
- Focused regression coverage includes failed atomic writes, unchanged ratchet
  version on recovery, history/receipt retry, copied-envelope deduplication,
  account cancellation, recovery backpressure, disabled history, real fanout
  dispatch, bridge result propagation and cleanup after a committed receipt.

## Boundaries

The recovery journal is bounded to 64 undelivered entries per peer and uses the
existing device state encryption key. It adds no wire format or cryptographic
primitive. History and receipts remain separate stores; the recoverable commit
bridges their interruption window rather than migrating them into one database.

Gateway socket receives, secondary sealed events and sender outbox transactions
are outside this round. Pod state backup remains best effort. Clearing browser
storage or losing the device state key can lose local history and pending
recovery; this does not provide cross-device recovery from such loss.
