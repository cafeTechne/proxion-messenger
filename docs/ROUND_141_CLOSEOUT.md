# Round 141: bridge text publisher ownership

Status: complete. Rounds 130–140 remain closed.

New encrypted WebID text intents record the originating bridge pod WebID in
`podActor`. It remains stable in the encrypted job through disconnect, credential
replacement and reload. A device-scoped last-known actor supports new offline
intents; it is cleared on explicit pod sign-out. No active replacement account
is inferred as the owner of an older prepared job.

Actor and bridge generation checks cover compose awaits, preparation, dispatch,
confirmation and recovery. The final bridge router refuses a mismatched expected
actor, and that local routing marker never enters the signed recipient envelope.
Pod setup/reachability events retire the old bridge before rediscovery. Native
DID-only conversations keep their original identity and saved ciphertext.

Bridge fetch commands also capture `expected_webid`. The gateway rejects stale
or malformed expectations before network I/O, while older clients without the
optional field retain their existing path/owner/body checks. A `pod_changed`
response cancels queued requests and rediscovers the actual bridge instead of
continuing with replacement credentials.

Pre-141 prepared bridge jobs without source-account evidence remain saved and
paused. The pending view explains the missing owner and offers manual text review
with a possible-duplication warning; a new Send gesture uses a new id. Safe
pre-encryption text cancellation remains available. A known account mismatch
asks for the original account rather than claiming a recipient/device error.
Already accepted media retains its confirmed state and original-account cleanup
requirements.

Validation:

- Frontend: 2,575 passed, 64 optional integrations skipped; one additional
  rediscovery regression and all 46 affected bridge checks passed afterward,
  giving 2,576 verified checks.
- Backend: 4,745 passed in the full non-integration run. Two Node-dependent
  checks initially lacked Node on PATH; both passed on a seven-check targeted
  rerun with the installed runtime. All 4,747 selected checks are verified;
  14 optional integrations were deselected. All 60 focused bridge tests pass.
- ESLint, Ruff on the changed server module, cold-path, whitespace and i18n
  gates pass. Pseudo-locale updated; 15 existing draft-locale warnings remain.
- The live three-account Solid/desktop/browser journey replaces the gateway's
  real throwaway pod credentials, reloads under the replacement account, proves
  no publication or payload change, restores the original account and receives
  one readable message with identical ciphertext and expected publisher. Existing
  bidirectional file/voice/forward/recovery/block and signer-takeover checks pass.

Shell cache: v304. No database or cryptographic protocol migration. This binds
queued publication; it does not turn the gateway's device-owned identity/history
into independently migrated multi-WebID profiles. Ambiguous older prepared
sends cannot be automatically assigned an owner. Prepared retirement and native
media/transfer durability remain in Rounds 142–145.
