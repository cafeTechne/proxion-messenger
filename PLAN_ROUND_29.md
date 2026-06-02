# PLAN_ROUND_29: Federated Group Rooms + Cross-Gateway Ephemeral Events

Three gaps confirmed against live codebase. All other suspected gaps were
found closed (search UI wired, blocklist done, delivery ACKs done, relay
queue persisted + retried on startup, avatar support complete).

Confirmed gaps:

1. **Local rooms are gateway-local** — `_handle_send_room` line 450 in
   `_gateway_rooms.py` broadcasts only to `room["members"]` (local WebSocket
   connections). Members on other gateways never receive room messages.

2. **Presence is gateway-local** — `_handle_set_presence` line 24 in
   `_gateway_misc.py` updates `_user_presence` and writes to pod but does
   not notify federated peers. Cross-gateway DM peers see each other as
   perpetually offline.

3. **Typing indicators are gateway-local** — `_handle_typing` line 1778
   in `_gateway_rooms.py` delivers to `peer_ws = self._any_socket(...)`.
   If the peer is on another gateway, `_any_socket` returns None and the
   indicator is silently dropped.

---

## T1 — Federated room members + cross-gateway room relay

### The problem

When Bob (on gateway-B) joins Alice's local room (on gateway-A) using an
invite code, he connects to gateway-A's WebSocket directly. The `join_room`
handler adds his WebSocket to `room["members"]` (line 1398) and his webid
to `_store.add_room_member` (line 1412). This works while his WebSocket is
connected, but:

- Bob's gateway-B never learns the room exists
- If Bob disconnects from gateway-A and reconnects to his own gateway-B,
  he loses access to the room entirely
- When Alice sends a message, it only reaches currently-connected local sockets

**Desired behaviour:** A room can have *federated members* — users whose
home gateway has registered interest in that room. When a message is sent,
the room host gateway relays it to all registered foreign gateways.

### Schema change

Add table `room_federated_members` to `local_store.py`:

```sql
CREATE TABLE IF NOT EXISTS room_federated_members (
    room_id      TEXT NOT NULL,
    member_did   TEXT NOT NULL,
    gateway_url  TEXT NOT NULL,
    joined_at    REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
    PRIMARY KEY (room_id, member_did)
);
```

Methods on `LocalStore`:

```python
def add_federated_room_member(self, room_id: str, member_did: str, gateway_url: str) -> None
def remove_federated_room_member(self, room_id: str, member_did: str) -> None
def get_federated_room_members(self, room_id: str) -> list[dict]
    # returns [{"member_did": ..., "gateway_url": ...}, ...]
```

### Gateway join flow (new `announce_room_join` command)

When Bob on gateway-B wants to stay connected to Alice's room through his
own gateway, his frontend sends:

```json
{"cmd": "announce_room_join", "room_id": "...", "code": "...",
 "home_gateway": "https://bob.example.com"}
```

New handler `_handle_announce_room_join(websocket, data)` in
`_gateway_misc.py`:

1. Validates `code` against the room's stored code (same check as
   `_handle_join_room`)
2. Gets caller's webid + asserted `home_gateway` from data
3. If `home_gateway` differs from this gateway's own URL:
   - Calls `self._store.add_federated_room_member(room_id, caller_webid, home_gateway)`
   - Sends confirmation to caller: `{"type": "federated_room_joined", "room_id": ..., "gateway": home_gateway}`
4. If same gateway: treat as normal join (no-op federation path)

### Broadcast changes in `_handle_send_room`

After line 455 (the local `for ws in room["members"]` loop), add:

```python
# Relay to federated (cross-gateway) members
if self._store:
    _fed_members = self._store.get_federated_room_members(room_id)
    _relayed_gateways: set[str] = set()
    for _fm in _fed_members:
        _gw = _fm["gateway_url"]
        if _gw in _relayed_gateways:
            continue
        _relayed_gateways.add(_gw)
        asyncio.create_task(self._relay_room_message(_gw, room_id, event))
```

New method `_relay_room_message(gateway_url, room_id, event)` in
`_gateway_pod.py` (PodSyncMixin):

```python
async def _relay_room_message(self, gateway_url: str, room_id: str, event: dict) -> None:
    from .relay import sign_relay_message, post_relay
    from .didkey import pub_key_to_did
    import secrets as _sec
    gw_did = pub_key_to_did(self.agent.identity_pub_bytes)
    relay_nonce = _sec.token_hex(8)
    ts = event.get("timestamp", datetime.now(timezone.utc).isoformat())
    sig = sign_relay_message(
        self.agent.identity_key, gw_did, room_id,
        event["message_id"], event.get("content", ""), ts, relay_nonce,
    )
    payload = {
        **event,
        "content_type": "room_message",
        "room_id": room_id,
        "relay_nonce": relay_nonce,
        "signature": sig,
        "origin_gateway_url": self._gateway_http_url(),
    }
    http_base = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
    await post_relay(http_base.rstrip("/") + "/relay", payload)
```

