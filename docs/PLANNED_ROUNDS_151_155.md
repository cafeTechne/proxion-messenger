# Planned rounds 151–155: functional MVP

Created 2026-11-26 from `MVP_GAP_AUDIT_151_155.md`, baseline `9e5b420`.
All five rounds are **planned, not implemented or verified**. Planning does not
reopen or enlarge rounds 130–150. Execute 151 first, then 152–154, then 155's
call checks and final signoff. Freeze new feature scope during this sequence.

The objective is zero known blocking defects in the supported MVP journeys,
with honest capability boundaries. The primary acceptance pair uses the browser
website. Verify gateway-free pod and gateway-backed conversations separately;
retain the existing mixed-mode and linked-device protections. Do not substitute
a gateway-served Chrome run for real Solid login or a packaged desktop check.

## Round 151 — Dependable MVP gates and an honest supported surface

Status: planned. Priority: release prerequisite.

Problem: the growing native browser script exceeds its default 10-minute runner
budget locally. Required CI coverage omits some existing session/call journeys,
and capability/history claims are spread across stale and current documents.

Bounded work:

- [ ] Inventory exposed core controls by conversation mode, role and device;
  publish a current MVP capability/storage matrix, using closed-round evidence.
  Label verification gaps separately from intentional protocol limits.
- [ ] Measure the existing runner path and partition the long native suite into
  bounded required tiers/jobs with explicit measured budgets. Preserve one
  combined integration gate and every original crash/replay/history assertion;
  document which tier owns each case so splitting cannot omit it.
- [ ] Isolate fixtures and guarantee browser/gateway cleanup when a tier fails or
  times out. Retain failure logs and exact phase names. Do not turn required
  prerequisites, timeouts or partial-tier skips into passes.
- [ ] Add required MVP sign-in/session/call coverage to appropriate bounded jobs
  or clearly assign the call gate to 155; provision the existing local CSS/Chrome
  prerequisites instead of relying on an optional local run.
- [ ] Reconcile current README/reliability-roadmap claims with the new matrix.
  Gate only demonstrably unsupported/broken combinations pending their fix;
  do not remove required core features merely to declare MVP success.

Acceptance: the documented required command/job set completes within its actual
budgets, reports missing prerequisites as failures, and maps every 142–150 crash
case to an executed gate. The supported UI/storage matrix reflects the actual
app and distinguishes device-local history from pod storage and primary-only
native attachments. Record all reproduced blockers for rounds 152–155.

Validation: runner classification/timeout/cleanup tests, shell/capability checks,
real required runner execution and retained logs; baseline unit suites as needed.
Likely areas: `web/scripts/run-smokes.mjs`, `smoke_multidevice_dm.mjs`, its journey
helpers, `smoke-runner.test.js`, CI workflows, transport gating and current docs.
Excludes: new messaging protocols, broad UI redesign, weaker tests/limits,
provider certification, automatic release publication.

## Round 152 — Make exposed Forward controls work safely

Status: planned. Depends on 151's supported-source/destination matrix.

Problem: generic Forward uses a gateway message lookup for native local ids and
has no pod-text command route. It cannot forward native attachment bytes this
way, and its room picker is not a general encrypted DM destination picker.

Bounded work:

- [ ] Reproduce the actual text/native-file/pod-media picker behavior and record
  supported source/destination pairs before changing dispatch.
- [ ] Bind the original source record, account, view/session and destination at
  selection. Reject deleted/unavailable/unverified sources and stale picker work.
- [ ] Forward text through existing destination send adapters and durable send
  paths where applicable. Private destinations retain E2E; an explicitly selected
  shared room is clearly identified as a room. Never reuse source ciphertext or
  let an absent local record fall back to unrelated gateway content.
- [ ] For native DM files/voice, create a fresh copy through the existing native
  media sender to supported native DM destinations: new id/key/header and exact
  verified file bytes. Preserve the existing pod-private-media copy path. Gate
  other combinations unless their existing adapter is demonstrably sufficient.
