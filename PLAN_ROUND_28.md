# PLAN_ROUND_28: Federation Hardening

Five confirmed gaps from the R27 gap audit, each with exact file locations.
No placeholder tasks — every edit target was verified against the live codebase.

---

## T1 — Voice invite via relay

**Gap:** `_handle_voice_invite` else-branch (lines 197–217 of `_gateway_voice.py`) falls back
only to a pod write. Cross-gateway voice calls cannot ring unless the callee happens to be
on a pod. Answer, ICE, and hangup already relay via `_relay_voice_signal`; invite does not.

**Fix:** Before the pod fallback, attempt relay if the target webid's gateway URL is known.

Edit `_gateway_voice.py` lines 197–217:

```python
else:
    # Target is on a different gateway.
    # First try: relay (fast, works without pod)
    _relayed = False
    if target_webid:
        try:
            _relayed = await self._relay_voice_signal(
                target_webid, "offer",
                {
                    "session_id": session_id,
                    "sdp_offer": sdp_offer,
                    "caller_webid": caller_webid,
                },
            )
        except Exception:
            pass
    # Second try: pod write (slower, requires federation cert)
    if not _relayed:
        try:
            client_entry = (self.dm_clients.get(cert_id) if cert_id else None) or (
                self._store
                and self._store.get_relationship_by_did(target_webid or "")
                and self.dm_clients.get(
                    (self._store.get_relationship_by_did(target_webid or "") or {}).get("certificate_id")
                )
                if target_webid else None
            )
            if client_entry:
                cert, pod_client = client_entry
                from .voice import signal_voice_invite
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, signal_voice_invite,
                    cert, pod_client, sdp_offer, session_id, caller_webid,
                )
        except Exception as exc:
            logger.debug("Pod voice_invite write skipped: %s", exc)
```

Frontend already handles `signal_type="offer"` in `handleVoiceSignalRelay` →
calls `showVoiceBanner()`. No frontend change needed.

**New tests:** `tests/test_voice_invite_relay.py` (3 tests)
- relay attempted when gateway URL known
- pod fallback when relay returns False
- pod fallback skipped when relay succeeds

---

## T2 — TLS auto-cert (self-signed, first-run)

**Gap:** `_make_ssl_context()` (line 1496 `gateway.py`) returns `None` when
`PROXION_SSL_CERT` / `PROXION_SSL_KEY` env vars are not set.
`run_gateway.py` never generates certs. Gateway starts unencrypted even though
WebRTC requires HTTPS for `getUserMedia` on non-localhost origins.

**Fix:** New module `proxion_messenger_core/tls.py` that generates a self-signed
RSA-2048 cert with 365-day validity, CN=proxion-gateway, SAN for localhost +
socket hostname. Cert is written to `~/.proxion/tls/cert.pem` +
`~/.proxion/tls/key.pem` on first run; paths returned for `_make_ssl_context`.

```
tls.py exports:
  ensure_self_signed_cert(cert_dir: Path) -> tuple[Path, Path]
    - creates cert_dir if missing
    - if cert.pem + key.pem exist and not expired: return paths unchanged
    - else: generate, write, return paths
```

Edit `run_gateway.py`:
- After loading `.env`, before constructing `GatewayConfig`:
  ```python
  if not os.environ.get("PROXION_SSL_CERT"):
      from proxion_messenger_core.tls import ensure_self_signed_cert
      import pathlib
      _cert, _key = ensure_self_signed_cert(
          pathlib.Path.home() / ".proxion" / "tls"
      )
      os.environ["PROXION_SSL_CERT"] = str(_cert)
      os.environ["PROXION_SSL_KEY"] = str(_key)
  ```

Dependency: `cryptography` is already in `proxion-messenger-core` requirements
(used by `sealed_relay.py`). No new dep.

**New tests:** `tests/test_tls.py` (3 tests)
- `ensure_self_signed_cert` creates cert + key files
- second call returns same files (idempotent)
- cert SAN includes `localhost`

---

## T3 — File relay for cross-gateway DMs

**Gap:** `gateway.py` line 3142 hard-rejects any relay payload containing `"file"`:
```python
if "file" in data:
    return "400 Bad Request", '{"error":"unsupported_relay_attachment"}'
```
`_ALLOWED_RELAY_KEYS` does not include `"file"`. `_handle_send_file` in
`_gateway_dm.py` returns an error string for federated rooms instead of relaying.
The `/relay` endpoint already has a 128 KiB size cap, which accommodates files
up to ~90 KB after base64 encoding.

**Fix:**