### Receive side — new `/relay` content_type handler

In `gateway.py` `_handle_relay_post`, after the `voice_signal` branch:

```python
if data.get("content_type") == "room_message":
    return await self._handle_room_relay(data)
```

New method `_handle_room_relay(data)`:

1. Validate `room_id`, `message_id`, `from_webid`, `signature`
2. Verify signature (same pattern as regular relay)
3. Find local members of `room_id` in `_local_rooms`
4. Deliver `event` to each connected member socket
5. Store message if `_store` available
6. Return `"200 OK"`, `'{"status":"delivered"}'`

Add new relay fields to `_ALLOWED_RELAY_KEYS`:

```python
"room_id", "room_name", "content_type",
```

(`content_type` is already there; add `room_id` and `room_name`.)

### Frontend — `announce_room_join` command

In `web/main.js`, when a user joins a room (inside `joinRoom()` or after
`room_joined` event), if the gateway URL in localStorage differs from the
room's origin gateway, send:

```javascript
socket.send(JSON.stringify({
    cmd: "announce_room_join",
    room_id: event.room_id,
    code: event.code,
    home_gateway: localStorage.getItem("proxion_gateway_http_url") || "",
}));
```

Handle `federated_room_joined` event: show a toast "Room joined via
federation — messages will sync to your gateway".

Handle `room_relay` messages from `/relay`: when `event.type === "room_message"`,
deliver to the active room view the same as a local room message.

**New tests:** `tests/test_federated_rooms.py` (5 tests)

---

## T2 — Cross-gateway presence relay

### The problem

`_handle_set_presence` (line 24, `_gateway_misc.py`) updates `_user_presence`
locally and optionally writes to the pod, but never notifies federated peers.
Users on different gateways appear perpetually offline to each other.

### Fix

After the pod write (line 61 `_gateway_misc.py`), relay presence to all
known peers on other gateways:

```python
# Relay presence to all known federated peers
if webid and self._peer_gateway_urls:
    _presence_payload = {
        "content_type": "presence",
        "from_webid": webid,
        "status": status,
        "status_message": status_message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    for _peer_did, _peer_gw in list(self._peer_gateway_urls.items()):
        asyncio.create_task(self._relay_ephemeral(_peer_gw, _presence_payload))
```

New method `_relay_ephemeral(gateway_url, payload)` in `_gateway_pod.py`:

```python
async def _relay_ephemeral(self, gateway_url: str, payload: dict) -> None:
    """POST a lightweight ephemeral event (presence/typing) to a peer gateway.
    No signature verification required — payloads are advisory only."""
    from .relay import post_relay
    http_base = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
    try:
        await post_relay(http_base.rstrip("/") + "/relay", payload, timeout=3.0)
    except Exception:
        pass  # ephemeral — fire-and-forget
```

### Receive side

In `_handle_relay_post`, add before the voice_signal branch:

```python
if data.get("content_type") == "presence":
    return await self._handle_presence_relay(data)
```

New method `_handle_presence_relay(data)`:

1. Extract `from_webid`, `status`, `status_message`, `updated_at`
2. Validate `status` ∈ `{"online", "away", "busy", "offline"}`
3. Update `self._user_presence[from_webid]` with the incoming values
4. Broadcast `{"type": "presence", ...}` to all local clients who have
   this webid in their contact list (use `_user_presence` broadcast pattern)
5. Return `"200 OK"`, `'{"status":"ok"}'`

Add `"status"`, `"status_message"`, `"updated_at"` to `_ALLOWED_RELAY_KEYS`
(they may already exist — if not, add them).

### Frontend

No change needed — `presence` events from the WebSocket already update
the UI via the existing `case "presence"` handler.

**New tests:** `tests/test_presence_relay.py` (3 tests)

---

## T3 — Cross-gateway typing indicators

### The problem

`_handle_typing` (line 1778, `_gateway_rooms.py`) for DMs:
if `peer_ws = self._any_socket(threads[0]["peer_webid"])` returns None
(peer on another gateway), the typing event is dropped silently.

### Fix

In `_handle_typing` after the local socket attempt, add relay fallback:

