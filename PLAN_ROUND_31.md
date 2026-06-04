# PLAN_ROUND_31: Security Hardening + UI Catch-up

Two classes of work: security vulnerabilities confirmed by audit, and frontend
gaps that leave R27–R30 backend features unreachable from the UI.

---

## Security

### S1 — Remove SVG from allowed file MIME types

**Risk: HIGH** (`_gateway_dm.py:123`)

`image/svg+xml` is in `_ALLOWED_FILE_MIMES`. SVG is XML and can embed
`<script>`, `<foreignObject>`, CSS `url()`, and `xlink:href` references. Even
when served as a `data:` URI in an `<img>` tag, SVG files served from the same
origin at a download endpoint can be navigated to directly, bypassing the img
sandbox, and execute inline scripts. The CSP (`script-src 'self'`) blocks
external scripts but not inline ones without `unsafe-inline`, and the CSP is
not set on download responses.

**Fix:** Remove `"image/svg+xml"` from `_ALLOWED_FILE_MIMES` in
`_gateway_dm.py`. Clients sending SVG files will receive
`file_type_not_allowed: image/svg+xml`.

Also remove `"application/xhtml+xml"` if present (it has the same risk).

No migration or frontend change needed.

**New test:** `tests/test_svg_upload_blocked.py` (2 tests)
- SVG file upload is rejected with the correct error message
- PNG file upload is still accepted (regression guard)

---

### S2 — Validate `origin_gateway_url` before storing as peer gateway pin

**Risk: MEDIUM** (`gateway.py:3370–3372`)

In `_handle_relay_post`, the `origin_gateway_url` field comes directly from the
relay payload (attacker-controlled). It is stored via `_record_peer_gateway`
without first validating the URL against private IP ranges. The
`_is_safe_gateway_url` / `_validate_relay_target` helper already exists and is
used before outbound requests, but is not called here.

Consequence: an attacker can send a relay message claiming
`"origin_gateway_url": "http://169.254.169.254"` and the gateway records that
as the trusted peer URL, enabling future SSRF when the victim's gateway later
tries to relay back.

**Fix:** In `_handle_relay_post` at line 3371, guard the `_record_peer_gateway`
call:

```python
        # Record the sender's gateway URL — validate first to prevent SSRF
        if origin_gateway and from_webid:
            if _is_safe_gateway_url(origin_gateway):
                self._record_peer_gateway(from_webid, origin_gateway)
            else:
                logger.debug("relay: rejected unsafe origin_gateway_url from %s: %s",
                             from_webid, origin_gateway)
```

`_is_safe_gateway_url` is already imported at the top of `gateway.py` (line 29).

**New test:** `tests/test_relay_ssrf_guard.py` (3 tests)
- Private IP in `origin_gateway_url` is not stored
- Loopback address in `origin_gateway_url` is not stored
- Valid public URL in `origin_gateway_url` is stored normally

---

### S3 — Validate `home_gateway` URL in `announce_room_join`

**Risk: MEDIUM** (`_gateway_misc.py`, `_handle_announce_room_join`)

The `home_gateway` field in `announce_room_join` is stored directly to
`room_federated_members` without validating the URL. It is later used as the
target of `_relay_room_message` calls, which make outbound HTTP requests.
A malicious client can inject `"home_gateway": "http://10.0.0.1/admin"` and
trigger SSRF on every subsequent room message.

**Fix:** In `_handle_announce_room_join`, validate `home_gateway` before
accepting it:

```python
    from .relay import _validate_relay_target as _vrt
    if not _vrt(home_gateway.replace("wss://", "https://").replace("ws://", "http://")):
        await websocket.send(json.dumps({"type": "error", "message": "invalid_home_gateway"}))
        return
```

Add this check before the `same_gateway` comparison.

**New test:** added to `tests/test_federated_member_visibility.py` (1 additional test)
- `announce_room_join` with a private-IP `home_gateway` returns error, not stored

---

## Frontend GUI catch-up

