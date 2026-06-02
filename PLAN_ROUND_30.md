# PLAN_ROUND_30: Federated Room Completeness

Four confirmed hard gaps found by auditing the R29 implementation.
Closed items confirmed: invite expiry (max_uses + TTL), multi-device relay drain,
webhook delivery on room_relay, scheduled messages via room relay.

---

## Summary of gaps

| # | Gap | File:line | Complexity |
|---|-----|-----------|------------|
| T1 | Federated member visibility | `_gateway_misc.py` `_handle_announce_room_join` | S |
| T2 | Federated reactions | `_gateway_rooms.py:1024` | S |
| T3 | Federated edit/delete | `_gateway_rooms.py:668,718` | M |
| T4 | Push notifications for room relay | `gateway.py:3644` `_handle_room_relay` | M |

---

## T1 — Federated member visibility in room member list

**Gap:** `_handle_announce_room_join` (added in R29, `_gateway_misc.py`) records
the caller's DID + home gateway in `room_federated_members` but never broadcasts
a `room_member_joined` event to the room's currently connected local members.
Locally connected users cannot see that a remote peer has joined.

**Fix:** In `_handle_announce_room_join`, after calling
`self._store.add_federated_room_member(room_id, caller_webid, home_gateway)`,
broadcast to all sockets in `self._local_rooms[room_id]["members"]`:

```python
join_event = json.dumps({
    "type": "room_member_joined",
    "room_id": room_id,
    "webid": caller_webid,
    "display_name": self._name_for(websocket, caller_webid),
    "federated": True,
    "gateway": home_gateway,
})
for _ws in list(self._local_rooms.get(room_id, {}).get("members", set())):
    try:
        await _ws.send(join_event)
    except Exception:
        pass
```

Also add `get_federated_room_members` to the `get_room_members` response. In
`_gateway_rooms.py` `_handle_get_room_members` (or equivalent list-members
path), merge federated members from `_store.get_federated_room_members(room_id)`
into the returned list, tagged with `{"federated": true, "gateway": "..."}`.

Find where room member lists are built and sent to the frontend — search for
`get_room_members` or `members_list` event. The frontend already handles
`room_member_joined` events (it updates the members panel) so no frontend change
is needed for the join notification. The member list merge may need a small
frontend adjustment to render the `federated: true` badge.

**Frontend:** In `web/main.js`, when rendering room members, if `m.federated`
is true, append a small indicator (e.g. `"🔗"` or `"(remote)"`) after the name.
Find the members panel render code (search for `members-panel` or `member-list`).

**New tests:** `tests/test_federated_member_visibility.py` (3 tests)
- `announce_room_join` broadcasts `room_member_joined` to local members
- `room_member_joined` carries `federated: true` and correct gateway URL
- Federated members appear in `get_federated_room_members` result

---

## T2 — Federated reactions relay

**Gap:** `_handle_add_reaction` (line 1024, `_gateway_rooms.py`) — when a
local room reaction is added, the broadcast loop at lines 1024–1028 only
iterates `room["members"]` (local WebSocket connections). Federated member
gateways never receive `reaction_added` events.

`_handle_remove_reaction` has the same gap (find it at `_gateway_rooms.py`
below line 1069).

**Fix:** After the local broadcast loop in `_handle_add_reaction` (line 1028),
add the same federated relay pattern used by `_handle_send_room`:

```python
            # Relay reaction to federated member gateways
            if self._store:
                _fed_members = self._store.get_federated_room_members(room_id)
                _relayed_gws: set = set()
                for _fm in _fed_members:
                    _gw = _fm["gateway_url"]
                    if _gw not in _relayed_gws:
                        _relayed_gws.add(_gw)
                        asyncio.create_task(self._relay_ephemeral(_gw, {
                            "content_type": "room_reaction",
                            "room_id": room_id,
                            "message_id": message_id,
                            "emoji": emoji,
                            "from_webid": sender_webid,
                            "action": "add",
                        }))
```

Apply the same change to `_handle_remove_reaction` with `"action": "remove"`.

**Receive side:** In `gateway.py` `_handle_relay_post`, add before the
sealed-DM branch:

```python
        if data.get("content_type") == "room_reaction":
            return await self._handle_room_reaction_relay(data)
```

New method `_handle_room_reaction_relay(data)` in `gateway.py`:

