# Proxion Universal Architecture - Technical Reference

Proxion is a federated, decentralized messaging platform built on the Solid (Social Linked Data) protocol. Unlike traditional "walled-garden" platforms, Proxion operates without a central server. All user data, including identity, messages, and relationships, remains under the user's direct control within their **Solid Pod**.

This document serves as the primary technical reference for the Proxion protocol stack.

---

## 1. Stack Diagram

The architecture bridges lightweight clients with heavy cryptographic Pod interactions via a local Gateway sidecar.

```ascii
                                +-----------------------------+
                                |  1. Client Applications     |
                                |  (Web UI, Tauri Shell, CLI) |
                                +-----------------------------+
                                              |
                                              | JSON Websockets over wss://
                                              | HTTP Local API
                                              v
+-----------------------------------------------------------------------------------------+
|                                2. Proxion Gateway (gateway.py)                          |
|                                                                                         |
|  [ WebSocket Sub-protocol ]     [ Outbox / Sync ]        [ Identity & Rate Limiter ]    |
|  - Real-time command intake     - Offline queuing        - AgentState & cert cache      |
|  - Event broadcasting           - Push/Poll Syncing      - Blocklist enforcement        |
+-----------------------------------------------------------------------------------------+
                                              |
                                              | Python Function Calls
                                              v
+-----------------------------------------------------------------------------------------+
|                                3. Proxion-Core Library                                  |
|                                                                                         |
|  [ Federation ]  [ Cryptography ]  [ Resource Management ] [ Media / E2E Engine ]       |
|  - Handshakes    - AES-256-GCM     - room.py               - voice.py signaling         |
|  - DPoP Tokens   - Ed25519 Sigs    - readstate.py          - files.py attachment cap    |
+-----------------------------------------------------------------------------------------+
                                              |
                                              | DPoP Authenticated HTTP (Solid WAC/ACP)
                                              | Notifications (WebSocketChannel2023)
                                              v
+-----------------------------------------------------------------------------------------+
|                                4. Solid Pod Providers                                   |
|                                                                                         |
|  [ Pod 1 (Alice) CSS ]         [ Pod 2 (Bob) ESS ]         [ Pod 3 (Charlie) ]          |
|  - stash://                    - stash://                  - stash://                   |
|  - Permissions                 - Permissions               - Permissions                |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Decentralized Identity & Data (Solid)

### 2.1 The Solid Pod
Every Proxion user has a Pod (Personal Online Data Store). The Pod is a standard web server that supports the Solid Protocol, providing:
- **WebID**: A unique URI that serves as the user's global identifier.
- **Resource Storage**: Data is stored as Linked Data (JSON-LD, Turtle) or raw blobs.
- **Access Control (WAC)**: Fine-grained permissions (Read, Write, Control) managed via `.acl` files.

### 2.2 Identity Model
Identity revolves around the combination of an Ed25519 Keypair and the WebID.
- **AgentState**: A local persistence snapshot carrying the private keys and the base Solid URL identifiers.
- **Federation Certificates**: `RelationshipCertificate`s are generated during mutual opt-in handshakes.
- **Certificate Lifecycle**: Certificates inherently enforce timeouts on capability authorizations between independent Pods. Expiration requires `renew_cert` procedures before messages can continue to flow. 
- **DPoP**: We use Demonstrating Proof-of-Possession (DPoP) at the HTTP layer, proving key ownership dynamically per operation.

### 2.3 The "Stash"
To facilitate federation without complex discovery, Proxion standardizes paths within the user's Pod, referred to as the **Stash**:
- `stash://profile/`: Identity cards, avatars, and presence status.
- `stash://messages/`: Personal DMs.
- `stash://rooms/`: Multi-user chat rooms.
- `stash://outbox/`: Local persistent queue for offline delivery.

---

## 3. Direct Message Flow (DMs)

A simple one-on-one DM utilizes strict E2E encryption and writes directly to the destination Pod.