### G1 — Send `announce_room_join` after joining a room

**Gap:** `announce_room_join` was added in R29 backend but the frontend never
sends it (`web/main.js` — zero occurrences). Federated room relay (R29/R30)
is entirely dead without this command. The handler exists; it just needs to be
called.

**When to send:** After the `room_joined` WebSocket event is received, if the
user has a non-empty `proxion_gateway_http_url` in localStorage that differs
from the room host's gateway (i.e., they are connecting to a room on a foreign
gateway, or they want their own home gateway to receive relayed messages).

**Fix:** In `web/main.js`, find the `case "room_joined":` handler and add:

```javascript
                case "room_joined": {
                    // ... existing room_joined handling ...
                    // R31: announce home gateway for federated relay
                    const _homeGw = localStorage.getItem("proxion_gateway_http_url") || "";
                    if (_homeGw && socket?.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            cmd: "announce_room_join",
                            room_id: event.room_id,
                            code: event.code || "",
                            home_gateway: _homeGw,
                        }));
                    }
                    break;
                }
```

Also send on reconnect: when the gateway sends `room_joined` for rooms the
user is already a member of (hydration), the same block fires automatically.

**New test:** No backend change — this is purely a frontend event. Verified
by checking the existing `test_announce_room_join_stores_federated_member`.

---

### G2 — Group voice call: leave button + channel UI

**Gap:** `join_voice_channel` (line 2690, `web/main.js`) is sent but
`leave_voice_channel` has no UI button. Users can join a group voice channel
but cannot cleanly exit — they must disconnect entirely. The voice banner
(`voice-banner`) shown during 1:1 calls has a hang-up button, but there is
no equivalent for group channels.

**Fix:**

1. In `web/main.js` `joinVoice(roomId)`, set a module-level flag
   `_inVoiceChannel = roomId` when joining, null on leave.

2. In the room header area (where the group call / voice button is), add a
   conditional "Leave channel" button that appears when `_inVoiceChannel`
   matches the current room:

```javascript
function leaveVoiceChannel() {
    if (!_inVoiceChannel) return;
    socket.send(JSON.stringify({cmd: "leave_voice_channel", room_id: _inVoiceChannel}));
    _inVoiceChannel = null;
    document.getElementById("leave-voice-channel-btn")?.style?.setProperty("display", "none");
    // Close WebRTC peer connections for all channel members
    for (const [peerId, pc] of Object.entries(peerConnections)) {
        pc.close();
        delete peerConnections[peerId];
    }
}
```

3. In `web/index.html`, add inside the room chat header (near `#room-voice-btn`
   or wherever the voice call button lives):

```html
<button id="leave-voice-channel-btn" style="display:none;background:#dc2626;border:none;color:#fff;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:0.8em;">Leave channel</button>
```

4. Wire: when `voice_peer_joined` arrives and `_inVoiceChannel` is set, show
   the leave button. When `leave_voice_channel` is sent, hide it.

5. Handle `voice_peer_left` — if the last peer leaves and we're alone, show a
   toast "You are alone in the channel".

---

### G3 — Federated member badge in room members panel

**Gap:** `memberHtml(m)` (line 375, `web/main.js`) renders member items but
ignores `m.federated`. R30's `get_room_members` returns `federated: true` for
cross-gateway members but the UI shows them identically to local members.

**Fix:** In `memberHtml`, add the federation indicator to the returned HTML:

