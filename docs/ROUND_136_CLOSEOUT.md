# Round 136: durable gateway offline-device queues

Offline device fanout now commits opaque payload and routing data to SQLite
before the gateway reports acceptance. Local and relayed copies survive a process
restart. Schema migration 66 adds `pending_device_fanout` without changing stored
identities, messages or wire formats. The old RAM queue has been removed.

## Delivery and ownership

Queue identity is authenticated sender, logical message id, recipient account
and recipient device. Canonical JSON makes key-order-only retries equivalent;
conflicting queued payloads never replace an accepted copy. FIFO order uses a
monotonic database id. Capacity checks and insertion share one write transaction.

Peers sometimes address the gateway DID while its devices register under a
local account DID. Durable acceptance resolves that alias through stored device,
owner or relationship ownership. An ambiguous alias is refused, never made
visible to arbitrary connected accounts. Registration reads/removes only the
actual account/device's entries and rechecks block, revocation and row existence
before sending, including after a stale snapshot or account cleanup.

Draining uses a non-destructive read, socket write and scoped removal, rather
than popping a batch before transmission. Failure retains pending rows. A crash
after write but before removal can replay identical ciphertext; existing client
receipts suppress duplicate decryption/history. No lease needs recovery after a
process dies because rows are never destructively claimed before delivery.

## Capacity and retention

- At most 100 entries per account/device and 500 distinct account/device queues.
- At most 32 MiB of logical payload plus routing-identifier bytes across the
  queue; this is not a hard limit on SQLite file/index/WAL size.
- Payload JSON is limited to 128 KiB; routing identifiers and reads are bounded.
- No automatic TTL or drop-oldest eviction of accepted pending entries. Full or
  failed storage refuses acceptance so senders/relays retain their retries.
- A recipient's explicit block removes that sender's pending rows. Device
  revocation purges its pending copies and prevents new acceptance. Account
  deletion removes owned/authored entries without touching unrelated accounts.
- Offline acceptance requires a file-backed store. Disabling persistence or
  using an in-memory SQLite database cannot claim durable offline acceptance.

## Validation

- Focused tests cover schema-65 upgrade preservation, FIFO restart recovery,
  concurrent capacity enforcement, exact retry/conflict behavior, byte limits,
  ownership isolation, blocking, revocation and account deletion.
- Gateway tests cover failed SQLite acceptance, commit-before-ack interruption,
  failed drains, write-before-removal interruption, stale account-cleanup
  snapshots, unknown owners and gateway-address aliases.
- The real paired-device browser journey passed both a relayed peer copy and a
  local self-sync copy: leave the device offline, verify SQLite acceptance,
  SIGKILL/restart its gateway, reconnect the device, restore one readable message,
  verify queue removal, then reload and retain one history record. Existing
  pairing, fanout, stable outbox retry, receive recovery and revocation checks
  also pass in that journey.

All 4,740 selected backend tests are verified. The full non-integration run
passed 4,737; three schema-version comparisons caught a stale metadata constant.
After aligning that constant with migration 66, all 57 affected migration,
store-integrity and release-gate checks passed on a targeted rerun. Fourteen
optional integration tests were deselected. Ruff and the cold-path gate pass,
as do whitespace checks and the real restart browser journey described above.
Frontend production code and the shell cache version are unchanged; the browser
smoke harness gained isolated gateway restart support.

## Remaining boundaries

Socket-write success remains the existing server acceptance boundary. The gap
between that write and a durable client receive commit is not closed by this
round; no end-to-end read/commit acknowledgement protocol was added. Gateway
identity keys and its database must both remain available for normal restart
recovery. Previously lost RAM entries cannot be reconstructed retroactively.
Unaccepted sender self-sync retries remain Round 139 work. Rounds 137–140 remain
planned, and Rounds 130–135 stay closed.
