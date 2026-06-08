# Post-Baseline Roadmap — Next Focus After DoSE

Once all exit gates pass for 30 consecutive days, backend hardening rounds pause.
The following areas become the primary investment targets.

## Trust UX Signals

- Surface security tier and risk register status in the owner dashboard
- Show peer attestation status for connected gateways
- Provide actionable remediation hints when an exit gate fails

## Operational Ergonomics

- One-command incident drill runner with automated result recording
- SLO snapshot automation (scheduled `security_slo_snapshots` writes)
- Recovery runbook linked from `get_security_exit_gate_status` response

## Product Adoption Blockers

- Onboarding wizard: guided pod setup with health check
- Mobile-first connection status indicators
- Simplified invite flow with QR code support

## Performance and Mobile Reliability

- WebSocket reconnect backoff tuning for mobile network transitions
- Message delivery latency SLO (P95 < 500 ms on local relay)
- SQLite WAL checkpoint tuning for low-memory devices

## Trigger Conditions to Re-Enter Hardening

Return to security-primary rounds if any of the following occur:
1. New critical/high finding confirmed in risk register
2. Major spec drift (Solid Protocol, Notifications Protocol) with breaking risk
3. Material architecture change (new federation transport, new authn layer)
4. Regulatory or compliance requirement mandating a new control

---

## R35 governance right-sizing (June 2026)

Removed 5 test-only governance modules with zero `src/` importers:
`policy_state_machine`, `federation_attest`, `solid_oidc_conformance`,
`connectivity_diagnostics`, `key_lifecycle_policy` (+ their tests). Recoverable
from git history.

Confirmed the assurance subsystem is already opt-in:
- `continuous_assurance` loop requires `PROXION_ENABLE_CONTINUOUS_ASSURANCE=1`.
- Startup guards (`supply_chain`, `config_verify`, `provenance_verify`,
  `sdk_support_guard`) early-return unless their `PROXION_REQUIRE_*` flag is set.
- `tests/test_assurance_gating.py` locks these defaults in.

**Keep (real user value):** SSRF guards, relay signature validation
(`relay.py`), trust pinning (`peer_gateway_pins`), `blocklist.py`,
security-event audit log, `acp.py`, `attenuation.py`, `authz.py`.

**Opt-in (gated, default-off):** `continuous_assurance`, `supply_chain`,
`security_exit_gates`, `integrity_consensus`, `recovery_drill_runner`,
`incident_sim`, `policy_quality`, `config_verify`, `sdk_support_guard`,
`provenance_verify`.

**Pending keep/cut decision (NOT yet removed):**
- `wg_overlay` — full WireGuard-overlay subsystem, 13 test files, zero `src`
  importers. Never wired into the gateway. Decide: future infra or remove.
- `nss_setup` — Node-Solid-Server helper, test-only. Verify CLI usage first.
