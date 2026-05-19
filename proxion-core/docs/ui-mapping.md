# EI0 UI Mapping

This document maps the EI0 reference implementation in `proxion-core` to the
Universal Architecture (UI) invariants and sections.

## UI Invariants I1–I6

- I1 (No Authority Amplification): `proxion_core/attenuation.py` enforces
  `narrower_perms` as a subset of parent permissions and adds caveats only.
- I2 (Single-Use Tickets): `proxion_core/tickets.py` stores `redeemed` and
  rejects any second redemption.
- I3 (Finite Authority): `proxion_core/tickets.py` and `proxion_core/tokens.py`
  require expirations and validate TTLs on mint/verify.
- I4 (Contextual Authorization): `proxion_core/validator.py` evaluates caveats
  against `RequestContext` and denies on failure.
- I5 (No Global Identity Requirement): tokens bind to
  `holder_key_fingerprint`, which can be ephemeral; no global identifiers are
  required or persisted.
- I6 (Audience & Possession): `proxion_core/validator.py` checks `aud` binding
  and proof-of-possession (default fingerprint match or custom verifier).
  Optional revocation is enforced via `proxion_core/revocation.py` and
  `proxion_core/validator.py`.

## §8 Lifecycle Semantics

- Ticket lifecycle: `Created/Issued` at `mint_ticket`, `Redeemed` at
  `redeem_ticket`, and `Invalid` on redemption or TTL expiry.
- Capability lifecycle: `Issued` at `issue_token`, `Delegated` at `derive_token`,
  `Expired` when `ctx.now >= token.exp`, and `Revoked` via optional
  `revocation_list` in `validate_request` (backed by `RevocationList`).

## §9 Failure Semantics

- Missing/invalid/expired token or errors in validation: `validate_request`
  returns `DENY` (Decision.allowed = False).
- Store failures and caveat evaluation errors are treated as `DENY` by default.
- Revocation check errors also return `DENY` (fail-closed).

## §10 Privacy Requirements

- Minimization: permissions are explicit `(action, resource)` pairs only.
- Pairwise/ephemeral identifiers: holder fingerprints are caller-supplied and
  can be per-session.
- Unlinkability: tokens and tickets use opaque random identifiers.
- Store opacity: ticket store is minimal metadata only; no protected content.
