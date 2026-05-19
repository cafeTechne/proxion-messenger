# Deprecation Exit Criteria: Legacy Solid Paths

This document defines the objective conditions that must be satisfied before
legacy Proxion Solid paths (custom `css_auth.py`, direct `solid_client.py`
transport, bespoke RDF parsing in `web/pod.js`) can be removed.

No legacy code may be deleted until **all** criteria in all three sections
are met and the sign-off fields are completed.

---

## Metric Thresholds (must hold for 14 consecutive days)

| Metric | Threshold | Source |
|---|---|---|
| Adapter error rate | < 0.5% | `solid_migration_errors.by_code` rollup |
| Dual-read mismatch rate | < 0.1% | `dual_read_mismatch_count` / total reads |
| Notification fallback rate | < 2% | `notifs_fallback_count` / total notif events |

All three metrics must simultaneously be below threshold for 14 consecutive
calendar days.  A single day above any threshold resets the 14-day clock.

---

## Security Conditions (must hold for 30 days)

- Zero critical security events linked to the adapter path (event type
  `adapter_auth_error`, `adapter_acl_bypass`, or `adapter_data_corruption`).
- Zero ACP policy validation errors caused by unknown predicates (checked by
  `validate_acp_predicates` in `acp.py`).
- No production incidents attributed to the cutover in the 30-day window.

---

## Process Conditions

- [ ] All integration tests pass with `PROXION_SOLID_CUTOVER_STAGE=3`.
- [ ] `PROXION_SOLID_AUTH_MODE=inrupt_bridge` is the active mode on all
      canary instances for ≥14 days with zero fallbacks.
- [ ] The `check:solid-sdk` CI gate is green on all supported platforms.
- [ ] A full regression run against a live CSS v7 and ESS instance passes.
- [ ] Deprecation PR reviewed and approved by security owner and platform owner.

---

## Sign-off

| Role | Name | Date | Notes |
|---|---|---|---|
| Security Owner | | | |
| Platform Owner | | | |
| On-call Lead | | | |

---

*Document status: DRAFT — thresholds and conditions are provisional until the
first canary release is evaluated.*
