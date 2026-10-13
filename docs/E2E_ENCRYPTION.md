# Proxion direct-message encryption

Status: working draft. Describes the protocol as implemented in this repository;
not yet proposed to any standards body. The format may change.

This document specifies how Proxion encrypts one-to-one direct messages (DMs)
between browsers, as the code in this repository does it today. Every statement
cites the function that implements it. Where this document and the code disagree,
the code is authoritative and this document has a bug.

The key words MUST, MUST NOT and SHOULD are used in the sense of RFC 2119, and only
for behavior the code enforces. Everything else is descriptive.

## Contents

1. [Scope](#1-scope)
2. [Goals and threat model](#2-goals-and-threat-model)
3. [Identities and keys](#3-identities-and-keys)
4. [Key publication and discovery](#4-key-publication-and-discovery)
5. [Session setup](#5-session-setup)
6. [Message encryption](#6-message-encryption)
7. [Envelope format](#7-envelope-format) (7.5: edits, reactions and files)
8. [Delivery](#8-delivery)
9. [Multiple devices](#9-multiple-devices)
10. [Authentication and verification](#10-authentication-and-verification)
11. [State storage](#11-state-storage)
12. [Interoperability notes](#12-interoperability-notes)
13. [Test vectors](#13-test-vectors)
14. [Changelog](#14-changelog)

## 1. Scope

Covered: the text body of a one-to-one DM sent from the web client
(`web/main.js` `_sendComposed`, `_tryDmFanout`), encrypted by `web/e2e.js`, and
carried either over a Proxion gateway or dropped directly into the recipient's
Solid pod (`web/webdm.js`). Also covered, over a gateway: DM edits, reactions and
file attachments (inline and chunked), sealed with the same ratchet session
([7.5](#75-edits-reactions-and-files)).

Not covered by this encryption layer:

- Room messages, and edits, reactions and files in rooms. Rooms are shared Solid
  Long Chat resources and are not end-to-end encrypted (see
  [POD_DATA_MODEL.md](POD_DATA_MODEL.md)).
- Call media. 1:1 calls use DTLS-SRTP; `web/callsec.js` binds the DTLS fingerprint
  to the Ed25519 identity (see [CALLS.md](CALLS.md)). Only the identity relation is
  mentioned here.
- Gateway-to-gateway transport sealing (`sealed_relay.py`), described in
  [section 8.3](#83-cross-gateway-relay) only as it affects what a gateway sees.

The Python core also contains an X3DH plus Double Ratchet module
(`proxion_messenger_core/e2e_session.py`) and gateway handlers for prekey bundles and
`e2e_v` 2 and 3 messages (`_gateway_dm.py` `_handle_upload_prekeys`,
`_handle_get_prekey_bundle`, `_handle_sealed_dm`, `_handle_save_session_state`). The
web client does not call any of them, so they are not part of the protocol described
here.

## 2. Goals and threat model

### 2.1 What the design protects

- **Message bodies against gateways and pods.** When the sender holds an X25519
  public key for the recipient, the DM text leaves the browser only as AES-256-GCM
  ciphertext (`e2e.js` `_ratchetEncryptImpl`). The sender's gateway, any peer
  gateway on the relay path and the recipient's pod store and forward that
  ciphertext without a key for it.
- **No plaintext downgrade for a keyed peer.** Once a peer key is cached
  (`isE2EEnabled`), a failed encryption aborts the send instead of falling back to
  plaintext (`main.js` `_sendComposed` `_encryptedSend`; `_tryDmFanout` skips the
  device on failure).
- **Forward secrecy within a session.** Message keys come from a hash chain that is
  stepped forward and not kept (`advanceChain`), so a later state snapshot does not
  contain earlier message keys, except keys held in the skipped-key buffer
  ([6.6](#66-skipped-message-keys)).
- **Post-compromise recovery.** Each time the direction of conversation changes,
  both sides contribute a fresh X25519 ratchet key and the root key is re-derived
  (`_dhRatchetReceive`, `_dhRatchetSend`), so an attacker who copied a state snapshot
  loses access after the next round trip, provided they cannot also tamper with
  traffic.
- **Forged or corrupted ciphertext does not damage the session.** Decryption works
  on a copy of the state and commits only after the AES-GCM tag verifies
  (`_ratchetDecryptImpl`).
- **DM edits, reactions and files against gateways and pods.** For a keyed peer,
  the new text of an edit, the emoji of a reaction, and a file's bytes, name, type,
  size, dimensions and spoiler flag leave the browser only inside ratchet messages
  or under a per-file AES-256-GCM key carried in one ([7.5](#75-edits-reactions-and-files)).
  A failed seal sends nothing.
- **Sender authenticity on the gateway-free path,** via an Ed25519 envelope
  signature checked against a signer identity published in the sender's own pod
  ([10.1](#101-envelope-signatures)).

### 2.2 What it does not protect

- **The first message to a peer whose key is unknown is sent in plaintext.** If no
  X25519 key is cached for the peer when the user sends, `_sendComposed` sends the
  `content` unencrypted. In the gateway-free build the client first tries to fetch
  the key from the peer's pod (`fetchAndCachePeerPub`). The DM header shows the
  thread as not encrypted in this state (`e2e-status.js` `_updateE2EStatus`).
- **Messages sent before the first reply have weaker properties.** There are no
  signed prekeys or one-time prekeys. The initiator derives its first sending chain
  from its own fresh ratchet key and the recipient's long-term X25519 identity key
  ([5.1](#51-initiator)). Until the recipient replies:
  - every message in that first chain depends on the recipient's long-term private
    key, so a later compromise of that key exposes those messages if the ciphertext
    was recorded;
  - the key agreement does not involve the initiator's identity key, so it does not
    authenticate the initiator. Authentication of the initiator comes only from the
    envelope signature (gateway-free path) or from the authenticated gateway session
    (gateway path).
- **Metadata.** Gateways see the sender and recipient identifiers, message ids,
  timestamps, reply ids, display names, ciphertext length and the ratchet header
  fields (`_gateway_dm.py` `_handle_local_dm` builds and stores this event). On the
  gateway-free path the recipient's pod stores the full envelope, including sender
  WebID, display name and timestamp, until the recipient deletes it. Push
  notifications carry a thread id and display name (`_push_offline_dm`). For
  sealed edits, reactions and files the gateway also sees the id of the message
  acted on, who reacted, an opaque reaction token per reaction, and the
  ciphertext length (which reveals the edit or file size to within the 16-byte tag).
- **Key distribution through the gateway.** On the gateway path, a peer's X25519
  key arrives from the user's own gateway (in `relationships`, `contact_added`, and
  on every gateway-delivered DM event) and is cached without further proof
  (`main.js` `_handleEventAsync`, `cachePeerPub`). Per-device keys for multi-device
  fan-out also come from the gateway (`get_peer_device_keys`). A gateway that
  substitutes keys can read messages encrypted to the substituted key. Comparing
  safety numbers ([10.3](#103-safety-numbers)) is the defense; it is manual and covers
  the account's primary key, not per-device keys.
- **Endpoint compromise.** Anyone who can run code in the user's browser origin can
  use the non-extractable keys while the page is open and can read decrypted
  messages, which are cached in plaintext in local IndexedDB (`dmHistorySave`).
- **Content the user chooses to archive.** The opt-in DM pod archive writes
  decrypted messages to the user's own pod as JSON-LD (`pod.js`
  `podArchiveDmMessage`, gated by `dmPodArchiveEnabled`).
- **Lost state.** There is no key escrow. If a device loses its identity key, peers'
  existing sessions with that device stop decrypting until a new session forms.

## 3. Identities and keys

Each browser holds two independent long-term key pairs. Neither is derived from the
other.

| Key | Algorithm | Created in | Stored | Purpose |
|---|---|---|---|---|
| Identity signing key | Ed25519 | `main.js` `generateOrLoadIdentity` | IndexedDB `proxion-identity`, record `identity`, non-extractable `CryptoKey` | Gateway auth challenge, envelope signatures, call fingerprint signatures, device certificates |
| Encryption identity key | X25519 | `e2e.js` `_genIdentity` | IndexedDB `proxion-e2e-keys`, store `keys`, non-extractable `CryptoKey` | Recipient side of session setup |
| State key | AES-256-GCM | `e2e.js` `_genIdentity` | Same record as the X25519 key | Encrypts ratchet state at rest |

- The identity is the Ed25519 public key as a `did:key` (multicodec prefix `0xed 0x01`
  followed by the 32-byte key, base58btc with a `z` prefix; `main.js`
  `_pubBytesToDid`, decoded by `dmsig.js` `didToEd25519Pub`).
- The X25519 public key is carried as the unpadded base64url encoding of the 32 raw
  bytes (`e2e.js` `b64uEnc`, taken from the JWK `x` member).
- The X25519 record key is `self` when no Solid account is signed in, and
  `self::<WebID>` when one is (`_selfKeyId`), so two Solid accounts in one browser
  get separate encryption keys. Peer caches and ratchet state in localStorage are
  namespaced the same way (`e2eScopedKey`).
- If IndexedDB is unavailable, the X25519 private key is kept as an extractable JWK
  in localStorage (`proxion_e2e_x25519_priv_jwk`) and the state key is derived from it
  with HKDF-SHA256 (IKM = the private scalar, salt = `proxion-e2e-state-v1`, empty
  info, 32 bytes) (`_doInit`, `_identityFromLsJwk`).
- E2E is enabled only when the browser supports X25519 in WebCrypto (`_doInit`
  feature test).

**How the X25519 key is bound to an identity.** There is no signature by the Ed25519
key over the X25519 key. The binding is one of:

1. *Pod location.* The key is read from a fixed path in the pod derived from the
   peer's WebID ([4.2](#42-discovery)). Only the pod owner can write there.
2. *Signed envelope.* The gateway-free envelope includes the sender's `x25519_pub`
   inside the signed fields; the recipient caches it only when the signature verifies
   against the sender's published signer ([10.1](#101-envelope-signatures)).
3. *Gateway session.* The gateway stores the key the client supplies at `register`
   under the session's authenticated identity (`_gateway_auth.py`, `save_x25519_pub`
   and `save_device_e2e_key`) and hands it to related peers.

## 4. Key publication and discovery

### 4.1 Publication

After initialization, a signed-in client writes its X25519 public key to its own pod
(`e2e.js` `_publishPubToPod`):

```
PUT {podRoot}proxion/identity/x25519-pub.json
Content-Type: application/json

{"version": 1, "pub": "<base64url X25519 public key>"}
```

It then writes an access control resource for that file granting the owner Read,
Write and Control and granting public Read (`foaf:Agent`), scoped with
`acl:accessTo` to the single file and with no `acl:default` (`pod.js`
`podPublishIdentityAcl`, `_publicReadAclBody`; the ACP form is used on servers that
advertise ACP). The ACL is read back to confirm `acl:Read` is present.

The client also publishes `proxion/identity/signer.json` with the same ACL
(`pod.js` `podPublishSigner`):

```json
{"version": 1, "signer": "<did:key of this device>", "account_did": "<account did:key or null>"}
```

With a gateway, the client also sends `x25519_pub` in its `register` command
(`connection.js`). The gateway records it per account and per device and returns it
to related peers in `relationships` and `contact_added` events and through
`get_peer_device_keys`.

### 4.2 Discovery

The pod root of a peer is derived from the WebID (`webdm.js`
`peerPodRootFromWebId`): the part of the WebID before `/profile/`, including the
trailing slash; otherwise the WebID's origin followed by `/`.

`fetchAndCachePeerPub` reads `{peerPodRoot}proxion/identity/x25519-pub.json` with the
signed-in Solid session, after an SSRF check on the pod root (`ssrf.js`
`isPeerPodRootAllowed`). The key is accepted only if `version` is `1` and `pub` is a
string.

### 4.3 Key change

`cachePeerPub` replaces the cached key when a different key arrives and clears the
local "verified" mark for that peer, so a previous verification does not carry over
to a new key. It does not delete the existing ratchet state for that peer.

## 5. Session setup

A session is identified by a local *peer id* string, not by anything on the wire:

- the peer's WebID (gateway-free DMs, and `local_dm` threads);
- the peer's `did:key` (gateway DMs over a relationship certificate, where the view's
  `peerWebid` is the contact's `peer_did`; `view.js`);
- `<account>#<device_id>` for a multi-device fan-out copy (`_tryDmFanout`,
  `_handleEventAsync`).

State for a peer id is created on first use and never explicitly reset.

### 5.1 Initiator

When `ratchetEncrypt` finds no state for the peer id, it calls `_initSenderState`
with the cached peer X25519 key `IK_B`:

```
(r_A, R_A)        = new X25519 key pair (the first ratchet key)
dh                = X25519(r_A, IK_B)
(RK, CK_s)        = KDF_RK(0x00 * 32, dh)
state.theirRatchetPub = IK_B
state.recvChain   = null
```

### 5.2 Responder

When `ratchetDecrypt` finds no state for the peer id, it calls `_initReceiverState`
with the message's `ratchet_pub` `R_A`:

```
dh1               = X25519(ik_B, R_A)            ; ik_B: own X25519 identity private key
(RK0, CK_r)       = KDF_RK(0x00 * 32, dh1)
(r_B, R_B)        = new X25519 key pair
dh2               = X25519(r_B, R_A)
(RK, CK_s)        = KDF_RK(RK0, dh2)
state.theirRatchetPub = R_A
```

The responder's sending chain is ready immediately; its first reply carries `R_B`,
which makes the initiator perform a DH ratchet step ([6.5](#65-dh-ratchet)).

A responder creates state from whichever message first decrypts for that peer id.
The new state is committed only if the AES-GCM tag of that message verifies.

## 6. Message encryption

### 6.1 Primitives

| Function | Definition | Code |
|---|---|---|
| DH | X25519, 32-byte output | `crypto.subtle.deriveBits({name:'X25519'}, ..., 256)` |
| KDF_RK(rk, dh) | HKDF-SHA256, IKM = `dh`, salt = `rk` (32 bytes), info = ASCII `ratchet`, output 64 bytes; new root key = bytes 0..31, new chain key = bytes 32..63 | `e2e.js` `kdfRk` |
| KDF_CK(ck) | message key = HMAC-SHA256(`ck`, `0x01`); next chain key = HMAC-SHA256(`ck`, `0x02`) | `e2e.js` `advanceChain` |
| AEAD | AES-256-GCM, 96-bit nonce, 128-bit tag, no associated data | `_ratchetEncryptImpl`, `_ratchetDecryptImpl` |

The plaintext is the UTF-8 encoding of the message text.

`e2e.js` also exports `deriveRootAndChain` (HKDF with salt `proxion-dm-v1`, info
`root`). It is a legacy utility and is not used by the ratchet.

### 6.2 State

Per peer id (`e2e.js`, comment above `_encState`):

| Field | Meaning |
|---|---|
| `rootKey` | 32-byte root key |
| `myRatchetPrivJwk`, `myRatchetPub` | current own ratchet key pair (private as JWK, public base64url) |
| `theirRatchetPub` | last ratchet public key received (initially the peer identity key on the initiator side) |
| `pendingDhRatchet` | true after a DH ratchet on receive; the next send rotates the own ratchet key |
| `sendChain`, `sendMsgNum`, `sendPrevLen` | sending chain key, next message number, length of the previous sending chain |
| `recvChain`, `recvMsgNum` | receiving chain key and next expected message number |
| `skippedKeys` | map `"<ratchet_pub>:<msg_num>"` to base64url message key, oldest first |
| `ver` | save counter, incremented on every `saveRatchetState`; used to pick the newest stored copy ([11](#11-state-storage)) |

A stored state without `rootKey` is from an earlier format and is discarded on load
(`loadRatchetState`).

### 6.3 Sending

`_ratchetEncryptImpl`:

1. Load state; if none, set up as initiator ([5.1](#51-initiator)). If
   `pendingDhRatchet` is set, perform the sending half of the DH ratchet first.
2. Record header values `msg_num = sendMsgNum`, `pn = sendPrevLen`,
   `ratchet_pub = myRatchetPub`.
3. `(MK, CK_s) = KDF_CK(CK_s)`; increment `sendMsgNum`.
4. Generate a fresh random 12-byte nonce (`crypto.getRandomValues`) and encrypt with
   AES-256-GCM under `MK`.
5. Save state, then return `ciphertext`, `nonce`, `msgNum`, `pn`, `ratchetPub`.

Operations on the same peer id are serialized by a per-peer promise queue
(`_serialize`) so a send and a receive cannot interleave and overwrite each other's
state.

### 6.4 Receiving

`_ratchetDecryptImpl(peerId, ciphertext, nonce, msg_num, ratchet_pub, pn)`:

1. `ratchet_pub` MUST be present, and `msg_num` and `pn` MUST be non-negative safe
   integers; otherwise decryption fails.
2. Load state and deep-copy it. If `"<ratchet_pub>:<msg_num>"` is in `skippedKeys`,
   use and remove that key and go to step 6. No ratchet step is taken for such a
   message, so a late message from an older chain does not move the session.
3. If there is no state, set up as responder ([5.2](#52-responder)). Else if
   `ratchet_pub` differs from `theirRatchetPub`, perform the receiving half of the DH
   ratchet using `pn`.
4. If `msg_num < recvMsgNum`, fail ("already consumed"). If
   `msg_num - recvMsgNum > MAX_SKIP`, fail before deriving any key.
5. Step the receiving chain, buffering each skipped message key, until
   `recvMsgNum == msg_num`, then derive `MK` and step once more.
6. Decrypt. Only if the tag verifies is the updated state saved. On any failure the
   stored state is unchanged.

### 6.5 DH ratchet

Receiving half (`_dhRatchetReceive`), on a new `ratchet_pub` `R'`:

```
drop skipped keys of every chain except the current receiving chain
buffer skipped keys of the current receiving chain up to min(pn, recvMsgNum + MAX_SKIP)
dh                   = X25519(myRatchetPriv, R')
(rootKey, recvChain) = KDF_RK(rootKey, dh)
recvMsgNum = 0; theirRatchetPub = R'; pendingDhRatchet = true
```

Sending half (`_dhRatchetSend`), at the next send:

```
(r', R')             = new X25519 key pair
dh                   = X25519(r', theirRatchetPub)
(rootKey, sendChain) = KDF_RK(rootKey, dh)
sendPrevLen = sendMsgNum; sendMsgNum = 0; myRatchet = (r', R'); pendingDhRatchet = false
```

### 6.6 Skipped message keys

Two bounds apply (`e2e.js`):

- `MAX_SKIP` (20) bounds the keys one incoming message can make the receiver
  derive, per chain. In the current chain, a message whose `msg_num` is more than
  `MAX_SKIP` ahead of `recvMsgNum` MUST fail before any key is derived, without
  changing stored state. During a DH ratchet, at most `MAX_SKIP` keys of the retiring
  chain are derived; if `pn` asks for more, the remaining messages of that chain are
  given up and the message on the new chain is still processed. A forged `msg_num` or
  `pn` therefore costs at most `2 * MAX_SKIP` chain steps.
- `MAX_SKIPPED_KEYS` (100) bounds the stored buffer. When it is exceeded the oldest
  entries are evicted first (`_capSkippedKeys`).

Buffered keys also expire by chain: a DH ratchet step drops the keys of every chain
except the one being retired, so only the current and the previous receiving chain
can still deliver late messages. Otherwise a key is removed when its message
arrives.

### 6.7 Nonces

Each message uses a fresh random 96-bit nonce sent in the `nonce` header field.
Each message key encrypts exactly one message on a normal send.

## 7. Envelope format

All binary values are unpadded base64url unless noted. `sig` is standard base64
with padding (`dmsig.js` `_b64`).

### 7.1 Ratchet header (common to every path)

| Field | Type | Meaning |
|---|---|---|
| `content` | string | AES-GCM ciphertext followed by the 16-byte tag, base64url |
| `e2e` | boolean | `true` when `content` is ratchet ciphertext |
| `nonce` | string | 12-byte AES-GCM nonce, base64url |
| `msg_num` | integer | message number in the sending chain |
| `pn` | integer | length of the sender's previous sending chain |
| `ratchet_pub` | string | sender's current X25519 ratchet public key, base64url |
| `x25519_pub` | string | sender's long-term X25519 public key, base64url (lets the recipient learn it) |

There is no protocol version field in the ratchet header of a message. Sealed edits,
reactions and files add `e2e_v` (7.5).

### 7.2 Gateway-free single envelope

Built by `webdm.js` `envelopeFromDmPayload` and signed in `dropDm`:

```json
{
  "v": 1,
  "from_webid": "https://alice.example/profile/card#me",
  "from_display_name": "Alice",
  "message_id": "<uuid>",
  "content": "<base64url ciphertext and tag>",
  "e2e": true,
  "nonce": "<base64url 12 bytes>",
  "msg_num": 0,
  "pn": 0,
  "ratchet_pub": "<base64url 32 bytes>",
  "x25519_pub": "<base64url 32 bytes>",
  "reply_to_id": null,
  "timestamp": "2026-01-01T00:00:00.000Z",
  "signer": "did:key:z6Mk<...>",
  "sig": "<base64 Ed25519 signature>",
  "cert": { "<optional device certificate, see 9.2>": "" }
}
```

`timestamp` is the sender's clock at envelope creation. `signer`, `sig` and `cert`
are absent when the sender has no signing key.

### 7.3 Gateway-free fan-out envelope

Built in `webdm.js` `dropFanout`:

```json
{
  "v": 1,
  "kind": "fanout",
  "from_webid": "https://alice.example/profile/card#me",
  "message_id": "<uuid>",
  "to_device_id": "did:key:z6Mk<device>",
  "payload": {
    "content": "<...>", "e2e": true, "nonce": "<...>", "msg_num": 0, "pn": 0,
    "ratchet_pub": "<...>", "x25519_pub": "<...>",
    "from_device_id": "did:key:z6Mk<sender device>",
    "thread_id": "<sender's thread id>", "thread_peer": "<peer account>",
    "reply_to_id": "<present only on a reply>"
  },
  "signer": "did:key:z6Mk<...>",
  "sig": "<base64 Ed25519 signature>"
}
```

### 7.4 Gateway commands and events

To its own gateway the client sends `local_dm` (fields `target_webid`, `thread_id`,
`message_id`, optional `reply_to_id`) or `send_dm` (field `cert_id`), with the
ratchet header fields from 7.1 merged in (`_sendComposed`). The gateway forwards the
header fields unchanged and delivers an event of the form:

```json
{
  "type": "message", "source": "local_dm",
  "thread_id": "<thread>", "from_webid": "<sender id>", "from_display_name": "Alice",
  "content": "<ciphertext>", "timestamp": "<gateway clock>", "message_id": "<uuid>",
  "reply_to_id": null, "local": true,
  "e2e": true, "nonce": "<...>", "msg_num": 0, "pn": 0,
  "ratchet_pub": "<...>", "x25519_pub": "<...>"
}
```

(`_gateway_dm.py` `_handle_local_dm`). The gateway rejects a `local_dm` whose
`content` exceeds 16,384 bytes.

Multi-device sends use `{"cmd": "send_dm_fanout", "message_id", "fanout": [{"to_webid",
"to_device_id", "payload"}]}`, where `payload` is the object shown in 7.3. The gateway
MUST reject more than 16 entries, takes the sender from the authenticated session
rather than the command, and delivers `{"type": "dm_fanout", "message_id",
"from_webid", "to_device_id", "payload"}` (`_handle_send_dm_fanout`).

### 7.5 Edits, reactions and files

`web/e2e-secondary.js` `createSecondaryCrypto` seals these in a `dm` or `local_dm`
view whose peer key is cached (`isE2EEnabled`, the test the message path uses).
Each is its own ratchet message (6.3) on the session for the same peer id as the
message text, and carries the fields of 7.1 (without `x25519_pub`) plus:

| Field | Type | Meaning |
|---|---|---|
| `e2e_v` | integer | `1`, the version of this format |

The ratchet plaintext is the five bytes `0x00 'p' 'x' 's' '1'` followed by UTF-8
JSON with `"v": 1`, a kind `k`, and:

| `k` | Fields |
|---|---|
| `edit` | `mid` (id of the edited message), `text` |
| `react` | `mid`, `emoji`, `tok` (the token below) |
| `file` | `key` (32 bytes, base64url), `iv` (12 bytes, base64url), `name`, `mime`, `size`; optional `w`, `h`, `spoiler` |

A receiver MUST drop the event when the kind differs from the event type, when
`mid` differs from the event's `message_id`, or when `tok` differs from the event's
`emoji` (`checkEditBody`, `checkReactionBody`). The message path treats a
decrypted plaintext that starts with the prefix as undecryptable (`isSealedPlain`).

**Edits.** `edit_message` and `edit_local_message` carry the ciphertext in
`content`. The gateway delivers `message_edited` with the ciphertext in
`new_content`, the header fields, and `from_webid` (the editor), which selects
the session to decrypt with.

**Reactions.** `add_reaction` carries a token in `emoji` (`~` followed by 11
base64url characters, 64 random bits, `newReactionToken`) and the header with the
ciphertext in `content`. The gateway stores and relays the token as the emoji.
`remove_reaction` names the token and carries no header. Each device keeps the
token-to-emoji map it learned in localStorage (`proxion_e2e_react_tokens`, account
scoped, at most 2000 entries, cleared on logout) so stored reactions render after a
reload; a token the device never decrypted is not shown.

**Files.** The bytes are encrypted with AES-256-GCM under a fresh random 256-bit
key and 96-bit nonce, without associated data (`encryptFile`). The key, nonce and
metadata travel in the sealed `file` body.

- Inline: `send_file` with `filename: "file"`, `mime_type:
  "application/octet-stream"`, `data_b64` the base64 ciphertext, and the header
  (ciphertext in `content`). No dimensions or spoiler flag outside the header. The
  gateway applies only its size and executable-signature checks to a sealed file
  (`_gateway_dm.py` `_handle_send_file`), replaces the declared name and type with
  the placeholders, and stores the summary as a bare paperclip.
- Chunked: the whole file is encrypted first and the ciphertext is split into
  chunks (`filetransfer.js` `sendFileChunked`). `file_offer` carries the
  placeholder name and type, the ciphertext size and the header. The receiver
  opens the header before accepting and refuses the offer (`bad_offer`) when it
  cannot.
- The ciphertext is drawn again in the rare case its first bytes match an
  executable signature the gateway refuses (`isBlockedMagic`).
- After decrypting, the receiving client applies the MIME allowlist and the
  executable-signature check the gateway applies to plaintext files
  (`checkFileBody`, `openFileBytes`).

**Own echoes.** A sender cannot decrypt its own ratchet messages. It keeps the
plaintext of its last 32 sealed sends keyed by `ratchet_pub:msg_num` and matches
the gateway's echo of each (`createSealedOutbox`).

**Edit history.** Earlier versions of a DM message are kept in the local history
cache (`dmhistory.js` `applyEditToRecord`, up to 50 per message). The edit-history
view of a DM reads only that cache, since the gateway holds them as ciphertext.

**Gateway checks.** `command_validation.py` `sealed_fields` accepts a header only
with `e2e` true, `e2e_v` 1, base64url `content` (at most 16,384 characters for
edits, 1,024 for reactions, 4,096 for files), `nonce` and `ratchet_pub` of at most
32 and 64 characters, and integer `msg_num` and `pn` in [0, 2^31). Anything else
claiming a header is refused with `invalid_e2e_fields`, by command and by relay.
Rooms refuse sealed edits, reactions and files. Relays forward the header fields
(`e2e_v` is on the relay key allow-list).

**Compatibility.**

- Reading: an edit, reaction or file event with neither `e2e` nor `e2e_v` comes
  from a client that predates this format and is applied as before. One that claims
  encryption in another version, or with a broken header, is dropped (edits,
  reactions) or shown as `[could not decrypt]` without file data (files). It is
  never shown as text.
- Sending: clients exchange no capability or version signal, so a client seals
  whenever it holds the peer's key and never falls back to plaintext for such a
  peer; when sealing fails nothing is sent. A peer on an older client cannot read
  sealed events: an edit replaces the shown text with ciphertext, a reaction shows a
  token, and a sealed file shows as an unreadable attachment named `file`. A gateway
  that predates this format refuses sealed inline files and chunked offers (type
  check), rejects relays that carry `e2e_v`, and delivers same-gateway edits and
  reactions without the header, so the peer cannot open them. None of these paths
  carries plaintext.
- To a peer whose key is unknown, edits, reactions and files are sent in plaintext,
  as message text is (2.2).

**Limits.**

- Multi-device: events are sealed to the peer id used for single sends (the
  account's key), not fanned out per device (9.1). Other devices of the peer, and
  the sender's other devices, cannot open them and drop them.
- Delivery is live or through the relay queue. A gateway that replays a stored
  edit or reaction later (history) holds ciphertext or a token the forward ratchet
  cannot reopen, so a device that was offline keeps the earlier text and does not
  show that reaction.
- The gateway-free build does not carry edits, reactions or files
  (`podtransport.js` routes no such commands).
- On the pod-backed certificate path (8.2) the pod copies written by
  `messaging.py` and `files.py` contain the ciphertext without the header.

## 8. Delivery

### 8.1 Pod inbox drop (no gateway)

Each user's client creates `{podRoot}proxion/dm-inbox/` as an LDP basic container
with an ACL granting the owner full control and the public Append (`pod.js`
`_ensureDropBox`, `_inboxAclBody`). A sender POSTs the JSON envelope (7.2 or 7.3)
into the recipient's inbox with its own Solid session, refusing redirects and
unsafe pod roots (`_dropTo`).

The recipient lists the inbox, reads up to 200 drops per pass with a size cap on
each, and processes each independently (`_readDropBox`, `webdm.js` `drainOnce`). It
watches the container with Solid notifications to drain again on change
(`webdm.js` `start`).

For a single envelope (`_handleEnvelope`): decrypt with the ratchet; verify the
signature ([10.1](#101-envelope-signatures)); cache `x25519_pub` only if the envelope
verified; hand the message to the UI with `sender_verified`; store the plaintext in
local history; then DELETE the drop. A drop that fails to decrypt is left in place
for a later attempt.

A fan-out envelope addressed to a different `to_device_id` is left in place for the
sibling device that shares the inbox.

### 8.2 Same-gateway delivery

The gateway delivers the event in 7.4 to every live socket of the recipient
identity, stores the message (with `content` as received) in its SQLite store
(`save_message`), and, when it has a pod connected, writes the event to
`stash://pod/local_dms/<sha256(thread_id)[:16]>/<message_id>.json` on the operator's
pod (`_sync_local_dm_to_pod`). For an encrypted DM the stored `content` is
ciphertext.

A `send_dm` that carries the ratchet header (`e2e` set, or `ratchet_pub` present)
always takes this path: the gateway resolves the peer from the relationship and
handles it as a `local_dm`, so the header and ciphertext reach the recipient as
sent (`_gateway_dm.py` `_handle_send_dm`). This holds also when the gateway has a
pod client for the conversation (`dm_clients`). For such a message the gateway does
not write the additional relationship-thread copy described next, because that copy
would not carry the header.

Only a `send_dm` without the ratchet header, to a conversation in `dm_clients`, is
composed as a signed `Message` and written to the pod with an `enc1:` layer
(`messaging.py` `compose`, `msgcrypto.py`: AES-256-GCM with a key from HKDF-SHA256
over the certificate's canonical JSON, info `proxion-message-key-v1`). That layer
protects data between gateways and pods; any holder of the certificate can derive
its key, so it is not end-to-end encryption. It carries `content` and `reply_to_id`
only.

### 8.3 Cross-gateway relay

If the recipient has no socket on the sender's gateway and a peer gateway is known,
the sender's gateway POSTs to `{peerGateway}/relay` (`_handle_local_dm`):

- `from_webid` is the sending gateway's `did:key`, and the body is signed with the
  gateway's Ed25519 key over `from_webid`, `to_webid`, `message_id`, `content`,
  `timestamp` and `relay_nonce` (`relay.py` `sign_relay_message`). The ratchet header
  fields ride alongside, outside that signature.
- When the peer gateway's seal key is known, the inner payload is additionally
  sealed: ephemeral X25519 to the peer gateway's key, HKDF-SHA256 (no salt, info
  `proxion-relay-seal-v1`), ChaCha20-Poly1305, encoded as
  `base64url(eph_pub || nonce || ciphertext)` (`sealed_relay.py`). The gateway's seal
  key is derived from its Ed25519 private key with HKDF-SHA256 (salt
  `proxion-gw-x25519-v1`) and published in `/.well-known/proxion` (`gateway.py`
  `_build_discovery_data`).
  It is a gateway key, distinct from any browser's X25519 key. Gateways keep seal
  keys (their own and those learned by discovery) in a separate table
  (`gateway_seal_keys`), and the key a gateway hands its clients as a peer's E2E key
  (`get_peer_browser_e2e_key`) is never a value from that table.
- The receiving gateway verifies, unseals if needed, stores the sender's browser
  X25519 key as an E2E key (`save_e2e_key`), and delivers the same event shape as 7.4
  with `source: "relay"` (`_gateway_http.py`).

Fan-out entries for a remote account are relayed one per device with
`content_type: "dm_fanout"`, a signature scoped to `dm-fanout`, and a transport id
`<message_id>:<device_id>` (`_relay_dm_fanout_entry`, `_handle_dm_fanout_relay`).
Undeliverable relays are queued for retry; fan-out envelopes for an offline local
device are held in memory (at most 100 per device) until it reconnects
(`_queue_fanout_envelope`).

In every relay case, the receiving gateway sees the same metadata as in 8.2 and the
DM body remains ratchet ciphertext.

## 9. Multiple devices

### 9.1 Per-device sessions

Each device has its own X25519 key and its own sessions. A sender resolves the
peer's devices and its own other devices from the gateway (`get_peer_device_keys`;
`identity.js` `devicesForPeer`, `ownOtherDevices`) and, in `_tryDmFanout`:

- if the peer has two or more devices, encrypts one copy per peer device and per own
  other device, each under the session for `<account>#<device_id>`, and sends them as
  one `send_dm_fanout`;
- otherwise sends the normal single message to the peer's primary key and sends
  self-sync copies to its own other devices.

The current device is never included. A self-sync copy carries `thread_id` and
`thread_peer` so the sibling device files it under the right conversation.

The gateway returns per-device keys only for the caller's own account or a related
account when authentication is enforced (`_gateway_misc.py`
`_handle_get_peer_device_keys`). For a remote peer it fetches the roster from the
peer gateway's `POST /devices` with a signed request (`_fetch_remote_device_keys`).
The roster entries are `{device_id, pub_b64u}`; they are not individually signed by
the account key.

In the gateway-free build there is no device roster; the single key at
`proxion/identity/x25519-pub.json` is used.

### 9.2 Device certificates

A secondary device has its own Ed25519 key and a certificate from the primary
(`device-cert.js` `issueDeviceCert`, mirroring `device_cert.py`):

```json
{"@type": "ProxionDeviceCert", "version": 1, "account_did": "did:key:...",
 "device_did": "did:key:...", "issued_at": 1767225600, "expires_at": 1798761600,
 "signature": "<base64 Ed25519 over canonical bytes>"}
```

Canonical bytes: for each of `account_did`, `device_did`, `issued_at`, `expires_at`
(as decimal strings), a 2-byte big-endian length then the UTF-8 bytes, joined by a
single `0x7c` byte. The lifetime is at most 400 days. A device attaches its
certificate as `cert` to gateway-free envelopes it signs.

## 10. Authentication and verification

### 10.1 Envelope signatures

Gateway-free envelopes are signed with the device's Ed25519 identity key (`dmsig.js`
`signDm`, `signFanout`). The signed bytes are the listed fields, each converted with
`String(value)` (absent or null as empty), each prefixed with its UTF-8 length as a
4-byte big-endian integer, joined by `0x7c`:

- single envelope: `from_webid`, `message_id`, `content`, `nonce`, `msg_num`, `pn`,
  `ratchet_pub`, `x25519_pub`, `e2e`, `reply_to_id`, `from_display_name`,
  `timestamp`;
- fan-out envelope: `from_webid`, `message_id`, `to_device_id`, then `content`,
  `nonce`, `msg_num`, `pn`, `ratchet_pub`, `x25519_pub`, `e2e` read from `payload`.

`from_device_id`, `thread_id`, `thread_peer` and `reply_to_id` inside a fan-out
`payload` are not signed.

A verifier tries this scheme, then, only if none of the fields added later (`e2e`,
`reply_to_id`, `from_display_name`; or `payload.e2e`) is present, a legacy scheme with
2-byte lengths and fewer fields (`verifyDmSig`, `verifyFanoutSig`).

The recipient treats an envelope as from `from_webid` (`sender_verified`) when
(`main.js`, `verifySender`):

1. the signature verifies under the key in `signer`, and
2. the sender's `proxion/identity/signer.json`, fetched from the pod derived from
   `from_webid`, names that `signer`, or names an `account_did` for which the attached
   `cert` is a valid device certificate with `device_did == signer`.

Verification marks messages; it does not reject them. An unverified message is
shown with an unverified indicator.

### 10.2 Gateway path

On the gateway path the sender of an event is the identity the gateway
authenticated for the sending socket (Ed25519 challenge signed by the browser,
`main.js` `_respondToAuthChallenge`). Envelopes are not separately signed by the
browser. Cross-gateway relays are signed by the sending gateway (8.3).

### 10.3 Safety numbers

The client shows one safety number per DM contact (`e2e.js` `safetyNumber`), both in
the bar under the chat header and in the Verify dialog (`e2e-status.js`). It is
computed in the browser from the two X25519 public keys that encrypt the
conversation: the base64url strings are sorted, joined with `|`, and hashed with
SHA-256; the first 24 bytes are read as six big-endian 32-bit integers, each reduced
modulo 100000 and printed as five digits. Both parties compute the same value, and the
gateway takes no part in computing it.

Marking a contact verified (`markPeerVerified`) stores the contact's key that was
compared as `proxion_e2e_verified_<peer>`. `isPeerVerified` is true only while that
key is still the cached one, and `cachePeerPub` also clears the mark when the key
changes (4.3), so a new key always reads unverified. The mark drives the header
badge, the bar, and the unverified indicator on the contact's messages.

The safety number does not cover per-device X25519 keys used for fan-out.

## 11. State storage

Ratchet state for a peer id is serialized as JSON and encrypted with the state key
(AES-256-GCM, random 12-byte nonce), stored as
`{"nonce": "<base64url>", "ct": "<base64url>"}` (`_encState`). It is written:

- to localStorage under `proxion_e2e_state_<peerId>` (account-scoped by
  `e2eScopedKey`), synchronously, and
- when signed in to a pod, to
  `{podRoot}proxion/e2e/<encodeURIComponent(peerId)>/state-<device>.enc`, where
  `<device>` is the device's X25519 public key (base64url). The PUT is not awaited;
  writes for one peer run one at a time and overlapping saves collapse to the latest
  (`_queuePodWrite`). No ACL is written for this resource; it inherits the access
  control of the containing `proxion/` container.

Because the state key is a per-device, non-extractable key, a state blob is readable
only on the device that wrote it, and each device writes its own pod resource.

On load (`loadRatchetState`) the in-memory cache is used if present. Otherwise every
copy this device can decrypt is read: localStorage and the per-device pod resource,
or, when that resource is missing, the earlier shared path
`proxion/e2e/<encodeURIComponent(peerId)>/state.enc`. The copy with the highest `ver`
is used, localStorage on a tie, so a pod copy whose write was lost does not roll the
session back. When the per-device pod resource was missing, the chosen state is
written to it. A blob at the shared path written by another device fails to decrypt
and is ignored. A state with no `ver` counts as 0.

The state contains the current ratchet private key as a JWK, chain keys, root key,
skipped message keys and `ver`.

Peer keys are cached in localStorage as `proxion_e2e_peer_pub_<peerId>` (account
scoped). Decrypted messages are cached in local IndexedDB for history.

## 12. Interoperability notes

An independent client that wants to exchange DMs with Proxion over the gateway-free
path needs to:

1. Publish an X25519 key at `proxion/identity/x25519-pub.json` and an Ed25519
   `did:key` at `proxion/identity/signer.json`, both publicly readable, and create a
   public-Append `proxion/dm-inbox/` container.
2. Implement the ratchet in [section 6](#6-message-encryption) exactly, including the
   zero initial root, the `ratchet` info string, the HMAC constants `0x01` and `0x02`,
   and the shared `MAX_SKIP` bound.
3. Produce and verify the envelope and signature formats in sections 7 and 10.1.
4. Derive pod roots from WebIDs as in 4.2. Servers whose WebID layout differs from
   `{root}profile/card#me` fall back to the origin.

Known open questions:

- **No authenticated, asynchronous session setup.** There are no prekeys, so the
  first chain depends on the recipient's long-term key and does not authenticate the
  initiator (2.2).
- **No associated data.** Header fields are not bound to the ciphertext by the AEAD;
  on the gateway-free path they are covered by the envelope signature, and on the
  gateway path they are not.
- **Key-to-identity binding.** The X25519 key is not signed by the Ed25519 identity,
  and per-device keys are not signed by the account key.
- **Session reset and simultaneous initiation.** There is no message to reset a
  session, and no rule for two peers who both start a session before either message
  arrives.
- **Versioning.** The ratchet header of a message has no version field; the envelope
  (`v`), the published key file (`version`) and sealed edits, reactions and files
  (`e2e_v`) are versioned. There is no capability exchange between clients.
- **Secondary events and devices.** Sealed edits, reactions and files are not fanned
  out to every device, and are not replayable from server history (7.5).
- **Groups.** Rooms are not end-to-end encrypted. Messaging Layer Security (MLS,
  RFC 9420) is a candidate long-term direction for an interoperable specification
  that would cover both groups and multi-device; adopting it would replace the
  per-peer ratchet described here.

## 13. Test vectors

Computed by running the exported functions of `web/e2e.js` and `web/dmsig.js` under
Node.js 20. All values are hex unless noted.

**KDF_RK** (`kdfRk`)

```
rk   = 00 * 32
dh   = 01 * 32
root = 527efb32ee46108740562a4a3adc9da0f13d8f4f8757afbb47cf294876fc0d51
ck   = 012a0d827207d62176353625314fb68f1e4a17aa28d4719fba6176c6e4634eb7

rk   = 02 * 32
dh   = 03 * 32
root = a5c17db126ce4d373b50e3d8ce52428a70b1481f756b1a5fe9b3d45b17f2bcc0
ck   = 665480076e334f4d0079202998f04510048ea0eb61b80360ddb61391654f052b
```

**KDF_CK** (`advanceChain`)

```
ck   = 04 * 32
mk   = 9cccfe5fbb1a0c5ffebce52268c0219041e1168c76fea3b0474b261981e09b6a
next = 2b206f7f327240de6f794efe12ce57bbad40f6d5bfb10a414f2a76bcd250223f
```

**Initiator setup** (5.1), using the RFC 7748 section 6.1 keys: the initiator's
ratchet private key is Alice's private key and the recipient identity key is Bob's
public key.

```
r_A   = 77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a
IK_B  = de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f
dh    = 4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742
RK    = ceef2b1e3dd2a2f5033ae13f908378389b6088bbf0f667c7929e40d5746d549c
CK_s  = 8c78d629acbffec94ddd66101b33b37ab5be83319f232049771265714080b15e
MK_0  = f50714585ae25409f8bdcf9901a63069e09c576e4d98af5befcb025d71979dce
```

**Encryption-key safety number** (`safetyNumber`), inputs as base64url:

```
a  = hSDwCYkwp1R0i33ctD73Wg2_Og0mOBr066SpjqqbTmo
b  = 3p7bfXt9wbTTW2HC7OQ1Nz-DQ8hbeGdNrfx-FG-IK08
sn = 24594 99591 04903 81211 44774 95312   (same for either argument order)
```

**Canonical signed bytes** (`canonicalDmBytes`) for

```json
{"from_webid": "https://alice.example/profile/card#me", "message_id": "m1",
 "content": "Y2lwaGVy", "nonce": "bm9uY2U", "msg_num": 0, "pn": 0,
 "ratchet_pub": "UkFU", "x25519_pub": "WDI1", "e2e": true, "reply_to_id": null,
 "from_display_name": "Alice", "timestamp": "2026-01-01T00:00:00.000Z"}
```

```
0000002568747470733a2f2f616c6963652e6578616d706c652f70726f66696c652f63617264236d65
7c000000026d317c0000000859326c77614756797c00000007626d39755932557c00000001307c0000
0001307c00000004556b46557c00000004574449317c00000004747275657c000000007c0000000541
6c6963657c00000018323032362d30312d30315430303a30303a30302e3030305a
```

(One continuous byte string, wrapped for width.)

## 14. Changelog

- Unreleased: ratchet state is stored per device on the pod and the newest copy is
  loaded (11); skipped keys are bounded per message and in total, and expire by
  chain (6.6); buffered keys are tried before a DH ratchet step (6.4); `send_dm`
  with a ratchet header always goes through the relay path (8.2); gateway seal keys
  are stored apart from browser E2E keys (8.3).
- Unreleased: DM edits, reactions and file attachments are sealed with the DM
  ratchet (7.5), with `e2e_v` 1.
- Unreleased: first description of the protocol as implemented. Replaces
  `E2E_ENCRYPTION_ROADMAP.md`, which described an earlier plan.
