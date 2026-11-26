# Round 150 closeout

Status: complete (2026-11-26), within `PLANNED_ROUNDS_146_150.md`.

## Delivered within the bounded plan

Pending sends consumes account/connection-scoped native state events. A bounded
50-transfer queue coalesces updates every 300 ms. Known-row progress patches do
not re-inspect large drafts; row/control identity survives progress and reconnect.
Unchanged live text/attributes are not rewritten. When a completed row disappears,
keyboard focus moves to Refresh. Disconnected actions use aria-disabled plus an
action guard so their focused controls remain available to keyboard readers.

Panel close, Escape, account/session replacement, connection stop and page
teardown cancel timers/subscriptions. Connection-generation changes rebind work
without clearing the same account's controls. Actual account replacement clears
the rows, and native feedback caches cannot cross accounts even when local
transfer ids coincide. Observers disconnect with the panel subscription.

Refusal/rate/storage feedback matches an outstanding original transfer, command
and part before updating it. Gateway storage safety and SQLite failures return
bounded correlation metadata without acknowledging custody or delivery. Retry
keeps the existing ciphertext/header/id and the existing deadline behavior.
History-disabled and failed history/storage writes have distinct feedback; the
existing history preference is reachable by keyboard. Bytes and receive journals
remain until the original protocol permits cleanup. Key changes remain a retained,
unavailable-original-key state; no reset or retargeting is offered.

Source-only cancellation and distinct recipient decline are preserved. No
prepared retirement, larger limits, new provider or media fanout was added.
MAX_SKIP remains 20. Rounds 144 and 145 retain their existing safety/protocol
outcomes; closed rounds' scope is unchanged.

## Validation

- Full frontend: **2,849 passed, 64 skipped** (`r150-accepted-web.log`).
- Full backend: **4,929 passed, 15 skipped** (`r150-closeout-core.log`).
- Focused coverage includes coalescing without repeated draft inspection,
  stale-account and connection-generation races, Escape teardown, colliding
  account-local transfer ids, phase/part error correlation, SQLite write failure,
  original inline attempt binding and retained history-failure journals.
- Focused real browser acceptance passed (`r150-feedback-browser-4.log`):
  committed-part progress, an injected authenticated-socket rate error, actual
  gateway interruption/reconnect, the same focused Retry control, identical
  prepared source/header, failed receiver history, preferences and successful
  retry. Rate and storage error generation are also covered by gateway tests.
- Final combined real three-browser/two-gateway acceptance: **passed, exit 0**
  (`r150-accepted-combined-browser.log`). It covers native caption edits,
  replies, reactions, deletion/pins, Saved/search/older offline Blob navigation,
  live feedback/focus/reconnect, inline adaptation, GIF and voice byte retention,
  2 MiB and maximum-size transfer restart/history/lost-ACK recovery, distinct
  decline, independent mirror recovery, offline device fanout, revocation and
  sidebar block cleanup. The original baseline history assertion also passes.
- ESLint, whitespace and cold-path checks passed. Locale checks passed with 15
  translation-completeness warnings; existing locale fallback remains. Shell
  cache v334 includes the changed production modules and copy.

The first combined browser run passed the native feature journeys, both large
transfer sizes and distinct decline, but a later baseline-history assertion
exposed test pollution. The navigation journey's 550 synthetic cache rows kept
the original baseline message outside the initial 500-row load. The journey now
removes only those fixture rows after proving older/offline navigation. Neither
the history limit nor the baseline assertion was weakened.

Uncorrelated gateway errors are not guessed onto another transfer. Older gateways
without the correlation metadata cannot provide these per-transfer error details.
External Solid sign-in/provider services are not exercised by the native local-
gateway browser fixture; account boundaries also have unit/IndexedDB race coverage.

The preceding bounded rounds have separate closeouts: `ROUND_146_CLOSEOUT.md`,
`ROUND_147_CLOSEOUT.md`, `ROUND_148_CLOSEOUT.md` and `ROUND_149_CLOSEOUT.md`.
