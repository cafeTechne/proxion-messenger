# PLAN_ROUND_32: Room Bans + Mutes, Seen-By UI, Device Panel

Four tasks. T1 and T4 are new feature work; T2 and T3 build on
infrastructure that already exists but is incomplete or incorrectly scoped.

**Confirmed-existing (not re-implemented):**
- Kick member (`_gateway_rooms.py:1557`), role management, ownership transfer
- `room_read_receipts` table + `mark_room_read()` + `get_room_last_read()`
- `message_receipts` table (migration 42, `delivered_at` + `read_at` per receiver)
- `settings-receipts-toggle` + `proxion_receipts_enabled` localStorage key
- `set_receipts_enabled` command + gateway handler → `self._receipts_enabled`
  (but this is a single gateway-wide flag — see T2)
- `list_devices` / `_handle_list_devices` / `unregister_device` — complete backend
- Message pagination + infinite scroll

---

## T1 — Room bans, mutes, and moderation UI

### 1a. Schema (migration 53)

Two new tables added together:

```sql
CREATE TABLE IF NOT EXISTS room_bans (
    room_id    TEXT NOT NULL,
    banned_did TEXT NOT NULL,
    banned_by  TEXT NOT NULL,
    banned_at  REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
    reason     TEXT,
    PRIMARY KEY (room_id, banned_did)
);

CREATE TABLE IF NOT EXISTS room_mutes (
    room_id    TEXT NOT NULL,
    muted_did  TEXT NOT NULL,
    muted_by   TEXT NOT NULL,
    muted_at   REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
    expires_at REAL,   -- NULL = indefinite
    PRIMARY KEY (room_id, muted_did)
);
```

### 1b. `LocalStore` — 7 new methods

```python
# Bans
def ban_room_member(self, room_id, banned_did, banned_by, reason="") -> None
def unban_room_member(self, room_id, banned_did) -> None
def get_room_bans(self, room_id) -> list[dict]
def is_room_banned(self, room_id, did) -> bool

# Mutes
def mute_room_member(self, room_id, muted_did, muted_by, expires_at=None) -> None
def unmute_room_member(self, room_id, muted_did) -> None
def is_room_muted(self, room_id, did) -> bool
    # Returns False if expires_at is set and time.time() > expires_at
```

### 1c. `_gateway_rooms.py` — 4 new handlers + 2 enforcement points

**`_handle_ban_member(websocket, data)`**
1. `_check_room_permission(websocket, room_id, "admin")` — owner or admin only.
2. Extract `target_webid`, optional `reason` from data.
3. `self._store.ban_room_member(room_id, target_webid, caller_webid, reason)`.
4. If target has a live socket in `room["members"]`, invoke the existing kick
   path (remove socket, call `_cleanup_voice_sessions(ws)`, send `kicked_from_room`).
5. Broadcast to all room members:
   `{"type": "member_banned", "room_id": ..., "webid": ..., "display_name": ..., "reason": ...}`

**`_handle_unban_member(websocket, data)`**
1. Owner or admin only.
2. `self._store.unban_room_member(room_id, target_webid)`.
3. Broadcast: `{"type": "member_unbanned", "room_id": ..., "webid": ...}`

**`_handle_mute_member(websocket, data)`**
1. Owner or admin only.
2. Compute `expires_at = time.time() + duration_seconds` if `duration_seconds`
   in data, else `None`.
3. `self._store.mute_room_member(...)`.
4. Broadcast: `{"type": "member_muted", "room_id": ..., "webid": ..., "expires_at": ...}`

**`_handle_unmute_member(websocket, data)`**
1. Owner or admin only.
2. `self._store.unmute_room_member(...)`.
3. Broadcast: `{"type": "member_unmuted", "room_id": ..., "webid": ...}`

**`_handle_get_room_bans(websocket, data)`**
1. Owner or admin only.
2. `bans = self._store.get_room_bans(room_id)`.
3. Enrich with display names from `_store.get_display_name`.
4. Send: `{"type": "room_bans", "room_id": ..., "bans": [...]}`

**Ban enforcement in `_handle_join_room`** (insert after code validation, before adding to members):
```python
if self._store and self._store.is_room_banned(room_id, joiner_webid):
    await websocket.send(json.dumps({
        "type": "error", "message": "banned_from_room",
    }))
    return
```

**Ban enforcement in `_handle_announce_room_join`** (same check, same position):
```python
if self._store and self._store.is_room_banned(room_id, caller_webid):
    await websocket.send(json.dumps({"type": "error", "message": "banned_from_room"}))
    return
```

