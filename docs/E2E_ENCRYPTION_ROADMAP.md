# Proxion End-to-End Encryption Roadmap

## Current state

Messages in the current release are encrypted in transit via TLS (WebSocket over `wss://`) but the gateway server can read plaintext message content. This is acceptable for self-hosted deployments where the operator and users are the same person, and where the SSRF guard and relay validation are the primary security boundaries.

The gateway already has the structural groundwork for E2E:
- Every user has a persistent Ed25519 identity (`did:key`) stored in browser localStorage.
- The `msgcrypto.py` module (`proxion-core/src/proxion_core/msgcrypto.py`) implements `derive_message_key`, `decrypt_message`, and `is_encrypted` for pod-to-pod federated messages.
- The DID-to-public-key resolver (`didkey.py`) is in place.

---

## Design: Double Ratchet over WebSocket

### Key exchange

1. On first message to a peer, the sender fetches the peer's DID from the gateway's `registered` roster.
2. The sender generates an ephemeral X25519 key pair for this thread.
3. The sender performs X25519 DH between its ephemeral private key and the peer's Ed25519 public key (converted to Montgomery form via `ed25519_to_x25519`).
4. HKDF-SHA256 derives an initial root key and chain key.
5. The ephemeral public key is attached to the first message in a `key_header` field.

### Ratchet progression

- Each message advances the sending chain key (HMAC-SHA256 step).
- Message keys are derived from chain key steps and used for AES-256-GCM encryption.
- The receiver advances its own chain key on receipt and caches message keys for out-of-order delivery (up to 20 skipped messages).
- On reply, the receiver uses its own ephemeral key for the reverse DH, establishing forward secrecy.

### Wire format

```json
{
  "type": "message",
  "thread_id": "...",
  "content": "<base64-encoded AES-256-GCM ciphertext>",
  "e2e": true,
  "key_header": "<base64 ephemeral pub key, only on first message>",
  "nonce": "<base64 AES-GCM nonce>",
  "tag": "<base64 AES-GCM auth tag>"
}
```

The gateway sees `e2e: true` and forwards the ciphertext opaquely without logging content.

---

## Implementation plan

### Phase 1 — Browser-side library (no gateway changes needed)

1. Add `web/e2e.js` — ES module with:
   - `ed25519_to_x25519(pubBytes)` — Montgomery-form conversion
   - `deriveSharedSecret(myEphemPriv, peerPub)` — X25519 via SubtleCrypto `deriveBits`
   - `ratchetSend(threadId, plaintext)` → `{ciphertext, nonce, keyHeader?}`
   - `ratchetReceive(threadId, ciphertext, nonce, keyHeader?)` → `plaintext`
   - localStorage-backed ratchet state per `thread_id`

2. `renderMessage` in `main.js` calls `ratchetReceive` when `event.e2e === true`.
3. `sendMessage` calls `ratchetSend` when the thread has a known peer DID.

**No gateway changes.** The gateway sees opaque base64 ciphertext and does not need to understand it.

### Phase 2 — Key fingerprint verification UI

1. Display the peer's DID short-form (`did:key:z...abc`) in the DM header.
2. Add a "Verify" button that shows the full DID and a 6-word safety number (BIP39 words derived from BLAKE2b of both parties' public keys).
3. Mark threads as "verified" in localStorage after the user confirms.

### Phase 3 — Group rooms

Group key exchange is harder. The options are:

**Option A — Sender Keys (Signal-style):** Each member generates a symmetric sender key and encrypts it for every other member using their individual E2E channel. Scales poorly beyond ~20 members.

**Option B — MLS (Messaging Layer Security, RFC 9420):** Standards-based, scales to thousands of members, supports forward secrecy and post-compromise security. The `openmls` Rust library exists; a WASM build would enable browser use. Significantly higher implementation complexity.

**Recommendation:** Implement Sender Keys for Phase 3 (matches current room sizes), with a clear migration path to MLS as the `openmls` WASM ecosystem matures.

### Phase 4 — Pod-backed persistent key material

Currently ratchet state lives in `localStorage` and is lost if the browser storage is cleared. Phase 4 stores encrypted ratchet snapshots on the user's Solid pod (via the existing `pod.js` write path), so users can recover message history after clearing or across devices.

The snapshot is encrypted with a key derived from the user's Ed25519 identity key, so the pod server cannot read it.

---

## Non-goals

- **Gateway-side decryption:** The gateway will never decrypt E2E content. Moderation and search on E2E threads must be client-side.
- **SMS/phone fallback:** Out of scope.
- **Key escrow / recovery via server:** Deliberately excluded. Lost keys = lost history (acceptable tradeoff for sovereignty).

---

## Open questions

1. **SubtleCrypto X25519 support:** `crypto.subtle.deriveBits` with `ECDH` over `X25519` is supported in all modern browsers (Chrome 113+, Firefox 119+, Safari 17+). We should add a feature-detect and surface a warning for older browsers.
2. **Ed25519 → X25519 conversion:** The `ed25519_to_x25519` point conversion is not in SubtleCrypto. It requires a small JS implementation of the birational map `(u, v) = ((1+y)/(1-y), sqrt(-486664)*u/x)`. The `@noble/curves` library provides this; alternatively, a ~50-line pure-JS implementation avoids the dependency.
3. **Ratchet state size:** A Double Ratchet with 20 skipped-message keys per chain, stored per thread, is ~2KB per thread in JSON. For power users with hundreds of DM threads this approaches localStorage quotas (~5MB in most browsers). Phase 4 (pod sync) is the mitigation.
