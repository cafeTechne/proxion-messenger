# Round 148 closeout

Completed 2026-11-25 within the existing native deletion/pin scope.

Native delete-for-everyone now authorises the original attachment's author using
an authenticated conversation and author-relative UUID reference. Delete-for-me
uses a separate local-only operation. History v3 stores bounded deletion evidence
in the same transaction that removes the message and its Blob. Failed writes
roll back all three. Delayed originals, chunk finalisation, caption updates and
late quote lookups cannot restore deleted file/history data.

Native pins work for either participant and appear alongside ordinary server
pins. Retained previews load local metadata; Jump loads the actual cached file,
including offline. A missing original displays an unavailable result without a
phantom message. A linked reader can unpin its authenticated retained reference
after signed parent preflight, without receiving attachment bytes. Clock/action-id
ordering converges across browser/gateway actor aliases; deletion dominates pin
replay. Ordinary pin notifications refresh the server portion of the mixed list.

Delete/pin uses a distinct capability and wire/proof version inside the bounded
reference transport. Reply-only devices cannot drain or acknowledge these frames.
The durable outbox binds action, actor, conversation and frozen recipients before
encryption. Local state commits first, and only the matching full consumption
proof releases the affected device hold. Deleting a history row does not cancel
its own action journal or an earlier validated caption intent. Prepared caption
and delete headers remain exact across restart and complete against the retained
tombstone without restoring the original.

Evidence:

- Full frontend: 2,820 passed, 64 skipped
  (`r148-connected-final-web.log`). Changed production JavaScript passed ESLint;
  whitespace and cold-path checks passed. Application imports are cached in
  shell v326.
- Full backend: 4,927 passed, 15 skipped, 221 warnings
  (`r148-connected-full-core.log`). This covers the complete separate capability,
  signed relay, recovery, full proof and ordinary-ingress bypass guards.
- Focused connected control/outbox/pin/quote/caption checks: 205 passed, with an
  additional ordinary-pin refresh regression. Persistence checks cover both
  authors sharing a UUID, foreign actors/threads/accounts, atomic rollback,
  Blob-save/delete races, delayed captions, capacity without tombstone eviction,
  disabled-history local delete and v2 migration.
- Real three-browser/two-gateway acceptance passed
  (`r148-action-focused-browser-4.log`): ordinary/native pin coexistence, both
  participants' pin/unpin, reply-only mirror exact-frame retention through
  source restart and upgrade, unavailable linked preview and retained-reference
  unpin, reload/offline preview and jump, non-author delete exclusion,
  local-only deletion preserving the peer's Blob, author deletion of both
  participants' files, history/Blob cleanup, lost full delete ACK, both gateway
  restarts, exact frozen delete and caption headers, delayed original replay
  through both persistence entry points, and readable later ordinary text.
- The preserved caption, reply and reaction journeys passed earlier in the
  integration run. Round 150 retains the final combined end-to-end gate.

Limits: deletion is an interpreted conversation action, not a remote recall or
prepared source cancellation guarantee. Attachment bytes still belong to their
original primary caches. State is capped at 2,000 entries per account database;
ordinary history eviction retains replay evidence, and capacity fails closed.
No new file/ratchet/custody caps, key reset, retirement protocol, broader room
moderation or attachment-byte fanout is introduced. Round 144's staged safety
outcome and round 145's ordinary receive protocol remain unchanged. Shared
Saved/search/reference navigation remains round 149.
