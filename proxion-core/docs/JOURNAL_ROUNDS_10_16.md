# Proxion Development Journal

A running log of findings, spec gaps, and design decisions encountered while
building real applications on top of the Proxion protocol stack.

Entries are append-only. Each entry has a date, a category tag, and a status.

**Categories:** `spec-gap` | `bug` | `design-decision` | `finding` | `resolved`  
**Status:** `open` | `closed` | `partially-addressed` | `deferred`

---

## 2026-04-08 — Round 10 kickoff: federated messaging stress test

### Context

Unit tests and the developer example (`proxion-core/docs/example_app.py`) verify
the protocol mechanics in isolation. Round 10 stress-tests the spec by building a
minimal federated messaging layer — two `AgentState` instances exchanging messages
via their Solid Pods using capability tokens — with no central server.

The goal is not a polished feature. The goal is to find every place the code has
to reach *outside* the spec to make a real use case work.

---

### [J-001] spec-gap | closed
**Discovery: no machine-readable way to find a peer's store URL or Pod URL**

To initiate a federation handshake, Alice needs Bob's `proxion store serve` URL
and Bob's Solid Pod URL. Currently these must be passed out-of-band (manually).
Real apps cannot do this.

The spec has no `proxion agent publish` or `/.well-known/proxion` endpoint.
A peer's identity card (`proxion agent export-identity`) contains `identity_pub`
and `store_pub` but no Pod URL or store URL.

**Impact:** Every app built on this protocol must solve discovery independently,
leading to fragmentation. This is how Mastodon ended up with instance-specific
user lookup conventions.

**Proposed fix:** Add `pod_url` and `store_url` optional fields to the identity
card. Add a `GET /.well-known/proxion-identity` route to `proxion store serve`
that returns the identity card. A peer who knows your store URL can then
bootstrap everything else from it.

**Resolution (2026-04-09):** `store_server.build_app()` now exposes both
`/info` and `/.well-known/proxion-identity` with `store_pubkey`,
`identity_pubkey`, `pod_url`, and version metadata. The CLI now supports
advertising `--identity-pubkey` and `--pod-url` on `proxion store serve`, and
`proxion agent export-identity` can embed optional `store_url`/`pod_url`.

---

### [J-002] spec-gap | closed
**Receipt writing: validate_request succeeds but nothing writes to the Pod**

`docs/example_app.py` step 4 of "To build an app on this protocol" says:
> Write receipts to a Solid Pod for auditable, user-owned access history

`validate_request()` returns a `Decision` but has no hook to emit a receipt.
`AuthenticatedSolidClient` exists but is never called from the validation path.
The audit log (`proxion validator serve --audit-log`) is local NDJSON, not
Pod-resident — it is not user-owned and not portable.

**Impact:** The sovereignty promise ("your access history lives in your Pod")
is unimplemented. A user cannot inspect or revoke access records themselves.

**Proposed fix:** Add an optional `receipt_writer: Callable[[Token, RequestContext, Decision], Awaitable[None]]` param to `validate_request` (or as a post-validate hook on `build_validator_app`). Default is no-op. App developer supplies an `AuthenticatedSolidClient`-backed writer.

**Resolution (2026-04-09):** `validate_request()` now accepts an optional
`receipt_writer` callback and invokes it for allow/deny decisions without
affecting policy outcome on writer failure. `build_validator_app()` propagates
the hook to `POST /validate`, and `messaging.make_pod_receipt_writer()` provides
a ready-made Solid Pod writer for ALLOW receipts.

---

### [J-003] spec-gap | addressed
**Multi-device: no sub-cert or device delegation concept**

A cert is issued to a single `holder_key_fingerprint`. Bob's phone and Bob's
laptop are two different key pairs. If Bob wants both devices to act under the
same RelationshipCertificate, he must either:
(a) share the private key across devices (bad), or
(b) get Alice to issue a separate cert to each device (operationally painful).

Neither is acceptable for a messaging app where a user has 2-3 devices.

**Impact:** Every device is a separate federation identity. There is no concept
of "Bob" as an aggregate of Bob's devices.

**Proposed fix (deferred):** Introduce a `DelegationCert` — a cert issued by
a root identity to a device sub-key, scoped to a subset of capabilities. The
validator checks the delegation chain: root cert → device cert → token.
This is Round 11+ scope; document here so the design accounts for it.

