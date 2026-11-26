# Functional MVP gap audit — rounds 151–155

Audit date: 2026-11-26. Source baseline: `9e5b420`.
Status: planning audit, not a new browser acceptance run or deployed-site audit.

The baseline is substantial: rounds 142–143 and 146–150 have completed scoped
acceptance, including a real three-browser/two-gateway journey, 2,849 frontend
and 4,929 backend tests. Round 130 repaired the original approval, persistence
and friendship flows. Historical TODO lists do not establish that those defects
have returned. Round 144 remains a completed design-only safety outcome; round
145's receive-commit protocol remains unchanged.

## Findings and evidence

| Finding | Evidence and confidence | MVP consequence / assigned round |
| --- | --- | --- |
| Acceptance execution has outgrown its default budget | `web/scripts/run-smokes.mjs:46` gives each script 10 minutes. The completed combined native journey took roughly 12–13 minutes locally. CI also places all UI/cross-gateway scripts in one 30-minute job. This is a measured local budget mismatch, not evidence that the latest GitHub run failed. | A passing standalone journey is insufficient evidence that the required aggregate can finish. Partition/budget it without losing crash coverage. **151**. |
| Required coverage is uneven across user journeys | `pod-required`/`pod-acp` require real room/media/friend checks, but `smoke_web_session.mjs`, `smoke_web_signedin.mjs` and `smoke_cross_mode_call.mjs` are only in the broader optional pod group. Gateway call checks already exist in cross-gateway. | Explicitly require the supported MVP sign-in/session and call paths; missing prerequisites remain failures for required jobs. **151, 153, 155**. |
| Forward does not dispatch by the actual source/destination protocol | `web/modals.js:50–106` recognizes only the pod DM asset prefix as private media. Other sources send `forward_message` with the displayed content and local id. `main.js` exposes Forward for native files; `_gateway_rooms.py` looks up that id in the gateway message store, while native history ids are `native:<actor>:<UUID>`. `podtransport.js` has no `forward_message` route. These are confirmed source-level mismatches; exact UI failures still need browser reproduction. | Native file Forward cannot copy its bytes through this handler; pod text forwarding has no corresponding raw command handler. Explicit room sharing can be intentional, but private destinations must never receive a plaintext shortcut. Use existing client send/media adapters and gate unsupported combinations. **152**. |
| The generic forwarding picker is not an encrypted DM picker | `forwardTargets()` enumerates `[data-room-id]`; only the pod-private-media path uses DM targets. The generic callback captures a socket/id but does not bind an original account/view/message record for its delayed action. | Offer only supported destinations with clear room/private labels, preserve the actual original source, and reject stale picker actions. This is an existing-control repair, not broad forwarding interoperability. **152**. |
| Notification client selection ignores app scope | `web/sw.js:364` picks the first window whose URL contains the origin. A landing page or another app on the same host can qualify, irrespective of this service worker's app path. This selection defect is visible in source; browser impact needs a multi-tab test. | A notification can focus a page which cannot handle the navigation message. Select an appropriate app client within its actual scope. **154**. |
| Cold notification navigation is time-based and lacks a scoped pending intent | `main.js:9483–9493` retries `?thread=` navigation about 10 seconds, then stops; this consumer does not remove the parameter. The warm message contains only a thread id and navigates immediately. | Slow sign-in/sidebar restore can drop the click; later reload can reuse an old target. Add lifecycle-bound navigation and explicit unavailable behavior, including old-account notifications. **154**. |
| Core two-user access is implemented, but needs a fresh integrated MVP check | Existing friends and room smokes cover persisted requests, live approval and reload. Some older join tests explicitly call `drainOnce`; newer journeys exercise automatic discovery. No new invitation or membership defect was reproduced in this audit. | Verify the user's original browser/browser workflow without forced drains or re-login, plus returning-session and denied-write cases. Fix only reproduced regressions. **153**. |
| Calls have coverage, but failure/device/platform evidence is incomplete | `smoke_call.mjs` covers real tracks, verified peers, decline, cancellation, reload and offline peers. This audit did not run permission denial, missing-device, screen-track end or physical mobile/desktop WebView checks. Cross-mode call smoke is not required by the current pod CI groups. | Validate lifecycle and supported device failures, and distinguish automated fake-media evidence from real hardware/NAT testing. Do not label all calls broken or universally verified. **155**. |
| Public capability/history claims need a current release reference | README says everything lives in the pod; native history/drafts can be device-local and native attachment bytes remain primary-only. `DM_RELIABILITY_ROADMAP.md:57` still says 142–145 are planned. Older beta-gap notes are explicitly historical. | Create one current supported-feature/storage matrix and reconcile current entry-point claims without expanding old rounds. **151, final 155 signoff**. |

## MVP boundary

Primary user acceptance is two people using the browser website. Retain gateway
primary and existing desktop/pod interoperability as separate tested modes; a
website served by a gateway is not the same protocol as the gateway-free pod
website. A gateway browser run is not a packaged desktop/WebView test.

The MVP includes sign-in/return, invitations and approvals, persistent rooms,
friends and direct text messaging, supported message controls, files/voice notes,
history/reference navigation, honest pending/retry states, notification entry
points and supported one-to-one calls. Unsupported protocol combinations must
not appear as working controls. The existing room call path must also keep its
required gateway regression gate; new room-call parity is excluded.

Retained limits are not new bugs to "fix" by removing protections: no prepared
source cancellation/reset, no larger MAX_SKIP or file/queue limits, no native
attachment byte fanout, no inferred orphan deletion, no claim that CSS ACP tests
certify every external Solid provider, and no guarantee of closed-app push
without supported infrastructure/permission.

## Definition of MVP-ready

- Zero unresolved P0/P1 defects in the agreed supported core journeys. P0 means
  data loss/exposure or broken account isolation; P1 means a core flow cannot
  complete or recover through its supported UI. Lower-severity issues are listed.
- Every exposed core action has a working supported path or is deliberately
  unavailable before an operation starts. Passing isolated helpers is not enough.
- A fresh pair of accounts can join, approve, exchange DMs/room messages and
  attachments, reload/reconnect and continue without manual inbox-drain hooks.
- Applicable failure/retry checks retain the correct account, original destination,
  bytes and delivery evidence. No placeholder is counted as successful delivery.
- Required unit and real-browser jobs complete within measured budgets; required
  skips and partial runs are not success. Publish the mode/provider/platform,
  exact revision and outstanding manual checks alongside the results.
- Smoke tests have isolated fixtures and retain the end-of-suite history,
  revocation and policy assertions that caught the last fixture pollution issue.

This means a verified supported MVP with no known blocking defects, not a proof
that no bug exists on every provider, browser, device or network. See
`PLANNED_ROUNDS_151_155.md` for the five bounded implementation plans.
