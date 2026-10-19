# Proxion Testing Guide

## Quick single-user test (no pod, local only)

Automated suites are described in [CONTRIBUTING.md](CONTRIBUTING.md) and
[web/README.md](web/README.md). This guide is the manual walk-through.

Use a scratch data dir and blank pod settings so a test never touches a real pod:

```powershell
$env:PROXION_DATA_DIR = "$env:TEMP\proxion-test"
$env:PROXION_CSS_URL = ""; $env:PROXION_CSS_EMAIL = ""; $env:PROXION_CSS_PASSWORD = ""
python run_gateway.py
```

Open `https://127.0.0.1:8080/` and accept the self-signed certificate (or set
`$env:PROXION_AUTO_TLS = "0"` first and use `http://`). Onboarding flow:
1. Welcome → Continue
2. Display name → Continue
3. Set presence → Continue
4. Pod sign-in → click **Skip for now →**
5. Join or create a room → Create a Room
6. Done → Open Proxion

Send messages. Reactions, pinning, and profile cards should all work.
Voice calls require a second browser tab (see below).

---

## Two-user test (same gateway, two tabs)

Same gateway, two browser tabs. Tests local relay.

1. Both tabs: `https://127.0.0.1:8080/`, complete onboarding with different display names
2. Tab A: copy DID from Settings → share with Tab B
3. Tab B: click ＋ Add Contact → paste Tab A's DID → DM opens
4. Send messages in both directions
5. Click 📞 Call — verify voice ring + accept + audio (check DevTools → Media)

---

## Two-user test (separate gateways, federation)

Two loopback gateways cannot verify each other's self-signed certificates, so run
them on plain http/ws with loopback relaying allowed (the same settings the
two-gateway smokes use, see `scripts/run_test_gateway.py`):

```powershell
# both terminals
$env:PROXION_AUTO_TLS="0"; $env:PROXION_HOST="127.0.0.1"; $env:PROXION_REQUIRE_AUTH="0"
$env:PROXION_ALLOW_PRIVATE_RELAY="1"; $env:PROXION_ALLOW_INSECURE_FEDERATION="1"
$env:PROXION_CSS_URL=""; $env:PROXION_PUBLIC_URL=""

# Terminal A
$env:PROXION_DATA_DIR="$env:TEMP\proxion-a"; $env:PROXION_HTTP_PORT="8080"; $env:PROXION_WS_PORT="7474"
python run_gateway.py

# Terminal B
$env:PROXION_DATA_DIR="$env:TEMP\proxion-b"; $env:PROXION_HTTP_PORT="8081"; $env:PROXION_WS_PORT="7475"
python run_gateway.py
```

Alice at `http://127.0.0.1:8080`, Bob at `http://127.0.0.1:8081`.
Bob: Add Contact → paste Alice's address (Settings shows it, `did:key:…@http://127.0.0.1:8080`).
`npm run smoke:cross-gateway` in `web/` automates this.

---

## Solid OIDC flow (requires internet + a Solid pod account)

1. Open `https://127.0.0.1:8080/` in a fresh private window (no localStorage)
2. Onboarding steps 1–3 (name + presence)
3. Step 4: click **Sign in with solidcommunity.net**
4. Authenticate at solidcommunity.net → confirm redirect back to `https://127.0.0.1:8080/`
5. Verify:
   - Onboarding auto-advances to step 5 (join/create room)
   - Pod-connect banner is hidden