**Mute enforcement in `_handle_send_room`** (insert after membership check):
```python
if self._store and self._store.is_room_muted(room_id, sender_webid):
    await websocket.send(json.dumps({
        "type": "error", "message": "you_are_muted",
    }))
    return
```

### 1d. `gateway.py` — command routing (5 new)

```python
elif cmd == "ban_member":    await self._handle_ban_member(websocket, data)
elif cmd == "unban_member":  await self._handle_unban_member(websocket, data)
elif cmd == "mute_member":   await self._handle_mute_member(websocket, data)
elif cmd == "unmute_member": await self._handle_unmute_member(websocket, data)
elif cmd == "get_room_bans": await self._handle_get_room_bans(websocket, data)
```

### 1e. Frontend — member action buttons (ban + mute alongside kick)

**In `web/main.js`** — the members modal list is built in the `case "room_members":`
handler around line 1611. The current `kickBtn` is rendered when `isOwner && !isSelf`.
Extend the same condition to also produce ban and mute buttons:

```javascript
const banBtn = isOwner && !isSelf
    ? `<button data-rm-action="ban" data-room-id="${event.room_id}" data-webid="${m.webid}"
               style="margin-left:4px;background:#451a03;border:none;color:#fed7aa;
                      padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;">
         Ban
       </button>` : "";
const muteBtn = isOwner && !isSelf
    ? `<button data-rm-action="mute" data-room-id="${event.room_id}" data-webid="${m.webid}"
               style="margin-left:4px;background:#1c1917;border:none;color:#a8a29e;
                      padding:3px 8px;border-radius:3px;cursor:pointer;font-size:0.75em;">
         Mute
       </button>` : "";
```

Add `${banBtn}${muteBtn}` alongside `${ownerBtn}${kickBtn}` in the member row.

Wire in the existing event delegation block at line 5242:
```javascript
else if (rmAction === 'ban') {
    showConfirm(`Ban ${webid.slice(-12)} from this room?`, () => {
        const reason = prompt("Reason (optional):") || "";
        socket.send(JSON.stringify({cmd:"ban_member", room_id:roomId, webid, reason}));
    });
} else if (rmAction === 'mute') {
    const dur = prompt("Mute duration: 5m / 1h / 24h / (blank = indefinite)") || "";
    const secs = dur==="5m" ? 300 : dur==="1h" ? 3600 : dur==="24h" ? 86400 : null;
    socket.send(JSON.stringify({
        cmd: "mute_member", room_id: roomId, webid,
        ...(secs ? {duration_seconds: secs} : {}),
    }));
}
```

**Ban list panel** — triggered by a "View bans" button visible to owner/admin.
Add button to the members modal footer area (near the "Close" button):

```html
<button id="room-bans-btn"
        style="display:none;background:#1c1917;border:1px solid #57534e;
               color:#a8a29e;padding:5px 12px;border-radius:4px;
               cursor:pointer;font-size:0.8em;">
  View ban list
</button>
```

Show it when `isOwner` by toggling `display`. Click → send
`{cmd:"get_room_bans", room_id}` and open a small overlay (`#room-bans-panel`):

```html
<!-- in index.html, near other panels -->
<div id="room-bans-panel"
     style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);
            z-index:1010;align-items:center;justify-content:center;">
  <div style="background:#1c1917;border-radius:8px;padding:20px;
              min-width:340px;max-width:480px;color:#f5f5f4;">
    <h3 style="margin:0 0 12px;">Banned members</h3>
    <div id="room-bans-list" style="min-height:40px;"></div>
    <button id="room-bans-close"
            style="margin-top:14px;background:#292524;border:none;
                   color:#f5f5f4;padding:6px 14px;border-radius:4px;cursor:pointer;">
      Close
    </button>
  </div>
</div>
```

Handle `room_bans` event: render each ban entry (name, reason, date, unban button).
Unban button sends `{cmd:"unban_member", room_id, webid}`.

**System messages in feed** for moderation events:

```javascript
case "member_banned":
    _appendSystemMsg(`${escHtml(event.display_name || event.webid.slice(-12))} was banned` +
                     (event.reason ? ` (${escHtml(event.reason)})` : ""));
    if (_membersRoomId === event.room_id) showRoomMembers(event.room_id);
    break;
case "member_unbanned":
    _appendSystemMsg(`${escHtml(event.webid.slice(-12))} was unbanned`);
    break;
case "member_muted":
    _appendSystemMsg(`${escHtml(event.webid.slice(-12))} was muted` +
                     (event.expires_at ? ` (until ${new Date(event.expires_at*1000).toLocaleTimeString()})` : ""));
    break;
case "member_unmuted":
    _appendSystemMsg(`${escHtml(event.webid.slice(-12))} was unmuted`);
    break;
```

