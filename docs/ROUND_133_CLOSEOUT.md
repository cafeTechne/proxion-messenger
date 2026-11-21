# Round 133: account-bound ratchet pod backups

Rounds 130–132 remain closed. This round fixes asynchronous ratchet storage work
that could otherwise continue under a replacement Solid session.

## Changes

- Backup jobs capture the initiating session's fetch function, WebID, session id,
  pod root, device public key and state encryption key. Jobs recheck that context
  before starting a PUT and discard stale queued work.
- Queues are scoped to the account and destination resource. A stalled old
  account/resource cannot block a different account/resource. Valid overlapping
  saves still coalesce to the newest blob and preserve per-resource write order.
- State reads check context after response/body/decryption awaits. Saves and
  legacy migration check before committing or returning results. A cancelled
  remote backup leaves its already committed local copy intact.
- Shell cache v296 distributes the change.

## Validation

The initial regression tests reproduced seven failures before the fix. Coverage
now includes replacement and in-place account changes, session expiry or
replacement, session-id changes, root changes, device-key replacement, delayed
read responses and bodies, encryption-time changes, migration cancellation,
queue restart, captured fetch behavior and newest-blob coalescing.

- Frontend: 2,462 passed, 64 existing gated/skipped; changed production module
  passes ESLint; diff whitespace checks pass.
- Browser DM smoke: passed, including signed encrypted delivery, interrupted
  history recovery, receipt reconciliation, media, reload and blocking.
- Cross-mode DM smoke: passed, including encrypted bridge delivery and media,
  interrupted history recovery, receipts, reload, blocking and receiving-role
  takeover. Tests used isolated local gateways and a throwaway Solid server.

## Limits

Requests already dispatched cannot be recalled; this prevents stale queued
requests from starting. Pod backups remain best effort, with local encrypted
state authoritative on a version tie. This does not redesign global identity
initialization, all in-memory ratchet cache account scoping, gateway socket DM
recovery or sender outbox transactions.
