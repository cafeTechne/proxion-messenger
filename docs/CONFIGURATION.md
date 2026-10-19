# Configuration reference

Every environment variable the gateway, its launcher and the bundled tools read.
`run_gateway.py` also loads a `.env` file next to it (see `.env.example`). The
desktop app sets its own values for the sidecar (listed in `main.rs`) and needs
no configuration.

Boolean switches are on only when set to `1` unless noted. The "Read in" column
names the file to check when in doubt.

## Network and TLS

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `PROXION_HOST` | `0.0.0.0` | Interface to bind. `127.0.0.1` keeps the gateway local-only | `gateway.py` |
| `PROXION_HTTP_PORT` | `8080` | Web UI and HTTP endpoints | `run_gateway.py` |
| `PROXION_WS_PORT` | `7474` | WebSocket port (`PROXION_PORT` is an older alias) | `gateway.py` |
| `PROXION_PUBLIC_URL` | unset | WebSocket URL given to browsers and peers. When unset, `run_gateway.py` tries a UPnP port mapping | `gateway.py`, `run_gateway.py` |
| `PROXION_HTTP_PUBLIC_URL` | derived | Base URL peers use for `/relay` and other federation endpoints | `_gateway_pod.py` |
| `PROXION_ALLOWED_ORIGINS` | loopback + desktop webview | Comma-separated browser Origins allowed to open the WebSocket. Replaces the default list | `gateway.py` |
| `PROXION_AUTO_TLS` | `1` | `run_gateway.py` creates a self-signed cert in `<data dir>/tls` when no cert is set. `0` serves plain http/ws | `run_gateway.py` |
| `PROXION_SSL_CERT`, `PROXION_SSL_KEY` | unset | PEM cert and key; the gateway then serves https and wss | `gateway.py` |
| `PROXION_MAX_CLIENTS` | `200` | Concurrent WebSocket connections | `gateway.py` |
| `PROXION_MAX_CONNECTIONS_PER_IP` | `8` | Concurrent WebSocket connections per address | `gateway.py` |
| `PROXION_BIND_WAIT` | `0` | Seconds to wait for a busy port before giving up (the desktop app uses 15) | `gateway.py` |

## Storage and logging

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `PROXION_DATA_DIR` | `./data` beside `run_gateway.py`; `~/.proxion/data` for a packaged executable | Keys, database, logs, TLS cert, invite stash | `run_gateway.py` |
| `PROXION_WEB_DIR` | bundled `web/` | Directory served as the web UI | `run_gateway.py` |
| `PROXION_LOG_LEVEL` | `INFO` | Python log level | `run_gateway.py` |
| `PROXION_LOG_FORMAT` | `text` | `json` for structured logs | `run_gateway.py` |
| `PROXION_AUDIT_RETENTION_DAYS` | `90` | Audit log retention | `gateway.py` |
| `PROXION_SECURITY_EVENT_RETENTION_DAYS` | `90` | Security event retention | `gateway.py` |
| `PROXION_DM_SESSION_RETENTION_DAYS` | `90` | Retention for expired DM session records | `gateway.py` |
| `PROXION_IDEMPOTENCY_RETENTION_HOURS` | `72` | Retention for idempotency keys | `gateway.py` |
| `PROXION_SKIP_LEGACY_MIGRATION` | unset | Skip copying the blocklist and stash from the old shared `~/.proxion` (tests set it) | `gateway.py` |

## Solid pod

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `PROXION_CSS_URL` | unset | Solid server for the gateway's own pod. CSS, JSS or NSS, detected from the URL | `gateway.py` |
| `PROXION_CSS_EMAIL`, `PROXION_CSS_PASSWORD` | unset | Account on that server. Leave all three blank for local-only rooms | `gateway.py` |
| `PROXION_CSS_DEFAULT_URL` | unset | Pod provider pre-filled in the browser's sign-in screen | `gateway.py` |
| `PROXION_MAX_TOKEN_AGE_S` | `1800` | Refresh a pod access token older than this | `css_auth.py` |

