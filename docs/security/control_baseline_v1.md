# Control Baseline v1 — Proxion Messenger Backend

This document freezes the minimum set of controls that define "ship-ready backend security."
Controls marked **required** must be active in any production deployment.
Controls marked **conditional** are required only when the relevant feature is enabled.

## Required Controls

### 1. Identity / Authn Verification
- DPoP proof validated on every authenticated request
- WebID format checked; non-HTTP WebIDs rejected
- OIDC ID token claims validated (iss, aud, sub, exp, iat, nonce)

### 2. Replay Protection
- Relay nonces deduped in `relay_seen_nonces` (TTL 600s)
- Relay message IDs deduped in `relay_seen_ids`
- DPoP JTIs deduped in `dpop_seen_jti`
- Invite nonces deduped in `invite_seen_nonces`

### 3. Revocation Enforcement
- Certificate revocations stored in `revocations`
- Trust revocations stored in `trust_revocations`
- Revoked peers rejected at relay + invite handlers

### 4. Tamper-Evident Audit / Security Events
- All security-relevant events written to `security_events`
- Audit log chain integrity: `prev_hash` + `entry_hash` per row
- Checksum verification on critical tables (5-minute loop)

### 5. Backup / Restore / Import Guardrails
- Dual-control: prepare + confirm recovery operations
- Global operation budgets enforced (`operation_budgets`)
- Scoped budgets enforced per identity and IP (`operation_budget_scopes`)
- Import provenance recorded

### 6. Degraded / Containment Modes
- Adaptive tiers: T0 normal → T1 elevated → T2 restrictive → T3 containment
- Auto-escalation from abuse signal rollups (auth lockouts, replay rejects, DB integrity)
- Drift escalation when `PROXION_DRIFT_ESCALATION_MODE` is set

### 7. Signed Config / Provenance (conditional)
- `PROXION_REQUIRE_SIGNED_CONFIG=1`: policy hash enforced at startup
- `PROXION_REQUIRE_BUILD_PROVENANCE=1`: build provenance checked before network bind
- `PROXION_REQUIRE_RUNTIME_INTEGRITY=1`: runtime file hashes checked at startup

## Separation of Concerns

Critical controls (1–6) apply regardless of deployment profile.
Control 7 is conditional on operator configuration and is recommended for production.
