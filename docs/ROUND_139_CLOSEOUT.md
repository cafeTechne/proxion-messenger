# Round 139: independent sender-device synchronization

Status: complete. Rounds 130–138 remain closed.

Outgoing jobs now persist acceptance per frozen target. Peer delivery becomes
confirmed only when all intended peer targets are accepted; own-device evidence
cannot confirm a peer. Unaccepted mirrors remain saved after peer confirmation.
Retries filter out accepted targets and preserve each remaining encrypted payload,
logical id and conversation mapping. Retrying mirrors never rewrites peer history
as pending or resends an accepted primary. Partial fanout acknowledgements
accumulate durably rather than requiring one all-target acknowledgement.

Primary ciphertext is committed and transmitted before own-device preparation.
A failed or full mirror journal retains recovery work while the peer can receive
its copy. Per-target preparation reuses the existing atomic ratchet send journal;
large or duplicated ciphertext stores are not introduced. Accepted target journals
are released idempotently. Job revisions prevent delayed storage scans from
rolling accepted targets back. Cleanup interruption retains the accepted state.

The pod fanout adapter emits explicit target acceptance rather than a synthetic
all-peer echo. Sender-device discovery is awaited when unavailable immediately
after reload, so an uninitialized roster cannot silently freeze an empty mirror
plan. Saved plans never acquire newly linked devices. Revoked/changed mirrors
remain blocked for review while peer confirmation remains settled; their spent
ratchets are not rolled back or silently discarded.

Interrupted legacy confirmed jobs that still exist are migrated conservatively:
peer acceptance is retained and unproven mirrors retry from saved ciphertext.
Mirrors already discarded by earlier releases cannot be recovered retroactively.

Validation: 2,556 frontend checks passed, 64 optional checks skipped;
focused tests after the final storage-size simplification pass. ESLint, whitespace,
i18n and cold-path checks pass. The expanded three-device live journey verifies
lost mirror acknowledgements through sender reload and SIGKILL/gateway restart:
only the exact mirror ciphertext retries, with one readable history row per
device. Existing offline peer/self-sync restart and device revocation steps also
pass. The smoke now waits for independent mirror completion and installs its
acknowledgement-loss hook before paced socket construction. Shell cache: v302.

The acceptance boundary remains pod/gateway acceptance, not a read receipt or
proof of durable client receive storage. Existing storage limits still apply;
blocked prepared mirrors stay bounded in the pending list until a supported
recovery or explicit local reset. No new pairing, history backfill, key-transfer
or ratchet-reset protocol was added. Browser/desktop WebID threads retain their
existing single-consumer/pairing limitations.