## Authentication and owner endpoints

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `PROXION_REQUIRE_AUTH` | auto | `1` requires proof of key ownership on every WebSocket; `0` disables it. Unset: required unless bound to loopback, and always required while the app's tunnel is open | `gateway.py`, `_gateway_auth.py` |
| `PROXION_ADMIN_API_TOKEN` | unset | Bearer token for `/backup`, `/restore`, `/export`, `/import`, `/contacts`, `/message-edits`, `/security-snapshot`. Required behind a reverse proxy ([SELF_HOSTING.md](SELF_HOSTING.md)) | `_gateway_http.py` |
| `PROXION_API_TOKEN` | unset | Older name for the same token, used when `PROXION_ADMIN_API_TOKEN` is unset | `_gateway_http.py` |
| `PROXION_AUTH_TIMEOUT_SECONDS` | `30` | Time a new socket has to complete registration | `gateway.py` |
| `PROXION_SESSION_IDLE_TIMEOUT_S` | `86400` | Close sessions idle this long | `gateway.py` |
| `PROXION_SINGLE_SESSION` | unset | Closes an identity's other sockets when it signs in again | `_gateway_auth.py` |
| `PROXION_DISPLAY_NAME` | unset | Display name on the gateway's own public profile | `_gateway_http.py` |
| `PROXION_SAFE_MODE` | unset | Refuses `/restore` and `/import` | `_gateway_http.py` |
| `PROXION_REQUIRE_RECOVERY_APPROVAL` | unset | Restore needs the id of an approved recovery operation | `_gateway_http.py` |
| `PROXION_REQUIRE_IMPORT_MANIFEST` | unset | Import and restore need an `X-Proxion-Import-Manifest` header (source, sha256) | `_gateway_http.py` |

## Federation and relays

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `PROXION_ALLOW_PRIVATE_RELAY` | unset | Allow relaying to loopback or LAN gateways. Local two-gateway testing only | `network.py` |
| `PROXION_ALLOW_INSECURE_FEDERATION` | unset | Allow `http://` federation targets | `gateway.py` |
| `PROXION_FEDERATION_QUARANTINE` | unset | Hold inbound federation invites for review instead of applying them | `_gateway_http.py` |
| `PROXION_ALLOW_UNVERIFIED_KEY_ROLLOVER` | unset | Accept a relay signed by a key previously marked untrusted | `relay.py` |
| `PROXION_RELAY_NODE` | unset | Store sealed blobs for other gateways (mailbox relay) | `_gateway_mailbox.py` |
| `PROXION_RELAY_FALLBACK_URL` | unset | Relay node to use when a peer is unreachable. No relay is used unless this is set | `_gateway_mailbox.py` |
| `PROXION_ENABLE_DID_WEB` | unset | Publish and resolve `did:web` | `did_web.py` |
| `PROXION_ENABLE_ACCESS_GRANTS` | `0` | Solid access grants for third-party pod access | `_gateway_misc.py` |
| `PROXION_ACCESS_GRANTS_ISSUER_ALLOWLIST`, `PROXION_ACCESS_GRANTS_SCOPE_ALLOWLIST` | unset | Comma-separated allowlists for access grants | `_gateway_misc.py` |

## Calls, push and messages

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `TURN_URL`, `TURN_SECRET` | unset | Your coturn and its shared secret. Both set: clients get time-limited TURN credentials. The web client otherwise falls back to `openrelay.metered.ca` unless the user disables it | `gateway.py` |
| `PROXION_VAPID_PUBLIC_KEY`, `PROXION_VAPID_PRIVATE_KEY`, `PROXION_VAPID_SUBJECT` | unset | Web Push keys for notifications to a closed browser | `gateway.py` |
| `PROXION_LINK_PREVIEWS` | `0` | Server-side link previews | `gateway.py` |
| `PROXION_EXPIRE_TICK_S` | `30` | How often disappearing messages are swept (0.5 to 300) | `_gateway_rooms.py` |
| `PROXION_PREKEY_LOW_THRESHOLD` | `5` | Ask a connected client for more one-time prekeys below this count | `gateway.py` |
| `PROXION_SPK_MAX_AGE_SECONDS` | `604800` | Ask a connected client to rotate a signed prekey older than this | `gateway.py` |
| `PROXION_CHECKSUM_INTERVAL` | `300` | Seconds between checksum checks of security-critical tables | `gateway.py` |

## Hardening (off by default)

These back the controls described in [security/](security/).

