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
7. [Envelope format](#7-envelope-format)
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
Solid pod (`web/webdm.js`).

Not covered by this encryption layer:

- Room messages. Rooms are shared Solid Long Chat resources and are not end-to-end
  encrypted (see [POD_DATA_MODEL.md](POD_DATA_MODEL.md)).
- DM edits (`web/edit.js` `commitEdit` sends `edit_message` / `edit_local_message`
  with the new text in `content`), reactions (`web/reactions.js`, `add_reaction`
  with the emoji in `emoji`) and file attachments (`send_file` with `data_b64`).
  These are sent to the gateway without ratchet encryption.
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
  notifications carry a thread id and display name (`_push_offline_dm`).
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
| `skippedKeys` | map `"<ratchet_pub>:<msg_num>"` to base64url message key |

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

1. `ratchet_pub` MUST be present; otherwise decryption fails.
2. Load state and deep-copy it. If none, set up as responder ([5.2](#52-responder)).
   Else if `ratchet_pub` differs from `theirRatchetPub`, perform the receiving half of
   the DH ratchet using `pn`.
3. If `"<ratchet_pub>:<msg_num>"` is in `skippedKeys`, use and remove that key.
4. Else if `msg_num < recvMsgNum`, fail ("already consumed").
5. Else step the receiving chain, buffering each skipped message key, until
   `recvMsgNum == msg_num`, then derive `MK` and step once more.
6. Decrypt. Only if the tag verifies is the updated state saved. On any failure the
   stored state is unchanged.

### 6.5 DH ratchet

Receiving half (`_dhRatchetReceive`), on a new `ratchet_pub` `R'`:

```
buffer skipped keys of the current receiving chain up to pn
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

`MAX_SKIP` is 20 (`e2e.js`). It bounds the total number of entries in `skippedKeys`
across all chains, not per chain. A receiver MUST fail decryption, without changing
stored state, when buffering one more skipped key would exceed the bound, both in
the in-chain loop and while advancing to `pn` during a DH ratchet. Buffered keys
have no expiry; they are removed only when the matching message arrives.

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

There is no protocol version field in the ratchet header.

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

When the gateway also holds a relationship certificate and pod client for the
conversation (`dm_clients`), `send_dm` instead composes a signed `Message` and writes
it to the pod with an additional `enc1:` layer (`messaging.py` `compose`,
`msgcrypto.py`: AES-256-GCM with a key from HKDF-SHA256 over the certificate's
canonical JSON, info `proxion-message-key-v1`). That layer protects data between
gateways and pods; any holder of the certificate can derive its key. It carries the
`content` and `reply_to_id` of the command; it does not carry the ratchet header
fields.

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
  `proxion-gw-x25519-v1`) and published in `/.well-known/proxion` (`gateway.py`).
  It is a gateway key, distinct from any browser's X25519 key.
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

Two values are shown in the client:

- **Encryption-key safety number** (`e2e.js` `safetyNumber`, shown by
  `e2e-status.js` `_openVerifyModal`): the two X25519 public keys as base64url
  strings are sorted, joined with `|`, and hashed with SHA-256; the first 24 bytes are
  read as six big-endian 32-bit integers, each reduced modulo 100000 and printed as
  five digits. Confirming it stores `proxion_e2e_verified_<peer>` = `1` locally; the
  mark is cleared when the cached key changes (4.3).
- **Identity safety number** (`GET /fingerprint/<did>` on the user's own gateway,
  `safety_numbers.py`): SHA-512 over the two `did:key` strings and Ed25519 public keys
  in sorted order, each id length-prefixed, folded into 12 groups of five digits. It is
  computed by the user's gateway from the gateway's own identity key and the peer's
  DID, and covers Ed25519 identities only.

Neither value covers per-device X25519 keys used for fan-out.

## 11. State storage

Ratchet state for a peer id is serialized as JSON and encrypted with the state key
(AES-256-GCM, random 12-byte nonce), stored as
`{"nonce": "<base64url>", "ct": "<base64url>"}` (`_encState`). It is written:

- to localStorage under `proxion_e2e_state_<peerId>` (account-scoped by
  `e2eScopedKey`), and
- when signed in to a pod, to `{podRoot}proxion/e2e/<encodeURIComponent(peerId)>/state.enc`
  with a non-awaited PUT. No ACL is written for this resource; it inherits the access
  control of the containing `proxion/` container.

On load the in-memory cache is used first, then the pod copy, then localStorage
(`loadRatchetState`). Because the state key is a per-device, non-extractable key, a
state blob is readable only on the device that wrote it. The state contains the
current ratchet private key as a JWK, chain keys, root key and skipped message keys.

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
- **Versioning.** The ratchet header has no version field; only the envelope (`v`)
  and the published key file (`version`) are versioned.
- **Scope.** Edits, reactions and attachments are outside the encrypted channel (1).
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

- Unreleased: first description of the protocol as implemented. Replaces
  `E2E_ENCRYPTION_ROADMAP.md`, which described an earlier plan.
