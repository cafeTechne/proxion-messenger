# Round 137: E2E account and session isolation

Status: complete. Rounds 130–136 remain closed.

The crypto runtime now captures the bound account and authentication generation
before initialization or queued work. Account changes clear private runtime keys
and ratchet caches; session replacement invalidates unfinished work without
changing persisted namespaces. Expired sessions retain their account binding and
pause encrypted operations. Reauthentication restores the same identity,
ratchets and encrypted pending jobs.

Initialization installs keys only in its initiating context. IndexedDB inserts
and legacy identity claims are atomic, so competing tabs cannot replace a
committed identity. The localStorage fallback uses account-scoped keys, and
returning fallback identities migrate into IndexedDB without rotation. Legacy
keys remain claimable by only one account. Cancellation never triggers legacy
fallback. Receiver key derivation uses the captured private key.

Per-peer serialization is scoped by runtime generation. A blocked old account
cannot block a new account's peer queue. State loads, saves and queued backups,
peer discovery, outgoing jobs and receive recovery check context before applying
results. The composition root supplies transient session generations to outgoing,
gateway receive and browser inbox engines, and requires active tab leadership
for crypto work. Stable persisted account/device scopes and ciphertext journals
retain their existing format. Shell cache: v300.

Validation:

- Frontend: 2,530 passed, 64 optional integration checks skipped.
- Added real WebCrypto/IndexedDB lifecycle tests without module resets: account
  switch/back, expiry, delayed init/encrypt/decrypt, same-account replacement,
  concurrent initialization, stale queued work, fallback identity migration and
  inactive-tab refusal. Outbox/receive/browser-drop generation tests also pass.
- ESLint on changed production modules, diff whitespace and cold-path gate pass.
- Isolated browser journeys pass: account lifecycle; encrypted browser DMs with
  crash recovery/media/voice/forwarding; desktop pod bridge in both directions;
  two tab takeovers with every DM readable. The tab smoke now observes DOM
  mutations: its earlier animation-frame polling timed out in hidden tabs even
  when the notice was already present.

Limits: this does not introduce new pairing, cross-device private-key transfer,
ratchet reset or message recall. The legacy no-IndexedDB fallback retains its
existing extractable localStorage security tradeoff. Explicit logout/reset keep
existing purge semantics. Backend code and protocols did not change. External
provider/browser combinations beyond the isolated local journeys remain unverified.
