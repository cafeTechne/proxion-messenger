# Round 135: durable encrypted text sending

New encrypted text sends now commit a frozen intent before encryption and keep
an encrypted outbox until explicit confirmation. Each per-peer/device ciphertext
is saved atomically with its ratchet advance, so preparation can resume after a
crash without spending another key. Only a persisted prepared frame is sent.
Online sends, disconnected sends, browser pod DMs and desktop bridge DMs all use
this ledger. Reload and Retry preserve ids, ciphertext, ratchet headers and pod
envelope metadata. The compose target is captured before asynchronous key lookup.

History is saved before transmission; the history-disabled preference still
skips permanent history. Temporary encrypted drafts survive until confirmation.
An initial intent-write failure returns the text to the composer before any
ratchet advance or transmission. Explicit sign-out purges local outbox jobs.

Only an own echo/history row, successful pod drop or acknowledgement covering
all intended recipient devices confirms a send. Self-sync-only acknowledgements
cannot confirm a primary send. Confirmed state commits before cleanup, so a crash
while clearing history flags or ratchet journal entries never retransmits a
confirmed job. Uncertain sends stay visibly pending with “Delivery not yet
confirmed” and a Retry affordance.

The gateway acknowledgement audit found failed device sockets being reported
as delivered without a retry copy. Failed writes now queue, full queues refuse
new entries without evicting older accepted messages, exact retries share a
queue slot, and remote fanout only acknowledges relay/queue acceptance. Inbound
relay deduplication commits after acceptance, allowing retry after backpressure.
The existing ack schema and encryption primitives are unchanged. Shell v298
includes the new outbox module.

## Validation

- Frontend: 2,506 passed, 64 existing gated/skipped.
- Browser pod, gateway text, paired-device gateway fanout and desktop pod-bridge
  journeys all passed the same fault test: fail the initial intent commit and
  retain the composer, fail prepared-frame persistence after encryption, reload,
  deliver but lose acknowledgement, reload again, and verify identical encrypted
  wire fields and exactly one receiver history record.
- These journeys also retain previous receive recovery, disabled history, reply,
  pairing, sender self-sync, encrypted media, reload, blocking and receiving-role
  checks where applicable.
- Real crypto tests cover failed intent/state/prepared commits and partially
  prepared fanout; retry preserves message numbers and ciphertext. Additional
  tests cover account isolation, partial/incorrect acknowledgements, uncertain
  UI state, cleanup interruption, stale restore snapshots and explicit logout.
- Changed production modules pass ESLint; Ruff and the backend cold-path gate
  pass. The i18n gate passes with translation-completeness warnings;
  the new confirmation wording has canonical English and pseudo-locale coverage
  and uses normal per-key English fallback in untranslated locales.

Backend: all 4,720 selected non-integration tests verified. The full run passed
4,718; its only two failures were toolchain checks unable to find `node`. Both
passed on a targeted rerun with the test Node installation on PATH. Fourteen
integration tests were deselected; the separate browser journeys used real
isolated gateways and throwaway Solid servers.

## Retained limits

This is a local outgoing text outbox. Gateway acceptance may include an offline
retry queue; it does not prove recipient reading. The existing local offline
device queue is process-local and does not survive a gateway restart. Sender
self-sync mirrors remain best effort and never override recipient confirmation.

The outbox holds at most 128 pending jobs and each peer's ratchet journal at most
64 pending send results. It does not retarget frozen recipients after roster
changes. Clearing browser storage, explicit logout or losing device keys can
remove pending sends. Legacy history-only pending rows have no saved ciphertext
and keep their old reconstruction path; the stable-ciphertext guarantee covers
new outbox sends. Files, sealed secondary events and opt-in plaintext sending
remain outside this round. Rounds 130–134 stay closed.
