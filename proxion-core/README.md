# Proxion Core (EI₀)

> **Reference Implementation (Witness)**

This repository contains the **Existential Instantiation Zero (EI₀)** for Proxion.

## Purpose

This EI₀ reference implementation is a **frozen witness** for the Universal Architecture.

Status: EI₀ is frozen as of v0.1.0. Architectural changes must occur at the UI level or via separate EI repositories.

The purpose of `proxion-core` is to prove that the Universal Architecture defined in `proxion-spec` is implementable. It serves as a **witness** to the specification.

**It is explicitly NOT:**
*   A product.
*   The only way to implement Proxion.
*   Optimized for high performance or specific application constraints.
*   Production-ready.

## Constraints & Assumptions (EI)

As a reference implementation, this codebase is intentionally minimal and "boring":

*   **No Network Stack**: Core logic is pure functions or local state transitions; transport is abstracted.
*   **No UI**: No user interface or interactive flows are implemented.
*   **No Identity Provider**: Keys are generated locally; no dependence on external OIDC/DID resolvers.
*   **In-Memory/Simple Storage**: No database requirements.

## Scope

This repository implements:
*   Capability Issuance (signing).
*   Capability Verification (checking signatures and chains).
*   Caveat Evaluation (attenuation logic).
*   Redemption (proof-of-possession).
*   Expiry Checks.

## Relationship to Other Instantiations

Other implementations (EI₁, EI₂, ...) may use:
*   WireGuard or WebRTC for transport.
*   Solid Pods for storage.
*   Hardware Security Modules (HSMs) for key management.
*   VS Code or Browser extensions for UI.

This repository (`EI₀`) avoids those dependencies to remain a pure logic reference.

## Optional Revocation

## Caveat Examples

These examples show typed caveats that operate on `RequestContext` without any DSL.

IP allowlist:

```python
from proxion_core import ip_allowlist, issue_token, validate_request, RequestContext

caveat = ip_allowlist({"127.0.0.1"})
token = issue_token(
    permissions={("read", "resource")},
    exp=exp,
    aud="aud1",
    caveats=[caveat],
    holder_key_fingerprint="fp1",
    signing_key=signing_key,
    now=now,
)

ok_ctx = RequestContext("read", "resource", "aud1", now, ip="127.0.0.1")
fail_ctx = RequestContext("read", "resource", "aud1", now, ip="10.0.0.1")

assert validate_request(token, ok_ctx, {"holder_key_fingerprint": "fp1"}, signing_key).allowed
assert not validate_request(token, fail_ctx, {"holder_key_fingerprint": "fp1"}, signing_key).allowed
```

Time window (epoch seconds):

```python
from proxion_core import time_window

caveat = time_window(not_before=now.timestamp(), not_after=(now.timestamp() + 30))
```

Nonce match:

```python
from proxion_core import nonce_matches

caveat = nonce_matches("nonce-123")
```


Revocation is supported via an in-memory list with TTL entries. The validator
denies if a token is revoked, and fails closed on revocation errors.

Example:

```python
from datetime import datetime, timezone
from proxion_core import RevocationList, validate_request

revocations = RevocationList()
now = datetime.now(timezone.utc)
revocations.revoke(token, now)

decision = validate_request(
    token,
    ctx,
    {"holder_key_fingerprint": token.holder_key_fingerprint},
    signing_key,
    revocation_list=revocations,
)
```


## Licensing

Licensed under the Apache License, Version 2.0.
