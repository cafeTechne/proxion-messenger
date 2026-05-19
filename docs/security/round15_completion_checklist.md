# Round 15 Completion Checklist

All items must be marked complete before declaring Round 15 done.

## Technical Controls

- [ ] `federation_attest.py` — peer attestation verification implemented
- [ ] `provenance_verify.py` — build provenance verifier implemented
- [ ] `policy_state_machine.py` — tier state machine with cooldown implemented
- [ ] `security_exit_gates.py` — all 5 exit gate evaluators implemented
- [ ] `integrity_consensus.py` — cross-node digest consensus implemented
- [ ] `scripts/build_provenance_sign.mjs` — provenance manifest generator implemented
- [ ] `event_stream.py` — monotonic sequence IDs and gap detection added

## Schema

- [ ] `_SCHEMA_VERSION == 38` in `local_store.py`
- [ ] Migration 37 applied: `peer_attestations`, `operation_budget_scopes`, `policy_tier_transitions`, `event_stream_cursors`
- [ ] Migration 38 applied: `security_slo_snapshots`, `security_drill_results`
- [ ] All new CRUD helpers tested

## Exit Gate Verification

- [ ] `get_security_exit_gate_status` command registered and owner-only
- [ ] All 5 gates evaluate without errors against a fresh DB

## SLO Evidence

- [ ] 30-day trailing SLO window in range (or initial baseline established)

## Drill Evidence

- [ ] At least one incident drill with `status = pass` recorded
- [ ] At least one recovery drill with `status = pass` recorded

## Documentation

- [ ] `docs/security/definition_of_secure_enough.md` — Stop Rule present and unambiguous
- [ ] `docs/security/control_baseline_v1.md` — all must-have controls listed
- [ ] `docs/security/security_slos.md` — SLO targets defined
- [ ] `docs/security/post-baseline-roadmap.md` — next-focus areas documented

## Signoff

- [ ] Security owner review complete
- [ ] Engineering owner review complete