### Step-by-Step Execution:
1. **Compose**: Client commands `{"cmd": "send_dm", "content": "..."}`.
2. **Encrypt**: Data passes to `msgcrypto.py`. An AES-GCM string is returned with `enc1:` prefixed.
3. **Sign**: The `Message` structure generates an Ed25519 signature over its canonical bytes.
4. **Push**: The gateway PUTs the message payload into the targeted recipient's `.acl` granted stash folder `stash://messages/thread/{cert_id}/`.
5. **List**: Recipient's background listener lists `.json` items locally on their pod.
6. **GET**: Downloads the newly observed `{message_id}.json`.
7. **Decrypt**: The local recipient generates the symmetrical cryptographic material via their `RelationshipCertificate` HKDF hash properties to seamlessly reconstruct the original layout.

---

## 4. Room Message Flow

Unlike traditional groups on walled-gardens, instances replicate history asynchronously per individual Pod natively offloading single-points of failure across decentralized resources. There is no central server dictating moderation or hosting logic permanently. 

### 4.1 Topology Overview
1. Alice organizes a Room creating the generic layout inside her `stash://rooms/{room_id}` folder natively establishing an ownership hierarchy dynamically.
2. She grants Bob and Charlie Read+Write `.acl` (or ACP) abilities providing them remote interaction privileges onto her Solid Resource targets.
3. Every single message pushed into this room inherently utilizes Alice's endpoint node as the delivery transit vehicle.
4. If Alice turns off her Pod, Bob and Charlie can no longer utilize the specific URI boundaries attached to Alice's domain mapping resulting in offline communication limitations.
5. Mitigation natively exists utilizing `mirror.py` application logic. Clients intelligently scrape E2E histories recursively mirroring data offline directly into localized `/stash/` implementations mitigating host uptime inconsistencies efficiently.

### 4.2 Handling Concurrency Issues Locally
Since Solid pods lack generic ACID transactional database logic natively via standard `PUT` file mapping, race conditions for identically timestamped payloads occur uniquely. Proxion resolves indexing collisions exclusively via `readstate.py` mechanisms forcing local chronological aggregation against the actual HTTP Modified headers dynamically rendering sorted timelines predictably for users.

---

## 5. E2E Encryption Cryptography

Proxion defaults to utilizing zero-knowledge **AES-256-GCM** properties securing user interaction models natively against untrustworthy Pod hosts, alongside strong forward-secrecy capabilities generated per handshake interaction.

### 5.1 Technical Primitive Choices
- **Asymmetric**: Ed25519 (RFC 8032) is exclusively utilized for all identity tracking and data-integrity signature mapping, generating extremely small payload overhead inside JSON blobs.
- **Key Derivation (KDF)**: `HKDF-SHA256` derives a symmetric AES-256 wrapping key mapping the shared `RelationshipCertificate` bytes recursively against a fixed contextual application salt, mitigating local derivation attacks.
- **Symmetric Block Cipher**: `AES-256-GCM` provides Authenticated Encryption with Associated Data (AEAD) ensuring tampering to the text locally causes an `InvalidTag` rejection failure explicitly blocking manipulation locally by pod operators. E.g. A pod manager cannot simply alter a "yes" message into a "no" message without possessing the derivation materials.

### 5.2 Decryption Pipeline Workflow
1. The raw `Message` JSON string payload is analyzed locally verifying `signature` bindings match perfectly against the `from_pub_hex` metadata element. 
2. The payload string extracts the `enc1:` header enforcing compatibility boundaries explicitly. Older generation text simply renders natively if missing.
3. The suffix bytes are processed through standard Web-Safe Base64Url un-encoding routines exposing the underlying Ciphertext and 16-byte nonce/IV initialization vectors.
4. The locally derived `RelationshipCertificate` HKDF bytes construct the local symmetric AEAD context exposing the underlying UTF-8 encoded text payloads into the UX interfaces directly.