`_appendSystemMsg(text)`:
```javascript
function _appendSystemMsg(text) {
    const feed = document.getElementById("message-feed");
    if (!feed) return;
    const el = document.createElement("div");
    el.className = "system-msg";
    el.textContent = text;
    feed.appendChild(el);
    feed.scrollTop = feed.scrollHeight;
}
```

**New tests:** `tests/test_room_bans.py` (5) + `tests/test_room_mutes.py` (4)

---

## T2 — "Seen by" UI with per-user opt-out

### The existing receipts toggle and its bug

`settings-receipts-toggle` already exists. It fires `set_receipts_enabled`
to the gateway, which stores the result in `self._receipts_enabled` — a
**single boolean shared across all users on this gateway** (`gateway.py:211`).
If Alice turns receipts off, Bob also stops sending receipts. This is wrong.

**Fix:** Make `_receipts_enabled` per-user.

In `gateway.py`, change:
```python
self._receipts_enabled: bool = True
```
to:
```python
self._client_receipts_prefs: dict = {}  # webid → bool, default True
```

In `process_command`:
```python
elif cmd == "set_receipts_enabled":
    _caller = self._client_webids.get(websocket, "")
    if _caller:
        self._client_receipts_prefs[_caller] = bool(data.get("enabled", True))
```

In `_gateway_rooms.py` `_handle_mark_read`, replace the `self._receipts_enabled`
check with:
```python
if self._client_receipts_prefs.get(reader_webid, True) and peer_gw and ...:
```

Also: when broadcasting `read_receipt` to room members (line 1271), skip members
who have opted out:
```python
for ws in list(self._local_rooms[thread_id].get("members", set())):
    _ws_webid = self._client_webids.get(ws, "")
    if ws is not websocket and self._client_receipts_prefs.get(_ws_webid, True):
        try:
            await ws.send(receipt_payload)
```
This way members who opted out don't receive others' read notifications either —
the setting is truly bilateral: you neither announce nor observe.

### 2b. `LocalStore` — one new method

```python
def get_message_readers(self, message_id: str) -> list[dict]:
    """Return all receivers who have read this message."""
    # SELECT receiver_webid, read_at FROM message_receipts
    # WHERE message_id = ? AND read_at IS NOT NULL
    # ORDER BY read_at
```

### 2c. `_gateway_rooms.py` — expose readers on demand

New handler `_handle_get_message_readers(websocket, data)`:
1. Caller must be a member of the room containing `message_id`.
2. `readers = self._store.get_message_readers(message_id)`.
3. Enrich: add `display_name` from `_store.get_display_name`.
4. Send: `{"type": "message_readers", "message_id": ..., "readers": [...]}`

Also: when `_handle_mark_read` fires and receipts are enabled, insert into
`message_receipts`:
```python
if reader_webid and message_id_read and self._store:
    from datetime import datetime, timezone
    self._store.save_message_receipt(message_id_read, reader_webid,
                                     datetime.now(timezone.utc).isoformat())
```

Add `save_message_receipt(message_id, receiver_webid, read_at)` to `LocalStore`:
```python
def save_message_receipt(self, message_id, receiver_webid, read_at):
    # INSERT OR REPLACE INTO message_receipts
    # (message_id, receiver_webid, delivered_at, read_at) VALUES (?,?,?,?)
    # ON CONFLICT DO UPDATE SET read_at = excluded.read_at
```

Route in `gateway.py`:
```python
elif cmd == "get_message_readers":
    await self._handle_get_message_readers(websocket, data)
```

### 2d. Frontend — lazy "seen by" indicator

**`web/main.js`** — in `renderMessage()`, add after the message bubble:

```javascript
const isRoom = msg.source === "local_room" || msg.source === "relay";
const receiptsOn = localStorage.getItem("proxion_receipts_enabled") !== "0";
const seenByHtml = isRoom && receiptsOn
    ? `<div class="seen-by-row" data-msg-id="${msg.message_id}"
            style="font-size:0.72em;color:#475569;margin-top:2px;
                   min-height:14px;"></div>`
    : "";