- [ ] Show pending/error/success from actual destination outcomes, keep retries
  bound to the copy, prevent double activation and preserve normal room Forward.

Acceptance: a user forwards supported text and a native attachment using actual
controls; the destination gets one readable copy after retry/reload and the
original is unchanged. Private sends do not use a plaintext gateway shortcut.
Unsupported combinations are unavailable before sending, not broken after click.
A switched account/view, deleted source or lost acknowledgement cannot send to
another destination or generate a second header for the same retry.

Validation: dispatch/reference/account and exact-retry tests; real two-party
browser text/file/voice forwarding with failed send, reload/retry and byte hashes;
existing pod-media forwarding and normal room controls remain green.
Likely areas: `modals.js`, `main.js`, existing text/media outboxes and adapters,
`native-message-ref.js`, renderer/menu gating, gateway Forward compatibility.
Excludes: cross-protocol asset migration, new media fanout, forwarding original
private keys/author claims, recalls or prepared retirement/reset.

## Round 153 — Complete the returning-user room/friend/DM journey

Status: planned. Depends on 151. Existing implementations are the starting point.

Problem: approval and persistence were repaired, but the MVP must pass the actual
website workflow as a whole after auth delay, idle time and return. No recurrence
of the original invitation bug has been established by this planning audit.

Bounded work:

- [ ] Run fresh browser/browser sign-in, owned-room restore, invite link, live
  pending approval, accepted membership, friends and room-member-to-DM entry.
  Use real auth/ACL storage and automatic subscription/poll fallback; no forced
  drains, injected membership success or extra re-login to trigger visibility.
- [ ] Cover existing-tab invite handoff, sign-in-required invitation carry-through,
  owner/joiner reload, offline return, failed membership write and later retry.
- [ ] Verify notification/pending request discovery within a documented normal
  polling bound, with subscription failure, idle tabs and duplicate requests.
- [ ] Verify private friend/request state, account A→B replacement and sign-out;
  wrong-account delayed work must not display or mutate the former account.
- [ ] Fix only concrete failures found in this bounded funnel. Keep explicit
  retryable stages until actual membership/ACL/intent writes complete.

Acceptance: two browser users can create/invite/approve, see the same room,
exchange room messages, add each other as friends and open a DM from the room.
Both reload and continue. Requests appear automatically, failures remain
retryable, and signing into another account exposes no former rooms/private data.
Existing removed-member/closed-room denial and WAC/ACP behavior remain intact.

Validation: required real local-CSS OIDC browser journeys for supported WAC/ACP
cases, account-race tests and existing friendship/room lifecycle suites. Reuse
current strong journeys rather than replacing them with a happy-path demo.
Likely areas: auth/session lifecycle, webjoin/room-approvals, webrooms/crossrooms,
webfriends/member entry points, tablock and session/room/friend smoke helpers.
Excludes: redesigning identity, new providers, room permission expansion,
new friendship features, broader room administration or reopening round 130.

## Round 154 — Reliable notification and cold/warm navigation

Status: planned. Depends on 151 and 153's session readiness contract.

Problem: the service worker can focus a non-app same-origin page; cold `?thread=`
navigation silently expires after about 10 seconds and is not consumed here.
Warm notification navigation is also separate from guarded restore/readiness.

Bounded work:

- [ ] Reproduce landing-page/app multi-tab, installed/subpath PWA, warm message
  and cold notification entry. Select only an eligible app client within the
  actual registration/app scope; otherwise open the correct app URL.
- [ ] Bind a content-free navigation intent to the applicable account/session or
  safely refuse ambiguous old-account notifications. Wait for actual auth/sidebar
  readiness with bounded lifetime and explicit expiry/unavailable UI.
- [ ] Consume handled URL intents so reload does not repeat an old navigation.
  Reuse guarded conversation selection; never invent a thread or silently open
  a same-id conversation belonging to a replacement account.
