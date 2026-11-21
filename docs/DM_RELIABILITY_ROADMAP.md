# DM reliability roadmap: rounds 133–140

These rounds are bounded reliability work. Rounds 130–132 stay closed.

| Round | State | Scope |
| --- | --- | --- |
| 133 | Complete | Account-bound asynchronous ratchet storage and pod backup jobs. See [closeout](ROUND_133_CLOSEOUT.md). |
| 134 | Complete | Gateway socket encrypted text and device fanout receive recovery, including interrupted history, reload and replay. See [closeout](ROUND_134_CLOSEOUT.md). |
| 135 | Complete | Durable encrypted text sending and stable-payload retries. See [closeout](ROUND_135_CLOSEOUT.md). |

## Round 135 acceptance criteria

The completed round audited the existing ordering in `web/main.js`, `web/e2e.js`, `web/connection.js`
and `web/sendqueue.js`, plus the pod send path, before choosing storage changes.
The round stayed limited to outgoing encrypted text DMs and device fanout.

- Encryption and outbox persistence must not strand a draft after interrupted
  storage or leave the next retry with incompatible ratchet state.
- Persist the stable message id and exact encrypted payload before transmission.
  Reload and retries must use that payload without encrypting again.
- Keep uncertain transmission visibly pending until explicit delivery evidence
  arrives. Sender retries must not duplicate recipient messages.
- Inject failures around encryption, local persistence, socket/pod transmission
  and confirmation; verify reload recovery through real browser journeys.

No new messaging features, new cryptographic primitives or broad transport or
database rewrite belongs to Round 135. Secondary sealed events and media sending
remain separate unless an existing text-send dependency requires a narrow fix.


## Follow-up rounds 136–140

Rounds 136–140 address the retained limits in this order:

| Round | Scope |
| --- | --- |
| 136 | Complete: accepted offline-device queues persist across restart. See [closeout](ROUND_136_CLOSEOUT.md). |
| 137 | Complete: account/session isolation. See [closeout](ROUND_137_CLOSEOUT.md). |
| 138 | Complete: pending-send review and safe controls. See [closeout](ROUND_138_CLOSEOUT.md). |
| 139 | Complete: independent sender-device synchronization. See [closeout](ROUND_139_CLOSEOUT.md). |
| 140 | Complete for pod-asset DM media. Native gateway transfers remain follow-up. See [closeout](ROUND_140_CLOSEOUT.md). |

See [the five bounded plans](PLANNED_ROUNDS_136_140.md) for dependencies,
acceptance tests, likely code areas and explicit exclusions. Rounds 136–140 are
complete within their audited boundaries; see closeouts for retained limits. Earlier completed-round scope is unchanged.


## Follow-up rounds 141–145

The next plans address bridge text actor binding, native gateway inline media,
resumable chunk transfers, evidence-based prepared-send retirement and durable
recipient-commit acknowledgements. See [the bounded plans](PLANNED_ROUNDS_141_145.md).
No completed round is reopened. New cryptographic/control protocol activation
requires its own safety and compatibility checkpoint.

Round 141 is complete: [bridge publisher ownership closeout](ROUND_141_CLOSEOUT.md).
Rounds 142–145 remain planned.