1. Remove the `if "file" in data` early-return in `gateway.py`.
2. Add `"file"`, `"file_name"`, `"file_type"`, `"file_size"` to `_ALLOWED_RELAY_KEYS`.
3. In `_gateway_dm.py` `_handle_send_file`, after the payload-size check,
   attempt relay for cross-gateway DMs the same way `_handle_send_dm` does
   (sign + POST to `/relay`). If the file bytes + metadata exceed 90 KB,
   return an explicit error to the client instead of silently failing.

File payload format in relay (same fields as DM, plus):
```json
{
  "from_webid": "...", "to_webid": "...", "message_id": "...",
  "content": "<caption or empty>", "timestamp": 123,
  "signature": "...", "relay_nonce": "...",
  "file": "<base64>", "file_name": "photo.jpg",
  "file_type": "image/jpeg", "file_size": 45000
}
```

**Size limit:** reject at gateway if `len(base64_file) > 98304` (96 KB base64
≈ 72 KB binary) before relaying — keeps total payload under 128 KiB.

**New tests:** `tests/test_file_relay.py` (4 tests)
- relay POST with `"file"` key is accepted (was 400, should be 200)
- oversized file rejected with 413
- `_handle_send_file` calls `post_relay` for cross-gateway DM
- `_handle_send_file` returns error when file > 96 KB

---

## T4 — Reachability detection + NAT warning

**Gap:** When `PROXION_PUBLIC_URL` is unset and the gateway is behind NAT, peers
cannot reach it via the `.well-known/proxion` `gateway_http_url`. Nothing warns
the user. The onboarding step-6 ("You're all set!") says nothing about public
reachability.

**Fix — server side:** In `/.well-known/proxion` handler (gateway.py line 1747),
add `"nat_warning": true` when `config.public_url` is None:
```python
if not self.config.public_url:
    discovery_data["nat_warning"] = True
```

**Fix — frontend banner:** In `web/main.js`, after WebSocket `open` + identity
established, fetch `/.well-known/proxion` and if `nat_warning` is true, show a
dismissible yellow banner above the chat area:
```
"Federation is limited: no public URL configured.
 Set PROXION_PUBLIC_URL in .env to allow peers on other gateways to reach you."
```
Banner shows once per session (dismissed = sessionStorage flag).

**Fix — onboarding step-6:** Update `web/index.html` ob-step-6 to show the
user's Proxion address (already displayed elsewhere via `#my-proxion-addr`)
with a short explanation:
```
Your Proxion address: <span id="ob-my-addr"></span>
Share this with contacts so they can add you from any Proxion gateway.
```
Populate `#ob-my-addr` in the JS finish-step handler.

**New tests:** `tests/test_nat_warning.py` (2 tests)
- `.well-known/proxion` includes `nat_warning: true` when public_url is None
- `.well-known/proxion` omits `nat_warning` when public_url is set

---

## T5 — TURN / federation health indicators in /health

**Gap:** `/health` (line 1823 `gateway.py`) reports `status`, `connected_clients`,
`pod_available`, `uptime_s` but nothing about TURN or relay readiness.
No UI surface for operators to verify federation is healthy.

**Fix — server:** Add to `/health` JSON:
```json
{
  "turn_configured": true,
  "relay_capable": true,
  "public_url_set": true
}
```
- `turn_configured`: `bool(self.config.turn_url and self.config.turn_secret)`
- `relay_capable`: `bool(self.config.public_url)` — gateway can be reached by peers
- `public_url_set`: same as relay_capable (alias for clarity in UI)

**Fix — frontend:** In the settings panel (`web/main.js` `renderSettingsPanel()`
or equivalent), after loading, fetch `/health` and render a small status grid:

```
Federation
  Public URL:  ✓ configured  /  ✗ not set (federation limited)
  TURN server: ✓ configured  /  ✗ not set (WebRTC may fail across NAT)
  Pod:         ✓ connected   /  ✗ offline
```

**New tests:** `tests/test_health_indicators.py` (3 tests)
- `/health` includes `turn_configured: false` when TURN not set
- `/health` includes `turn_configured: true` when TURN configured
- `/health` includes `relay_capable` reflecting public_url presence

---

## T6 — Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_voice_invite_relay.py` | 3 | T1 |
| `tests/test_tls.py` | 3 | T2 |
| `tests/test_file_relay.py` | 4 | T3 |
| `tests/test_nat_warning.py` | 2 | T4 |
| `tests/test_health_indicators.py` | 3 | T5 |
| **Total** | **15** | |

All tests use existing fixtures (`gateway`, `gateway_with_turn`, `tmp_path`).
No new test infrastructure needed.

---

## Out of scope for R28

- `did:web` identity support — requires full DID resolver; separate round
- Multi-device sync UX — address copy + QR already exist (confirmed closed in R27 audit)
- Relay queue drain on connect — already implemented in `_gateway_auth.py` (confirmed closed)
- Large file transfers (>128 KB) — chunked upload protocol; separate round
