# Definition of Secure Enough (DoSE) — Proxion Messenger Backend

## Top Risk Register (Top 10)

| # | Risk | Status |
|---|------|--------|
| 1 | Authn bypass via forged WebID or replay | mitigated |
| 2 | Relay replay attack (DPoP/nonce bypass) | mitigated |
| 3 | Invite code brute-force or abuse | mitigated |
| 4 | Trust update injection (peer gateway spoofing) | mitigated |
| 5 | Identity key compromise or rollover abuse | mitigated |
| 6 | DB integrity tampering at rest | mitigated |
| 7 | Federation quarantine bypass | mitigated |
| 8 | Spec drift causing silent protocol regression | mitigated |
| 9 | Build artifact substitution | mitigated |
| 10 | Auto-containment false-positive causing DoS | accepted |

## Control Baseline v1

Must-have controls for a ship-ready backend:

- Identity/authn verification (DPoP + WebID validation)
- Replay protection (relay + DPoP + invite nonce dedup)
- Revocation enforcement (cert and trust revocations honored)
- Tamper-evident audit and security event log
- Backup/restore/import guardrails (dual-control, budgets)
- Degraded/containment modes (adaptive tiers T0–T3)
- Signed config/provenance checks (when enabled in deployment profile)

## Security SLOs

| SLO | Target |
|-----|--------|
| Relay replay false-negative incidents | 0 / 30d |
| Authn bypass incidents | 0 / 30d |
| Critical security control downtime | < 0.1% / 30d |
| Auto-containment false-positive rate | < 1% of activations |
| MTTR-S (tabletop drills) | < 60 min |

## Validation Evidence Checklist

- [ ] All schema migrations applied and tested (`_SCHEMA_VERSION == 39`)
- [ ] `get_security_exit_gate_status` returns all gates `pass`
- [ ] 30-day trailing SLO window in range
- [ ] At least one incident drill `pass` in last 30 days
- [ ] At least one recovery drill `pass` in last 30 days
- [ ] Key lifecycle policy: no overdue rotations
- [ ] Continuous assurance loop state: `green` or `amber`
- [ ] Signoff recorded by security + engineering owners

## Stop Rule

If all exit gates pass for **30 consecutive days**, pause new backend hardening rounds.
Shift roadmap allocation to reliability, UX, and performance.

New backend security rounds require one of:
- New critical/high finding in risk register
- Major spec drift with breaking risk
- Material architecture change

## Maintenance Mode (R16)

When `evaluate_security_program_stability_gate` returns `recommendation="hold_line"` (all gates pass for 45 consecutive days, no critical events), transition to **monthly security maintenance cycle**:

1. Run `run_recovery_drill` monthly for each template.
2. Review continuous assurance snapshots — investigate any `amber` or `red` states.
3. Rotate identity key if `key_lifecycle_policy` flags overdue.
4. Review and archive evidence verification records quarterly.
5. No new hardening rounds without a trigger from the stop rule above.

## Escalation Rule

If any exit gate fails after a 30-day passing streak:
1. Identify root cause within 48 hours
2. Apply hotfix or accept risk with documented rationale
3. Reset the 30-day clock
