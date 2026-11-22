# Round 145: acknowledge durable device receive commits

Status: complete for encrypted device fanout. Native file/chunk recovery remains
in the open Rounds 142–143. This does not implement those media adapters.

A client opts in with `dm_commit_v: 1` on register, preserved through the
signature challenge and delegated-device registration. The registered event
advertises server support. Older clients retain the socket-write boundary;
new clients do not send recovery commands to older gateways.

For a supported live device, the gateway now commits the opaque envelope to
SQLite before sending it. Registration and periodic recovery replay the same
row without removing it. Recovery batches remain capped at 100 envelopes per
device and requests are throttled to one per four seconds per socket. Browsers
request recovery every five seconds. Existing 32 MiB global pending-byte,
100/device and 500-target admission limits remain; accepted work is not evicted.

Each replay carries `receive_commit: {v: 1, queue_id, envelope_digest}`. The
SHA-256 digest identifies the gateway's exact canonical opaque payload. A
`dm_receive_commit` echoes that tuple plus sender, logical message id and local
receive fingerprint. The authenticated socket determines the recipient account
and device; client-supplied recipient identities cannot authorize removal.
Revoked devices and sibling/foreign accounts cannot acknowledge another device.

The browser journals decryption, commits enabled local history, then commits a
content-free exact-envelope receipt before acknowledging. If local history is
disabled, its existing intentional-discard policy remains: the receive journal
exists before acknowledgement, and durable consumption evidence remains without
turning history back on. This is consumption under the chosen retention policy,
not a promise that intentionally disabled history will appear after reload.
A failed history/receipt write leaves recovery pending and sends no commit.

The gateway atomically writes a content-free committed-envelope receipt before
removing the payload, then returns `dm_receive_committed`. Both acknowledgement
and reply are idempotent. The browser protects an unconfirmed receipt from
capacity eviction until that exact server reply arrives. A lost client commit,
a lost server reply, or an interrupted cleanup replays from the browser's saved
receipt without decrypting a spent header. Account/session changes stop stale
callbacks. Replay identity includes every cryptographic payload field; route
annotations and server receipt tokens do not change the receive fingerprint.

Schema 67 adds committed fanout receipts (including original queue id) so a late
retry cannot replace a committed envelope. Admission reserves a receipt slot
for each accepted pending row; the combined global cap is 100,000. At capacity,
new work is refused, not silently accepted or evicted. Account deletion purges
owned/authored pending rows and receipts; existing block/device-revocation
cleanup still removes pending ciphertext. Relayed duplicate keys include the
signed content hash so changed content cannot bypass conflict validation using
an earlier transport id.

Validation:

- Full frontend, including the native transfer integrity/history checks: 2,601 passed,
  64 optional integration checks skipped.
- Backend non-integration run: 4,747 passed, one check skipped, 14
  integration checks deselected. The final store/ack changes and added migration,
  authentication and registration checks passed focused reruns afterward.
- Store tests cover restart, exact/conflicting retry, foreign/sibling/changed
  acknowledgements, idempotence, reserved receipt capacity, account purge and
  schema-66 migration without changing pending ciphertext.
- Receive tests cover history/receipt failure, lost commit, original-account
  changes, duplicate recovery, exact server confirmation and protected receipts
  at capacity. ESLint, Ruff, cold-path and whitespace gates pass.
- The live three-browser/two-gateway journey blocks history, verifies the live
  envelope remains in SQLite, SIGKILLs the gateway, and reloads the recipient.
  It then drops commit acknowledgements, reloads again after receipt commit,
  permits replay, and checks one readable history record and an empty queue.
  Pairing, both-device cross-gateway decryption, replies, sender self-sync,
  exact mirror retries and offline SIGKILL/restart journeys also pass.

Gateway acceptance, durable client consumption and reading remain distinct.
The sender's existing fanout acknowledgement is gateway acceptance, not a read
receipt or proof that every recipient has committed. Pre-existing source-side
federation relay expiry/rejection behavior is not redesigned here. The stronger
recipient boundary applies after a supported recipient gateway has admitted
the envelope; legacy clients retain their documented weaker boundary. This
cannot recover a permanently lost/revoked device or intentionally deleted local
storage. No room/ordinary-DM protocol redesign, larger crypto skip buffer or new
cryptographic reset protocol is included.
