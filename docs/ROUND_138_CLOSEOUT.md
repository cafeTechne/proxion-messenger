# Round 138: pending-send review and safe controls

Status: complete. Rounds 130–137 remain closed.

Settings now has a Pending direct messages section for this account/device's
saved sends and legacy uncertain rows. It shows destination, localized date,
connection/information waits, uncertain confirmation, finishing cleanup, failures
and changed recipient plans. Retry uses the existing job and prepared ciphertext.
Names are escaped, buttons work with the keyboard, status changes use a polite
live region, and account/session changes clear or invalidate the view.

Stop unsent message is offered only before preparation. Both inspection and the
serialized cancellation action check every per-recipient send journal: a crash
after spending a key but before saving frames still prevents cancellation. No
ratchet is rolled back, no prepared send is recalled, and skip bounds are
unchanged. Offline intents remain unencrypted when connectivity is known to be
unavailable. Queue admission is serialized by account/session to enforce the
128-intent limit under concurrent submissions; failures preserve composer text
and show a useful message without restoring text into another account.

Single-recipient intents now freeze the peer key too. Device rosters refresh
while recovering device-addressed jobs. Missing information waits; a removed or
changed frozen target requires attention rather than silently retargeting the
job. New devices are never added to saved plans.

Legacy history-only rows no longer automatically re-encrypt with their old id.
Review requires consent and an empty composer in the original conversation;
pressing Send creates a new id using the normal path. The prompt warns of
possible prior delivery and duplication. Reconnection cannot trigger these
manual reviews or overwrite an existing draft.

Validation: 2,541 frontend checks passed with 64 optional skips; four additional
composition-root recovery tests passed after that run, for 2,545 verified checks.
Focused control tests cover spent keys without frames, prepare/cancel races,
queue capacity, frozen device changes, account replacement and manual-only
legacy recovery. ESLint, whitespace and i18n checks pass. New strings use the
established English fallback for incomplete draft locales; pseudo-locale is
regenerated (the i18n gate reports 15 existing draft-locale warnings).

The live browser DM journey and the three-device gateway journey both pass,
including pending-list review after reload, keyboard Retry, exact-ciphertext
recovery with lost confirmation, one received history row, pairing, self-sync,
revocation and durable offline fanout through SIGKILL/restart. Shell cache: v301.

Prepared cancellation remains deliberately unavailable. A changed prepared plan
stays saved; a deliberate new send uses a new id and may duplicate prior delivery.
Full safe retirement/reset of prepared ratchets needs its own protocol design.
This round does not change the gateway/client acceptance boundary or make
sender mirrors independently durable; that remains Round 139.