### 5.3 Operator Access Limits
- The host serving the `.json` resource natively receives the ciphertext and validation signature alone. 
- While the host cannot read the message directly, they can identify the underlying user interaction topology graph simply parsing `stash://messages/{cert_id}/` boundaries identifying activity logs natively. 

---

## 6. Voice Architecture

Proxion uses a **Signaling over Messaging** pattern eliminating conventional STUN/TURN overhead requirements for establishing basic connections.

1. **Signaling via Pods**: SDP offers, SDP answers, and asynchronous ICE candidates are serialized directly into standard JSON Proxion messages injected straight into the recipient's thread containers natively.
2. **WebRTC Pipeline**: The payload completes P2P resolution enabling video pipelines bypassing all proprietary networking.
3. **STUN / TURN Relays**: Instances where double symmetric NAT firewalls limit WebRTC require backend assistance. Servers can adopt standard `coturn` deployments generating ephemeral 24-hour MAC-validated tokens locally via client side JavaScript (`web/index.html`) using standard SubtleCrypto methodologies.

---

## 7. Security Properties

Because Proxion utilizes conventional web protocols (Solid WAC/ACP), structural metadata is implicitly public to the host provider while payload data remains secured.

| Data Type                | Protected By         | Visible To Pod Operator        |
| :----------------------- | :------------------- | :----------------------------- |
| **Message Content**      | E2E Encryption (AES) | No                             |
| **Reaction Emoji**       | E2E Encryption       | No                             |
| **Sender WebID**         | Metadata Field       | **Yes**                        |
| **Timestamp**            | Metadata Field       | **Yes**                        |
| **Attachment Filename**  | Metadata Field       | **Yes**                        |
| **Presence Status**      | Public Resource      | **Yes**                        |
| **Room Membership**      | WAC / ACP ACLs       | **Yes** (via .acl / .acr tags) |

---

## 8. Development & Implementations

### 8.1 CSS Pod Deployments
Standardized development pipelines run Docker-based `Community Solid Server` images enabling flexible TLS implementation strategies utilizing generic node web reverse proxies mapping traffic streams into `nginx` boundaries:
```yaml
# docker-compose.yml 
version: "3.8"
services:
  css:
    image: solidproject/community-server:latest
    ports:
      - "3000:3000"
    volumes:
      - ./data:/data
```

### 8.2 Key Rotation Procedures
In the event an AgentState is leaked, E2E rotations are processed entirely locally mapping a new underlying private key over the unified WebID footprint via:
1. `RevocationList` instantiation broadcast updating signature expiration protocols cross network.
2. Peer connections dropping all messages derived post-revocation flag.

### 8.3 Federation Across Pods
- Proxion embraces universally standardized LinkedData, mitigating compatibility conflicts.
- Implementers must identify between legacy WAC vs ACP protocol permissions dynamically checking namespace flags natively.

### 8.4 Troubleshooting Notes
- **WAC Returns 403**: Reauthorize the underlying relationship handshake confirming `DPoP` payload matches. This usually indicates that the `aud` (audience) claim does not accurately represent the Solid Identity Provider (IdP). Double check that the Capability Access Tokens derived from the `RelationshipCertificate` possess the correctly padded HMAC validations expected by the local server verification intercept. Ensure agent state `.json` stores have synchronized.
- **Token Scope Expiration**: Trigger periodic renewals internally ensuring the gateway loop automatically reconstructs expired authorization flags. The OAuth 2.0 Solid-OIDC implementation generally expires access tokens within strict boundaries (e.g., 5 minutes) necessitating a continuous loop processing daemon natively polling `/.well-known/openid-configuration` endpoints validating signing keys.
- **NAT Filtering Issues**: Reference `ops/setup_coturn.sh` instructions to force local clients through unified TURN resolution mapping via UDP pipelines. When WebRTC signaling hits an ICE candidate phase returning purely `host` IP nodes which are unmappable, you must ensure the dynamic WebCrypto generated HMAC-SHA1 tokens injected into the `iceServers` array haven’t drifted outside local machine time syncing tolerances (often strict 24-hour UTC validation paths).
- **Stale Inbox Deduplication**: Investigate the cached `~/.proxion/rooms` or readstate pointers to evaluate if the long-polling loop dropped a `WebSocketChannel2023` event push mechanism. Purging the `readstate.json` file natively re-syncs the entire timeline.
- **WireGuard Routing**: Legacy node tunnels deployed out of `wg.py` might enter zombie states if the `wg-quick` down functions fail explicitly. Investigate the `/etc/wireguard/wg0.conf` to scrub `AllowedIPs` and purge offline nodes via `wg_show()`.

