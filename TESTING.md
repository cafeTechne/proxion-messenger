# Proxion Testing Guide

## Quick single-user test (no pod, local only)

```powershell
$env:PROXION_HTTP_PORT = "8080"
python run_gateway.py
```

Open `http://127.0.0.1:8080/`. Onboarding flow:
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

1. Both tabs: `http://127.0.0.1:8080/` — complete onboarding with different display names
2. Tab A: copy DID from Settings → share with Tab B
3. Tab B: click ＋ Add Contact → paste Tab A's DID → DM opens
4. Send messages in both directions
5. Click 📞 Call — verify voice ring + accept + audio (check DevTools → Media)

---

## Two-user test (separate gateways, federation)

```powershell
# Terminal A
$env:PROXION_HTTP_PORT="8080"; $env:PROXION_WS_PORT="7474"
$env:PROXION_PUBLIC_URL="ws://localhost:7474"; python run_gateway.py

# Terminal B
$env:PROXION_HTTP_PORT="8081"; $env:PROXION_WS_PORT="7475"
$env:PROXION_PUBLIC_URL="ws://localhost:7475"; python run_gateway.py
```

Alice at `http://localhost:8080`, Bob at `http://localhost:8081`.
Bob: Add Contact → paste `{alice_did}@http://localhost:8080`.

---

## Solid OIDC flow (requires internet + a Solid pod account)

1. Open `http://127.0.0.1:8080/` in a fresh private window (no localStorage)
2. Onboarding steps 1–3 (name + presence)
3. Step 4: click **Sign in with solidcommunity.net**
4. Authenticate at solidcommunity.net → confirm redirect back to `http://127.0.0.1:8080/`
5. Verify:
   - Onboarding auto-advances to step 5 (join/create room)
   - Pod-connect banner is hidden
6. Create a room, send a message
7. Navigate to your pod in a browser tab:
   `https://yourname.solidcommunity.net/proxion/rooms/{room_id}/messages/{msg_id}.json`
   — confirm the JSON file exists
8. Refresh `http://127.0.0.1:8080/` — session restores, no re-login prompt
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

Both tabs must be on the same gateway (two-user test above). After adding each other as contacts and exchanging at least one plaintext DM (so both sides cache the peer's X25519 pub key):

### Setup verification

1. Tab A opens DM thread with Tab B — look for the `🔓 E2E` badge in the chat header.
2. Tab A sends a message. Gateway WS frame should have `"e2e":true,"ratchet_pub":"...","pn":0`.
3. Tab B receives and decrypts automatically — message text is plaintext in the feed.
4. Badge in Tab A's header becomes `🔒 E2E` after first encrypted exchange? No — badge is 🔓 until manually verified. The lock/unlock only changes via the Verify button.
5. DevTools → Application → Local Storage: `proxion_e2e_state_<peerId>` key is present.

### Multi-round ratchet

6. Send 5 messages A → B, then 5 messages B → A. All should decrypt correctly with no errors.
7. Alternate: A sends 1, B sends 1, A sends 1, etc. — DH ratchet steps trigger on each direction change. All decrypt correctly.

### Safety number verification

8. Tab A: click **Verify** button in DM header → verify modal opens.
9. Modal shows a 30-digit safety number (6 groups of 5 digits, e.g. `12345 67890 ...`).
10. Tab B: open same modal — safety number must be **identical** (it's symmetric by design).
11. Tab A: click **Mark as Verified** → badge changes to `🔒 Verified`.
12. Reload Tab A → verified state persists (stored in localStorage).

### Plaintext fallback (first contact)

13. Clear all `proxion_e2e_*` localStorage keys in Tab A only. Reload Tab A.
14. Tab A sends a message — no peer pub key yet → message sent in plaintext (no `"e2e":true` in WS frame).
15. Tab B receives and decrypts as plaintext (no error).
16. Tab A's outgoing message includes `x25519_pub` — Tab B caches it.
17. Tab B sends a reply — still no pub for A yet from B's perspective → plaintext.
18. After one round-trip both sides have each other's pub → next send from either side is encrypted.

### Ratchet state persistence

19. Exchange 3 encrypted messages. Close Tab A completely (not just reload — close the tab).
20. Reopen `http://127.0.0.1:8080/` in a new tab. Re-register (DID auto-loaded from localStorage).
21. Send an encrypted message — should work without any re-init prompt.

### E2E unit tests

```powershell
cd web && npm test          # 39 tests: 15 pod.js + 24 e2e.js
```

### Python interop tests

```powershell
python -m pytest proxion-messenger-core/tests/test_e2e_crypto.py -v   # 28 tests
```

Verifies HKDF-SHA256 salt/info strings, HMAC chain constants, AES-256-GCM wire format,
`kdfRk` vectors, full ratchet round DH symmetry, and `safetyNumber` format — all must match
the JS implementation in `web/e2e.js`.

---

## Resetting state

```powershell
Remove-Item -Recurse -Force ./data   # wipe gateway SQLite
```

To also clear browser session: DevTools → Application → Clear Storage → Clear site data.

To reset E2E ratchet state only (re-derives fresh keypair and clears peer states):
DevTools → Application → Local Storage → delete all `proxion_e2e_*` keys, then reload.
