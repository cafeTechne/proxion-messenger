# Solid SDK Migration Matrix

Authoritative one-to-one mapping of each current Proxion Solid surface to its
target Inrupt SDK component, migration mode, risk, and exit criteria.

**Not in Scope by Default**: Access grants remain disabled unless the deployment
profile explicitly requires delegated third-party access
(`PROXION_ENABLE_ACCESS_GRANTS=1`).  Enabling access grants expands the
permission surface and requires a separate security review.

---

## Matrix

| Current Module | Current Function | Target SDK Package | Target API | Migration Mode | Security Risk if Wrong | Observability Signals | Rollback Switch | Decommission Condition |
|---|---|---|---|---|---|---|---|---|
| `css_auth.py` | `fetch_access_token` / `get_token` | `@inrupt/solid-client-authn-node` | `Session.login` + `session.fetch` | `shadow` | Token theft or silent auth downgrade; nonce/signature errors must not be retried via legacy | `auth_mode_active`, `auth_mode_fallback_count`, `auth_mode_last_failure_code` in `pod_status` | `PROXION_SOLID_AUTH_MODE=legacy` | All canary instances at `auth_mode_active=inrupt_bridge` for ≥14 days, zero fallbacks |
| `solid_client.py` | `get()` | `@inrupt/solid-client` | `getSolidDataset` / `getFile` | `shadow` (dual-read) | Data exposure from wrong resource URL resolution | `dual_read_mismatch_count` in `solid_migration_errors` | `PROXION_SOLID_DUAL_READ=0` | Mismatch rate < 0.1% for 14 consecutive days |
| `solid_client.py` | `put()` | `@inrupt/solid-client` | `overwriteFile` / `saveSolidDatasetAt` | `canary` | Silent write failures; data loss if error handling mismatches | HTTP 4xx/5xx counts by normalised code | `PROXION_SOLID_CUTOVER_STAGE=0` | Error rate < 0.5% for 14 days after stage-3 canary |
| `solid_client.py` | `delete()` | `@inrupt/solid-client` | `deleteFile` | `canary` | Residual resource exposure if delete silently fails | SolidError counts in migration error store | `PROXION_SOLID_CUTOVER_STAGE=0` | Same as PUT |
| `solid_client.py` | `list()` | `@inrupt/solid-client` | `getSolidDataset` + `getContainedResourceUrlAll` | `shadow` | ACL bypass if list returns stale or wrong membership | Mismatch count in dual-read log | `PROXION_SOLID_DUAL_READ=0` | Mismatch rate < 0.1% for 14 days |
| `acp.py` | `set_acp_policy` / `set_acl_auto` | `@inrupt/solid-client` | `acp_ess.*` / `access_grant.*` helpers | `cutover` | ACL document injection if unknown predicates are interpolated | `acp_rejects_unknown_critical_predicates` test gate | `PROXION_SOLID_CUTOVER_STAGE=0` | Zero ACP validation errors in prod for 30 days |
| `gateway.py` | `_setup_pod_connection` | `@inrupt/solid-client-authn-node` | `createSession` | `shadow` | Pod locked out if bridge session management fails | `auth_mode_active` telemetry | `PROXION_SOLID_AUTH_MODE=legacy` | Same as css_auth |
| `gateway.py` | `poll_loop` | `@inrupt/solid-client-notifications` | `WebsocketNotification.connect` | `auto` | Missed updates if notification channel drops silently | `notifs_fallback_count`, `notifs_last_fallback_reason` | `PROXION_SOLID_NOTIFS_MODE=legacy` | Fallback rate < 2% for 14 days |
| `web/pod.js` | `readResource` | `@inrupt/solid-client` | `getSolidDataset` | `shadow` | Stale reads or wrong data serialisation | `PROXION_SOLID_USE_ADAPTER_GET` flag + console warnings | Set flag to `0` | Mismatch rate < 0.1% for 14 days |
| `web/pod.js` | `writeResource` | `@inrupt/solid-client` | `overwriteFile` | `dual-write` | Duplicate writes or ETag conflicts | HTTP conflict (409) count | Set flag to `0` | Error rate < 0.5% for 14 days |
| `web/pod.js` | `listContainer` | `@inrupt/solid-client` | `getContainedResourceUrlAll` | `shadow` | Wrong contact/invite listing | Mismatch count | Set flag to `0` | Mismatch rate < 0.1% |
| Notification watchers | push / poll fallback | `@inrupt/solid-client-notifications` | `WebsocketNotification` | `auto` | Silent notification drops during fallback | `notifs_fallback_count` | `PROXION_SOLID_NOTIFS_MODE=legacy` | Fallback rate < 2% for 14 days |

---

## Migration Modes

| Mode | Description |
|---|---|
| `shadow` | Both paths run; legacy result returned; adapter output compared silently. |
| `dual-read` | Both paths called; mismatch logged and metriced; caller sees legacy result. |
| `canary` | Adapter is primary for a small traffic slice; legacy available via kill switch. |
| `cutover` | Adapter is primary for all traffic; legacy blocked at stage 3 except emergency override. |

---

## Not in Scope by Default

- **Access grants** (`@inrupt/solid-client-access-grants`): disabled unless
  `PROXION_ENABLE_ACCESS_GRANTS=1`.  Enabling activates delegated third-party
  access patterns and requires an additional security review before deployment.
- **Vocabulary constants** (`@inrupt/vocab-solid`, `@inrupt/vocab-inrupt-core`):
  replacing hardcoded IRIs is a low-risk enhancement tracked separately from the
  auth/data migration.  Local app-private namespace constants are exempt.