- [ ] Validate supported message/invite/missed-call notification actions against
  actual product destinations. Prevent duplicate navigation and stale callbacks.
- [ ] Preserve permission-denied, unavailable-service-worker and gateway-free
  foreground-only behavior. Settings must distinguish supported capability from
  registration success; do not imply closed-app push works without its backend.

Acceptance: notification clicks reach the intended existing conversation or the
appropriate request/call review in the right app, after slow sign-in as well as
warm boot. A landing page is not selected; handled links do not replay on reload;
removed threads and wrong-account notifications have an explicit safe outcome.
Permission denial remains usable and message/invite content stays private.

Validation: service-worker client selection and intent lifecycle tests; actual
service worker/subpath browser navigation, slow sidebar/auth, account replacement,
missing threads and no-permission checks. Distinguish injected PushEvent/click
coverage from real external Web Push delivery and physical OS notification tests.
Likely areas: `sw.js`, `main.js` push/deep-link consumers, `notifications.js`,
`push.js`, tablock/readiness and existing navigation/notification tests.
Excludes: new push providers, guaranteed OS background delivery, remote thread
backfill, notification redesign or weakening account checks.

## Round 155 — Call lifecycle, device failures and MVP signoff

Status: planned. Depends on 151–154; final signoff also depends on their closeouts.

Problem: gateway call media/lifecycle checks already exist, but a working chat MVP
needs verified supported call failures and honest hardware/network evidence.
This audit has not reproduced a new call defect.

Bounded work:

- [ ] Run existing gateway and browser/cross-mode one-to-one call journeys with
  real remote tracks and verified identities; wire their supported paths into
  required bounded jobs. Retain the existing gateway room-voice regression.
- [ ] Exercise microphone/camera permission denial, absent/busy media device,
  preview cancellation, decline, peer reload/disconnect and subsequent redial.
  UI must leave ringing/connecting and release tracks/timers on both ends.
- [ ] Verify supported camera/mute/device changes and screen-share end/stop;
  context/account loss cannot leave recording tracks or a stale call overlay.
  Fix only reproduced existing-feature failures in this lifecycle.
- [ ] Record physical browser/mobile and packaged-desktop checks separately
  where claimed; fake media and a local WebRTC link do not prove remote NAT,
  TURN reachability, OS permissions or every platform. Missing evidence remains
  explicit; it is not converted to a pass or a new relay implementation task.
- [ ] Run the 151 required gate set against the final revision, including 152–154
  cases and preserved 142–150 recovery/policy gates. Reconcile the MVP matrix,
  release-facing limitations and a zero-open-P0/P1 core-journey defect ledger.

Acceptance: supported peers can establish a call, see/hear actual received media,
end/cancel/decline, recover from the tested faults and call again without stuck
UI or live orphan tracks. The final supported MVP journeys are green on the
recorded revision; all five closeouts name actual evidence and outstanding
platform/provider checks. No release-ready claim relies on a skipped required
case or an unresolved core-flow blocker.

Validation: targeted call lifecycle/permission tests; required real browser call
and existing room-voice smokes, explicit manual hardware matrix, appropriate full
unit suites, required WAC/ACP/core journeys and bounded native/fanout crash gates.
Likely areas: voice/call signaling, webcalls/cross-mode routing, media preview and
in-call controls, call/room-voice smokes, CI and the current MVP capability matrix.
Excludes: new call types, new TURN infrastructure, universal NAT/OS guarantees,
prepared cancellation/reset, larger limits, native media fanout or publication.

## Completion discipline

A round closes after its user-visible acceptance passes, not after an adapter or
unit test is added. Keep implementation checkpoints distinct from completed
closeouts. Record discovered failures, fixes, exact revision/commands, skip
reasons and retained limits. New requests or unrelated discoveries go into a
separate future backlog; they do not silently enlarge these five rounds.

Do not publish a release or push on the user's behalf as part of these plans.
The user is handling the current push. This planning batch starts no application
implementation and makes no claim about the live deployment or current CI result.
