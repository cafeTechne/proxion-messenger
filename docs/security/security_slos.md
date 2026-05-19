# Security SLOs — Proxion Messenger Backend

These SLOs define the operational targets for the 30-day rolling window evaluated
by the `slo_gate` exit gate. All SLOs apply to production deployments.

## SLO Table

| SLO ID | Metric | Target | Measurement |
|--------|--------|--------|-------------|
| SLO-01 | Relay replay false-negative incidents | 0 / 30d | `security_events` where `event_type = 'relay_replay_false_negative'` |
| SLO-02 | Authn bypass incidents | 0 / 30d | `security_events` where `event_type = 'authn_bypass_confirmed'` |
| SLO-03 | Critical security control downtime | < 0.1% / 30d | Gateway uptime with all T0 controls active |
| SLO-04 | Auto-containment false-positive rate | < 1% of activations | `containment_false_positive` / `containment_activated` events |
| SLO-05 | MTTR-S (tabletop drills) | < 60 min | `security_drill_results.duration_seconds` for drill_type=recovery |

## Alert Thresholds

| Threshold | Action |
|-----------|--------|
| SLO-01 or SLO-02 breached | Immediate incident response, reset 30-day clock |
| SLO-03 > 0.05% | Investigate degraded mode root cause |
| SLO-04 > 0.5% | Review auto-escalation thresholds |
| SLO-05 > 45 min | Update runbooks and retry drill |

## Evidence Storage

SLO evaluation snapshots are persisted in `security_slo_snapshots` with:
- `window_start`, `window_end` — 30-day window bounds
- `metrics_json` — per-SLO measurement and pass/fail
- `evaluated_at` — timestamp of evaluation

Drill results are persisted in `security_drill_results` with:
- `drill_type` — `incident | recovery | rollback`
- `status` — `pass | fail`
- `duration_seconds`
- `findings_json`