**Resolution (2026-04-09, Round 14):** Added `delegate_cert()` as an
issuer-mediated delegation helper: the original issuer can mint a sub-cert for
another holder key (e.g. Bob laptop) under the same issuer identity, and tokens
issued from that sub-cert validate with existing flow.

**Resolution (2026-04-09, Round 15):** Validator-side chain verification is now
complete. `validate_request()` accepts an optional `delegation_cert` parameter
and checks: cert expiry, Ed25519 signature, subject matches the token holder,
and delegated capabilities do not exceed the parent cert's scope. CLI command
`proxion cert delegate` exposes the full delegation workflow for device key
onboarding.

---

### [J-004] spec-gap | closed
**Revocation window: old tokens valid up to TTL after cert revocation**

When Alice revokes Bob's cert, any tokens Bob already holds continue to pass
`validate_request` until they expire (default TTL: 1 hour).

`revoke_tokens_for_certificate()` exists but requires the issuer to enumerate
all tokens they previously issued — there is no issuer-side token store. The
validator has no way to enumerate tokens it has accepted.

**Impact:** For a messaging app a 1-hour window is significant. A terminated
employee or a compromised device can read messages for up to an hour after
revocation.

**Proposed fix:** Two complementary approaches:
1. Short-TTL tokens (5 min) for sensitive resources, combined with a
   `POST /token` renewal flow on the validator.
2. Issuer-side token ledger: `issue_from_certificate` optionally records the
   token rev-ID to a store mailbox; `revoke_tokens_for_certificate` drains it.
   This is spec work, not just a code change.

**Resolution (2026-04-09):** Option 1 is now implemented: validator server has
`POST /token/renew` to re-issue short-lived tokens after validating the current
token + PoP proof, with TTL clamp (1s..1h). A messaging helper
`renew_thread_token()` was added for thread token refresh workflows.

**Resolution (2026-04-09, Round 14):** Option 2 is now implemented: cert-bounded
token issuance can record revocation IDs to a per-cert ledger mailbox, and
`revoke_tokens_via_ledger()` revokes all recorded tokens in one call.

---

### [J-005] spec-gap | closed
**Pod ACL bootstrap: LDP ACL format for Proxion tokens vs Solid DPoP is unspecified**

`SolidClient.set_acl()` exists in `proxion-core/src/proxion_core/solid_client.py`
and `AuthenticatedSolidClient` gates access via capability token fingerprint
checking. But:

- Real Solid servers (CSS, ESS) expect WAC/ACP policies in Turtle or JSON-LD.
- `AuthenticatedSolidClient` is a Proxion-side enforcement layer, not a real
  Solid server.
- There is no spec for how a Proxion capability token maps to a WAC `acl:agent`
  triple, or how `set_acl` should be called to grant Bob read access on Alice's Pod.

**Impact:** Two Proxion nodes can handshake and mint tokens, but if they try to
read/write a *real* CSS/ESS Pod, the Pod will reject requests that don't carry
DPoP-bound tokens. The Proxion token and the Solid DPoP token are parallel
systems with no bridge.

**Proposed fix (architectural):** Define a Proxion↔Solid bridge layer:
a small service that holds a Solid DPoP identity and a Proxion capability token;
when a Proxion token is presented, the bridge verifies it and proxies the request
to the Pod using DPoP. This is a significant design item — record here for
whoever picks up Solid integration next.

**Resolution (2026-04-10, Round 16):** Implemented the Proxion↔Solid DPoP bridge
as a first-party library component rather than a separate proxy service.
`proxion_core.dpop.make_dpop_proof()` generates RFC 9449 DPoP JWTs using the
agent's Ed25519 identity key (no separate key pair needed).
`CssClientCredentials` handles OAuth2 client-credentials token issuance and
caching against a CSS `/oidc/token` endpoint.
`DpopSolidClient` subclasses `SolidClient` and overrides `_dynamic_headers()`
to inject `Authorization: DPoP <token>` and `DPoP: <proof>` per request.
`CssAccountManager.setup_agent()` and `build_dpop_client()` provide a one-call
onboarding flow for new agent↔Pod pairs.
Integration tests (skipped without `CSS_ALICE_URL`) cover PUT/GET round-trips
and messaging over real CSS 7 Pod instances.

