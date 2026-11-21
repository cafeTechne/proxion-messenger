# DM reliability rounds 133–135

These rounds are bounded reliability work. Rounds 130–132 stay closed.

| Round | State | Scope |
| --- | --- | --- |
| 133 | Complete | Account-bound asynchronous ratchet storage and pod backup jobs. See [closeout](ROUND_133_CLOSEOUT.md). |
| 134 | Complete | Gateway socket encrypted text and device fanout receive recovery, including interrupted history, reload and replay. See [closeout](ROUND_134_CLOSEOUT.md). |
| 135 | Planned | Durable encrypted text sending and stable-payload retries. |

## Round 135 acceptance criteria

Audit the existing ordering in `web/main.js`, `web/e2e.js`, `web/connection.js`
and `web/sendqueue.js`, plus the pod send path, before choosing storage changes.
Keep this round limited to outgoing encrypted text DMs and device fanout.

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