```python
async def _handle_room_reaction_relay(self, data: dict) -> tuple[str, str]:
    room_id = data.get("room_id", "")
    message_id = data.get("message_id", "")
    emoji = data.get("emoji", "")
    from_webid = data.get("from_webid", "")
    action = data.get("action", "add")
    if not all([room_id, message_id, emoji, from_webid]):
        return "400 Bad Request", '{"error":"missing_reaction_fields"}'
    room = self._local_rooms.get(room_id)
    if not room:
        return "404 Not Found", '{"error":"room_not_found"}'
    event_type = "reaction_added" if action == "add" else "reaction_removed"
    if self._store and action == "add":
        self._store.save_reaction(room_id, message_id, emoji, from_webid)
    elif self._store and action == "remove":
        self._store.delete_reaction(room_id, message_id, emoji, from_webid)
    event = json.dumps({
        "type": event_type,
        "thread_id": room_id,
        "message_id": message_id,
        "emoji": emoji,
        "from_webid": from_webid,
    })
    for ws in list(room.get("members", set())):
        try:
            await ws.send(event)
        except Exception:
            pass
    return "200 OK", '{"status":"ok"}'
```

Add `"room_reaction"` as a valid `content_type` value in `_ALLOWED_RELAY_KEYS`
comment (the key `content_type` is already allowed; no new top-level keys needed).

**New tests:** `tests/test_federated_reactions.py` (4 tests)
- `_handle_add_reaction` creates relay tasks for federated gateways
- `_handle_remove_reaction` creates relay tasks for federated gateways
- `_handle_room_reaction_relay` delivers `reaction_added` to local members
- `_handle_room_reaction_relay` delivers `reaction_removed` to local members

---

## T3 — Federated edit and delete relay

**Gap (delete):** `_handle_delete_local_message` (line 648, `_gateway_rooms.py`)
— after broadcasting `message_deleted` to `_local_rooms[thread_id]["members"]`
(line 668–672), no relay is sent to federated gateways.

**Gap (edit):** `_handle_edit_local_message` (line 685) — after broadcasting
`message_edited` to local members (line 718–722), no relay to federated gateways.

**Fix (delete):** In `_handle_delete_local_message`, after the local broadcast
loop (line 672), add:

```python
        # Relay delete to federated member gateways
        if thread_id in self._local_rooms and self._store:
            _fed = self._store.get_federated_room_members(thread_id)
            _seen: set = set()
            for _fm in _fed:
                _gw = _fm["gateway_url"]
                if _gw not in _seen:
                    _seen.add(_gw)
                    asyncio.create_task(self._relay_ephemeral(_gw, {
                        "content_type": "room_delete",
                        "room_id": thread_id,
                        "message_id": message_id,
                        "from_webid": caller_webid or "",
                    }))
```

**Fix (edit):** In `_handle_edit_local_message`, after the local broadcast
loop (line 722), add:

```python
        # Relay edit to federated member gateways
        if thread_id in self._local_rooms and self._store:
            _fed_edit = self._store.get_federated_room_members(thread_id)
            _seen_edit: set = set()
            for _fm in _fed_edit:
                _gw = _fm["gateway_url"]
                if _gw not in _seen_edit:
                    _seen_edit.add(_gw)
                    asyncio.create_task(self._relay_ephemeral(_gw, {
                        "content_type": "room_edit",
                        "room_id": thread_id,
                        "message_id": message_id,
                        "new_content": new_content,
                        "edited_at": edited_at,
                        "from_webid": caller_webid_edit or "",
                    }))
```

**Receive side:** In `gateway.py` `_handle_relay_post`, add:

```python
        if data.get("content_type") == "room_edit":
            return await self._handle_room_edit_relay(data)
        if data.get("content_type") == "room_delete":
            return await self._handle_room_delete_relay(data)
```

New handler `_handle_room_edit_relay(data)` in `gateway.py`:

```python
async def _handle_room_edit_relay(self, data: dict) -> tuple[str, str]:
    room_id = data.get("room_id", "")
    message_id = data.get("message_id", "")
    new_content = data.get("new_content", "")
    edited_at = data.get("edited_at", "")
    from_webid = data.get("from_webid", "")
    if not all([room_id, message_id, new_content]):
        return "400 Bad Request", '{"error":"missing_edit_fields"}'
    room = self._local_rooms.get(room_id)
    if not room:
        return "404 Not Found", '{"error":"room_not_found"}'
    if self._store:
        self._store.update_message(message_id, new_content, edited_at,
                                   editor_webid=from_webid)
    event = json.dumps({
        "type": "message_edited",
        "message_id": message_id,
        "thread_id": room_id,
        "new_content": new_content,
        "edited_at": edited_at,
        "has_history": True,
    })
    for ws in list(room.get("members", set())):
        try:
            await ws.send(event)
        except Exception:
            pass
    return "200 OK", '{"status":"ok"}'
```

New handler `_handle_room_delete_relay(data)` in `gateway.py`:

