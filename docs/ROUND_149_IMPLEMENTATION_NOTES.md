# Round 149 implementation checkpoint

Status: in progress. The guarded navigation coordinator is tested but is not
connected to main.js yet. Saved account-bound storage/UI guards are connected.
Cross-conversation Saved/search/reply/pin acceptance remains incomplete. This
records the existing scope and does not add work to closed rounds.

Implemented:

- `reference-navigation.js` accepts an original conversation/message reference,
  finds an existing conversation, opens it before resolving the actual message,
  and focuses only its exact revealed element. It does not create a message from
  a bookmark snapshot. Missing/deleted/foreign targets return explicit failure.
- Navigation guards account scope, request generation, view revision and peer
  identity. Leaving and returning to the same view, replacing the account,
  superseding navigation or removing the conversation aborts stale work before
  reveal/focus. Adapters remain responsible for authenticated actual lookups.
- Saved reads/writes bind to the initiating account and complete their IndexedDB
  transaction before reporting success. An account switch during a read returns
  no former-account snapshots; a switch during removal aborts it without deleting
  either account's copy. Bookmark import strips unrequested bytes/fields and
  observes the existing 500-entry cap.
- Saved UI reads/actions retain their scope/generation. Old-account buttons and
  late reads cannot navigate or populate another account's panel. The controller
  now supports a complete navigation reference callback, preserving the existing
  id-only callback until the main coordinator is wired. Snapshots retain the
  original thread/type and bounded room target/timestamp metadata.

Still required:

- Connect the coordinator in main.js with authoritative sidebar conversation
  types and a monotonic view revision. Pass original references from Saved and
  search; replace the search interval that currently jumps after a view change.
- Load actual cached DM rows/native Blobs outside the current history window.
  Check deletion state and authenticated native authorship; never use snapshot
  text as a replacement message. Preserve keyboard focus if background history
  merges rerender the feed after navigation.
- Reuse existing Pod room pin/reference readers, verifying target/container/id,
  membership/account state and deletion. Bound any gateway message lookup/wait;
  do not guess another conversation or add history backfill/global remote search.
- Share navigation through reply/pin entry points without bypassing existing room
  adapters. Add scoped local-only offline search using existing local DM history
  and copy; preserve ordinary online search and room paths.
- Wire account/session teardown to cancel navigation and clear Saved UI. Check
  delete/unavailable bookmarks against the new guarded resolver. Run real
  cross-conversation/older-cache/offline/focus/account acceptance and final gates
  before writing a closeout. No user-visible navigation acceptance is claimed.

Evidence so far (2026-11-25): navigation coordinator and Saved boundary tests
passed, including IndexedDB account-switch races, bounded imports, original
conversation reference callbacks, missing/foreign/deleted targets, view/account
replacement and stale focus suppression. Full frontend checkpoint: 2,834 passed,
64 skipped (`r149-saved-checkpoint-final-web.log`), including the explicit Saved
load-error notice and conversation-failure change. Final focused Saved/navigation
checks: 25 passed. ESLint, whitespace and cold-path checks passed. No backend
code changed. The coordinator is not imported/cached by the app until integration;
the connected Saved changes are included in shell cache v327.