---

---

## 2026-04-09 — proxion_core.messaging implementation findings

`proxion_core/messaging.py` and `tests/test_messaging.py` (18 tests, all passing)
implemented. The following additional spec gaps were discovered.

---

### [J-006] spec-gap | closed
**`stash://` URIs are resolver-local, not globally addressable**

`SolidClient.list()` returns absolute HTTP URIs from the Turtle body (e.g.
`http://alice.pod.example/messages/thread/abc/msg1.json`). But `SolidClient.get()`
only accepts `stash://` URIs — it calls `resolver.resolve()` which maps
`stash://` → HTTP using the client's configured base URL.

This means you cannot round-trip: HTTP URI → stash:// URI without knowing
the resolver's base URL. `receive()` works around this by extracting the
message filename from the HTTP URI and reconstructing the stash path, relying
on the convention that `stash://messages/...` maps to the same path segment
at the Pod base.

**Impact:** Any code that mixes resolver-relative `stash://` URIs with the
HTTP URIs returned by `list()` will break if the Pod is at a non-root path
(e.g. `https://pod.example/users/alice/`). The scheme is not usable as a
stable, shareable identifier.

**Proposed fix:** Either (a) switch to full HTTP URIs everywhere and drop
`stash://` as an internal implementation detail, or (b) add a `resolve_back()`
method to `SolidResolver` that maps an absolute HTTP URI back to a `stash://`
URI given a known base URL.

**Resolution (2026-04-09):** Closed via option (b). `SolidResolver.resolve_back()`
was added and `SolidClient.list()` now returns `stash://` URIs by converting each
`ldp:contains` HTTP URL back through the resolver. `messaging.receive()` now reads
those URIs directly and no longer reconstructs paths from filenames.

---

### [J-007] spec-gap | closed
**Cert capabilities cannot reference the cert ID — the ID is minted during the handshake**

A `RelationshipCertificate` is produced by `run_local_handshake()`. The
capabilities must be declared in the invite, before the cert exists. Therefore
capability resources like `stash://messages/thread/{cert_id}/` are impossible
to express — the cert ID is not known at invite time.

**Consequence in messaging module:** `thread_path(cert_id)` returns
`stash://messages/thread/{cert_id}/`, but the cert's capabilities must grant
`read` on `stash://messages/` (the parent prefix). This is a wider grant than
necessary — Bob gets read on ALL threads in Alice's `messages/` container, not
just their specific thread.

**Impact:** Least-authority is violated. If Alice has multiple federated
relationships, each peer can read all threads, not just their own.

**Proposed fix (two options):**
1. Generate the cert ID before the handshake and include it in the invite.
   The responder echoes it back; finalization uses the pre-agreed ID.
2. Issue a thread-scoped token post-handshake (a "narrowing token") derived
   from the cert, scoped to the specific thread path. The narrowing token
   is what gets presented for reads — the cert is just the root grant.
   Option 2 fits naturally with `derive_token()` and doesn't require changing
   the handshake protocol.

**Resolution (2026-04-09):** Runtime least-authority is addressed with
`messaging.narrow_to_thread()`, which derives a thread-scoped read token from
the broader cert grant.

**Update (2026-04-09, Round 12):** `messaging.receive()` has an enforced
least-authority mode: when called with `holder_state` and `signing_key`, it
mints a thread-scoped token and performs message `get()` reads through
`AuthenticatedSolidClient`.

**Update (2026-04-09, Round 13):** Handshake now supports precomputed
`certificate_id` in `FederationInvite`, propagated through cert issuance. This
enables invite-time thread-scoped capabilities such as
`stash://messages/thread/{cert_id}/` without post-handshake widening.

---

### [J-008] spec-gap | closed
**AuthenticatedSolidClient aud="" doesn't match tokens from issue_from_certificate**

`AuthenticatedSolidClient.__init__` defaults `aud=""`. But
`issue_from_certificate()` sets `token.aud = cert.issuer` (the issuer's
identity pub hex). `validate_request` checks `token.aud == ctx.aud` and
returns `audience_mismatch` when the default `""` is used.

**Test `test_j008_authenticated_client_aud_mismatch` confirms this behaviour.**

