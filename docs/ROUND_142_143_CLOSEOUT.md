# Rounds 142–143 acceptance audit

Status: complete for the original bounded delivery/recovery scope of both
rounds. All acceptance checkpoints below have verified evidence (2026-11-22).

This audit covers the original bounded work in `PLANNED_ROUNDS_141_145.md`.
It does not claim that every message action or every application feature works.

## Requirement-to-evidence matrix

| Requirement | Implementation and evidence |
| --- | --- |
| 142: audit native file, GIF and voice routes; distinguish echo from delivery | `main.js` routes keyed attachments and microphone recordings through `native-media.js`; the real GIF tray calls that same attachment entry point. `nativeGifJourney` sends through the actual keyboard control; `auditNativeVoiceHistory` checks one identical, non-empty WebM per primary after repeated reloads. `test_native_media_custody.py` and the native contracts distinguish custody, header consumption and full file/history commit. |
| 142: bounded scoped drafts, frozen original bytes/id/destination/header | `native-media-drafts.js` persists source → prepared → sealed transitions with revision checks, original peer key and stable send id. Ten drafts and a 32 MiB ciphertext reservation bound each scope; no admission eviction. Draft, sender and account/generation tests cover concurrent limits, failed writes, changed keys and exact reopening. |
| 142: atomic ratchet preparation and exact retry | `e2e-secondary.js` validates prepared AES bytes/metadata before sealing; the shared ratchet journals a receive-held send. Native preparation/send/receive tests and `e2e.test.js` cover interrupted sealing, exact journal replay and typed pending-header refusal. The live inline crash journey compares original ciphertext and header. MAX_SKIP remains 20. |
| 142: stable gateway identity and exact deduplication | Native custody stores bind authenticated sender, original recipient/thread, client UUID, media/header/ciphertext identity. Conflicts fail closed. Schema 71 binds adapted chunk manifests separately, preserving an accepted inline envelope's queue/receipt identity. Store/gateway migration tests and `nativeInlineAdaptationJourney` verify the accepted 400 KiB case through a real restart. |
| 142: retain sources through required history and policy failures | Sender cleanup requires exact header/full progress, retained own history and idempotent ratchet completion. Quota and cleanup failure tests keep sources. Block/revocation now stop both pending and forwarded-unfinished rows while preserving prior committed/declined facts. Source account/key changes cannot retarget or cancel prepared bytes. |
| 142: honest accessible pending/retry and legacy behavior | Pending sends displays scoped states, labelled progress, retry, distinct recipient decline and source-only cancellation. Real keyboard retry/decline and sidebar-block journeys exercise controls. Strict version/preflight checks keep an unsupported source unspent; legacy live delivery retains its explicit reload warning. |
| 143: inventory old chunk states, limits and acknowledgement meaning | The transfer contract documents active legacy transfer versus durable custody/header/history boundaries. Legacy file-transfer integrity suites remain in the full regressions. The new path neither upgrades an old socket write into a durable receipt nor routes native data into pods. |
| 143: persistent immutable manifests, ciphertext parts and resume | `native-media-inbox.js`, chunk send/receive controllers and gateway `native_media_parts` persist exact bounded bytes. Gateway admission reserves declared capacity; 64 KiB part writes reject conflicting identity/length/hash and verify the complete ordered SHA-256 before complete custody. Missing reads/pull windows are bounded. Store and real HTTP tests include 2 MiB and 25 MiB ciphertext. |
| 143: authenticated negotiation, deduplication and finite reconciliation | Preflight binds an online opted-in original primary key. Auth-challenge tests preserve strict version/public-key fields through real signatures; owner/linked/foreign-session tests deny inappropriate operations. Signed gateway envelopes bind the full request digest and exact original identities. Lost/foreign/unsigned replies cannot release custody. Native part/fetch commands use the existing file rate budget, not the general pacer or reconnect queue. |
| 143: durable header/history/receipt finalization | Header identity excludes file-body corruption; full receive identity binds header plus ciphertext SHA-256. The encrypted ratchet journal retains only key metadata. File history v2 atomically saves a bounded Blob/reference, verifies retained history after retention, hydrates on reload and clears associated bytes on deletion/expiry. Protected full/decline receipts survive lost replies; reconciliation rotates bounded batches. Late part completion cannot undo decline. |
| 143: policy, capacity, context, decline and legacy cases | Native store/gateway/controller suites cover full stores, duplicate/unordered parts, wrong account/key/manifest/token, stale generations, linked roles, source/recipient block/revocation, account purge and original history preferences. Decline/full commit have one atomic terminal winner; decline clears recipient custody without claiming full delivery or deleting the prepared source. `nativeDeclineJourney` tests the keyboard control through lost requests and both gateway/browser restarts. |
| 143: accessible progress/resume/failure controls | Pending sends exposes upload/download progress, explicit retry and negotiated recipient decline, including durable incoming admissions before header completion. Prepared cancellation is absent. Storage failure, changed-key/account and legacy states remain distinct. UI tests validate progress bounds, labels, keyboard controls and stale-account actions. |

