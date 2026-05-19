# Proxion EI0 Conformance Checklist

This checklist maps Universal Architecture (UI) requirements to EI0 enforcement
points and tests. It is intended for traceability and later automation.

## UI §7 Invariants (I1–I6)

| Requirement | UI Ref | EI0 Enforcement | Evidence (tests) | Notes |
|---|---|---|---|---|
| Derived capabilities MUST NOT widen permissions beyond parent. | §7 I1 | `proxion_core/attenuation.py:derive_token` | `proxion-core/tests/test_attenuation.py::AttenuationTests.test_widening_raises` | Attenuation is enforced structurally. |
| Permission Tickets MUST be single-use. | §7 I2 | `proxion_core/tickets.py:redeem_ticket` | `proxion-core/tests/test_tickets.py::TicketTests.test_ticket_redeemed_twice_denies` | Ticket store uses atomic redeemed flag. |
| Tickets and capabilities MUST be time-bounded. | §7 I3 | `proxion_core/tickets.py:mint_ticket`, `proxion_core/validator.py:validate_request` | `proxion-core/tests/test_tickets.py::TicketTests.test_ticket_expired_denies`, `proxion-core/tests/test_tokens.py::TokenTests.test_token_expired_denies` | Expiration is checked on every redemption/validation. |
| Authorization MUST depend on request context, not only token possession. | §7 I4 | `proxion_core/validator.py:validate_request` evaluates caveats | `proxion-core/tests/test_tokens.py::TokenTests.test_caveat_fails_denies`, `proxion-core/tests/test_caveats.py::CaveatTests.test_time_window_denies_outside` | Caveats are typed predicates over `RequestContext`. |
| Authorization MUST NOT require global stable identifiers. | §7 I5 | `proxion_core/tokens.py:issue_token` uses `holder_key_fingerprint` only; no global IDs | N/A | Witnessed via minimal design: no global identifiers are required. |
| Authorization MUST bind tokens to audience AND require proof-of-possession. | §7 I6 | `proxion_core/validator.py:validate_request` checks `aud` and PoP verifier | `proxion-core/tests/test_tokens.py::TokenTests.test_audience_mismatch_denies`, `proxion-core/tests/test_tokens.py::TokenTests.test_missing_or_invalid_proof_denies` | PoP is a hook; stronger instantiations MAY exist. |

## UI §8 Lifecycle Semantics

### Tickets

| Requirement | UI Ref | EI0 Enforcement | Evidence (tests) | Notes |
|---|---|---|---|---|
| Ticket lifecycle MUST be `Created -> Issued -> Redeemed -> Invalid`. | §8.1 | `proxion_core/tickets.py:mint_ticket`, `redeem_ticket` | `proxion-core/tests/test_tickets.py::TicketTests.test_ticket_redeemed_twice_denies` | Redeem invalidates irreversibly. |
| Expired tickets MUST be rejected. | §8.1 | `proxion_core/tickets.py:redeem_ticket` | `proxion-core/tests/test_tickets.py::TicketTests.test_ticket_expired_denies` | Expired tickets are removed on check. |

### Capabilities

| Requirement | UI Ref | EI0 Enforcement | Evidence (tests) | Notes |
|---|---|---|---|---|
| Capabilities MUST be issued with expiration. | §8.2 | `proxion_core/tokens.py:issue_token` | `proxion-core/tests/test_tokens.py::TokenTests.test_token_expired_denies` | Tokens reject past expiration at issue time. |
| Delegation MUST be via attenuation only. | §8.2 | `proxion_core/attenuation.py:derive_token` | `proxion-core/tests/test_attenuation.py::AttenuationTests.test_widening_raises` | Permissions are subset-only. |
| Expired capabilities MUST be rejected. | §8.2 | `proxion_core/validator.py:validate_request` | `proxion-core/tests/test_tokens.py::TokenTests.test_token_expired_denies` | Checked against `ctx.now`. |
| Revoked capabilities MUST be rejected when revocation is used. | §8.2 | `proxion_core/revocation.py:RevocationList`, `proxion_core/validator.py:validate_request` | `proxion-core/tests/test_validator.py::ValidatorTests.test_revoked_token_denies` | Optional; EI0 provides an in-memory revocation list. |

## UI §9 Failure & Denial Semantics (Fail-Closed)

| Requirement | UI Ref | EI0 Enforcement | Evidence (tests) | Notes |
|---|---|---|---|---|
| Missing/invalid token MUST result in DENY. | §9 | `proxion_core/validator.py:validate_request` | `proxion-core/tests/test_tokens.py::TokenTests.test_malformed_token_denies` | Any validation error returns deny. |
| Expired or revoked token MUST be denied without fallback. | §9 | `proxion_core/validator.py:validate_request` | `proxion-core/tests/test_tokens.py::TokenTests.test_token_expired_denies`, `proxion-core/tests/test_validator.py::ValidatorTests.test_revoked_token_denies` | Revocation check is optional but enforced when configured. |
| Store or verification failures MUST fail closed. | §9 | `proxion_core/validator.py:validate_request` | `proxion-core/tests/test_validator.py::ValidatorTests.test_revocation_derivation_error_denies` | Revocation derivation errors deny. |

## UI §10 Privacy Requirements (P1–P4)

| Requirement | UI Ref | EI0 Enforcement | Evidence (tests) | Notes |
|---|---|---|---|---|
| SHOULD use pairwise identifiers for relationships. | §10 P1 | `proxion_core/tokens.py:issue_token` accepts per-session fingerprints | N/A | Witnessed via design; identifiers are caller-supplied and can be ephemeral. |
| MUST encode minimum required permissions. | §10 P2 | `proxion_core/tokens.py:issue_token` requires explicit permission list | `proxion-core/tests/test_tokens.py::TokenTests.test_permission_missing_denies` | Minimization is by explicit enumeration. |
| SHOULD avoid cross-session correlation. | §10 P3 | `proxion_core/tickets.py:mint_ticket`, `proxion_core/tokens.py:issue_token` use opaque random IDs | N/A | Opaque random IDs reduce correlation; stronger instantiations MAY exist. |
| MUST NOT require visibility into protected content. | §10 P4 | `proxion_core/tickets.py` and validation logic avoid content storage | N/A | EI0 stores only minimal metadata. |