```javascript
function memberHtml(m) {
    const color = webidColor(m.webid);
    const displayName = m.display_name || m.webid || "?";
    const initial = escHtml(displayName[0].toUpperCase());
    const presenceClass = m.status === "online" ? "online" : m.status === "away" ? "away" : m.status === "busy" ? "busy" : "";
    const fedBadge = m.federated
        ? `<span title="Federated member (${escHtml(m.gateway || 'remote gateway')})" style="font-size:0.65em;color:#64748b;margin-left:4px;vertical-align:middle;">&#x1F517;</span>`
        : "";
    return `<div class="member-item" data-msg-action="profile" data-webid="${escHtml(m.webid)}" data-name="${escHtml(displayName)}">
        <div style="position:relative;display:inline-block;margin-right:8px;">
            <div class="avatar placeholder" style="background:${color};width:28px;height:28px;line-height:28px;font-size:12px;font-weight:bold;text-align:center;">${initial}</div>
            <div class="avatar-presence ${presenceClass}" title="${escHtml(m.status || '')}"></div>
        </div>
        <span>${escHtml(m.display_name || m.webid.slice(0, 12))}${fedBadge}</span>
    </div>`;
}
```

The `🔗` emoji (U+1F517) is universally supported and signals "linked from
elsewhere". Hovering shows the gateway URL via the `title` attribute.

---

### G4 — Wire message feed avatar clicks to full contact profile panel

**Gap:** `data-msg-action="profile"` clicks in the message feed (line 5172)
call `showProfileCard` (old popover) but NOT `showContactProfile` (the R29
full panel). The panel exists and is wired to the members panel (lines 5201,
5210), but not to message feed avatars.

**Fix:** Change line 5172 from:

```javascript
case 'profile': showProfileCard(webid, name, e.clientX, e.clientY); break;
```

To:

```javascript
case 'profile':
    showProfileCard(webid, name, e.clientX, e.clientY);
    showContactProfile(webid);
    break;
```

This mirrors the existing behaviour in lines 5199–5202 for the members panel.
Both the quick hover card (legacy) and the full panel open — the hover card
gives the immediate compact view; the panel gives full detail.

---

### G5 — Room history snapshot on federated join

**Gap:** When a remote user sends `announce_room_join`, the backend acknowledges
with `federated_room_joined` but sends no history. The new federated member
joins a blank view even if the room has 200 messages.

**Fix:** In `_handle_announce_room_join` (`_gateway_misc.py`), after storing
the federated member, send the last 50 messages from `_store.get_messages`:

```python
    # Send recent history to the federated member
    if self._store:
        _history = self._store.get_messages(room_id, limit=50)
        if _history:
            await websocket.send(json.dumps({
                "type": "room_history",
                "room_id": room_id,
                "messages": _history,
            }))
```

**Frontend:** Handle `room_history` event in `web/main.js`:

```javascript
case "room_history":
    if (activeView && activeView.id === event.room_id) {
        // Prepend history messages to the feed without duplicating
        const existing = new Set(allMessages.map(m => m.message_id));
        const newMsgs = (event.messages || []).filter(m => !existing.has(m.message_id));
        allMessages = [...newMsgs, ...allMessages];
        newMsgs.forEach(m => { messageMap[m.message_id] = m; });
        // Re-render the feed with history
        renderMessages();
    }
    break;
```

Check that `get_messages(room_id, limit=50)` exists in `LocalStore` — if the
method name differs, use the correct one (`get_room_messages` or similar). The
returned dicts must match the message event format frontend expects.

**New tests:** `tests/test_room_history_on_join.py` (3 tests)
- `announce_room_join` sends `room_history` event when history exists
- `announce_room_join` sends empty history event when room has no messages
- History is limited to 50 messages (no unbounded dump)

---

## Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_svg_upload_blocked.py` | 2 | S1 |
| `tests/test_relay_ssrf_guard.py` | 3 | S2 |
| 1 new test added to `test_federated_member_visibility.py` | 1 | S3 |
| `tests/test_room_history_on_join.py` | 3 | G5 |
| **Total new** | **9** | |

G1–G4 are frontend-only changes; their correctness is verified by existing
backend tests and the established test for `announce_room_join`.

---

## Out of scope for R31

- HTTP-only room join (no WebSocket) — separate round
- Voice federation in rooms (ICE relay across gateways) — separate round
- Large file chunking (>512 KB) — separate round
- SVG sanitization/allowlist as an alternative to blocking — DOMPurify or
  server-side XML sanitizer would need a new dependency; blocking is safer