## Crash checkpoints

`smoke_multidevice_dm.mjs` runs real primary browser contexts, two HTTP/WebSocket
gateways and a linked device. The larger-file journey covers 2 MiB plaintext and
maximum-size ciphertext: 25 MiB, with 16 bytes reserved for the existing GCM tag.
Its assertions retain the original ciphertext/header, compare readable file
hashes and require one history record per primary.

| Boundary | Evidence |
| --- | --- |
| Source preparation before/after sealing | Inline journey aborts the sealed-draft write after ratchet preparation, reloads and uses keyboard retry with the exact saved ciphertext/header. |
| Partial source upload | Larger-file journey restarts source/gateway after three durable upload parts and refuses resending the accepted prefix. |
| Durable offer/header with zero downloaded parts | Both gateways/browsers restart and the exact zero-part admission survives, for 2 MiB and maximum-size files. |
| Partial recipient download | Restart recipient/gateway after eight durable parts, resume only missing parts and decrypt later text after the header hold releases. |
| Complete ciphertext before history can commit | Both gateways/browsers restart while Blob history fails; complete ciphertext survives for finalization, for 2 MiB and maximum-size files. |
| Saved history/protected receipt before final ACK | Lose full receive commits, restart both endpoints and confirm one retained readable file plus exact final source cleanup. |
| Explicit decline | Lose the decline request, restart both gateways/browsers, confirm protected decline evidence, no delivered file, unchanged retained source and later text decryptability. |
| Block while upload is partial | Real sidebar block clears partial gateway bytes, survives restart, retains the exact spent source and offers no unsafe cancellation or upload retry. |

## Validation and limits

Latest completed backend gate: **4,852 passed, 15 skipped**, including forwarded
policy cleanup. Latest completed frontend gate: **2,702 passed, 64 skipped**.
The real browser route/policy run passed GIF, voice byte equality, accepted-inline
migration, large-file recovery, decline, block and all preserved device-fanout
fault cases. The strengthened offer-only and complete-ciphertext/pre-history checkpoints also passed for both sizes; the complete browser run exited successfully with no uncaught page errors.
Ruff, ESLint, cold-path, translation and whitespace gates also apply at closeout.

The live harness uses local gateways and test authentication mode; strict
signature-challenge registration and authorization are separately covered by
backend conformance tests. This is not a claim of a production TLS/Pod-provider
integration test. Skipped optional integrations are not counted as evidence.

The supported durable media path remains primary-only with local history enabled.
It does not add media fanout, larger file/frame/skip limits, new codecs, room-file
redesign or prepared cancellation/reset. Round 144 stays design-only; Round 145's
device receive-commit protocol and its real crash/mirror/revocation checks remain
preserved. Permanently lost keys/devices and intentionally cleared local storage
are not recoverable by this protocol.

## Follow-up feature finding

Native history uses private keys shaped `native:<actor>:<wire UUID>`; the actor
alias differs across gateway endpoints. Generic edit/delete/reaction/pin/reply
code currently passes UI keys as message references. For example,
`_handle_edit_local_message` validates a plain id (`[\w.-]{1,128}`), so these
native UI keys cannot be valid edit targets. Wire identity, local history identity
and message-action routing require separate bounded follow-up work. That finding
does not change these rounds' delivery/recovery scope or claim those actions
complete.


Reproducible primary gates (with the repository Python/Node dependencies and a
Chrome/Chromium binary available):

```sh
(cd proxion-messenger-core && python -m pytest -q)
(cd web && node node_modules/vitest/vitest.mjs run)
(cd web && node smoke_multidevice_dm.mjs)
python scripts/check_cold_paths.py
(cd web && node scripts/i18n_check.mjs)
```

The browser run includes the source/recipient/gateway checkpoints, real GIF tray,
voice byte audit, accepted-inline upgrade, keyboard decline and sidebar block.
These are checked outcomes, not inferred from a successful socket write.