---

## 9. Data Portability and Mirroring

Because users interact across independent server limits, Data Mirroring ensures content remains accessible across platform closures or unexpected bans. 

### 9.1 Selective Archival
The Gateway continuously supports `mirror.py` application workflows natively iterating through shared `stash://rooms/{room_id}` content. Because E2E keys reside within local `AgentState`, a malicious or failing Pod simply restricts further messages. Previous threads mirrored natively backward into personal local Storage remain persistently readable forever.

### 9.2 Complete Extraction
All history is intrinsically independent. The `export_thread_to_markdown()` functionalities ensure users walk away with fully serialized plain-text records of every encrypted interaction they've participated in, bypassing vendor-lock entirely.

---

## 10. Conclusion and Future Vision

The scope of Proxion targets building an inherently hostility-resistant environment enabling true communications autonomy without the heavy technical burdens typically associated with federated landscapes like Matrix or P2P DHT meshes. By offloading routing onto standardized Semantic Web boundaries, any simple HTTP file-storage platform acting as a Solid Provider natively assumes the heavy-lifting of network transit. E2E encryption secures the trust boundary entirely onto the endpoint clients. 

## 11. Notifications and Push Delivery

### 11.1 The Polling Baseline
By default, the Proxion architecture heavily favors a standard polling loop across decentralized resources. While polling inherently wastes bandwidth against empty inboxes, it guarantees universal compatibility across any un-specialized HTTP hosting service acting natively as a Solid Provider.

### 11.2 WebSocketChannel2023 
For modern, fast-paced environments, Proxion integrates with the `Solid Notifications Protocol`, preferentially targeting `WebSocketChannel2023`.
- The Gateway detects the `Link: <...>; rel="http://www.w3.org/ns/solid/terms#storageDescription"` targets indicating Push Notification availability.
- It spins off an independent, unblocking Python `asyncio.Task` natively subscribing to the endpoint URL.
- Incoming traffic immediately triggers the inbox parser bypassing the poll loop explicitly, yielding instant "WhatsApp-style" responsiveness mapped natively onto open standards.

## 12. Threat Modeling & Cryptographic Weaknesses

No system is entirely foolproof. Proxion's architecture explicitly documents recognized threat boundaries natively enabling users to gauge personal operational security thresholds accurately.

### 12.1 Pod Operator Collusion
Because routing leverages standard Web semantics, a malicious Node operator natively intercepts timing boundaries identifying explicit connections across users (the "Metadata Problem"). Operators can definitively identify *who* Alice is explicitly talking to, and *when*, even if they cannot decrypt the payloads natively.

### 12.2 Cipher Downgrade Attacks
The Gateway specifically searches payload prefixes ensuring payloads begin identically with `enc1:`. A man-in-the-middle maliciously stripping this header dynamically converts the rendering engine backward into plaintext-parsing strategies. Users natively relying on the platform's E2E encryption must audit client implementations avoiding automatic rendering of unauthenticated strings. 

### 12.3 Key Escalation
Proxion implements standard `Ed25519` key boundaries bound directly alongside `AgentState`. An adversary capturing this JSON block permanently secures impersonation privileges identical strictly to the underlying user inherently until explicit `RevocationList` updates flag the environment.

