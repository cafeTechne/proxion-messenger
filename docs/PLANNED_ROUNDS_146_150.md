# Planned rounds 146–150

These are five bounded follow-ups from the completed 142–143 acceptance audit.
Rounds 130–145 retain their existing scope and outcomes. In particular, 144
remains design-only: no prepared-source cancellation, unilateral key reset or
larger crypto skip buffer. The completed 145 receive-commit boundary is unchanged.
A component or staged adapter is not a completed round; the user-visible
acceptance cases below must work and be verified.

## Audit basis and order

The audit confirmed that native file history keys contain an actor namespace
that differs across gateways, while edit/delete/reaction/pin code sends UI keys
as wire references. The edit gateway accepts only a plain message id, so a
native history key is not a valid target. It also confirmed that Saved records
store their thread but the Jump button drops that information, and pending
transfer progress refreshes only on explicit review/refresh rather than live
state changes. These are follow-up interaction defects, not missing transfer
custody guarantees. Search/reply/pin variants require concrete reproduction as
part of their specified acceptance cases; do not invent failures if they pass.

Implement 146 first; 147–148 reuse its reference rules. Navigation (149) and
feedback (150) complete the corresponding existing controls. Each round must
finish its concrete work before its completion notes/checks are marked done.

## Round 146: Valid native attachment references and caption edits

Status: in progress.

Reference checkpoint (2026-11-22): a tested, currently unconnected reference
module separates private history keys from wire UUIDs and resolves author roles
across gateway aliases. Five checks cover collisions, foreign scope, malformed
references and ambiguous originals. Persistence and edit routing are still open;
no working native caption edit is claimed by this component.

Problem: a native attachment bubble uses `native:<actor>:<UUID>` locally, but
its edit action sends that private key to a gateway requiring a plain wire id.
Cross-gateway actor aliases also differ. Editing must target the original file
without replacing its ciphertext, immutable custody identity or history Blob.

Bounded work:

- [ ] Define one validated native message reference and separate it from local
  history/DOM ids. Persist the reference and safely resolve existing native
  history records using authenticated conversation/author context.
- [ ] Resolve and validate a native edit before ratchet preparation; never fix
  invalid references by weakening the ordinary message-id validator.
- [ ] Route authorised caption edits through both gateway endpoints and map
  incoming edits back to each primary's existing native history key.
- [ ] Keep exact edit preparation/retry and retained caption history through
  interrupted sends/reload; preserve original file bytes and ownership.
- [ ] Test author/thread/account/key changes, conflicting/foreign references,
  duplicate events and a later normal text message.

Acceptance: both primary accounts can edit their own native file caption in a
real cross-gateway conversation. Each peer sees one updated caption after
reload and the original file hash is unchanged. A non-author cannot edit it.
An interrupted edit reuses its prepared header/action identity, and a failed
reference check spends no encryption position. Existing text/room edits pass.

Exclusions: file replacement/re-upload, changed custody receipts, media fanout,
new room permissions, resets or prepared file retirement.

## Round 147: Replies and reactions on native attachments

Status: not started. Depends on 146.

Problem: reaction and reply targets currently use the same private UI ids.
A reference must identify the correct original message on each endpoint,
including when both participants choose the same wire UUID.

Bounded work:

- [ ] Apply 146's reference rules to native reply quotes and reaction add/remove.
- [ ] Bind references to the authenticated conversation and original author;
  resolve incoming references without confusing another sender/thread.
- [ ] Preserve original reaction tokens and duplicate/removal behavior through
  reload and delayed original-message/history arrival.
- [ ] Retain bounded pending reference state or fail explicitly when the
  original file is unavailable; do not create a phantom file/text bubble.
- [ ] Verify ordinary text/room reactions and replies remain functional.

Acceptance: each primary can reply to and react to the other's native file,
remove its own reaction, reload and retain the correct quote/reaction. A reply
jump reveals the correct original message. Collision/foreign-reference tests
cannot target another author's file. Repeated/delayed events do not duplicate
reactions or consume a second header for an exact retry; later text decrypts.

