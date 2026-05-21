# MVP Critical Flows

Defines the user journeys that must work correctly before the first release. Each flow
includes success criteria, expected persisted state, and recovery behavior on failure.

---

## 1. Onboarding and Contact Bootstrap

**Flow**: New user installs app → gateway generates Ed25519 identity → user registers
a device → user adds first contact via invite or DID.

**Success criteria**:
- Device appears in `device_registrations` with correct `owner_webid` and `public_key_b64`.
- Contact appears in `contacts` with `source` field set.
- Prekey bundle (SPK + OTPKs) uploaded and retrievable via `get_prekey_bundle`.

**Recovery**: Device registration is idempotent. Duplicate `register_device` calls with
the same `device_id` do not create duplicates.

---

## 2. DM — Online and Offline Catch-Up

**Flow**: Alice sends a DM to Bob while Bob is offline → Bob reconnects → Bob issues
`catch_up` for the thread → messages are delivered in order.

**Success criteria**:
- Messages saved to `messages` with monotonically increasing `seq` values.
- `get_messages_since_seq(thread_id, since_seq)` returns exactly the missed messages.
- After Bob ACKs via `catch_up_ack`, the watermark in `catchup_watermarks` updates.
- `batch_hash` in the `catch_up_batch` response matches the SHA-256 of sorted
  `message_id:seq` pairs, allowing Bob to detect truncation or reordering.

**Recovery**: If Bob reconnects and ACK is lost, re-issuing `catch_up` returns the
same batch (idempotent read). The watermark only advances on explicit ACK.

---

## 3. Room Rekey After Member Removal

**Flow**: Alice removes Carol from a room → gateway triggers sender key rotation →
Alice and Bob get a new epoch sender key → Carol's old key is deleted.

**Success criteria**:
- `delete_sender_keys_for_room` is called, wiping the old epoch.
- New sender key saved with `epoch` incremented.
- `sender_key_rotation` event emitted to remaining members with `next_epoch`.
- Carol receives a `removed_from_room` event and cannot decrypt post-removal messages
  (her cached key has a lower epoch — `decrypt_group_message` raises
  `sender_key_epoch_stale`).

**Recovery**: If a member misses the rotation event, they re-request the sender key
bundle on next connect. If the new key is not yet distributed, the room is temporarily
in a send-only state for that member until distribution completes.

---

## 4. File Attachment (E2E)

**Flow**: Alice encrypts a file with `encrypt_attachment` → uploads ciphertext to
server → embeds `attachment_key_payload` inside the E2E DM envelope → Bob decrypts
the E2E envelope, extracts the key payload, calls `decrypt_attachment`.

**Success criteria**:
- `validate_attachment_envelope` accepts the key payload (all required fields present).
- `decrypt_attachment(ciphertext_b64, key_b64, nonce_b64)` returns original bytes.
- `is_attachment_key_expired` returns `False` within the 7-day TTL window.
- Gateway rejects any attachment DM/room message where
  `validate_attachment_envelope` fails with `invalid_attachment_envelope` error.

**Recovery**: If the key is expired (`is_attachment_key_expired` → `True`), client
shows an "attachment expired" UI state. No server-side key storage; re-sending
requires re-encrypting and re-uploading.

---

## 5. Backup and Restore

**Flow**: User exports encrypted backup of `LocalStore` → installs on new device →
imports backup → all threads, contacts, and sessions are restored.

**Success criteria**:
- Schema version is preserved across export/import.
- All messages are present and `seq` values are consistent.
- DM sessions can be resumed (no forced X3DH re-init required for existing sessions).

**Recovery**: If the backup is corrupt, the user starts fresh. Contacts can be
re-added; messages sent while offline are in the sender's pod and can be re-synced
if pod access is available.