## 13. Application Hosting & Tauri Integrations

Proxion embraces generic deployment boundaries avoiding explicit requirement chaining against cloud infrastructure natively.

### 13.1 Local First 
The standard CLI and Web UI pipelines operate locally natively bypassing cloud requirements. Running `proxion chat gateway` establishes the socket interface mapping `127.0.0.1:7474`, strictly prohibiting external REST requests outside the `localhost` footprint protecting internal daemon topologies gracefully.

### 13.2 Tauri Application Shell
For consumer availability, the HTML/CSS/JS frontend leverages a Rust native bounding wrapper natively implemented utilizing Tauri Application configurations.
- The `SystemTray` implementations integrate natively onto macOS, Ubuntu, and Windows environments alerting users asynchronously utilizing Desktop push-notifications decoupled from the Web browser contexts natively.
- Background process polling hooks directly into Rust's multithreading environments bypassing generic browser memory throttles explicitly.

## 14. File Storage & Block Validation

Because messaging natively involves media interactions inherently increasing the payload bandwidth requirements dramatically against centralized Pod providers, Proxion implements strict usage enforcement boundary controls explicitly. 

### 14.1 Outbox Chunking
1. The `files.py` engine isolates outgoing attachments evaluating exact boundary file limits natively avoiding network traffic payloads exceeding 10 Megabytes dynamically.
2. The logic specifically restricts `.mime` derivations ensuring attachments bypass generic execution mapping matching standard `application/pdf`, `image/*`, or `video/*` variants explicitly preventing arbitrary zero-day malware distribution.
3. The attachment uploads into the dedicated `stash://files/{cert_id}` folders natively while the textual `Message` block embeds identical URI links cross-referencing the object dynamically via HTTP.

## 15. The Solid Community Standard

Proxion remains committed ensuring interoperability across standard web-boundary definitions. Implementers heavily favor natively writing properties inside standard `JSON-LD` (Linked Data) formats ensuring generic third-party applications inherit Proxion data mapping easily. A user deleting their Proxion installation seamlessly retains complete history ownership natively through generic standard web browsers parsing `https://alice.solidcommunity.net/stash/` implicitly. 

## 16. WebID Document Standard

The WebID document serves as the absolute source of truth for an actor's presence on the network.
- Hosted invariably at the root `/profile/card#me` URI natively against the Solid Protocol.
- Encoded using Turtle (`.ttl`) or `JSON-LD` mapping explicitly to W3C ontologies dynamically.
- Contains the Ed25519 public keys used for signature verification during the mutual capability handshake.

### 16.1 Modifying WebID Profiles
Proxion applications natively assist users modifying their decentralized identity cards. Updating the `<http://xmlns.com/foaf/0.1/name>` property automatically propagates the "Display Name" modifications out to federated endpoints passively during the next read cycle inherently avoiding active push notification strains for non-critical cosmetic updates.
Users explicitly managing their WebID endpoints manually must exercise extreme caution. Stripping the decentralized Identity Key bindings automatically invalidates 100% of underlying local Proxion `AgentState` caches preventing decryption operations universally until the identical key boundaries are restored onto the public file natively.

## 17. User Discovery & Social Graphs

Unlike centralized indexes mapping phone numbers or search strings natively against arbitrary strings, Proxion mandates strict Peer-to-Peer discovery preventing massive botnet scrapes natively. 
To share an identity, the Inviter constructs an offline serialized payload encoded carefully into the `prx1_` prefixed URL-Safe Base64 strings. This payload circumvents search graphs dynamically packaging:
- The inviter's fully qualified WebID.
- The destination inbox path URI routing targets securely.
- A cryptographic challenge securely tying the invite back against the original user uniquely.

User discovery remains explicitly external. Finding friends requires utilizing arbitrary conventional channels (Twitter, Email, SMS) distributing these invite blobs exclusively. There is no `search users by name` function structurally inside Proxion by design protecting activist topologies heavily.

