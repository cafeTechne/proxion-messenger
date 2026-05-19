# Proxion Core Capabilities

`proxion-core` (EI₀) is the reference logic for the Proxion Universal Architecture. It provides the cryptographic and logical primitives that power the entire ecosystem.

## 1. Capability Tokens (ZCAP-LD)
The core of Proxion security is the **Capability Token**. Unlike traditional Bearer tokens (JWTs), Proxion tokens are attenuated and cryptographically delegated.

- **Issuance**: Deterministic generation of tokens signed by the Issuer's private key.
- **Attenuation**: The ability to derive a new, more restricted token from an existing one without contacting the original issuer.
- **Delegation**: Securely passing a subset of rights to another entity (e.g., granting a mobile app "read" access to a "media" folder).

## 2. Attenuation & Caveats
The system supports sophisticated "fail-closed" logic for restricting token usage:

- **Type-Safe Caveats**: Logic to restricted actions based on:
    - **Time Windows**: (Not Before / Not After).
    - **IP Allowlists**: Restricting access to specific network origins.
    - **Nonces**: Preventing replay attacks.
- **Context Validation**: A `RequestContext` object provides the semantic environment (action, resource, audience, timestamp, IP) against which caveats are evaluated.

## 3. DPoP (Proof-of-Possession)
To prevent token theft, `proxion-core` enforces Proof-of-Possession.
- **Key Fingerprinting**: Tokens are bound to the public key fingerprint of the intended holder.
- **Challenge/Response**: Holders must sign a challenge (or the request itself) to prove they possess the private key associated with the token.

## 4. Federation Protocol Primitives
Primitives for establishing trust between independent Proxion instances:
- **Invitations**: Signed objects containing resource URIs, required capabilities, and relay hints.
- **Acceptances**: Signed responses to invitations containing the responder's public key and challenge signatures.
- **Challenge Markers**: Ephemeral tokens used to verify live possession of a key during the handshake.

## 5. Revocation & Expiry
- **Revocation Lists**: Support for checking tokens against a black-list of revoked IDs.
- **TTL Support**: Built-in support for token expiration and cleanup of stale revocation entries.

## 6. Cryptographic Standards
- **Ed25519**: Uses modern Edwards-curve Digital Signature Algorithm for high performance and high security.
- **Deterministic Serialization**: Ensures that signatures are calculated on a stable representation of the data.