```python
elif cert_id:
    if self._store:
        threads = [t for t in self._store.get_dm_threads() if t["thread_id"] == cert_id]
        if threads:
            peer_webid = threads[0]["peer_webid"]
            peer_ws = self._any_socket(peer_webid)
            if peer_ws and peer_ws != websocket:
                await peer_ws.send(json.dumps(typing_event))
            elif peer_webid:
                # Peer on different gateway — relay ephemeral typing event
                peer_gw = self._resolve_peer_gateway(peer_webid)
                if peer_gw:
                    asyncio.create_task(self._relay_ephemeral(peer_gw, {
                        "content_type": "typing",
                        "from_webid": typing_event["from_webid"],
                        "cert_id": cert_id,
                    }))
```

### Receive side

In `_handle_relay_post`:

```python
if data.get("content_type") == "typing":
    return await self._handle_typing_relay(data)
```

New method `_handle_typing_relay(data)`:

1. Extract `from_webid`, `cert_id`
2. Find local sockets for the DM thread peer (the local user in this DM)
3. Deliver `{"type": "typing", "from_webid": ..., "cert_id": ...}` to each socket
4. Return `"200 OK"`, `'{"status":"ok"}'`

Also add `"typing"`, `"presence"`, `"room_message"` to `_ALLOWED_RELAY_KEYS`
`content_type` values. (The key `content_type` already exists; no new top-level
keys are needed for ephemeral events beyond what's in the payload dict.)

**New tests:** `tests/test_typing_relay.py` (3 tests)

---

## T4 — Contact profile panel

### The problem

Clicking on a peer's name/avatar shows a brief hover card but no permanent
profile panel. There is no way to view a contact's full DID, gateway URL,
federation fingerprint, or last-seen time without opening settings.

### Fix — server side

New `GET /profile/{did}` endpoint in `_serve_http` (gateway.py):

1. Parse `{did}` from path
2. If `did == own_did`: return own profile from `_build_discovery_data()` + `_user_presence`
3. If known contact: load from `_store.get_display_name`, `_store.get_x25519_pub`,
   `_user_presence`, `_store.get_relationship_by_did`
4. Return JSON:

```json
{
  "did": "did:key:z...",
  "display_name": "Alice",
  "fingerprint": "abc123...",
  "gateway_url": "https://alice.example.com",
  "x25519_pub": "...",
  "status": "online",
  "status_message": "Working from home",
  "last_active_at": "2026-05-24T12:00:00Z",
  "nat_warning": false
}
```

### Fix — frontend

New `showContactProfile(webid)` function in `web/main.js`:

1. Fetches `/profile/{webid}` (falls back to cached data if offline)
2. Creates/shows a slide-in panel (`#contact-profile-panel`) on the right
   side of the screen (same pattern as `#pin-panel`)
3. Panel contents:
   - Avatar (from existing avatar cache or initials fallback)
   - Display name + DID (truncated with copy button)
   - Gateway URL with federation status indicator (✓/✗ relay_capable)
   - Security fingerprint (with tooltip: "Verify this fingerprint with your
     contact out-of-band to confirm identity")
   - Presence status + status message
   - Last active time
   - "Start DM" button (if not already in a DM)

New HTML in `web/index.html` — `#contact-profile-panel` (same style as
`#pin-panel`, `display:none`, `position:fixed`, `right:0`):

```html
<div id="contact-profile-panel" style="display:none; position:fixed; right:0; top:0; bottom:0; width:300px; ...">
  <button id="contact-profile-close">×</button>
  <div id="contact-profile-avatar" ...></div>
  <div id="contact-profile-name" ...></div>
  <div id="contact-profile-did" ...></div>
  <div id="contact-profile-gateway" ...></div>
  <div id="contact-profile-fingerprint" ...></div>
  <div id="contact-profile-status" ...></div>
  <button id="contact-profile-dm-btn">Send DM</button>
</div>
```

Wire `showContactProfile` to:
- Clicking a peer's name in the members panel
- Clicking a peer's avatar/name in the message feed (already partial — member
  click currently fires a hover card; upgrade to full panel)

**New tests:** `tests/test_profile_endpoint.py` (4 tests)
- Own profile returns correct DID and display_name
- Known contact profile returns stored data
- Unknown DID returns 404
- Fingerprint is present and correct format

---

## T5 — Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_federated_rooms.py` | 5 | T1 |
| `tests/test_presence_relay.py` | 3 | T2 |
| `tests/test_typing_relay.py` | 3 | T3 |
| `tests/test_profile_endpoint.py` | 4 | T4 |
| **Total** | **15** | |

---

## Out of scope for R29

- Large file chunking (>512 KB) — requires a multi-part upload protocol with
  chunk reassembly and DB tracking; separate round
- Room history sync on join — new members on a different gateway need
  catch-up messages; protocol design needed
- did:web identity — full DID resolver; separate round
- Read receipts over relay — acceptable current impl (HTTP-level ACK)