```python
async def _handle_room_delete_relay(self, data: dict) -> tuple[str, str]:
    room_id = data.get("room_id", "")
    message_id = data.get("message_id", "")
    if not room_id or not message_id:
        return "400 Bad Request", '{"error":"missing_delete_fields"}'
    room = self._local_rooms.get(room_id)
    if not room:
        return "404 Not Found", '{"error":"room_not_found"}'
    if self._store:
        self._store.delete_message(message_id)
    event = json.dumps({
        "type": "message_deleted",
        "message_id": message_id,
        "thread_id": room_id,
    })
    for ws in list(room.get("members", set())):
        try:
            await ws.send(event)
        except Exception:
            pass
    return "200 OK", '{"status":"ok"}'
```

**New tests:** `tests/test_federated_edit_delete.py` (5 tests)
- Delete relays `room_delete` to federated gateways
- Edit relays `room_edit` to federated gateways
- `_handle_room_delete_relay` delivers `message_deleted` to local members
- `_handle_room_edit_relay` delivers `message_edited` to local members
- `_handle_room_edit_relay` updates the message in the store

---

## T4 — Push notifications for relayed room messages

**Gap:** `_handle_room_relay` (line 3615, `gateway.py`) delivers room relay
messages to connected sockets but when `delivered == False` (no sockets
connected for any room member), it silently returns `202`. WebPush is never
triggered. Compare to `_gateway_dm.py` lines 531–556 which fire WebPush when
the DM target is offline — that same pattern is missing for room messages.

**Fix:** In `_handle_room_relay`, after the delivery loop and the
`self._store.save_message(...)` call, add a push notification attempt for
each room member who has no connected socket:

```python
    # WebPush for members with no active socket
    _vpk  = getattr(self, "_vapid_private_pem", None)
    _vsub = getattr(self, "_vapid_subject", None)
    if self._store and _vpk and _vsub:
        from .webpush import send_web_push
        _all_member_dids = self._store.get_room_members(room_id) or []
        for _mid in _all_member_dids:
            if _mid == from_webid:
                continue
            if self._any_socket(_mid):
                continue  # already delivered via WebSocket
            _subs = self._store.get_push_subscriptions(_mid)
            for _sub in (_subs or []):
                try:
                    send_web_push(
                        subscription={
                            "endpoint": _sub["endpoint"],
                            "keys": {
                                "p256dh": _sub["p256dh_b64"],
                                "auth":   _sub["auth_b64"],
                            },
                        },
                        payload={
                            "type": "message",
                            "thread_id": room_id,
                            "display_name": display_name,
                            "room_name": room.get("name", ""),
                        },
                        vapid_private_pem=_vpk,
                        vapid_subject=_vsub,
                    )
                except Exception:
                    pass
```

`send_web_push` is synchronous (calls the push endpoint over HTTP directly).
Wrap in `asyncio.get_event_loop().run_in_executor(None, ...)` if it becomes
a bottleneck, but for now match the DM pattern (sync, called from async context).

Same fix applies to `_handle_relay_post` for regular DM relay (the offline
push path in `_gateway_dm.py` already covers the local-DM case, but when a
DM arrives via relay, `_handle_relay_post` delivers it at line 3607 without
triggering push). Add the same push block to the DM relay delivery section
(after line 3613, in the `_handle_relay_post` main delivery path, when
`delivered == False`).

Locate the relevant section in `_handle_relay_post` near line 3605:

```python
        if target_sockets:
            # ... deliver to sockets ...
            if delivered_any:
                # ... store message ...
                return "200 OK", ...
        # target not online — check push subscriptions
        if self._store and self._vapid_private_pem and self._vapid_subject:
            from .webpush import send_web_push
            _subs = self._store.get_push_subscriptions(to_webid)
            for _sub in (_subs or []):
                try:
                    send_web_push(
                        subscription={...},
                        payload={"type": "message", "thread_id": cert_id or from_webid,
                                 "display_name": display_name},
                        vapid_private_pem=self._vapid_private_pem,
                        vapid_subject=self._vapid_subject,
                    )
                except Exception:
                    pass
        # ... existing relay queue logic ...
```

**New tests:** `tests/test_push_relay.py` (4 tests)
- `_handle_room_relay` calls `send_web_push` for offline room member
- `_handle_room_relay` skips push for members with active socket
- `_handle_room_relay` skips push when VAPID not configured
- DM relay path fires push when `to_webid` is offline and has subscription

---

## T5 — Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_federated_member_visibility.py` | 3 | T1 |
| `tests/test_federated_reactions.py` | 4 | T2 |
| `tests/test_federated_edit_delete.py` | 5 | T3 |
| `tests/test_push_relay.py` | 4 | T4 |
| **Total** | **16** | |

---

## Out of scope for R30

- Voice channels in federated rooms — requires coordinating ICE/SDP signaling
  across gateways for each peer pair; significant WebRTC protocol design
- Room history sync on join — requires a request/response protocol for catch-up;
  separate round
- HTTP-only room join (no WebSocket) — L complexity; separate round
- Relay message batching (efficiency) — not user-visible; address when scale
  warrants it
