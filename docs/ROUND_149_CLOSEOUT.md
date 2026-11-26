# Round 149 closeout

Completed 2026-11-26 within `PLANNED_ROUNDS_146_150.md`.

Saved, search, replies and pins carry original conversation references into one
account/view/revision guarded coordinator. It selects only existing sidebar
conversations, resolves actual retained records and focuses their actual rows.
DM/native lookups include retained Blobs beyond the 500-row initial history load;
Pod rooms reuse the existing verified pin readers. Gateway room lookup is bounded.
Missing, deleted and unavailable originals produce an explicit unavailable notice;
bookmark snapshots never become substitute messages or guessed conversations.

Saved transactions bind to their initiating account, abort stale writes and
suppress former-account reads/actions. Imports keep the existing 500-entry bound
and strip file bytes/unrequested fields. Session teardown clears Saved/search
and cancels navigation. Offline search uses retained local history. History
rerenders restore the focused reference row, making it focusable before focus.

Validation:

- Full frontend: 2,839 passed, 64 skipped (`r149-verified-full-web.log`).
- Focus correction regression and reference/Saved/cache tests passed; ESLint,
  whitespace and cold-path checks passed. Shell cache v331 includes the coordinator.
- Real three-browser/two-gateway journey passed (`r149-navigation-browser-8.log`):
  ordinary DM and room Saved jumps from other conversations; keyboard focus
  retained after background history merges; a 600 KiB native file outside the
  initial 500-row history load restored offline through Saved and search with
  exact SHA-256 bytes; delayed Blob lookup after leaving the view suppressed;
  deleted and missing-conversation bookmarks explicitly unavailable.
- Real IndexedDB account-switch races and coordinator account/view replacement
  tests passed. The browser fixture does not simulate a second Solid login.
- No backend implementation changed. Last full backend regression remains
  4,927 passed, 15 skipped (`r148-connected-full-core.log`).

Earlier browser retries exposed menu-test timing and a real missing-tabindex
focus restoration bug; both were corrected before the passing final journey.
No full history backfill, global remote search or new storage provider was added.
Rounds 144 and 145 and existing file/custody limits remain unchanged.
