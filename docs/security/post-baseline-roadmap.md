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