```

**Lazy fetch on scroll/visibility:** Add to the existing scroll handler in
`_gateway_rooms.py` (or as a `requestIdleCallback` after render):

```javascript
// After renderMessages(), request readers for the last N visible messages
function _fetchSeenByForVisible() {
    if (!activeView || activeView.type !== 'local_room') return;
    if (localStorage.getItem("proxion_receipts_enabled") === "0") return;
    document.querySelectorAll('.seen-by-row[data-msg-id]').forEach(el => {
        if (el.dataset.fetched) return;
        el.dataset.fetched = "1";
        socket?.send(JSON.stringify({
            cmd: "get_message_readers",
            room_id: activeView.id,
            message_id: el.dataset.msgId,
        }));
    });
}
```

Call `_fetchSeenByForVisible()` after `renderMessages()` and after the
scroll-up-to-load-more handler fires.

**Handle response:**
```javascript
case "message_readers": {
    const el = document.querySelector(
        `.seen-by-row[data-msg-id="${event.message_id}"]`);
    if (!el) break;
    const readers = (event.readers || []).filter(
        r => r.receiver_webid !== selfWebId);
    if (readers.length === 0) break;
    const names = readers.slice(0, 3)
        .map(r => escHtml(r.display_name || r.receiver_webid.slice(-8)))
        .join(", ");
    el.textContent = readers.length <= 3
        ? `Seen by ${names}`
        : `Seen by ${names} +${readers.length - 3} more`;
    break;
}
```

**Settings toggle behaviour (already wired, clarified):**
The existing `settings-receipts-toggle` already saves `proxion_receipts_enabled`
and sends `set_receipts_enabled`. With T2's fix, turning it off now:
1. Stops the user's read events from being stored/broadcast (server-side opt-out)
2. Skips fetching/rendering "seen by" rows (client-side opt-out)

No UI change to the settings toggle needed — it already exists and says
"Read receipts". The description text just needs to be updated to clarify both effects.

**New tests:** `tests/test_seen_by.py` (4 tests)
- `get_message_readers` returns correct readers from `message_receipts`
- `_handle_get_message_readers` sends `message_readers` event
- `set_receipts_enabled false` → user's read events NOT stored
- Room receipt broadcast skips opted-out members

---

## T3 — Device panel in settings (frontend only)

### No backend change needed

`list_devices` / `_handle_list_devices` / `unregister_device` are complete.

### `web/index.html` — new section in settings modal

Add after the Identity Backup section (before the closing `</div>`):

```html
<hr style="border-color:#334155;margin:16px 0 12px;">
<p style="margin:0 0 8px;font-size:0.8em;color:#94a3b8;
          text-transform:uppercase;letter-spacing:.05em;font-weight:600;">
  Linked Devices
</p>
<div id="settings-devices-list"
     style="font-size:0.85em;color:#94a3b8;min-height:24px;">
</div>
```

### `web/main.js` — request + render

In the `settings-btn` onclick handler, add alongside the existing `list_sessions` send:
```javascript
socket.send(JSON.stringify({cmd: "list_devices"}));
```

Handle `case "devices":`:
```javascript
case "devices": {
    const container = document.getElementById("settings-devices-list");
    if (!container) break;
    const devs = event.devices || [];
    if (devs.length === 0) {
        container.innerHTML = '<span style="color:#475569;">No linked devices.</span>';
        break;
    }
    container.innerHTML = devs.map(d => {
        const label = escHtml(d.display_name || d.device_id.slice(0, 16));
        const since = d.registered_at
            ? new Date(d.registered_at * 1000).toLocaleDateString() : "";
        const revokeBtn =
            `<button data-device-id="${escHtml(d.device_id)}"
                     style="background:transparent;border:none;color:#f87171;
                            font-size:0.8em;cursor:pointer;padding:2px 6px;">
               Revoke
             </button>`;
        return `<div style="display:flex;align-items:center;justify-content:space-between;
                            padding:4px 0;border-bottom:1px solid #1e293b;gap:8px;">
            <span style="flex:1">${label}<br>
              <span style="color:#475569;font-size:0.8em;">${since}</span>
            </span>
            ${revokeBtn}
        </div>`;
    }).join("");
    container.querySelectorAll("[data-device-id]").forEach(btn => {
        btn.addEventListener("click", () => {
            const id = btn.dataset.deviceId;
            showConfirm(`Revoke device "${id.slice(0, 16)}"?`, () => {
                socket.send(JSON.stringify({cmd: "unregister_device", device_id: id}));
                btn.closest("div").remove();
            });
        });
    });
    break;
}
```

**New test:** `tests/test_device_panel.py` (1 test)
- `_handle_list_devices` returns devices without `attestation_b64`

---

## Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_room_bans.py` | 5 | T1 bans |
| `tests/test_room_mutes.py` | 4 | T1 mutes |
| `tests/test_seen_by.py` | 4 | T2 |
| `tests/test_device_panel.py` | 1 | T3 |
| **Total new** | **14** | |

---

## Out of scope for R32

- Ban federation (relaying bans to federated gateways) — separate round
- "Seen by" in DMs — different receipt flow, separate round
- Large file chunking, voice federation — separate rounds