## 18. Rate Limiting Execution Handlers

Because public resources face untrusted endpoints directly, Gateway nodes maintain internal TokenBucket constraints explicitly isolated across independent WebSocket connections natively.
1. `blocklist.py` immediately identifies unauthenticated or flagged actor ranges denying capability evaluations inherently saving local compute.
2. The `RoomConfig` topologies dynamically broadcast internal rate limits (e.g. 1 message per 5 seconds) dropping spam traffic immediately before triggering backend storage write cascades implicitly.
3. If an adversary attempts raw POST floods explicitly against the backend Solid Pod directly (bypassing the Gateway's rules natively), standard CSS topologies enact explicit payload HTTP 413 limits natively preventing drive saturation generically.

## 19. Privacy and Telemetry Data

Proxion fundamentally rejects the monetization of telemetry and user engagement tracking. No background loops exist evaluating usage heuristics natively.
- No Google Analytics, nor implicit Sentry exception tracking exist within the Tauri environments natively.
- No centralized node catalogs metrics.
- The `proxion status` API calls query local daemon health locally without bridging reports upstream into developer hands by design.
Any developer bridging Proxion into traditional cloud-analytics environments must explicitly rewrite the core `gateway.py` loop behaviors dynamically.

## 20. Scalability Considerations

As communities expand natively within individual Pod instances serving hundreds of Room participants simultaneously, backend HTTP caching bottlenecks emerge natively.
Solid providers implementing the CSS architectures natively scale through proxy load-balancing strategies intercepting `GET` requests securely via `ETag` validations inherently bypassing the core backend Node.js engines entirely for identical file payloads natively. Because all historical messages are inherently immutable json blobs, standard web caching semantics provide exceptional read-scalability out of the box dynamically.

*Proxion: True data sovereignty for the modern web.*

---

## 21. Solid SDK Convergence Contract

Proxion is converging from bespoke Solid auth/data paths to the official
Inrupt SDK.  The canonical package set is:

| Package | Role | Gate |
|---|---|---|
| `@inrupt/solid-client-authn-node` | Server-side OIDC + DPoP token management | Required |
| `@inrupt/solid-client-authn-browser` | Browser OIDC flows | Required |
| `@inrupt/solid-client` | Resource CRUD, container listing, ETag helpers | Required |
| `@inrupt/solid-client-notifications` | WebSocketChannel2023 push notifications | Required |
| `@inrupt/solid-client-access-grants` | Delegated third-party access | Feature-gated (`PROXION_ENABLE_ACCESS_GRANTS=1`) |
| `@inrupt/vocab-solid` | Canonical Solid vocabulary IRIs | Required |
| `@inrupt/vocab-inrupt-core` | Inrupt core vocabulary IRIs | Required |

**Deprecation blocklist** — the following packages are forbidden and will fail
the `check:solid-sdk` CI gate if present:

- `solid-auth-client`
- `solid-auth-fetcher`
- Any `@inrupt/solid-client-authn-*` version outside the supported range

The migration is controlled by environment flags documented in `.env.example`
(`PROXION_SOLID_AUTH_MODE`, `PROXION_SOLID_CUTOVER_STAGE`, etc.).  See
`docs/solid_sdk_migration_matrix.md` for the full surface-by-surface plan and
`docs/deprecation_legacy_solid_paths.md` for exit criteria.

## 22. Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---|---|---|
| 403 on Pod PUT/GET | WAC ACL not set | Run `set_thread_read_acl` or `set_room_acl` |
| `DPoP token expired` | Clock skew > 60 s | Sync system clock; rotate DPoP key |
| Messages not appearing | Wrong `stash://` prefix | Check `thread_path()` matches sender's cert ID |
| WireGuard peer unreachable | Routing rule missing | `ip rule show`; restart `wg-quick@wg0` |
| CSS auth 401 | Client credentials revoked | Re-register via `CssAccountManager.setup_agent()` |