6. Create a room, send a message
7. Navigate to your pod in a browser tab:
   `https://yourname.solidcommunity.net/proxion/rooms/{room_id}/YYYY/MM/DD/chat.ttl`
   (today's Long Chat file; layout in [docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md))
   and confirm your message is in it
8. Refresh `https://127.0.0.1:8080/`: session restores, no re-login prompt
9. Open Settings → Pod section → confirm WebID shown and "● Connected"
10. Click "Sign out of Pod" → pod-connect banner reappears; settings shows disconnected

### Security checks during OIDC test

- DevTools → Network: after redirect back, confirm URL in browser bar has NO
  `?code=` or `?state=` — they should be stripped before the page finishes loading
- DevTools → Application → Local Storage: confirm `proxion_pod_webid` is set, but
  NO token or secret is stored (tokens live in IndexedDB via the Inrupt library)
- DevTools → Application → IndexedDB → `solid-client-authn` — session data is here
- DevTools → Network → Response Headers for `index.html` — confirm `Content-Security-Policy` present
- DevTools → Console → no CSP violation warnings after normal app use

---

## DM smoke test (local_dm path)

Two browser tabs on the same gateway. After completing onboarding in both tabs:

1. **Add contact**: Tab B: copy DID from Settings. Tab A: click ＋ Add Contact → paste Tab B's DID.
2. **DM thread opens** in Tab A. The chat header should show Tab B's display name.
3. **Send from A → B**: type a message and press Enter.
   - Tab A: message appears immediately (echo from gateway with `own: true`).
   - Tab B: message appears in the DM thread.
   - Tab A sidebar: DM entry updates its last-message preview.
4. **Send from B → A**: Tab B clicks the DM from Tab A's DID in the sidebar (if not already open), sends a reply.
   - Both directions must work.
5. **Reload Tab A**: DM thread history reloads from gateway SQLite (`read_dm` command). Messages persist.
6. **Typing indicator**: while Tab A types, Tab B should see a typing indicator (if implemented in UI).
7. **Unread badge**: Tab A with DM open, Tab B sends a message → no badge on Tab A. Switch Tab A to a room, then Tab B sends → unread badge appears on DM sidebar item.

---

## E2E encryption (full Double Ratchet)

Both tabs on the same gateway (two-user test above), contacts added both ways. The
protocol is specified in [docs/E2E_ENCRYPTION.md](docs/E2E_ENCRYPTION.md).

### First message

1. Tab A opens the DM with Tab B and sends. The client fetches B's key from the
   gateway's device roster before the first send; there is no plaintext first
   message. The WS frame carries `"e2e":true` and ratchet header fields.
2. Tab B decrypts automatically.
3. If no key can be found the message is not sent: the text stays in the composer
   and a notice explains why (an agent without an X25519 key, for example, can be
   messaged unencrypted only after choosing "Send unencrypted").

### Multi-round ratchet

4. Send 5 messages A → B, then 5 B → A. All decrypt with no errors.
5. Alternate one message each way several times. Each direction change is a DH
   ratchet step; all decrypt.
6. Edit a message, react to one, and attach a file. The gateway frames carry
   ciphertext for the edit and file, and an opaque `~…` token for the reaction;
   Tab B shows the real text, emoji and file.

### Safety number verification

7. Tab A: open the verify dialog from the DM header.
8. It shows a 30-digit safety number (6 groups of 5 digits).
9. Tab B: open the same dialog; the number must be **identical** (it is symmetric).
10. Tab A: click **Mark as verified**; the header shows Verified.
11. Reload Tab A; the verified state persists.

### Ratchet state persistence

12. Exchange 3 encrypted messages. Close Tab A completely (not just reload — close the tab).
13. Reopen `https://127.0.0.1:8080/` in a new tab. Re-register (DID auto-loaded from localStorage).
14. Send an encrypted message — should work without any re-init prompt.

### E2E unit tests

```powershell
cd web; npm test            # includes e2e.test.js and the other crypto suites
```

### Python interop tests

```powershell
python -m pytest proxion-messenger-core/tests/test_e2e_crypto.py -v
```

Verifies HKDF-SHA256 salt/info strings, HMAC chain constants, AES-256-GCM wire format,
`kdfRk` vectors, full ratchet round DH symmetry, and `safetyNumber` format — all must match
the JS implementation in `web/e2e.js`.

---

## Resetting state

```powershell
Remove-Item -Recurse -Force $env:PROXION_DATA_DIR   # wipe the test gateway's keys and database
```

To also clear browser session: DevTools → Application → Clear Storage → Clear site data.

To reset E2E ratchet state only (re-derives fresh keypair and clears peer states):
DevTools → Application → Local Storage → delete all `proxion_e2e_*` keys, then reload.