Exclusions: new emoji services, message forwarding redesign, media fanout and
new crypto retirement/reset protocols.

## Round 148: Native message deletion and pins

Status: not started. Depends on 146–147.

Problem: generic delete/pin commands do not distinguish a native file's local
history key from its gateway reference/provenance. Existing controls must apply
the operation to the intended delivered message and persist that result.

Bounded work:

- [ ] Authorise native delete-for-everyone using the original sender/reference;
  keep delete-for-me strictly local and preserve its account boundary.
- [ ] Retain bounded deletion evidence so delayed original events/edits cannot
  resurrect a deleted message or its local file cache.
- [ ] Make native pin/unpin work for either conversation participant, resolve
  previews locally and retain the reference across reload/offline recovery.
- [ ] Remove associated local history Blobs through the existing atomic cleanup
  rules when deletion requires it; do not infer source/gateway cancellation.
- [ ] Verify foreign-author/thread, stale events and ordinary message controls.

Acceptance: both peers can pin/unpin a delivered native file and reopen its
correct preview after reload. Only its author can delete it for everyone;
delete-for-me leaves the peer's copy. Deleted messages and file-cache references
do not return on delayed replay/reload. No plaintext filename/caption is added
to gateway storage as a shortcut. Prepared transfers remain outside this action.

Exclusions: recall guarantees, deletion of someone else's device storage,
prepared cancellation, broader room moderation and new file retention caps.

## Round 149: Saved/search/reference navigation across conversations

Status: not started.

Problem: Saved snapshots retain `thread_id`, but their Jump control passes only
a message id to the current view. Search/reference navigation also needs to
handle retained messages outside the currently rendered history window.

Bounded work:

- [ ] Make Saved Jump carry the original conversation and select it before
  revealing the message; retain keyboard focus and an explicit unavailable state.
- [ ] Share guarded navigation with search/reply/pin entry points where needed,
  reusing existing room/reference adapters rather than bypassing their checks.
- [ ] Retrieve an older retained local DM/native file when it is outside the
  current rendered/windowed history, including its bounded Blob reference.
- [ ] Abort stale navigation on account/view replacement, deleted messages or
  missing original conversations; never guess a replacement destination.
- [ ] Cover offline and normal text/room navigation alongside native files.

Acceptance: from conversation B, a Saved item for A opens A and reveals exactly
its original message with keyboard focus. An older cached native file is still
readable offline through search/reference navigation. Deleted/unavailable
references show a useful result instead of silently doing nothing or flashing
another thread's message. Account switches expose no former-account data.

Exclusions: full history backfill, global remote search, new storage providers
and guessing/inventing messages for missing references.

## Round 150: Live transfer feedback and actionable recovery

Status: not started.

Problem: Pending sends currently refreshes transfer progress only on explicit
review/refresh. Write/connection failures can leave a generic waiting state
without explaining the next supported action.

Bounded work:

- [ ] Update visible native progress/status from bounded scoped events, without
  rebuilding focused controls on every part or repeatedly reading large drafts.
- [ ] Coalesce announcements/updates; stop subscriptions on panel close,
  connection/account change and teardown.
- [ ] Correlate transfer refusal/rate/storage/key/history failures to the
  original active transfer and display an accurate recoverable state.
- [ ] Link to existing history/storage preferences or the original conversation
  where useful; preserve source-only cancellation and distinct decline semantics.
- [ ] Verify progress/retry/decline with keyboard and storage/connection faults,
  and rerun the preserved end-to-end transfer/fanout gates at final closeout.

Acceptance: a visible pending transfer updates as parts commit and survives
reconnect without losing focused controls or displaying another account's state.
History/quota/rate/key failures explain a supported next action and retain the
right bytes/journal. Retry resumes the same transfer; decline never becomes a
full delivery receipt. No prepared cancellation/reset or file-cap increase is
introduced. All five rounds receive accurate evidence-based closeout notes.

Exclusions: deleting prepared sources to reclaim quota, automatic trust/key
reset, broader notification redesign and new file/receipt/rate limits.
