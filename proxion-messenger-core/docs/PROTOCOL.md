# Proxion Protocol Specification

This document describes the protocol layer of Proxion — the federated, decentralized messaging platform built on the Solid Protocol. It covers the wire format, message flow, federation semantics, and extensibility mechanisms that enable real-time communication on user-owned data pods.

## Table of Contents

1. [Overview](#1-overview)
2. [Solid Foundation](#2-solid-foundation)
3. [Pod Containers and Stash](#3-pod-containers-and-stash)
4. [Direct Messages (DM) Flow](#4-direct-messages-dm-flow)
5. [Room-based Messaging](#5-room-based-messaging)
6. [Capability Certificates](#6-capability-certificates)
7. [End-to-End Encryption](#7-end-to-end-encryption)
8. [Access Control](#8-access-control)
9. [Real-time Gateway](#9-real-time-gateway)
10. [Presence and Activity](#10-presence-and-activity)
11. [Identity and DIDs](#11-identity-and-dids)
12. [File Sharing](#12-file-sharing)
13. [Voice and Media Signaling](#13-voice-and-media-signaling)
14. [Reactions and Threading](#14-reactions-and-threading)
15. [Read Receipts and Status](#15-read-receipts-and-status)
16. [Outbox and Retry Semantics](#16-outbox-and-retry-semantics)
17. [Peer Discovery and Trust](#17-peer-discovery-and-trust)
18. [OIDC Integration](#18-oidc-integration)

---

## 1. Overview

Proxion is a federated messaging protocol where:

- **Users own their data.** All messages, identity, and metadata live on the user's Solid Pod (a WebDAV + RDF store).
- **No central server.** Communication happens directly pod-to-pod via HTTPS or through optional gateways.
- **Capabilities, not permissions.** Access is granted via cryptographic certificates that bundle identity, scope, and optional constraints.
- **Real-time bridges.** WebSocket gateways optionally bridge pods to modern web browsers without requiring pod modifications.

### Design Principles

1. **Decentralization:** No single point of failure or censorship.
2. **Privacy:** Encryption at the application layer; pods control visibility.
3. **Interoperability:** Standard Solid RDF vocabularies; any Solid pod can host Proxion data.
4. **Federation:** Certificates enable controlled cross-pod communication.
5. **Extensibility:** New message types, reactions, and signals can be added without protocol changes.

---

## 2. Solid Foundation

### Pod Structure

Each user has a Solid Pod (typically hosted at `https://user.pod/`). The pod serves as a personal data store, exposing a WebDAV interface and RDF data.

**Key endpoints:**
- `/.well-known/openid-configuration` — OIDC discovery (if pod supports OpenID Connect)
- `/profile/card` — User identity (RDF/turtle)
- `/inbox/` — LDP Inbox for notifications (RFC 6047 LDP containers)
- `/.acl` — Web Access Control (WAC) lists
- `/.acr` — Access Control Resources (ACP) for ACP-enabled pods

### Authentication

Proxion supports:

- **DPoP (OAuth 2.0 Demonstration of Proof-of-Possession):** Cryptographic proof of key ownership. Clients sign a `DPoP` header with their Ed25519 private key for each request.
- **Client Credentials + DPoP:** A pod application can register as an OAuth 2.0 client and obtain tokens scoped to specific operations.

**DPoP Flow:**
1. Client generates Ed25519 key pair.
2. For each authenticated request, client creates a `DPoP` proof: `JWT { typ: "dpop+jwt", jti: uuid, htm: "GET"|"POST", htu: url, iat: now }`.
3. Server validates proof signature against the public key (sent in Authorization header or via JWK).
4. Server binds access token to the key, preventing token replay or theft.

---

## 3. Pod Containers and Stash

### Stash Container Structure

Each Proxion agent reserves container at `stash://` (a special namespace mapped to the pod):

```
stash://
  profile/
    identity.json      # IdentityCard: display_name, bio, avatar_url, did
    avatar.*           # Avatar image (jpg/png/webp)
  messages/
    thread/
      <cert_id>/       # One container per DM (named after RelationshipCertificate ID)
        messages/      # LDP container of message JSON files
        metadata.json  # Thread metadata (created_at, last_message_at, etc.)
  rooms/
    <room_id>/         # Room container
      room.json        # RoomConfig: name, owner_webid, topic, description, public, rate_limit, read_only
      messages/        # LDP container of messages
      directory/       # (Only in owner's pod)
        <room_id>.json # Link to public room
  receipts/
    <thread_id>/       # Read receipts subdirectory
      <message_id>.json  # ReadReceipt: reader_webid, read_at
  presence/
    <thread_id>.json   # PresenceDoc: status, display_name, status_text, avatar_url, active_since
  pins/
    <thread_id>/
      <message_id>.json  # PinnedMessage: message_id, thread_id, pinned_by_webid, pinned_at
  inbox/
    entries/           # Unified inbox entries (cached from multiple sources)
      <entry_id>.json  # InboxEntry: type, from_webid, summary, timestamp, link
  outbox/
    <record_id>.json   # OutboxRecord: target_url, payload, attempt, next_retry_iso
  peers/
    <did_hash>.json    # PeerRecord: did, pod_url, display_name, last_seen_iso, trusted
```

### LDP Containers

Proxion uses **Linked Data Platform (LDP)** containers for append-only lists (messages, reactions, etc.). Each container exposes:

- `Contains: <uri1>, <uri2>, ...` — List of member URIs
- Each member is a separate JSON resource

---

## 4. Direct Messages (DM) Flow

### Initiating a DM

1. **Alice** wants to message **Bob**.
2. Alice generates a `RelationshipCertificate` (RFC 7519 JWT):
   ```
   {
     "sub": "alice@pod.example",
     "aud": "bob@pod.example",
     "iat": <now>,
     "exp": <now + 30 days>,
     "issuer": "alice@pod.example",
     "urn:proxion:scope": "direct_message",
     "urn:proxion:cert_id": "<uuid>",
     "urn:proxion:delegated": false
   }
   ```
3. Alice signs with her Ed25519 identity key.
4. Alice POSTs the certificate to Bob's inbox (or stores it in a shared location).
5. Bob retrieves and validates the certificate (signature check, expiry, aud match).
6. Both parties now have a shared thread identifier: `dm:<cert_id>`.

### Message Exchange

**Alice sends to Bob:**

1. Alice composes a `Message` object:
   ```json
   {
     "id": "<uuid>",
     "from_webid": "alice@pod.example",
     "from_pub_hex": "<alice_ed25519_pubkey_hex>",
     "content": "Hello Bob!",
     "content_type": "text/plain",
     "timestamp": "2026-04-12T04:32:02Z",
     "encrypted": false,
     "reply_to_id": null,
     "mentions": []
   }
   ```
2. If encryption enabled: Alice derives a shared key from Bob's public key, encrypts content.
3. Alice PUTs to `stash://messages/thread/<cert_id>/messages/<message_id>.json`.
4. Alice updates WAC/ACP to grant Bob read access.
5. Alice notifies Bob via POD notification or polling.

**Bob receives:**

1. Bob polls or receives push notification for new messages.
2. Bob fetches the message from Alice's stash:// container.
3. If encrypted: Bob decrypts using the shared key.
4. Bob can optionally mark message as read by writing to `stash://receipts/<thread_id>/<message_id>.json`.

---

## 5. Room-based Messaging

### Room Creation

**Owner (Alice) creates a room:**

1. Alice generates a unique `room_id` (UUID hex).
2. Alice creates `stash://rooms/<room_id>/` container with `room.json`:
   ```json
   {
     "room_id": "<uuid>",
     "name": "Music Fans",
     "owner_webid": "alice@pod.example",
     "pod_url": "https://alice.pod",
     "stash_root": "stash://rooms/<room_id>/",
     "created_at": "2026-04-12T00:00:00Z",
     "public": true,
     "topic": "Share your favorite songs",
     "description": "A place to discuss music",
     "rate_limit": null,
     "read_only": false
   }
   ```
3. If `public: true`, Alice also writes to `stash://rooms/directory/<room_id>.json` (for discovery).
4. Alice sets WAC ACL granting herself Control, and initially no one else.

### Room Membership

**Bob joins room:**

1. Bob retrieves the room config from Alice's pod.
2. Bob generates a `RelationshipCertificate` for the room:
   ```
   {
     "sub": "bob@pod.example",
     "aud": "<room_id>",
     "urn:proxion:cert_id": "<cert_id>",
     "urn:proxion:scope": "room_membership"
   }
   ```
3. Bob sends (via invite code or direct link) to Alice.
4. Alice validates and approves. Alice updates WAC ACL: grants Bob read+write to `stash://rooms/<room_id>/messages/`.
5. Bob can now send and read messages in the room.

### Room Messages

Messages in a room are similar to DM messages but stored in `stash://rooms/<room_id>/messages/`.

- Room messages can be read by all approved members (ACL grants Read).
- Only members with Write access can send.
- Owner can delete messages or remove members (modify ACL).

---

## 6. Capability Certificates

### RelationshipCertificate

A signed JWT that grants scoped access. Structure:

```
Header: {
  "typ": "JWT",
  "alg": "EdDSA",
  "kid": "<public_key_hash>"
}

Payload: {
  "iss": "issuer_webid",
  "sub": "subject_webid",
  "aud": "audience (dm recipient or room_id)",
  "iat": <issued_at>,
  "exp": <expiry>,
  "urn:proxion:cert_id": "<unique_cert_id>",
  "urn:proxion:scope": "direct_message|room_membership|delegation",
  "urn:proxion:delegated": false|true
}

Signature: Ed25519(issuer_private_key, header.payload)
```

### Validation Rules

1. **Signature:** Must verify against issuer's public key (obtained from issuer's identity card).
2. **Expiry:** `exp` must be in the future.
3. **Subject/Audience:** `sub` must match the holder, and `aud` must match the target.
4. **Scope:** Determines allowed operations (send message, join room, delegate further).

### Delegation

A certificate can grant authority to a third party (e.g., a gateway, bot, or client library) by setting `"urn:proxion:delegated": true`. The third party can then act on behalf of the original holder with the same scope constraints.

---

## 7. End-to-End Encryption

### Key Derivation

When Alice wants to encrypt messages for Bob in a DM:

1. Alice derives a **message key** from the shared certificate:
   ```
   message_key = HKDF-SHA256(
     key=certificate_bytes,
     salt=thread_id,
     info="proxion-message",
     length=32
   )
   ```
2. Alice uses `message_key` for ChaCha20-Poly1305 AEAD encryption.

### Encryption Format

Each encrypted message has:

```json
{
  "id": "<uuid>",
  "from_webid": "alice@pod.example",
  "encrypted": true,
  "ciphertext": "<base64(ChaCha20-Poly1305(plaintext))>",
  "nonce": "<base64(12-byte random nonce)>",
  "aad": "<certificate_id>",
  "timestamp": "..."
}
```

### Optional Compression

Before encryption, the message is optionally compressed with zlib to reduce ciphertext size.

---

## 8. Access Control

### WAC (Web Access Control)

Traditional Solid ACLs using `.acl` files (Turtle RDF):

```turtle
@prefix acl: <http://www.w3.org/ns/auth/acl#>.
@prefix foaf: <http://xmlns.com/foaf/0.1/>.

<#owner>
  a acl:Authorization;
  acl:agent <https://alice.pod/profile/card#me>;
  acl:mode acl:Read, acl:Write, acl:Control;
  acl:default <stash://rooms/r1/>.

<#members>
  a acl:Authorization;
  acl:agent <https://bob.pod/profile/card#me>,
           <https://charlie.pod/profile/card#me>;
  acl:mode acl:Read, acl:Write;
  acl:default <stash://rooms/r1/>.
```

**Evaluation:** If a client (identified by WebID + certificate) makes a request, the server evaluates the ACL rules to decide Read/Write/Control.

### ACP (Access Control Policy)

Newer Solid pods support ACP (RFC forthcoming) via `.acr` resources (JSON-LD):

```json
{
  "@context": "http://www.w3.org/ns/solid/acp#",
  "policy": {
    "allow": ["Read", "Write"],
    "allOf": [{ "agent": "https://bob.pod/profile/card#me" }]
  },
  "owner": {
    "allow": ["Read", "Write", "Control"],
    "allOf": [{ "agent": "https://alice.pod/profile/card#me" }]
  }
}
```

**Auto-Detection:** Proxion probes the pod's Link header to detect WAC (`rel="acl"`) vs. ACP (`rel="acr"`), then uses the appropriate API.

---

## 9. Real-time Gateway

### Gateway Architecture

A **ProxionGateway** is an optional server that bridges pods to WebSocket clients (e.g., a web browser).

**Connection Flow:**

1. Client connects to gateway via WebSocket.
2. Gateway queries the agent's local pod for pending messages/events.
3. Gateway sends events to client in real-time.
4. Client sends commands (send_message, set_presence, etc.) to gateway.
5. Gateway validates, then writes changes back to pod(s).

### Event Types

```
{
  "type": "message",
  "thread_id": "dm:cert123",
  "message": { ... Message object ... }
}

{
  "type": "message_edited",
  "message_id": "msg1",
  "new_content": "Updated text"
}

{
  "type": "presence",
  "thread_id": "room:r1",
  "user_webid": "bob@pod.example",
  "status": "Online"
}

{
  "type": "message_read",
  "thread_id": "room:r1",
  "message_id": "msg1",
  "reader_webid": "bob@pod.example"
}

{
  "type": "error",
  "message": "Unknown thread"
}
```

### Command Handler Patterns

Clients send commands like:

```json
{
  "cmd": "send_message",
  "thread_id": "room:r1",
  "content": "Hello everyone!"
}

{
  "cmd": "mark_read",
  "thread_id": "room:r1",
  "message_id": "msg1"
}

{
  "cmd": "set_presence",
  "status": "Busy",
  "status_text": "In a meeting"
}
```

The gateway validates each command and updates state atomically.

---

## 10. Presence and Activity

### Presence Document

Each thread has a presence document at `stash://presence/<thread_id>.json`:

```json
{
  "thread_id": "room:r1",
  "user_webid": "alice@pod.example",
  "status": "Online|Away|Busy",
  "display_name": "Alice",
  "status_text": "Listening to music",
  "avatar_url": "stash://profile/avatar.png",
  "active_since": "2026-04-12T04:00:00Z"
}
```

### Presence Polling

The gateway polls presence every 60 seconds and broadcasts changes to connected clients. Presence is transient; old presence documents may be pruned after a TTL (e.g., 24 hours).

---

## 11. Identity and DIDs

### IdentityCard

Each user publishes an identity card at `stash://profile/identity.json`:

```json
{
  "display_name": "Alice",
  "avatar_url": "stash://profile/avatar.png",
  "bio": "Building the decentralized web",
  "proxion_version": "0.1.0",
  "did": "did:key:z6Mk..."
}
```

### DID (Decentralized Identifier)

Proxion uses **did:key** (W3C specification) for portable peer identification:

```
did:key:z6Mk...
    ^^^^^^^^
    base58btc(0xed01 + 32-byte-ed25519-pubkey)
```

**Advantages:**
- No central registry.
- Uniquely identifies a cryptographic key.
- Enables federation without pod URLs.

### DID Resolution

Given a DID, a client can:

1. Extract the public key: `did_to_pub_key(did) -> 32-byte pubkey`.
2. Use it to verify signatures on certificates or messages signed by that agent.

---

## 12. File Sharing

### FileAttachment

Files are shared via the `FileAttachment` abstraction:

```json
{
  "file_id": "<uuid>",
  "filename": "presentation.pdf",
  "mime_type": "application/pdf",
  "size": 1024000,
  "url": "stash://files/<file_id>/<filename>",
  "checksum": "sha256=...",
  "uploader_webid": "alice@pod.example",
  "uploaded_at": "2026-04-12T04:00:00Z"
}
```

### Upload Flow

1. Alice creates `stash://files/<file_id>/` container.
2. Alice PUTs file bytes to `stash://files/<file_id>/<filename>`.
3. Alice creates a message with `FileAttachment` metadata.
4. Alice grants members read access via ACL.

### Download Flow

1. Bob receives message with FileAttachment.
2. Bob fetches file from Alice's `stash://files/<file_id>/<filename>`.
3. Bob verifies checksum (optional).

---

## 13. Voice and Media Signaling

### VoiceInvite

WebRTC voice calls use **VoiceInvite** signaling:

```json
{
  "invite_id": "<uuid>",
  "from_webid": "alice@pod.example",
  "to_webid": "bob@pod.example",
  "thread_id": "dm:cert123",
  "channel_type": "group|direct",
  "sdp_offer": "v=0\no=...",
  "created_at": "2026-04-12T04:00:00Z",
  "expires_at": "2026-04-12T04:05:00Z"
}
```

### Signaling Flow

1. Alice generates WebRTC SDP offer and posts VoiceInvite to Bob's inbox.
2. Bob retrieves invite, generates SDP answer, and posts back.
3. Alice retrieves answer.
4. Both parties exchange ICE candidates via follow-up messages.
5. STUN/TURN servers facilitate NAT traversal.

**TURN Credentials:** Server provides HMAC-signed TURN credentials with 24-hour TTL tied to `selfWebId`.

---

## 14. Reactions and Threading

### Reactions

Reactions are **messages** with type `reaction`:

```json
{
  "id": "<uuid>",
  "type": "reaction",
  "from_webid": "bob@pod.example",
  "target_message_id": "msg1",
  "emoji": "❤️",
  "timestamp": "2026-04-12T04:01:00Z"
}
```

Stored in the same container as regular messages. Clients aggregate by target_message_id.

### Threading / Replies

Messages can include `reply_to_id`:

```json
{
  "id": "<uuid>",
  "from_webid": "bob@pod.example",
  "content": "I agree!",
  "reply_to_id": "msg1",
  "timestamp": "2026-04-12T04:02:00Z"
}
```

**Thread Tree Construction:** Given a root message, collect all replies recursively to build a tree of responses.

---

## 15. Read Receipts and Status

### ReadReceipt

Bob marks a message as read by writing:

```json
{
  "message_id": "msg1",
  "thread_id": "room:r1",
  "reader_webid": "bob@pod.example",
  "read_at": "2026-04-12T04:03:00Z"
}
```

To `stash://receipts/<thread_id>/<message_id>.json`.

### Polling

Alice can poll `stash://receipts/room:r1/` to see all read receipts for a room, or `stash://receipts/room:r1/msg1.json` for a single message.

---

## 16. Outbox and Retry Semantics

### OutboxRecord

Failed sends are queued for retry:

```json
{
  "id": "<uuid>",
  "target_url": "https://bob.pod/inbox/",
  "payload": { "type": "certificate", "jwt": "..." },
  "attempt": 1,
  "next_retry_iso": "2026-04-12T04:05:00Z",
  "created_iso": "2026-04-12T04:00:00Z"
}
```

### Retry Logic

1. Base delay: 10 seconds.
2. Exponential backoff: delay *= 2^(attempt - 1), capped at 1 hour.
3. Max attempts: 10.
4. On final failure: broadcast alert to user (e.g., "Message delivery failed").

---

## 17. Peer Discovery and Trust

### PeerRecord

Known peers are cached:

```json
{
  "did": "did:key:z6Mk...",
  "pod_url": "https://bob.pod",
  "display_name": "Bob",
  "last_seen_iso": "2026-04-12T04:00:00Z",
  "trusted": true
}
```

### Trust Models

1. **Manual:** User explicitly marks peer as trusted.
2. **Discovery:** Peers are discovered via room invites, DM certificates, or federation lookups.
3. **PKI:** Future: certificate pinning, revocation checks.

---

## 18. OIDC Integration

### OpenID Connect Discovery

Pods may expose OIDC endpoints:

```
GET /.well-known/openid-configuration
{
  "issuer": "https://pod.example",
  "authorization_endpoint": "https://pod.example/authorize",
  "token_endpoint": "https://pod.example/token",
  "registration_endpoint": "https://pod.example/register",
  "jwks_uri": "https://pod.example/.well-known/jwks.json"
}
```

### WebID to Issuer

Parse user's identity document for `oidcIssuer` claim:

```turtle
@prefix solid: <http://www.w3.org/ns/solid/terms#>.
<#me>
  solid:oidcIssuer <https://accounts.example> .
```

### Dynamic Registration

Apps can register with a pod's OAuth2 provider:

```
POST /register
{
  "application_type": "native",
  "redirect_uris": ["http://127.0.0.1:8080/callback"],
  "token_endpoint_auth_method": "none"
}

Response:
{
  "client_id": "...",
  "client_id_issued_at": 1234567890,
  "expires_at": 0,
  "redirect_uris": [...]
}
```

---

## Summary

Proxion combines **Solid Protocol** (pod-based data ownership), **cryptographic certificates** (scoped access), **RDF vocabularies** (standard semantics), and **real-time gateways** (modern UX) to enable a decentralized messaging platform where users control their data, third parties cannot intercept or censor, and interoperability is built-in.

The protocol is extensible: new message types, signals, and vocabulary terms can be added without breaking existing clients. Security relies on industry-standard cryptography (Ed25519, ChaCha20-Poly1305, HMAC) and Web standards (HTTP, OAuth2, OIDC, WebRTC).

---

*Document version: 1.0*  
*Last updated: 2026-04-12*