| Variable | Meaning | Read in |
|---|---|---|
| `PROXION_REQUIRE_SIGNED_CONFIG`, `PROXION_CONFIG_FILE`, `PROXION_CONFIG_SIG_FILE`, `PROXION_CONFIG_PUBKEY_HEX` | Refuse to start unless the config file carries a valid Ed25519 signature | `config_verify.py` |
| `PROXION_REQUIRE_RUNTIME_INTEGRITY`, `PROXION_MANIFEST_FILE`, `PROXION_MANIFEST_SIG_FILE`, `PROXION_RUNTIME_PUBKEY_HEX` | Verify the installed files against a signed manifest | `supply_chain.py` |
| `PROXION_REQUIRE_BUILD_PROVENANCE` | Require build provenance | `provenance_verify.py` |
| `PROXION_POLICY_FILE`, `PROXION_REQUIRE_POLICY_HASH` | Load a security policy file, optionally pinned by hash | `security_policy.py` |
| `PROXION_DRIFT_ESCALATION_MODE` | `off` (default), `restrictive` or `containment` on high spec drift | `security_policy.py` |
| `PROXION_ENABLE_CONTINUOUS_ASSURANCE`, `PROXION_ASSURANCE_INTERVAL_S` | Periodic self-checks (default interval 300 s) | `continuous_assurance.py` |
| `PROXION_ENABLE_INTEGRITY_CONSENSUS` | Cross-check integrity with peer gateways | `integrity_consensus.py` |
| `PROXION_ALLOW_WEAK_PASSPHRASE` | Accept passphrases shorter than 12 characters | `persist.py` |

## Solid SDK migration (advanced)

| Variable | Default | Meaning | Read in |
|---|---|---|---|
| `PROXION_SOLID_AUTH_MODE` | `legacy` | `legacy`, `inrupt_bridge`, `auto` or `sdk` | `solid_migration.py` |
| `PROXION_SOLID_NOTIFS_MODE` | `auto` | Solid notifications mode | `solid_migration.py` |
| `PROXION_SOLID_CUTOVER_STAGE` | `0` | `2` logs legacy pod calls; `3` blocks them | `solid_client.py` |
| `PROXION_SOLID_EMERGENCY_OVERRIDE` | `0` | Allow legacy calls at stage 3 | `solid_client.py` |
| `PROXION_SOLID_DUAL_READ` | `0` | Shadow-compare reads | `solid_client.py` |
| `PROXION_REQUIRE_SUPPORTED_SOLID_SDK_RUNTIME`, `PROXION_ALLOW_UNSUPPORTED_SDK_UNTIL` | unset | Refuse to start on an unsupported SDK, with a time-limited bypass (Unix timestamp) | `sdk_support_guard.py` |

See [solid_sdk_migration_matrix.md](solid_sdk_migration_matrix.md).

## Set by the launcher or the desktop app

| Variable | Meaning |
|---|---|
| `PROXION_SHUTDOWN_ON_STDIN_EOF` | Stop on a `shutdown` line or end of stdin (desktop sidecar) |
| `PROXION_UPNP_MAPPED` | Set by `run_gateway.py` when it mapped a port with UPnP |

## MCP server and CLI

| Variable | Default | Meaning |
|---|---|---|
| `PROXION_GATEWAY_URL` | `ws://127.0.0.1:7474` | Gateway the MCP server connects to ([MCP_SERVER.md](MCP_SERVER.md)) |
| `PROXION_GATEWAY_CAFILE` | `<PROXION_DATA_DIR>/tls/cert.pem` if present | Cert to trust for a `wss://` gateway |
| `PROXION_AGENT_STATE`, `PROXION_AGENT_PASSPHRASE`, `PROXION_AGENT_NAME` | unset, unset, `Proxion Agent` | MCP agent identity |
| `PROXION_STATE`, `PROXION_PASSPHRASE` | `~/.proxion/agent.json`, prompt | `proxion` CLI state file and its passphrase |
| `PROXION_SIGNING_KEY`, `PROXION_VALIDATOR_TOKEN` | unset | `proxion` validator commands |
| `CSS_ALICE_URL`, `CSS_CLIENT_ID`, `CSS_CLIENT_SECRET` | unset | `proxion` pod test commands against a CSS account |

## Build

| Variable | Meaning |
|---|---|
| `PROXION_CLOUDFLARED_VERSION`, `PROXION_CLOUDFLARED_SHA256` | Bundle a verified cloudflared into a local sidecar build ([RELEASE.md](RELEASE.md)) |
| `SOURCE_DATE_EPOCH` | Fixed timestamp for reproducible builds ([VERIFYING.md](VERIFYING.md)) |
