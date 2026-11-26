# Round 147 closeout

Completed 2026-11-25 within the existing reply/reaction scope.

Native attachment replies now retain authenticated, author-relative references
instead of sending private history keys. Both authors can reply to the other's
file, reload, see the correct quote and jump to its retained original. Missing
originals retain readable reply text with an explicit unavailable quote; they
never create a phantom attachment.

Native reactions use stable per-actor/original/emoji tokens and increasing
sequences. Add/remove/re-add survives reload. Source intent and local state
commit before encryption; recovery reconciles saved intents before another
sequence is assigned. Receiver state commits before protected full ACK. Older
adds cannot resurrect a newer remove. Incoming metadata can wait for a missing
original without adding a chat/file row. Pills hydrate only against an actual
original in the authenticated conversation.

Versioned commands, signed gateway relay, device-key capability gates and
recovery protect typed frames from legacy ACK/drop paths. All existing peer and
own-device targets remain in the frozen plan. Exact retries keep their send id
and ciphertext; gateway custody does not release the device's ordering hold.
Duplicate clicks coalesce. Reaction intents cannot be cancelled after optimistic
state is retained; this adds no prepared cancellation or retirement protocol.

Evidence:

- Full frontend: 2,791 passed, 64 skipped (`r147-reactions-full-web.log`).
- Full backend for the native reference protocol: 4,918 passed, 15 skipped,
  222 warnings (`r147-reference-full-core.log`). Subsequent reaction wiring
  changed frontend code only.
- Connected reaction/outbox/receive/history/rendering/service-worker checks:
  186 passed. Codec/store tests include same-UUID author collisions, foreign
  actor/thread/account rejection, failed transactions, delayed adds/removes,
  token conflicts, reload and bounded capacity without tombstone eviction.
- Real three-browser/two-gateway journey passed
  (`r147-reactions-browser.log`): both primaries reply/react to peer files;
  quote jumps/reloads; legacy mirror exact-frame retention across restart and
  upgrade; reaction add/remove/re-add with the original token; injected receiver
  state failure; dropped full ACK; exact source frames after gateway/browser
  restart; linked reaction metadata without attachment creation; later readable
  ordinary text. The preserved 145 protected-receive/SIGKILL checks and 146
  caption/restart/original-hash checks also passed in that run.
- Changed JavaScript passed ESLint; Python passed Ruff; whitespace and cold-path
  checks passed. New application imports are included in shell cache v322.

Limits: native attachment bytes still belong to their original primary caches.
Linked readers retain bounded reference metadata without receiving file bytes.
The reaction state bound is 2,000 records per scope, retaining remove tombstones
and failing closed at capacity. No new emoji vocabulary, larger ratchet/custody
limits, reset workflow, prepared cancellation or staged 144 safety protocol is
claimed. Delete/pin and shared navigation remain rounds 148–149.