Any code that constructs `AuthenticatedSolidClient` without passing
`aud=cert.issuer` will silently fail with `PermissionError("audience_mismatch")`.
This is a footgun — the error message is not obvious and the parameter is
easy to omit.

**Proposed fix:** `AuthenticatedSolidClient` should accept a `cert` parameter
and derive `aud` from it automatically. Or raise at construction time if
`aud=""` and a cert-derived token is provided.

**Resolution (2026-04-09):** `AuthenticatedSolidClient.__init__` now accepts
`cert` and auto-derives `aud=cert.issuer` when provided. Tests now assert that
`aud=""` still fails (regression guard) while `cert=...` succeeds without
explicit `aud`.

---

### [J-009] spec-gap | closed
**SolidClient makes unauthenticated HTTP requests**

`SolidClient.get()` / `.put()` / `.list()` send no `Authorization` header.
Real Solid servers (CSS, ESS) require either WebID-OIDC + DPoP or equivalent
auth for non-public resources.

**Impact:** The messaging module works against a local mock or a Pod server
configured for public unauthenticated access only. Production deployments
require a bridge layer (see J-005).

**Workaround in tests:** `_mock_pod_client()` in `test_messaging.py` uses an
in-memory dict, bypassing HTTP entirely. Real Pod integration remains untested.

**Resolution (2026-04-09):** `SolidClient` now accepts optional `auth_headers`
and injects them into `get/put/list/delete/set_acl` requests. This adds an auth
escape hatch for Bearer/API-key/DPoP-style integration without changing core
request flow.

**Follow-up:** [J-005] remains open; full Proxion-to-Solid DPoP/WebID-OIDC
bridging is still required for production real-Pod interoperability.

---

### Bidirectional messaging requires two certs (design finding)

`issue_from_certificate` sets `token.aud = cert.issuer`. A token minted from
Alice's cert (Alice = issuer) can only reach Alice's Pod namespace. Bob cannot
mint a token for Bob's Pod namespace using Alice's cert.

For bidirectional messaging, each party must be the issuer of their own cert:
- Alice issues cert_a → Bob can read from Alice's Pod
- Bob issues cert_b → Alice can read from Bob's Pod

This requires two `run_local_handshake` calls (or a symmetric handshake that
produces two certs, one per direction). The current single-cert handshake only
supports unidirectional read access.

`test_round_trip_bidirectional` in `test_messaging.py` demonstrates the
two-cert pattern as the working workaround.

**Resolution (2026-04-09, Round 12):** Added
`handshake.run_bidirectional_handshake()` to produce both directional certs in
one helper call. The messaging tests now use this helper directly, and
handshake tests cover issuer/subject role swaps, independent directional
capabilities, and audience behavior on minted tokens.

---

---

### [J-010] spec-gap | closed
**Thread ACL bootstrap: `set_thread_read_acl` writes a Proxion-internal entry, not real WAC**

`set_thread_read_acl(pod_client, cert)` writes a JSON ACL stub to the thread
container path via `SolidClient.set_acl()`. This is a Proxion-internal
convention — it is not a WAC `acl:agent` triple and is not understood by any
real Solid server.

**Impact:** Two Proxion nodes can mutually agree on access rights, but those
rights have no effect on a real CSS/ESS Pod. The actual enforcement lives in
`AuthenticatedSolidClient._check_allowed()` (capability token validation) — the
ACL write is redundant noise that creates a false impression of Pod-side access
control.

**Proposed fix:** Either (a) remove `set_thread_read_acl` entirely and rely
solely on capability token validation, or (b) implement the full WAC/ACP bridge
described in J-005. Until J-005 is resolved, this function is a no-op in
production and should be treated as a stub.

**Resolution (2026-04-10, Round 16):** `set_thread_read_acl` rewritten (option b
from Proposed fix). It now generates and PUTs a real W3C WAC Turtle document
with two named authorization stanzas: `<#owner>` (Read/Write/Control + acl:default)
and `<#subject>` (Read + acl:default). Signature: `set_thread_read_acl(pod_client,
cert, owner_webid, subject_webid) -> str`. `SolidClient.set_acl()` likewise updated
to accept WebID URLs and emit standards-compliant WAC Turtle. J-005 resolution
provides the DPoP-authenticated client needed for this to work against real Pods.

---

*Last updated: 2026-04-10*  
*Open items: none (Round 16 complete).*
