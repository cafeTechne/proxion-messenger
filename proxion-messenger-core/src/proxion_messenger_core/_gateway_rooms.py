"""RoomHandlerMixin — room management command handlers and helpers for ProxionGateway.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: _local_rooms, _room_codes, _store, _client_webids, _display_names,
                  _user_presence, _webid_sockets, _pending_ownership_transfers,
                  _room_disappear_timers, _rate_counters, _rate_lock, _session_meta,
                  _voice_sessions, _scheduled_messages, message_cache, room_memberships,
                  dm_clients, clients, agent, config, stash, outbox, blocklist,
                  read_state, identity_cache, broadcast(), broadcast_to_room(),
                  _any_socket(), _name_for(), _strip_thread_prefix(), _pod_client(),
                  _sync_room_message_to_pod(), _init_room_on_pod(),
                  _edit_room_message_on_pod(), _delete_room_message_on_pod(),
                  _fire_outgoing_webhook(), process_command(), _ws_public_url().
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from .room import RoomMembership

logger = logging.getLogger("proxion_messenger_core.gateway")


class RoomHandlerMixin:

    async def _init_room_on_pod(
        self,
        room_id: str,
        name: str,
        creator_webid: str,
        code: str = "",
        history_mode: str = "none",
    ) -> None:
        """Write room.json to the pod, establishing the room container."""
        client = self._pod_client()
        if not client:
            return
        async with self._pod_sync_sem:
            try:
                from .pod_room_store import PodRoomStore
                store = PodRoomStore(client)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, store.ensure_room_container, room_id, creator_webid, []
                )
                meta = {
                    "room_id": room_id,
                    "name": name,
                    "code": code,
                    "history_mode": history_mode,
                    "creator_webid": creator_webid,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                await loop.run_in_executor(None, store.write_room_meta, room_id, meta)
                logger.info(f"Initialized room {room_id} on pod")
            except Exception as exc:
                logger.debug(f"_init_room_on_pod failed [{room_id}]: {exc}")

    async def _grant_pod_room_read(self, room_id: str, joiner_webid: str) -> None:
        """Update the room container ACL to include *joiner_webid* as a Read member."""
        client = self._pod_client()
        if not client:
            return
        room_info = self._local_rooms.get(room_id, {})
        creator_webid = room_info.get("creator_webid", "")
        if not creator_webid:
            return
        member_webids = []
        if self._store:
            member_webids = [w for w in self._store.get_room_members(room_id) if w != creator_webid]
        else:
            member_webids = [self._client_webids.get(ws, "") for ws in room_info.get("members", set())]
            member_webids = [w for w in member_webids if w and w != creator_webid]
        async with self._pod_sync_sem:
            try:
                from .pod_room_store import PodRoomStore
                store = PodRoomStore(client)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, store.ensure_room_container, room_id, creator_webid, member_webids
                )
            except Exception as exc:
                logger.debug("_grant_pod_room_read failed [%s]: %s", room_id, exc)

    async def _sync_room_message_to_pod(self, room_id: str, message: dict) -> None:
        """Write-through a room message to the pod (fire-and-forget)."""
        client = self._pod_client()
        if not client:
            return
        async with self._pod_sync_sem:
            try:
                from .pod_room_store import PodRoomStore
                store = PodRoomStore(client)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, store.write_message, room_id, message)
            except Exception as exc:
                logger.debug(f"pod room write-through failed [{room_id}]: {exc}")

    async def _edit_room_message_on_pod(
        self, room_id: str, message_id: str, new_content: str, edited_at: str
    ) -> None:
        """Update a room message on the pod after a local edit."""
        client = self._pod_client()
        if not client:
            return
        try:
            from .pod_room_store import PodRoomStore
            store = PodRoomStore(client)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, store.update_message, room_id, message_id, new_content, edited_at
            )
        except Exception as exc:
            logger.debug(f"pod room edit failed [{room_id}/{message_id}]: {exc}")

    async def _delete_room_message_on_pod(self, room_id: str, message_id: str) -> None:
        """Remove a room message from the pod after a local delete."""
        client = self._pod_client()
        if not client:
            return
        try:
            from .pod_room_store import PodRoomStore
            store = PodRoomStore(client)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, store.delete_message, room_id, message_id)
        except Exception as exc:
            logger.debug(f"pod room delete failed [{room_id}/{message_id}]: {exc}")

    class _NullWs:
        """Stub websocket for offline scheduled message senders."""
        async def send(self, _): pass
        def __hash__(self): return id(self)
        def __eq__(self, other): return self is other

    def _scheduled_delivery_command(self, thread_id: str, actor: str, content: str):
        """Route a due scheduled message to the right send command for its thread
        type (room / cert-DM / local-DM). Returns None if the thread is unknown.

        Delivering everything as send_room silently dropped scheduled DMs, since a
        DM cert_id / local-DM thread id is not a room.
        """
        if thread_id in self._local_rooms:
            return {"cmd": "send_room", "room_id": thread_id, "content": content}
        if thread_id in self.dm_clients:
            return {"cmd": "send_dm", "cert_id": thread_id, "content": content}
        if self._store:
            for _t in self._store.get_dm_threads(owner_webid=actor):
                if _t.get("thread_id") == thread_id and _t.get("peer_webid"):
                    return {"cmd": "local_dm", "target_webid": _t["peer_webid"], "content": content}
        return None

    async def _scheduler_loop(self):
        """Poll for due scheduled messages every 10 seconds."""
        _consecutive_failures = 0
        while True:
            await asyncio.sleep(10)
            if not self._store:
                continue
            try:
                due = self._store.get_due_scheduled_messages(time.time())
                for sched in due:
                    _deliver = self._scheduled_delivery_command(
                        sched["thread_id"], sched["from_webid"], sched["content"])
                    if _deliver is None:
                        logger.warning("scheduled message %s: unknown thread %s — dropping",
                                       sched["id"], str(sched["thread_id"])[:24])
                        self._store.mark_scheduled_sent(sched["id"])
                        continue
                    # Mark sent only once we know how to deliver it (at-most-once).
                    self._store.mark_scheduled_sent(sched["id"])
                    sender_ws = next(
                        (ws for ws, wid in self._client_webids.items()
                         if wid == sched["from_webid"]),
                        None
                    )
                    null_ws = None
                    if sender_ws is None:
                        null_ws = self._NullWs()
                        self._client_webids[null_ws] = sched["from_webid"]
                        self._system_ws.add(null_ws)
                        sender_ws = null_ws
                    try:
                        await self.process_command(sender_ws, _deliver)
                    finally:
                        if null_ws is not None:
                            self._client_webids.pop(null_ws, None)
                            self._system_ws.discard(null_ws)
                _consecutive_failures = 0  # reset on success
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _consecutive_failures += 1
                logger.warning("_scheduler_loop error #%d: %s", _consecutive_failures, exc)
                if _consecutive_failures >= 10:
                    if self._store:
                        self._store.save_background_health_event("scheduler_loop", _consecutive_failures, str(exc)[:200])
                    await asyncio.sleep(60)
                else:
                    await asyncio.sleep(5)

    async def _expire_messages_loop(self):
        """Delete expired disappearing messages every 30 seconds (rooms + DM threads)."""
        from datetime import timedelta
        _consecutive_failures = 0
        while True:
            await asyncio.sleep(30)
            try:
                # --- Rooms ---
                for room_id, ms in list(self._room_disappear_timers.items()):
                    if ms <= 0:
                        continue
                    room = self._local_rooms.get(room_id)
                    if not room:
                        continue
                    cutoff = (datetime.now(timezone.utc) - timedelta(milliseconds=ms)).isoformat()
                    expired = []
                    for msg in list(room.get("messages", [])):
                        if msg.get("timestamp", "") < cutoff:
                            expired.append(msg.get("message_id"))
                    room["messages"] = [m for m in room.get("messages", []) if m.get("timestamp", "") >= cutoff]
                    if self._store and expired:
                        self._store.delete_messages_before(room_id, cutoff)
                    for mid in expired:
                        for ws in list(room.get("members", set())):
                            try:
                                await ws.send(json.dumps({
                                    "type": "message_deleted",
                                    "message_id": mid,
                                    "thread_id": room_id,
                                }))
                            except Exception:
                                pass
                    await asyncio.sleep(0)

                # --- DM threads ---
                for cert_id, ms in list(getattr(self, "_dm_disappear_timers", {}).items()):
                    if ms <= 0:
                        continue
                    if not self._store:
                        continue
                    cutoff = (datetime.now(timezone.utc) - timedelta(milliseconds=ms)).isoformat()
                    deleted = self._store.delete_messages_before(cert_id, cutoff)
                    if deleted:
                        await self._notify_dm_expired(cert_id, cutoff)
                _consecutive_failures = 0  # reset on success
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _consecutive_failures += 1
                logger.warning("_expire_messages_loop error #%d: %s", _consecutive_failures, exc)
                if _consecutive_failures >= 10:
                    if self._store:
                        self._store.save_background_health_event("expire_messages_loop", _consecutive_failures, str(exc)[:200])
                    await asyncio.sleep(60)
                else:
                    await asyncio.sleep(5)

    async def _notify_dm_expired(self, cert_id: str, cutoff: str) -> None:
        """Broadcast message_deleted events for DM threads to connected participants."""
        if not self._store:
            return
        # Find all sockets that have this cert_id in their active view
        for webid, sockets in list(self._webid_sockets.items()):
            for ws in list(sockets):
                try:
                    await ws.send(json.dumps({
                        "type": "dm_messages_expired",
                        "thread_id": cert_id,
                        "before_timestamp": cutoff,
                    }))
                except Exception:
                    pass

    async def process_link_previews(self, content: str, source: str, message_id: str):
        """Fetch and broadcast previews for URLs in a message."""
        from .linkpreview import extract_urls, fetch_link_preview
        urls = extract_urls(content)
        for url in urls[:3]:
            preview = await fetch_link_preview(url)
            if preview:
                await self.broadcast({
                    "type": "link_preview",
                    "source": source,
                    "message_id": message_id,
                    "preview": preview
                })

    async def _check_room_enforcement(self, websocket, room_id: str, membership: RoomMembership) -> Optional[str]:
        """Verify room safety rules (rate limits, read-only, cert expiry). Returns error string or None."""
        config = membership.room
        sender_webid = self._client_webids.get(websocket, "")

        # R1: Verify certificate has not expired
        if membership.cert and membership.cert.expires_at <= time.time():
            return "Certificate expired"

        if getattr(config, "read_only", False) and sender_webid != config.owner_webid:
            return "Room is read-only"

        rate_limit = getattr(config, "rate_limit", None)
        if rate_limit is not None and rate_limit > 0:
            webid = sender_webid or self.agent.identity_pub_bytes.hex()
            key = (webid, room_id)
            async with self._rate_lock:
                now = datetime.now(timezone.utc).timestamp()
                if key not in self._rate_counters:
                    self._rate_counters[key] = deque()

                while self._rate_counters[key] and self._rate_counters[key][0] < now - rate_limit:
                    self._rate_counters[key].popleft()

                if len(self._rate_counters[key]) > 0:
                    return f"Rate limit exceeded (min {rate_limit}s between messages)"

                self._rate_counters[key].append(now)

        return None

    def _check_room_permission(self, websocket, room_id: str, role: str = "member") -> bool:
        """Verify caller role in a room. Returns True if permitted."""
        caller_webid = self._client_webids.get(websocket, "")
        if not caller_webid:
            return False

        # System stubs (scheduler) get no blanket bypass — verify via store membership
        if websocket in self._system_ws:
            room = self._local_rooms.get(room_id)
            if not room:
                return False
            if role == "owner":
                return room.get("creator_webid") == caller_webid
            if self._store:
                return caller_webid in self._store.get_room_members(room_id)
            return False

        # For federated rooms (Solid Pod)
        if room_id in self.room_memberships:
            membership, _ = self.room_memberships[room_id]
            if role == "owner":
                return membership.room.owner_webid == caller_webid
            return True # Any member in self.room_memberships is allowed

        # For local (pod-free) rooms
        room = self._local_rooms.get(room_id)
        if room:
            if role == "owner":
                return room.get("creator_webid") == caller_webid
            return websocket in room.get("members", set())

        return False

    async def _handle_send_room(self, websocket, data: dict) -> None:
        room_id = data.get("room_id")
        content = data.get("content")
        encrypt = data.get("encrypt", True)
        # Content guards: reject empty/whitespace-only and oversized payloads
        if not content or not str(content).strip():
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "empty_content"}))
            return
        if len(str(content).encode("utf-8")) > 16_384:
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "content_too_large"}))
            return
        if data.get("content_type") == "attachment":
            from .attachment_crypto import validate_attachment_envelope
            _env_valid, _env_reason = validate_attachment_envelope(data.get("attachment_descriptor") or {})
            if not _env_valid:
                await websocket.send(json.dumps({
                    "type": "error",
                    "code": "invalid_attachment_envelope",
                    "message": _env_reason,
                }))
                return
        if room_id in self.room_memberships:
            membership, client = self.room_memberships[room_id]

            error = await self._check_room_enforcement(websocket, room_id, membership)
            if error:
                await websocket.send(json.dumps({"type": "error", "message": error}))
                return

            from .messaging import compose
            from .room import send
            msg = compose(self.agent.identity_key, membership.cert, content, encrypt=encrypt, reply_to_id=data.get("reply_to_id"))
            try:
                send(msg, client)
                logger.info(f"Sent message to room {room_id}")
            except Exception as e:
                logger.warning(f"Failed to send room message, enqueuing: {e}")
                self.outbox.enqueue(msg, room_id=room_id)
                await websocket.send(json.dumps({"type": "info", "message": "Message queued (offline)"}))

        elif room_id in self._local_rooms:
            if not self._check_room_permission(websocket, room_id):
                await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
                return
            sender_webid = self._client_webids.get(websocket, "unknown")
            if sender_webid and self._store and self._store.is_room_muted(room_id, sender_webid):
                await websocket.send(json.dumps({"type": "error", "message": "you_are_muted"}))
                return
            import uuid as _uuid_room
            sender_name = self._name_for(websocket, sender_webid)
            _client_mid = data.get("message_id", "")
            try:
                _uuid_room.UUID(_client_mid)
                message_id = _client_mid
            except (ValueError, AttributeError):
                message_id = "local-" + _uuid_room.uuid4().hex[:12]
            ts = datetime.now(timezone.utc).isoformat()

            # Reject non-incrementing seq_num (optional field — backward compatible)
            _provided_seq = data.get("seq_num")
            if _provided_seq is not None:
                _seq_int = int(_provided_seq)
                if _seq_int > 0 and self._store:
                    _max_seq = self._store.get_max_seq_num(room_id)
                    if _seq_int <= _max_seq:
                        await websocket.send(json.dumps({
                            "type": "error", "message": "invalid_sequence",
                        }))
                        return

            event = {
                "type": "message",
                "source": "room",
                "thread_id": room_id,
                "from_webid": sender_webid,
                "from_display_name": sender_name,
                "content": content,
                "timestamp": ts,
                "message_id": message_id,
                "reply_to_id": data.get("reply_to_id"),
                "local": True,
            }
            if content and content.startswith("/"):
                event["is_command"] = True
                event["command"] = content.split()[0][1:]
            if self._store:
                self._store.save_message(
                    message_id, room_id, "room",
                    sender_webid, sender_name, content, ts,
                    reply_to_id=data.get("reply_to_id"),
                    seq_num=int(data.get("seq_num") or 0),
                    prev_hash=str(data.get("prev_hash") or ""),
                )
                self._store.set_last_read(sender_webid, room_id)
                # Per-user unread: increment for non-senders only
                _other_wids = [
                    self._client_webids.get(_ws, "")
                    for _ws in self._local_rooms.get(room_id, {}).get("members", set())
                    if _ws is not websocket
                ]
                self._store.increment_room_unread(room_id, [w for w in _other_wids if w])
                self._store.mark_room_read(room_id, sender_webid, message_id, ts)
                # R13.14: track sender as known contact
                if sender_webid and sender_name:
                    self._store.upsert_contact(sender_webid, sender_name, source="room", owner_webid=sender_webid)
                # R13.4: metrics
                self._metrics["messages_total"] += 1
            asyncio.create_task(self._sync_room_message_to_pod(room_id, event))
            room = self._local_rooms[room_id]
            if room.get("history_mode") == "all":
                room["messages"].append(event)
            for ws in list(room["members"]):
                own = ws == websocket
                try:
                    await ws.send(json.dumps({**event, "own": own}))
                except Exception as exc:
                    logger.warning(f"Room relay failed for member: {exc}")
            # Relay to federated (cross-gateway) members
            if self._store:
                _fed_members = self._store.get_federated_room_members(room_id)
                _relayed_gws: set = set()
                for _fm in _fed_members:
                    _gw = _fm["gateway_url"]
                    if _gw not in _relayed_gws:
                        _relayed_gws.add(_gw)
                        asyncio.create_task(self._relay_room_message(_gw, room_id, event))
            if self._store:
                outgoing_hooks = self._store.get_webhooks_for_thread(room_id, "outgoing")
                for _wh in outgoing_hooks:
                    asyncio.create_task(self._fire_outgoing_webhook(_wh, event))

        else:
            await websocket.send(json.dumps({"type": "error", "message": f"Unknown room: {room_id}"}))

    async def _handle_get_rooms(self, websocket, data: dict) -> None:
        rooms = []
        for rid, (m, _) in self.room_memberships.items():
            rooms.append({"id": rid, "name": m.room_id})
        _caller_wid = self._client_webids.get(websocket)
        for rid, room in self._local_rooms.items():
            # room["members"] is a set of *live* websockets, rebuilt each run;
            # after a gateway restart it is empty even for legitimate members.
            # Fall back to persistent store membership (by webid) so reconnecting
            # members still get their rooms, and re-attach the websocket to the
            # live set (mirrors _handle_get_local_history).
            _in_live = websocket in room["members"]
            _in_store = bool(_caller_wid and self._store and
                             _caller_wid in self._store.get_room_members(rid))
            if not _in_live and not _in_store:
                continue
            if not _in_live and _in_store:
                room["members"].add(websocket)
            rooms.append({
                "id": rid,
                "name": room["name"],
                "code": room.get("code", ""),
                "invite_url": room.get("invite_url", ""),
                "history_mode": room.get("history_mode", "none"),
                "creator_webid": room.get("creator_webid", ""),
                "local": True,
            })
        await websocket.send(json.dumps({"type": "rooms", "rooms": rooms}))

    async def _handle_get_local_history(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        if thread_id in self._local_rooms:
            _caller_wid = self._client_webids.get(websocket)
            _in_live = websocket in self._local_rooms[thread_id]["members"]
            _in_store = bool(_caller_wid and self._store and
                             _caller_wid in self._store.get_room_members(thread_id))
            if not _in_live and not _in_store:
                await websocket.send(json.dumps({
                    "type": "local_history", "thread_id": thread_id, "messages": [],
                }))
                return
            if not _in_live and _in_store:
                self._local_rooms[thread_id]["members"].add(websocket)
        after_ts = data.get("after_timestamp")
        before_ts = data.get("before_timestamp")
        try:
            limit = min(int(data.get("limit", 100)), 200)
        except (TypeError, ValueError):
            limit = 100
        if thread_id and thread_id not in self._local_rooms and not self._client_webids.get(websocket):
            await websocket.send(json.dumps({
                "type": "local_history", "thread_id": thread_id, "messages": [],
            }))
            return
        if not thread_id:
            await websocket.send(json.dumps({
                "type": "local_history",
                "thread_id": thread_id,
                "messages": [],
            }))
        else:
            raw = None
            pod_ok = False
            is_room = thread_id in self._local_rooms

            if is_room and not before_ts and self._pod_client():
                try:
                    from .pod_room_store import PodRoomStore
                    pod_store = PodRoomStore(self._pod_client())
                    loop = asyncio.get_event_loop()
                    pod_msgs = await loop.run_in_executor(
                        None, pod_store.read_messages, thread_id, after_ts, limit
                    )
                    raw = pod_msgs
                    pod_ok = True
                    if self._store:
                        for m in pod_msgs:
                            try:
                                self._store.save_message(
                                    m["message_id"], thread_id, "room",
                                    m.get("from_webid", ""),
                                    m.get("from_display_name"),
                                    m.get("content", ""),
                                    m.get("timestamp", ""),
                                    reply_to_id=m.get("reply_to_id"),
                                    seq_num=int(m.get("seq_num") or 0),
                                    prev_hash=str(m.get("prev_hash") or ""),
                                )
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug(f"Pod history read failed [{thread_id}]: {exc}")
                    raw = None

            # Fall back to SQLite if pod failed OR if pod returned nothing (not yet synced)
            if (not pod_ok or not raw) and self._store:
                raw = self._store.get_messages(thread_id, after_ts, before_ts, limit)

            if raw is None:
                raw = []

            msgs = [
                {
                    "type": "message",
                    "source": m.get("thread_type", m.get("source", "room")),
                    "thread_id": m.get("thread_id", thread_id),
                    "from_webid": m.get("from_webid", ""),
                    "from_display_name": (
                        m.get("from_display_name")
                        or (self._store.get_display_name(m["from_webid"]) if self._store else None)
                    ),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", ""),
                    "message_id": m.get("message_id", ""),
                    "reply_to_id": m.get("reply_to_id"),
                    "edited_at": m.get("edited_at"),
                    "local": True,
                    **({"imported": True} if m.get("imported") else {}),
                }
                for m in raw
            ]
            reactions = []
            if is_room and self._store:
                reactions = self._store.get_reactions(thread_id)

            last_read_ts = 0
            caller_webid = self._client_webids.get(websocket)
            if caller_webid and self._store:
                last_read_ts = self._store.get_last_read(caller_webid, thread_id)

            # R7: Thread integrity check — verify monotonic seq_num and prev_hash continuity
            # Use `raw` (original DB rows) which carry seq_num/prev_hash before the response transform
            _integrity_warning = None
            if raw and self._store:
                _seq_ok = True
                _prev_ok = True
                _last_seq = None
                _last_prev_hash = None
                _first_offending_id = None
                for _m in raw:
                    _seq = _m.get("seq_num") or 0
                    _prev = _m.get("prev_hash") or ""
                    if _seq and _last_seq is not None and _seq != 0 and _seq <= _last_seq:
                        _seq_ok = False
                        _first_offending_id = _m.get("message_id")
                        break
                    if _prev and _last_prev_hash is not None and _last_prev_hash and _prev != _last_prev_hash:
                        _prev_ok = False
                        _first_offending_id = _m.get("message_id")
                        break
                    if _seq:
                        _last_seq = _seq
                    if _prev:
                        _last_prev_hash = _prev
                if not _seq_ok or not _prev_ok:
                    _integrity_warning = {
                        "type": "seq_num" if not _seq_ok else "prev_hash",
                        "first_offending_message_id": _first_offending_id,
                    }
                    _caller_ip = (self._session_meta.get(websocket) or {}).get("ip_addr", "")
                    self._store.save_security_event(
                        "thread_integrity_break", "warning",
                        webid=self._client_webids.get(websocket),
                        ip=_caller_ip or None,
                        details=f"thread_id={thread_id} type={_integrity_warning['type']} offending={_first_offending_id}",
                    )
                # Update checkpoint using the last raw row
                _last_raw = raw[-1] if raw else None
                if _last_raw:
                    self._store.upsert_thread_integrity_state(
                        thread_id=thread_id,
                        last_seq_num=_last_raw.get("seq_num") or 0,
                        last_prev_hash=_last_raw.get("prev_hash") or "",
                        checked_at=__import__("time").time(),
                    )

            _payload = {
                "type": "local_history",
                "thread_id": thread_id,
                "messages": msgs,
                "reactions": reactions,
                "last_read_ts": last_read_ts,
            }
            if _integrity_warning:
                _payload["integrity_warning"] = _integrity_warning
            await websocket.send(json.dumps(_payload))

    async def _handle_delete_local_message(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        thread_id = data.get("thread_id", "")
        caller_webid = self._client_webids.get(websocket)
        if not caller_webid:
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        if thread_id in self._local_rooms and websocket not in self._local_rooms[thread_id]["members"]:
            return
        if caller_webid and self._store:
            sender = self._store.get_message_sender(message_id)
            if sender and sender != caller_webid:
                await websocket.send(json.dumps({"type": "error", "message": "Cannot delete another user's message"}))
                return
        if message_id and self._store:
            self._store.delete_message(message_id)
        if message_id and thread_id in self._local_rooms:
            asyncio.create_task(self._delete_room_message_on_pod(thread_id, message_id))
        event = {"type": "message_deleted", "message_id": message_id, "thread_id": thread_id}
        if thread_id in self._local_rooms:
            for ws in list(self._local_rooms[thread_id]["members"]):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
            # Relay delete to federated member gateways
            if thread_id in self._local_rooms and self._store:
                _fed_del = self._store.get_federated_room_members(thread_id)
                _seen_del: set = set()
                for _fm in _fed_del:
                    _gw = _fm["gateway_url"]
                    if _gw not in _seen_del:
                        _seen_del.add(_gw)
                        asyncio.create_task(self._relay_ephemeral(_gw, {
                            "content_type": "room_delete",
                            "room_id": thread_id,
                            "message_id": message_id,
                            "from_webid": caller_webid or "",
                        }))
        else:
            # DM thread: deliver only to the two participants
            participants: set = {caller_webid}
            if self._store:
                dm_threads = [t for t in self._store.get_dm_threads(owner_webid=caller_webid)
                              if t["thread_id"] == thread_id]
                if dm_threads:
                    participants.add(dm_threads[0]["peer_webid"])
            payload = json.dumps(event)
            for identity in participants:
                await self._send_to_identity(identity, payload)

    async def _handle_edit_local_message(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        thread_id  = data.get("thread_id", "")
        new_content = (data.get("content") or "").strip()
        caller_webid_edit = self._client_webids.get(websocket)
        if not caller_webid_edit:
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        if thread_id in self._local_rooms and websocket not in self._local_rooms[thread_id]["members"]:
            return
        if caller_webid_edit and self._store:
            sender = self._store.get_message_sender(message_id)
            if sender and sender != caller_webid_edit:
                await websocket.send(json.dumps({"type": "error", "message": "Cannot edit another user's message"}))
                return
        if message_id and new_content:
            edited_at = datetime.now(timezone.utc).isoformat()
            if self._store:
                self._store.update_message(message_id, new_content, edited_at,
                                           editor_webid=caller_webid_edit or "")
            if thread_id in self._local_rooms:
                asyncio.create_task(
                    self._edit_room_message_on_pod(thread_id, message_id, new_content, edited_at)
                )
            event = {
                "type": "message_edited",
                "message_id": message_id,
                "thread_id": thread_id,
                "new_content": new_content,
                "edited_at": edited_at,
                "has_history": True,
            }
            if thread_id in self._local_rooms:
                for ws in list(self._local_rooms[thread_id]["members"]):
                    try:
                        await ws.send(json.dumps(event))
                    except Exception:
                        pass
                # Relay edit to federated member gateways
                if thread_id in self._local_rooms and self._store:
                    _fed_edt = self._store.get_federated_room_members(thread_id)
                    _seen_edt: set = set()
                    for _fm in _fed_edt:
                        _gw = _fm["gateway_url"]
                        if _gw not in _seen_edt:
                            _seen_edt.add(_gw)
                            asyncio.create_task(self._relay_ephemeral(_gw, {
                                "content_type": "room_edit",
                                "room_id": thread_id,
                                "message_id": message_id,
                                "new_content": new_content,
                                "edited_at": edited_at,
                                "from_webid": caller_webid_edit or "",
                            }))
            else:
                # DM thread: deliver only to the two participants
                participants_edit: set = {caller_webid_edit}
                if self._store:
                    dm_threads_edit = [t for t in self._store.get_dm_threads(owner_webid=caller_webid_edit)
                                       if t["thread_id"] == thread_id]
                    if dm_threads_edit:
                        participants_edit.add(dm_threads_edit[0]["peer_webid"])
                payload_edit = json.dumps(event)
                for identity in participants_edit:
                    await self._send_to_identity(identity, payload_edit)

    async def _handle_forward_message(self, websocket, data: dict) -> None:
        source_msg_id = data.get("message_id", "")
        target_thread_id = data.get("target_thread_id", "")
        actor = self._client_webids.get(websocket)
        if not actor or not source_msg_id or not target_thread_id:
            return

        source_row = None
        if self._store:
            source_row = self._store.get_message(source_msg_id)

        if not source_row:
            await websocket.send(json.dumps({"type": "error", "message": "Message not found"}))
            return

        target_room = self._local_rooms.get(target_thread_id)
        if target_room and websocket not in target_room.get("members", set()):
            await websocket.send(json.dumps({"type": "error", "message": "Not a member of target thread"}))
            return

        import uuid as _uuid_fwd
        new_msg_id = str(_uuid_fwd.uuid4())
        display_name = self._display_names.get(websocket, "")
        ts = datetime.now(timezone.utc).isoformat()
        event = {
            "type": "message",
            "source": "local_room" if target_thread_id in self._local_rooms else "local_dm",
            "thread_id": target_thread_id,
            "message_id": new_msg_id,
            "from_webid": actor,
            "from_display_name": display_name,
            "content": source_row.get("content", ""),
            "content_type": "text",
            "forwarded": True,
            "forwarded_from_name": source_row.get("from_display_name", ""),
            "timestamp": ts,
            "local": True,
        }

        if target_room:
            for ws in list(target_room.get("members", set())):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
        else:
            for ws in (websocket, self._any_socket(target_thread_id)):
                if ws:
                    try:
                        await ws.send(json.dumps(event))
                    except Exception:
                        pass
        if self._store:
            self._store.save_message(
                new_msg_id, target_thread_id,
                "local_room" if target_thread_id in self._local_rooms else "local_dm",
                actor, display_name, event["content"], ts,
                seq_num=int(data.get("seq_num") or 0),
                prev_hash=str(data.get("prev_hash") or ""),
            )

    async def _handle_get_room_members(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if room_id in self._local_rooms:
            _caller_wid_m = self._client_webids.get(websocket)
            _in_live_m = websocket in self._local_rooms[room_id]["members"]
            _in_store_m = bool(_caller_wid_m and self._store and
                               _caller_wid_m in self._store.get_room_members(room_id))
            if not _in_live_m and not _in_store_m:
                await websocket.send(json.dumps({"type": "room_members", "room_id": room_id, "members": []}))
                return
            if not _in_live_m and _in_store_m:
                self._local_rooms[room_id]["members"].add(websocket)
        members = []
        seen_webids: set = set()
        if room_id and self._store:
            for webid in self._store.get_room_members(room_id):
                seen_webids.add(webid)
                ws = self._any_socket(webid)
                display_name = (self._display_names.get(ws) if ws else None) or webid[:12]
                status = self._user_presence.get(webid, {}).get("status", "offline")
                members.append({"webid": webid, "display_name": display_name, "status": status})
        if room_id in self._local_rooms:
            for ws in list(self._local_rooms[room_id]["members"]):
                webid = self._client_webids.get(ws, "")
                if webid and webid not in seen_webids:
                    seen_webids.add(webid)
                    display_name = self._display_names.get(ws) or webid[:12]
                    status = self._user_presence.get(webid, {}).get("status", "offline")
                    members.append({"webid": webid, "display_name": display_name, "status": status})
        # Include federated (cross-gateway) members
        if room_id in self._local_rooms and self._store:
            for _fm in (self._store.get_federated_room_members(room_id) or []):
                _fwid = _fm.get("member_did", "")
                if _fwid and _fwid not in seen_webids:
                    seen_webids.add(_fwid)
                    _fdn = (self._store.get_display_name(_fwid) if self._store else None) or _fwid[:12]
                    _fst = self._user_presence.get(_fwid, {}).get("status", "offline")
                    members.append({
                        "webid": _fwid,
                        "display_name": _fdn,
                        "status": _fst,
                        "federated": True,
                        "gateway": _fm.get("gateway_url", ""),
                    })
        await websocket.send(json.dumps({
            "type": "room_members",
            "room_id": room_id,
            "members": members,
        }))

    async def _handle_leave_local_room(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if room_id not in self._local_rooms:
            await websocket.send(json.dumps({"type": "left_room", "room_id": room_id}))
        else:
            room = self._local_rooms[room_id]
            caller_did = self._client_webids.get(websocket, "")
            is_creator = room.get("creator_webid") == caller_did and caller_did

            room["members"].discard(websocket)
            if caller_did and self._store:
                self._store.remove_room_member(room_id, caller_did)

            if is_creator:
                other_members = list(room["members"])
                if not other_members:
                    self._room_codes.pop(room.get("code"), None)
                    del self._local_rooms[room_id]
                    if self._store:
                        self._store.delete_room(room_id)
                    await websocket.send(json.dumps({
                        "type": "left_room", "room_id": room_id,
                        "deleted": True,
                        "reason": "Room deleted — no remaining members.",
                    }))
                else:
                    # Prefer admins > mods > regular members when transferring ownership
                    _role_priority = {"admin": 0, "mod": 1, "member": 2}
                    _roles = self._store.get_all_room_roles(room_id) if self._store else {}
                    other_members.sort(
                        key=lambda ws: _role_priority.get(
                            _roles.get(self._client_webids.get(ws, ""), "member"), 2
                        )
                    )
                    new_owner_ws = other_members[0]
                    new_owner_did = self._client_webids.get(new_owner_ws, "")
                    new_owner_name = self._name_for(new_owner_ws, new_owner_did)
                    room["creator_webid"] = new_owner_did
                    if self._store and new_owner_did:
                        self._store.update_room_creator(room_id, new_owner_did)
                    for ws in list(room["members"]):
                        try:
                            await ws.send(json.dumps({
                                "type": "ownership_transferred",
                                "room_id": room_id,
                                "new_owner_did": new_owner_did,
                                "new_owner_name": new_owner_name,
                            }))
                        except Exception:
                            pass
                    await websocket.send(json.dumps({
                        "type": "left_room", "room_id": room_id,
                        "transferred_to": new_owner_name,
                    }))
            else:
                await websocket.send(json.dumps({"type": "left_room", "room_id": room_id}))

    async def _handle_delete_room(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if room_id not in self._local_rooms:
            await websocket.send(json.dumps({"type": "error", "message": "Room not found"}))
        else:
            room = self._local_rooms[room_id]
            caller_did = self._client_webids.get(websocket, "")
            if room.get("creator_webid") != caller_did or not caller_did:
                await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can delete this room"}))
            else:
                for ws in list(room["members"]):
                    try:
                        await ws.send(json.dumps({"type": "room_deleted", "room_id": room_id, "room_name": room["name"]}))
                    except Exception:
                        pass
                self._room_codes.pop(room.get("code"), None)
                del self._local_rooms[room_id]
                if self._store:
                    self._store.delete_room(room_id)

    async def _handle_transfer_ownership(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        to_did = data.get("to_did", "")
        if room_id not in self._local_rooms:
            await websocket.send(json.dumps({"type": "error", "message": "Room not found"}))
        else:
            room = self._local_rooms[room_id]
            caller_did = self._client_webids.get(websocket, "")
            if room.get("creator_webid") != caller_did or not caller_did:
                await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can transfer ownership"}))
            elif to_did == caller_did:
                await websocket.send(json.dumps({"type": "error", "message": "You are already the owner"}))
            else:
                to_ws = self._any_socket(to_did)
                if not to_ws or to_ws not in room["members"]:
                    await websocket.send(json.dumps({"type": "error", "message": "That user is not in this room or is offline"}))
                else:
                    self._pending_ownership_transfers[room_id] = {
                        "from_ws": websocket, "to_ws": to_ws, "to_did": to_did,
                    }
                    caller_name = self._name_for(websocket, caller_did)
                    await to_ws.send(json.dumps({
                        "type": "ownership_transfer_offer",
                        "room_id": room_id,
                        "room_name": room["name"],
                        "from_did": caller_did,
                        "from_name": caller_name,
                    }))
                    await websocket.send(json.dumps({
                        "type": "info",
                        "message": f"Transfer request sent to {self._name_for(to_ws, to_did)}. Waiting for their response.",
                    }))

    async def _handle_accept_ownership(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        pending = self._pending_ownership_transfers.get(room_id)
        if not pending or pending["to_ws"] != websocket:
            await websocket.send(json.dumps({"type": "error", "message": "No pending transfer for this room"}))
        else:
            room = self._local_rooms.get(room_id)
            if not room:
                await websocket.send(json.dumps({"type": "error", "message": "Room no longer exists"}))
            else:
                new_owner_did = pending["to_did"]
                new_owner_name = self._name_for(websocket, new_owner_did)
                room["creator_webid"] = new_owner_did
                if self._store and new_owner_did:
                    self._store.update_room_creator(room_id, new_owner_did)
                del self._pending_ownership_transfers[room_id]
                for ws in list(room["members"]):
                    try:
                        await ws.send(json.dumps({
                            "type": "ownership_transferred",
                            "room_id": room_id,
                            "new_owner_did": new_owner_did,
                            "new_owner_name": new_owner_name,
                        }))
                    except Exception:
                        pass

    async def _handle_decline_ownership(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        pending = self._pending_ownership_transfers.get(room_id)
        if pending and pending["to_ws"] == websocket:
            del self._pending_ownership_transfers[room_id]
            from_ws = pending["from_ws"]
            decliner_did = self._client_webids.get(websocket, "")
            try:
                await from_ws.send(json.dumps({
                    "type": "info",
                    "message": f"{self._name_for(websocket, decliner_did)} declined the ownership transfer.",
                }))
            except Exception:
                pass

    def _reaction_recipients(self, sender_webid: str, cert, room_id: str) -> set:
        """Local identities to deliver a cert/pod reaction to: the sender's own
        sessions plus the DM peer (cert.subject is an ed25519 pub hex → did:key).
        Used instead of a gateway-wide broadcast."""
        recips = set()
        if sender_webid:
            recips.add(sender_webid)
        _subj = getattr(cert, "subject", "") if cert else ""
        if _subj and not str(_subj).startswith("did:key:"):
            try:
                from .didkey import pub_key_to_did as _p2d
                _subj = _p2d(bytes.fromhex(_subj))
            except Exception:
                _subj = ""
        if _subj:
            recips.add(_subj)
        return {r for r in recips if r}

    async def _handle_add_reaction(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        room_id = data.get("room_id")
        message_id = data.get("message_id")
        emoji = data.get("emoji")
        sender_webid = self._client_webids.get(websocket, self.agent.identity_pub_bytes.hex())

        if room_id and room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id]["members"]:
                return
            if self._store:
                saved = self._store.save_reaction(room_id, message_id, emoji, sender_webid)
                if not saved:
                    await websocket.send(json.dumps({"type": "error", "message": "reaction_limit_reached"}))
                    return
            room = self._local_rooms[room_id]
            # Compose a signed reaction message for federated history integrity
            from .messaging import compose_reaction
            from .federation import RelationshipCertificate as _RC
            try:
                _synthetic_cert = _RC(
                    certificate_id=room_id,
                    issuer=self.agent.identity_pub_bytes.hex(),
                    subject="",
                    capabilities=[],
                )
                react_msg = compose_reaction(
                    self.agent.identity_key, _synthetic_cert, emoji, message_id
                )
            except Exception:
                react_msg = None
            event = {
                "type": "reaction_added",
                "thread_id": room_id,
                "message_id": message_id,
                "emoji": emoji,
                "from_webid": sender_webid,
            }
            if react_msg:
                event["reaction_message_id"] = react_msg.message_id
                asyncio.create_task(self._sync_room_message_to_pod(room_id, react_msg.to_dict()))
            for ws in list(room["members"]):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
            # Relay reaction to federated member gateways
            if self._store:
                _fed_react = self._store.get_federated_room_members(room_id)
                _seen_react: set = set()
                for _fm in _fed_react:
                    _gw = _fm["gateway_url"]
                    if _gw not in _seen_react:
                        _seen_react.add(_gw)
                        asyncio.create_task(self._relay_ephemeral(_gw, {
                            "content_type": "room_reaction",
                            "room_id": room_id,
                            "message_id": message_id,
                            "emoji": emoji,
                            "from_webid": sender_webid,
                            "action": "add",
                        }))
        elif cert_id and cert_id not in self.dm_clients:
            # Verify caller is a participant of this local DM thread
            _owner_did = _peer_did = ""
            if self._store:
                _cert_dict = self._store.get_relationship_by_cert_id(cert_id)
                if _cert_dict:
                    from .didkey import pub_key_to_did as _p2d_react
                    _owner_did = _p2d_react(self.agent.identity_pub_bytes)
                    _peer_did = _cert_dict.get("peer_did", "")
                    if sender_webid not in (_owner_did, _peer_did):
                        await websocket.send(json.dumps({"type": "error", "message": "Not a participant of this DM thread"}))
                        return
            if self._store and message_id and emoji:
                saved = self._store.save_reaction(cert_id, message_id, emoji, sender_webid)
                if not saved:
                    await websocket.send(json.dumps({"type": "error", "message": "reaction_limit_reached"}))
                    return
            event = {
                "type": "reaction_added", "thread_id": cert_id,
                "message_id": message_id, "emoji": emoji, "from_webid": sender_webid,
            }
            await websocket.send(json.dumps(event))
            # In this branch cert_id IS the peer's DID (local DM thread key), so
            # deliver to ALL of the peer's sessions (multi-device) rather than the
            # single socket _any_socket(cert_id) returned.
            if cert_id != sender_webid:
                await self._send_to_identity(cert_id, json.dumps(event))
        else:
            cert, client = None, None
            id_to_use = cert_id or room_id
            if cert_id and cert_id in self.dm_clients:
                cert, client = self.dm_clients[cert_id]
            elif room_id and room_id in self.room_memberships:
                membership, client = self.room_memberships[room_id]
                cert = membership.cert
            if cert and client:
                from .reactions import add_reaction
                react = add_reaction(cert, client, self.agent.identity_key, message_id, emoji)
                _react_event = json.dumps({
                    "type": "reaction_added",
                    "thread_id": id_to_use,
                    "message_id": message_id,
                    "emoji": emoji,
                    "from_webid": sender_webid,
                    "reaction_message_id": react.reaction_message_id,
                })
                # Scope to participants, not every gateway client (broadcast).
                for _r in self._reaction_recipients(sender_webid, cert, room_id):
                    await self._send_to_identity(_r, _react_event)
            else:
                await websocket.send(json.dumps({"type": "error", "message": f"Unknown target: {cert_id or room_id}"}))

    async def _handle_remove_reaction(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        room_id = data.get("room_id")
        reaction_message_id = data.get("reaction_message_id")
        message_id = data.get("message_id", "")
        emoji = data.get("emoji", "")
        sender_webid = self._client_webids.get(websocket, self.agent.identity_pub_bytes.hex())

        if room_id and room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id]["members"]:
                return
            if self._store and message_id and emoji:
                self._store.remove_reaction(room_id, message_id, emoji, sender_webid)
            room = self._local_rooms[room_id]
            event = {
                "type": "reaction_removed",
                "thread_id": room_id,
                "message_id": message_id,
                "emoji": emoji,
                "from_webid": sender_webid,
            }
            for ws in list(room["members"]):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
            # Relay reaction removal to federated member gateways
            if self._store:
                _fed_rm = self._store.get_federated_room_members(room_id)
                _seen_rm: set = set()
                for _fm in _fed_rm:
                    _gw = _fm["gateway_url"]
                    if _gw not in _seen_rm:
                        _seen_rm.add(_gw)
                        asyncio.create_task(self._relay_ephemeral(_gw, {
                            "content_type": "room_reaction",
                            "room_id": room_id,
                            "message_id": message_id,
                            "emoji": emoji,
                            "from_webid": sender_webid,
                            "action": "remove",
                        }))
        elif cert_id and cert_id not in self.dm_clients:
            # Verify caller is a participant of this local DM thread
            _owner_did_r = _peer_did_r = ""
            if self._store:
                _cert_dict_r = self._store.get_relationship_by_cert_id(cert_id)
                if _cert_dict_r:
                    from .didkey import pub_key_to_did as _p2d_rmreact
                    _owner_did_r = _p2d_rmreact(self.agent.identity_pub_bytes)
                    _peer_did_r = _cert_dict_r.get("peer_did", "")
                    if sender_webid not in (_owner_did_r, _peer_did_r):
                        await websocket.send(json.dumps({"type": "error", "message": "Not a participant of this DM thread"}))
                        return
            if self._store and message_id and emoji:
                self._store.remove_reaction(cert_id, message_id, emoji, sender_webid)
            event = {
                "type": "reaction_removed", "thread_id": cert_id,
                "message_id": message_id, "emoji": emoji, "from_webid": sender_webid,
            }
            await websocket.send(json.dumps(event))
            # cert_id is the peer's DID here — deliver to all the peer's sessions.
            if cert_id != sender_webid:
                await self._send_to_identity(cert_id, json.dumps(event))
        else:
            id_to_use = cert_id or room_id
            cert, client = None, None
            if cert_id and cert_id in self.dm_clients:
                cert, client = self.dm_clients[cert_id]
            elif room_id and room_id in self.room_memberships:
                membership, client = self.room_memberships[room_id]
                cert = membership.cert
            if cert and client:
                from .reactions import remove_reaction
                remove_reaction(cert, client, reaction_message_id)
                _rm_event = json.dumps({
                    "type": "reaction_removed",
                    "thread_id": id_to_use,
                    "message_id": message_id,
                    "emoji": emoji,
                    "from_webid": sender_webid,
                    "reaction_message_id": reaction_message_id,
                })
                # Scope to participants, not every gateway client (broadcast).
                for _r in self._reaction_recipients(sender_webid, cert, room_id):
                    await self._send_to_identity(_r, _rm_event)
            else:
                await websocket.send(json.dumps({"type": "error", "message": f"Unknown target: {id_to_use}"}))

    async def _handle_mark_read(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id")
        message_id_read = data.get("message_id", "")
        reader_webid = self._client_webids.get(websocket, "")

        if self._store and reader_webid and thread_id:
            self._store.set_last_read(reader_webid, thread_id)
            # R13.12: update room read receipt table
            if thread_id in self._local_rooms and message_id_read:
                self._store.mark_room_read(thread_id, reader_webid, message_id_read,
                                           datetime.now(timezone.utc).isoformat())
            # R32: save per-message receipt if receipts enabled
            if reader_webid and message_id_read and self._client_receipts_prefs.get(reader_webid, True):
                self._store.save_message_receipt(message_id_read, reader_webid,
                                                  datetime.now(timezone.utc).isoformat())

        # R10.1.1: relay read-receipt to peer gateway for cert DMs or relay DIDs
        if reader_webid and thread_id and message_id_read:
            cert_dict = self._store.get_relationship_by_cert_id(thread_id) if self._store else None
            if cert_dict:
                peer_did = cert_dict.get("peer_did", "")
                peer_gw = self._resolve_peer_gateway(peer_did) if peer_did else None
            elif thread_id.startswith("did:"):
                # Relay DM: thread_id is the sender's DID
                peer_did = thread_id
                peer_gw = self._resolve_peer_gateway(peer_did)
            else:
                peer_did = ""
                peer_gw = None
            if self._client_receipts_prefs.get(reader_webid, True) and peer_gw and peer_did and message_id_read:
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    from .relay import sign_relay_message as _sign_relay
                    from .didkey import pub_key_to_did as _p2d
                    from .network import async_safe_post as _asp
                    _ts = _dt.now(_tz.utc).isoformat()
                    _from_did = _p2d(self.agent.identity_pub_bytes)
                    _sig = _sign_relay(self.agent.identity_key, _from_did, peer_did, message_id_read, "", _ts)
                    http_base = peer_gw.replace("wss://", "https://").replace("ws://", "http://")
                    await _asp(http_base.rstrip("/") + "/relay/receipt", {
                        "from_did": _from_did,
                        "to_did": peer_did,
                        "message_id": message_id_read,
                        "thread_id": thread_id,
                        "timestamp": _ts,
                        "signature": _sig,
                    })
                except Exception:
                    pass

        if thread_id and reader_webid:
            receipt_payload = json.dumps({
                "type": "read_receipt",
                "thread_id": thread_id,
                "webid": reader_webid,
                "message_id": message_id_read,
            })
            if thread_id in self._local_rooms:
                for ws in list(self._local_rooms[thread_id].get("members", set())):
                    _ws_wid = self._client_webids.get(ws, "")
                    if ws is not websocket and self._client_receipts_prefs.get(_ws_wid, True):
                        try:
                            await ws.send(receipt_payload)
                        except Exception:
                            pass
            else:
                if thread_id != reader_webid:
                    peer_ws = self._any_socket(thread_id)
                    if peer_ws:
                        try:
                            await peer_ws.send(receipt_payload)
                        except Exception:
                            pass
                elif self._store:
                    for cand_webid in list(self._webid_sockets):
                        if cand_webid == reader_webid:
                            continue
                        threads = [t for t in self._store.get_dm_threads(owner_webid=cand_webid)
                                   if t["peer_webid"] == reader_webid]
                        if threads:
                            peer_ws = self._any_socket(cand_webid)
                            if peer_ws:
                                try:
                                    await peer_ws.send(receipt_payload)
                                except Exception:
                                    pass

        if thread_id in self.room_memberships or thread_id in self.dm_clients:
            try:
                from . import receipts
                await receipts.mark_message_read(
                    self.read_state.pod_client,
                    data.get("message_id"),
                    thread_id,
                    str(self.agent.webid),
                )
            except Exception as exc:
                logger.debug(f"Pod mark_read skipped: {exc}")

    async def _handle_update_last_read(self, websocket, data: dict) -> None:
        reader_webid = self._client_webids.get(websocket, "")
        channel_id = data.get("channel_id", "")
        if reader_webid and channel_id and self._store:
            self._store.set_last_read(reader_webid, channel_id)
            # Mark dirty for pod flush (flushed every 30 s by _read_position_flush_loop)
            _rp = getattr(self, "_dirty_read_positions", None)
            if _rp is not None:
                _rp[(reader_webid, channel_id)] = time.time()

    async def _handle_read_dm(self, websocket, data: dict) -> None:
        if not self._client_webids.get(websocket):
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        cert_id = data.get("cert_id", "")
        before_ts = data.get("before_timestamp")
        messages = []
        if self._store:
            raw = self._store.get_messages(
                cert_id,
                before_timestamp=before_ts,
                limit=data.get("limit", 100),
            )
            messages = [
                {
                    "type":             "message",
                    "source":           m.get("thread_type") or "dm",
                    "thread_id":        cert_id,
                    "from_webid":       m["from_webid"],
                    "from_display_name": m.get("from_display_name") or m["from_webid"][:12],
                    "content":          m["content"],
                    "timestamp":        m["timestamp"],
                    "message_id":       m["message_id"],
                    "reply_to_id":      m.get("reply_to_id"),
                    "local":            True,
                    **({"imported": True} if m.get("imported") else {}),
                }
                for m in raw
            ]
        await websocket.send(json.dumps({"type": "history", "thread_id": cert_id, "messages": messages}))

    async def _handle_read_room(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if room_id in self._local_rooms and websocket not in self._local_rooms[room_id]["members"]:
            await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
            return
        messages = [
            {
                "type": "message",
                "source": "room",
                "thread_id": room_id,
                "from_webid": m.from_pub_hex,
                "content": m.content,
                "timestamp": datetime.fromtimestamp(m.timestamp, tz=timezone.utc).isoformat(),
                "message_id": m.message_id,
            }
            for e in self.message_cache
            if getattr(e, "thread_id", None) == room_id
            for m in [e.message]
        ]
        await websocket.send(json.dumps({"type": "history", "thread_id": room_id, "messages": messages}))

    def _hmac_invite_code(self, raw_code: str) -> str:
        """Return HMAC-SHA256(invite_hmac_key, raw_code) as hex digest."""
        import hmac as _hmac
        import hashlib as _hl
        return _hmac.new(
            getattr(self, "_invite_hmac_key", b"fallback"),
            raw_code.encode("utf-8"),
            _hl.sha256,
        ).hexdigest()

    async def _handle_chat_room_create(self, websocket, data: dict) -> None:
        import secrets as _secrets
        import base64 as _b64
        room_name = (data.get("name") or "New Room").strip()[:100]
        history_mode = data.get("history_mode", "none")
        room_id = "room-" + _secrets.token_hex(6)
        # Generate 128-bit raw secret; present as base32 (26 chars, no padding)
        raw_bytes = _secrets.token_bytes(16)
        code = _b64.b32encode(raw_bytes).rstrip(b"=").decode().lower()
        code_hash = self._hmac_invite_code(code)
        if self.config.http_port:
            host_display = self.config.host if self.config.host != "0.0.0.0" else "127.0.0.1"
            invite_url = f"http://{host_display}:{self.config.http_port}/?join={code}"
        else:
            invite_url = code
        self._local_rooms[room_id] = {
            "name": room_name,
            "code": code_hash,   # store hash in-memory; only hash persisted
            "members": {websocket},
            "invite_url": invite_url,
            "history_mode": history_mode,
            "messages": [],
            "creator_webid": self._client_webids.get(websocket, ""),
        }
        self._room_codes[code_hash] = room_id   # hash → room_id lookup
        logger.info(f"Local room created: {room_name!r} ({room_id})")
        creator_webid = self._client_webids.get(websocket, "")
        # Invite expiry and max-use defaults (caller may override within caps)
        _req_hours = data.get("expires_hours")
        _req_uses = data.get("max_uses")
        _DEFAULT_HOURS = 7 * 24   # 7 days
        _MAX_HOURS = 720          # 30 days cap
        _DEFAULT_USES = 100
        _MAX_USES = 500
        if _req_hours is None:
            _invite_hours = _DEFAULT_HOURS
        else:
            try:
                _invite_hours = max(1, min(int(_req_hours), _MAX_HOURS))
            except (TypeError, ValueError):
                _invite_hours = _DEFAULT_HOURS
        if _req_uses is None:
            _invite_uses = _DEFAULT_USES
        else:
            try:
                _invite_uses = max(1, min(int(_req_uses), _MAX_USES))
            except (TypeError, ValueError):
                _invite_uses = _DEFAULT_USES
        _invite_expires_at = time.time() + _invite_hours * 3600

        if self._store:
            # store the hash as rooms.code — plaintext raw_code never persisted
            self._store.save_room(room_id, room_name, code_hash, invite_url, history_mode, creator_webid)
            if creator_webid:
                self._store.add_room_member(room_id, creator_webid)
            import uuid as _uuid_inv
            self._store.create_room_invite(
                str(_uuid_inv.uuid4()), room_id, code_hash,
                uses_left=_invite_uses,
                expires_at=_invite_expires_at,
            )
        asyncio.create_task(self._init_room_on_pod(
            room_id,
            room_name,
            creator_webid,
            code=code_hash,
            history_mode=history_mode,
        ))
        await websocket.send(json.dumps({
            "type": "room_created",
            "room_id": room_id,
            "name": room_name,
            "code": code,          # plaintext code sent to client for sharing
            "invite_url": invite_url,
        }))

    async def _handle_join_room(self, websocket, data: dict) -> None:
        code = (data.get("code") or "").strip()
        # Rate-limit join attempts per IP before doing any hash computation
        _ip = ""
        _meta = getattr(self, "_session_meta", {}).get(websocket, {})
        _ip = _meta.get("ip_addr", "")
        code_hash = self._hmac_invite_code(code) if code else ""
        if self._store and code_hash and _ip:
            _attempts = self._store.count_recent_join_attempts(code_hash, _ip, window_s=60.0)
            if _attempts >= 10:
                await websocket.send(json.dumps({"type": "error", "message": "Too many join attempts. Try again later."}))
                return
            self._store.record_join_attempt(code_hash, _ip)
        # Look up by hash (new HMAC flow) then fall back to plaintext (legacy rooms)
        room_id = self._room_codes.get(code_hash) or self._room_codes.get(code)

        # Per-room and global scoped rate limits (v2 — room-hint aware)
        if self._store and _ip:
            _room_hint = room_id or code_hash or code
            _per_room_failures = self._store.count_recent_join_attempts_v2(_ip, _room_hint, window_s=60.0)
            if _per_room_failures >= 5:
                await websocket.send(json.dumps({
                    "type": "error", "message": "join_rate_limited", "retry_after": 60,
                }))
                return
            _global_failures = self._store.count_recent_join_attempts_global_v2(_ip, window_s=60.0)
            if _global_failures >= 20:
                await websocket.send(json.dumps({
                    "type": "error", "message": "join_rate_limited", "retry_after": 60,
                }))
                return

        if room_id and room_id in self._local_rooms:
            joiner_webid = self._client_webids.get(websocket, "")
            if self._store and joiner_webid and self._store.is_room_banned(room_id, joiner_webid):
                await websocket.send(json.dumps({"type": "error", "message": "banned_from_room"}))
                return
            room = self._local_rooms[room_id]
            room["members"].add(websocket)
            logger.info(f"Client joined local room {room_id} via code {code}")
            if self._store and joiner_webid:
                self._store.add_room_member(room_id, joiner_webid)
            if joiner_webid:
                asyncio.ensure_future(self._grant_pod_room_read(room_id, joiner_webid))
            await websocket.send(json.dumps({
                "type": "room_joined",
                "room_id": room_id,
                "name": room["name"],
                "code": code,
                "invite_url": room.get("invite_url", ""),
            }))
            if room.get("history_mode") == "all":
                history = (
                    self._store.get_messages(room_id, limit=200)
                    if self._store else room["messages"]
                )
                for msg in history:
                    payload = msg if "type" in msg else {
                        "type": "message", "source": msg["thread_type"],
                        "thread_id": msg["thread_id"],
                        "from_webid": msg["from_webid"],
                        "from_display_name": msg.get("from_display_name"),
                        "content": msg["content"],
                        "timestamp": msg["timestamp"],
                        "message_id": msg["message_id"],
                        "reply_to_id": msg.get("reply_to_id"),
                        "local": True,
                    }
                    try:
                        await websocket.send(json.dumps(payload))
                    except Exception:
                        pass
            join_event = {
                "type": "room_member_joined",
                "room_id": room_id,
                "name": room["name"],
                "webid": self._client_webids.get(websocket, ""),
            }
            for ws in list(room["members"]):
                if ws != websocket and ws in self.clients:
                    try:
                        await ws.send(json.dumps(join_event))
                    except Exception:
                        pass
            # R19: trigger sender key exchange so the new member gets room E2E keys
            if joiner_webid:
                try:
                    await self._notify_new_member_sender_keys(room_id, joiner_webid)
                except Exception as _sk_exc:
                    logger.warning("sender_key notify on join failed: %s", _sk_exc)
        else:
            # Record failed attempt for per-room and global rate limiting
            if self._store and _ip:
                _room_hint = room_id or code_hash or code
                self._store.record_join_attempt_v2(_ip, _room_hint)
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"No room found with invite code \"{code}\". Make sure the room creator's gateway is running.",
            }))

    async def _handle_kick_member(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        target_webid = data.get("webid", "")
        if not self._check_room_permission(websocket, room_id, role="owner"):
            await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can kick members"}))
            return
        if room_id and target_webid:
            if self._store:
                self._store.remove_room_member(room_id, target_webid)
            if room_id in self._local_rooms:
                kicked_ws = self._any_socket(target_webid)
                if kicked_ws:
                    self._local_rooms[room_id]["members"].discard(kicked_ws)
            target_ws = self._any_socket(target_webid)
            if target_ws:
                try:
                    await target_ws.send(json.dumps({
                        "type": "kicked_from_room",
                        "room_id": room_id,
                    }))
                except Exception:
                    pass
            await websocket.send(json.dumps({
                "type": "member_kicked",
                "room_id": room_id,
                "webid": target_webid,
            }))
            # R17: re-key room so kicked member cannot decrypt future messages
            try:
                from .room_rekey import rotate_room_key, build_room_key_update_event
                remaining = list(self._store.get_room_members(room_id)) if self._store else []
                rekey_result = rotate_room_key(room_id, remaining, store=self._store)
                rekey_event = build_room_key_update_event(rekey_result)
                if room_id in self._local_rooms:
                    for ws in list(self._local_rooms[room_id]["members"]):
                        try:
                            sealed = rekey_result["sealed_keys"].get(
                                self._client_webids.get(ws, ""), None
                            )
                            payload = {**rekey_event, "sealed_key": sealed}
                            await ws.send(json.dumps(payload))
                        except Exception:
                            pass
            except Exception as _rk_exc:
                logger.warning("room_rekey after kick failed: %s", _rk_exc)
            # R19: rotate sender keys so removed member cannot decrypt E2E group messages
            try:
                await self._trigger_sender_key_rotation(room_id, target_webid)
            except Exception as _sk_exc:
                logger.warning("sender_key_rotation after kick failed: %s", _sk_exc)

    async def _handle_ban_member(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        target_webid = data.get("webid", "")
        reason = str(data.get("reason", ""))[:200]
        caller_webid = self._client_webids.get(websocket, "")
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        if not target_webid or not self._store:
            return
        self._store.ban_room_member(room_id, target_webid, caller_webid, reason)
        # Also kick if currently online
        room = self._local_rooms.get(room_id, {})
        target_ws = self._any_socket(target_webid)
        if target_ws and target_ws in room.get("members", set()):
            room["members"].discard(target_ws)
            if self._store:
                self._store.remove_room_member(room_id, target_webid)
            try:
                await target_ws.send(json.dumps({"type": "kicked_from_room", "room_id": room_id,
                                                  "message": "You were banned from this room"}))
            except Exception:
                pass
        display_name = self._store.get_display_name(target_webid) or target_webid[:12]
        event = json.dumps({"type": "member_banned", "room_id": room_id,
                            "webid": target_webid, "display_name": display_name, "reason": reason})
        for ws in list(self._local_rooms.get(room_id, {}).get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        self._relay_room_moderation(room_id, "ban", target_webid, caller_webid, reason=reason)

    async def _handle_unban_member(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        target_webid = data.get("webid", "")
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        if self._store:
            self._store.unban_room_member(room_id, target_webid)
        event = json.dumps({"type": "member_unbanned", "room_id": room_id, "webid": target_webid})
        for ws in list(self._local_rooms.get(room_id, {}).get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        self._relay_room_moderation(room_id, "unban", target_webid, self._client_webids.get(websocket, ""))

    async def _handle_mute_member(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        target_webid = data.get("webid", "")
        duration_seconds = data.get("duration_seconds")
        caller_webid = self._client_webids.get(websocket, "")
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        import time as _t
        expires_at = (_t.time() + float(duration_seconds)) if duration_seconds else None
        if self._store:
            self._store.mute_room_member(room_id, target_webid, caller_webid, expires_at)
        event = json.dumps({"type": "member_muted", "room_id": room_id,
                            "webid": target_webid, "expires_at": expires_at})
        for ws in list(self._local_rooms.get(room_id, {}).get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        self._relay_room_moderation(room_id, "mute", target_webid, caller_webid, expires_at=expires_at)

    async def _handle_unmute_member(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        target_webid = data.get("webid", "")
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        if self._store:
            self._store.unmute_room_member(room_id, target_webid)
        event = json.dumps({"type": "member_unmuted", "room_id": room_id, "webid": target_webid})
        for ws in list(self._local_rooms.get(room_id, {}).get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        self._relay_room_moderation(room_id, "unmute", target_webid, self._client_webids.get(websocket, ""))

    def _relay_room_moderation(self, room_id: str, action: str, target_webid: str,
                               caller_webid: str, reason: str = "", expires_at=None) -> None:
        """Fan a moderation action out to federated member gateways (R-C1)."""
        if not self._store or not target_webid:
            return
        _seen: set = set()
        for _fm in (self._store.get_federated_room_members(room_id) or []):
            _gw = _fm.get("gateway_url", "")
            if not _gw or _gw in _seen:
                continue
            _seen.add(_gw)
            _payload = {
                "content_type": "room_moderation", "action": action,
                "room_id": room_id, "webid": target_webid, "from_webid": caller_webid,
            }
            if reason:
                _payload["reason"] = reason
            if expires_at is not None:
                _payload["expires_at"] = expires_at
            asyncio.create_task(self._relay_ephemeral(_gw, _payload))

    async def _handle_get_room_bans(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        bans = self._store.get_room_bans(room_id) if self._store else []
        for b in bans:
            b["display_name"] = (self._store.get_display_name(b["banned_did"]) if self._store else None) or b["banned_did"][:12]
        await websocket.send(json.dumps({"type": "room_bans", "room_id": room_id, "bans": bans}))

    async def _handle_get_message_readers(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        room_id = data.get("room_id", "")
        if not message_id or not self._store:
            await websocket.send(json.dumps({"type": "message_readers", "message_id": message_id, "readers": []}))
            return
        # Permission: caller must be a member
        if room_id and room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id].get("members", set()):
                await websocket.send(json.dumps({"type": "message_readers", "message_id": message_id, "readers": []}))
                return
        readers = self._store.get_message_readers(message_id)
        for r in readers:
            r["display_name"] = self._store.get_display_name(r["receiver_webid"]) or r["receiver_webid"][:12]
        await websocket.send(json.dumps({"type": "message_readers", "message_id": message_id, "readers": readers}))

    async def _handle_pin_message(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        thread_id = data.get("thread_id", "")
        room_id = self._strip_thread_prefix(thread_id)
        if not self._check_room_permission(websocket, room_id, role="owner"):
            await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can pin messages"}))
            return
        if room_id in self._local_rooms and self._store:
            pinner_webid = self._client_webids.get(websocket, "")
            msg_row = self._store.get_messages_by_ids([message_id])
            content = (msg_row[0].get("content", "") if msg_row else data.get("content", ""))
            self._store.save_pin(room_id, message_id, pinner_webid, content)
            asyncio.create_task(self._sync_pin_to_pod(room_id, message_id, pinner_webid, content))
        client = None
        if thread_id in self.dm_clients:
            _, client = self.dm_clients[thread_id]
        elif thread_id in self.room_memberships:
            _, client = self.room_memberships[thread_id]
        if client:
            try:
                from .pins import pin_message as _pin
                _pin(client, thread_id, message_id)
            except Exception as exc:
                logger.debug(f"pin_message failed: {exc}")
        await websocket.send(json.dumps({"type": "message_pinned", "message_id": message_id, "thread_id": thread_id}))

    async def _handle_get_pins(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        room_id = self._strip_thread_prefix(thread_id)
        pins = []
        if room_id in self._local_rooms and self._store:
            stored = self._store.get_pins(room_id)
            pins = [{"message_id": p["message_id"], "pinned_by": p["pinned_by"],
                     "pinned_at": p["pinned_at"], "content": p.get("content", "")}
                    for p in stored]
        else:
            client = None
            if thread_id in self.dm_clients:
                _, client = self.dm_clients[thread_id]
            elif thread_id in self.room_memberships:
                _, client = self.room_memberships[thread_id]
            if client:
                try:
                    from .pins import get_pinned_messages
                    pins = [
                        {"message_id": p.message_id, "pinned_by": p.pinned_by, "pinned_at": p.pinned_at}
                        for p in get_pinned_messages(client, thread_id)
                    ]
                except Exception as exc:
                    logger.debug(f"get_pins failed: {exc}")
        await websocket.send(json.dumps({"type": "pins", "thread_id": thread_id, "pins": pins}))

    async def _handle_unpin_message(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        message_id = data.get("message_id", "")
        room_id = self._strip_thread_prefix(thread_id)
        if room_id in self._local_rooms:
            if not self._check_room_permission(websocket, room_id, role="owner"):
                await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can unpin messages"}))
                return
        if room_id in self._local_rooms and self._store:
            self._store.remove_pin(room_id, message_id)
            asyncio.create_task(self._delete_pin_from_pod(room_id, message_id))
        event = {"type": "unpinned", "thread_id": thread_id, "message_id": message_id}
        if room_id in self._local_rooms:
            room = self._local_rooms[room_id]
            for ws in list(room["members"]):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
        else:
            await websocket.send(json.dumps(event))

    async def _handle_set_disappear_timer(self, websocket, data: dict) -> None:
        # thread_id is a room_id for rooms, or a DM cert_id for DMs — the client
        # sends both under "room_id".
        thread_id = data.get("room_id", "")
        ms = data.get("ms", 0)
        caller = self._client_webids.get(websocket, "")
        if not caller:
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        try:
            ms = max(0, int(ms))
        except (TypeError, ValueError):
            await websocket.send(json.dumps({"type": "error", "message": "Invalid timer value"}))
            return

        if thread_id in self._local_rooms:
            # Room: only the owner may change it.
            if not self._check_room_permission(websocket, thread_id, role="owner"):
                await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can set the disappear timer"}))
                return
            self._room_disappear_timers[thread_id] = ms
            if self._store:
                self._store.set_room_disappear_timer(thread_id, ms)
            event = json.dumps({"type": "disappear_timer_updated", "room_id": thread_id, "ms": ms})
            for ws in list(self._local_rooms.get(thread_id, {}).get("members", set())):
                try:
                    await ws.send(event)
                except Exception:
                    pass
            return

        # DM thread: either participant may set it. Verify the caller is in it,
        # then wire it into the DM-expiry loop (this was previously unreachable —
        # the room-owner check above rejected DMs, so DM timers never worked).
        peer = None
        if self._store:
            for _t in self._store.get_dm_threads(owner_webid=caller):
                if _t.get("thread_id") == thread_id:
                    peer = _t.get("peer_webid")
                    break
        if peer is None:
            await websocket.send(json.dumps({"type": "error", "message": "Not a participant in this thread"}))
            return
        self._dm_disappear_timers[thread_id] = ms
        if self._store:
            self._store.set_room_disappear_timer(thread_id, ms)  # shared KV table, keyed by id
        event = json.dumps({"type": "disappear_timer_updated", "room_id": thread_id, "ms": ms})
        for _identity in {caller, peer}:
            if _identity:
                await self._send_to_identity(_identity, event)

    async def _handle_get_disappear_timer(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        ms = self._room_disappear_timers.get(room_id, 0) or self._dm_disappear_timers.get(room_id, 0)
        if not ms and self._store:
            ms = self._store.get_room_disappear_timer(room_id)
        await websocket.send(json.dumps({
            "type": "disappear_timer",
            "room_id": room_id,
            "ms": ms,
        }))

    async def _handle_send_voice_message(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        audio_b64 = data.get("audio_b64", "")
        sender = self._client_webids.get(websocket)
        if not sender or not thread_id or not audio_b64:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_voice_payload"}))
            return
        # Validate and clamp duration_ms to [250, 60000]
        try:
            duration_ms = int(data.get("duration_ms", 0))
        except (TypeError, ValueError):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_voice_payload"}))
            return
        if not (250 <= duration_ms <= 60_000):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_voice_payload"}))
            return
        # Validate audio_b64 decodes properly
        if len(audio_b64) > 700_000:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_voice_payload"}))
            return
        import base64 as _b64v, binascii as _biv
        try:
            _b64v.b64decode(audio_b64, validate=True)
        except _biv.Error:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_voice_payload"}))
            return
        import uuid as _uuid_voice
        message_id = str(_uuid_voice.uuid4())
        display_name = self._display_names.get(websocket, "")
        ts = datetime.now(timezone.utc).isoformat()
        event = {
            "type": "message",
            "source": "local_room" if thread_id in self._local_rooms else "local_dm",
            "thread_id": thread_id,
            "message_id": message_id,
            "from_webid": sender,
            "from_display_name": display_name,
            "content": "",
            "content_type": "audio",
            "audio_b64": audio_b64,
            "duration_ms": duration_ms,
            "timestamp": ts,
            "local": True,
        }
        if thread_id in self._local_rooms:
            for ws in list(self._local_rooms[thread_id].get("members", set())):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
        if self._store:
            self._store.save_voice_message(
                message_id, thread_id,
                "local_room" if thread_id in self._local_rooms else "local_dm",
                sender, display_name, audio_b64, duration_ms, ts,
            )

    async def _handle_search(self, websocket, data: dict) -> None:
        query = (data.get("query") or "").strip()
        webid = self._client_webids.get(websocket)
        if not webid or not query:
            await websocket.send(json.dumps({"type": "search_results", "query": query or "", "results": [], "next_offset": 0}))
            return
        member_threads: list = [
            rid for rid, room in self._local_rooms.items()
            if websocket in room.get("members", set())
        ]
        if self._store:
            dm_thread_ids = [t["thread_id"] for t in self._store.get_dm_threads(owner_webid=webid)]
            member_threads = list(set(member_threads) | set(dm_thread_ids))

        # Optional filters from client
        filter_thread_id = data.get("thread_id") or None
        filter_from_webid = data.get("from_webid") or None
        filter_before = data.get("before") or None
        filter_after = data.get("after") or None
        limit = min(int(data.get("limit") or 50), 100)
        offset = max(int(data.get("offset") or 0), 0)

        results = []
        next_offset = offset
        if self._store:
            rows = self._store.search_messages(
                query,
                member_threads if not filter_thread_id else None,
                limit=limit,
                offset=offset,
                thread_id=filter_thread_id,
                from_webid=filter_from_webid,
                before=filter_before,
                after=filter_after,
            )
            next_offset = offset + len(rows)
            for row in rows:
                room = self._local_rooms.get(row["thread_id"], {})
                thread_name = room.get("name") or row["thread_id"]
                results.append({
                    "message_id": row["message_id"],
                    "thread_id": row["thread_id"],
                    "thread_name": thread_name,
                    "content": row["content"],
                    "from_webid": row["from_webid"],
                    "from_display_name": row.get("from_display_name", ""),
                    "timestamp": row["timestamp"],
                })
        else:
            q = query.lower()
            for rid in member_threads:
                for msg in self._local_rooms.get(rid, {}).get("messages", []):
                    if q in (msg.get("content") or "").lower():
                        results.append({
                            "message_id": msg.get("message_id", ""),
                            "thread_id": rid,
                            "thread_name": self._local_rooms[rid].get("name", rid),
                            "content": msg.get("content", ""),
                            "from_webid": msg.get("from_webid", ""),
                            "from_display_name": msg.get("from_display_name", ""),
                            "timestamp": msg.get("timestamp", ""),
                        })
                        if len(results) >= limit:
                            break
            next_offset = offset + len(results)
        await websocket.send(json.dumps({
            "type": "search_results",
            "query": query,
            "results": results,
            "next_offset": next_offset,
        }))

    async def _handle_typing(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        room_id = data.get("room_id")
        typing_event = {
            "type": "typing",
            "cert_id": cert_id,
            "room_id": room_id,
            "from_webid": self._client_webids.get(websocket, self.agent.identity_pub_bytes.hex()),
        }
        if room_id and room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id]["members"]:
                return
            for ws in list(self._local_rooms[room_id]["members"]):
                if ws != websocket:
                    try:
                        await ws.send(json.dumps(typing_event))
                    except Exception:
                        pass
        elif cert_id:
            if self._store:
                threads = [t for t in self._store.get_dm_threads() if t["thread_id"] == cert_id]
                if threads:
                    peer_webid = threads[0].get("peer_webid", "")
                    peer_ws = self._any_socket(peer_webid) if peer_webid else None
                    if peer_ws and peer_ws != websocket:
                        await peer_ws.send(json.dumps(typing_event))
                    elif peer_webid:
                        # Peer is on a different gateway — relay ephemeral typing event
                        peer_gw = self._resolve_peer_gateway(peer_webid)
                        if peer_gw:
                            asyncio.create_task(self._relay_ephemeral(peer_gw, {
                                "content_type": "typing",
                                "from_webid": typing_event["from_webid"],
                                "cert_id": cert_id,
                            }))
        # no room_id and no cert_id — drop silently

    async def _handle_schedule_message(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        content = data.get("content", "").strip()
        send_at_iso = data.get("send_at", "")
        actor = self._client_webids.get(websocket)
        if len(content.encode("utf-8")) > 4_096:
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "content_too_large"}))
            return
        if not actor or not thread_id or not content or not send_at_iso:
            await websocket.send(json.dumps({"type": "error", "message": "Missing fields"}))
            return
        try:
            send_at_dt = datetime.fromisoformat(send_at_iso)
            send_at = send_at_dt.timestamp()
        except ValueError:
            await websocket.send(json.dumps({"type": "error", "message": "Invalid send_at timestamp"}))
            return
        if send_at <= time.time():
            await websocket.send(json.dumps({"type": "error", "message": "send_at must be in the future"}))
            return
        if send_at > time.time() + 365 * 86400:
            await websocket.send(json.dumps({"type": "error", "message": "Cannot schedule more than 1 year ahead"}))
            return
        import uuid as _uuid_sched
        sched_id = str(_uuid_sched.uuid4())
        sched = {
            "id": sched_id,
            "thread_id": thread_id,
            "from_webid": actor,
            "content": content,
            "send_at": send_at,
            "created_at": time.time(),
        }
        if self._store:
            self._store.save_scheduled_message(sched)
        if not hasattr(self, '_scheduled_messages'):
            self._scheduled_messages = []
        self._scheduled_messages.append(sched)
        await websocket.send(json.dumps({
            "type": "message_scheduled",
            "id": sched_id,
            "thread_id": thread_id,
            "send_at": send_at_iso,
            "content_preview": content[:60],
        }))

    async def _handle_list_scheduled(self, websocket, data: dict) -> None:
        actor = self._client_webids.get(websocket)
        if not actor or not self._store:
            await websocket.send(json.dumps({"type": "scheduled_list", "items": []}))
            return
        items = self._store.get_scheduled_messages_for_user(actor)
        await websocket.send(json.dumps({
            "type": "scheduled_list",
            "items": [{"id": i["id"], "thread_id": i["thread_id"],
                       "content_preview": i["content"][:60],
                       "send_at": datetime.fromtimestamp(i["send_at"], tz=timezone.utc).isoformat()}
                      for i in items],
        }))

    async def _handle_cancel_scheduled(self, websocket, data: dict) -> None:
        sched_id = data.get("id", "")
        actor = self._client_webids.get(websocket)
        if not actor or not sched_id or not self._store:
            return
        ok = self._store.cancel_scheduled_message(sched_id, actor)
        await websocket.send(json.dumps({
            "type": "scheduled_cancelled" if ok else "error",
            "id": sched_id,
            "message": "" if ok else "Not found or not yours",
        }))

    async def _handle_set_member_role(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        target_webid = data.get("webid", "")
        role = data.get("role", "member")
        actor = self._client_webids.get(websocket)
        if not actor or not room_id or not target_webid:
            return
        if role not in ("admin", "mod", "member"):
            await websocket.send(json.dumps({"type": "error", "message": "Invalid role: must be admin, mod, or member"}))
            return
        room = self._local_rooms.get(room_id)
        if not room:
            await websocket.send(json.dumps({"type": "error", "message": "Room not found"}))
            return
        # The creator's ownership is immutable — no one may change the creator's role
        # or use this command to revoke their privileges.
        room_creator = room.get("creator_webid", "")
        if target_webid == room_creator:
            await websocket.send(json.dumps({"type": "error", "message": "Cannot change the room owner's role"}))
            return
        actor_role = "member"
        if self._store:
            actor_role = self._store.get_room_role(room_id, actor)
        if actor != room_creator and actor_role not in ("admin",):
            await websocket.send(json.dumps({"type": "error", "message": "Only admins can set roles"}))
            return
        if self._store:
            self._store.set_room_role(room_id, target_webid, role)
        for ws in list(room.get("members", set())):
            try:
                await ws.send(json.dumps({
                    "type": "member_role_updated",
                    "room_id": room_id,
                    "webid": target_webid,
                    "role": role,
                    "set_by": actor,
                }))
            except Exception:
                pass

    async def _handle_get_room_roles(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        actor = self._client_webids.get(websocket)
        if not actor or not room_id:
            return
        roles = {}
        if self._store:
            roles = self._store.get_all_room_roles(room_id)
        await websocket.send(json.dumps({
            "type": "room_roles",
            "room_id": room_id,
            "roles": roles,
        }))

    async def _handle_create_webhook(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        direction = data.get("direction", "incoming")
        url = data.get("url", "")
        bot_name = (data.get("bot_name", "Bot") or "Bot")[:32]
        actor = self._client_webids.get(websocket)
        if not self._check_room_permission(websocket, thread_id, role="owner"):
            await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can create webhooks"}))
            return
        if direction not in ("incoming", "outgoing"):
            return
        if direction == "outgoing" and not url.startswith("https://"):
            await websocket.send(json.dumps({"type": "error", "message": "Outgoing URL must be HTTPS"}))
            return
        if direction == "outgoing" and self._store:
            _existing_out = self._store.get_webhooks_for_thread(thread_id, "outgoing")
            if len(_existing_out) >= 3:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "Webhook limit reached (max 3 outgoing per room)",
                }))
                return
        import uuid as _uuid_wh
        wh_token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        wh_id = str(_uuid_wh.uuid4())
        wh = {
            "id": wh_id,
            "thread_id": thread_id,
            "owner_webid": actor,
            "direction": direction,
            "token": wh_token,
            "url": url,
            "bot_name": bot_name,
            "created_at": time.time(),
        }
        if self._store:
            self._store.create_webhook(wh)
        response = {"type": "webhook_created", "id": wh_id, "direction": direction, "bot_name": bot_name}
        if direction == "incoming":
            http_base = (self.config.public_url or "http://localhost:8080") \
                .replace("wss://", "https://").replace("ws://", "http://")
            response["webhook_url"] = f"{http_base}/webhook/{wh_token}"
            response["token"] = wh_token
        else:
            response["secret"] = wh_token
            response["url"] = url
        await websocket.send(json.dumps(response))

    async def _handle_list_webhooks(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        actor = self._client_webids.get(websocket)
        if not actor or not self._store:
            return
        hooks = (self._store.get_webhooks_for_thread(thread_id, "incoming") +
                 self._store.get_webhooks_for_thread(thread_id, "outgoing"))
        mine = [h for h in hooks if h["owner_webid"] == actor]
        await websocket.send(json.dumps({
            "type": "webhook_list",
            "thread_id": thread_id,
            "webhooks": [{"id": h["id"], "direction": h["direction"],
                          "bot_name": h["bot_name"], "url": h.get("url", "")} for h in mine],
        }))

    async def _handle_delete_webhook(self, websocket, data: dict) -> None:
        wh_id = data.get("id", "")
        actor = self._client_webids.get(websocket)
        if not actor or not self._store:
            return
        ok = self._store.deactivate_webhook(wh_id, actor)
        await websocket.send(json.dumps({
            "type": "webhook_deleted" if ok else "error",
            "id": wh_id,
            "message": "" if ok else "Not found or not yours",
        }))

    async def _handle_rotate_webhook(self, websocket, data: dict) -> None:
        """Rotate a webhook's secret token (owner-only)."""
        wh_id = data.get("id", "")
        actor = self._client_webids.get(websocket)
        if not actor or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "Cannot rotate: not authenticated"}))
            return
        new_token = self._store.rotate_webhook_token(wh_id, actor)
        if new_token is None:
            await websocket.send(json.dumps({"type": "error", "message": "Webhook not found or not owner"}))
            return
        if self._store:
            self._store.save_security_event(
                "webhook_rotated", "info",
                webid=actor, ip=None,
                details=f"webhook {wh_id} token rotated",
            )
        await websocket.send(json.dumps({
            "type": "webhook_rotated",
            "id": wh_id,
            "token": new_token,
        }))

    # ------------------------------------------------------------------
    # Group E2E via Sender Keys (R18, schema v44)
    # ------------------------------------------------------------------

    async def _handle_upload_sender_key(self, websocket, data: dict) -> None:
        """Store caller's sender key state for a room."""
        sender_webid = self._client_webids.get(websocket, "")
        room_id = data.get("room_id", "")
        chain_key_b64 = data.get("chain_key_b64", "")
        iteration = int(data.get("iteration", 0))
        if not sender_webid or not room_id or not chain_key_b64 or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        self._store.save_sender_key(room_id, sender_webid, chain_key_b64, iteration)
        asyncio.create_task(self._sync_sender_key_to_pod(room_id, sender_webid, chain_key_b64, iteration))
        await websocket.send(json.dumps({
            "type": "sender_key_uploaded",
            "room_id": room_id,
            "sender_webid": sender_webid,
        }))

    async def _handle_get_sender_key(self, websocket, data: dict) -> None:
        """Return the stored sender key for a specific room + sender."""
        room_id = data.get("room_id", "")
        sender_webid = data.get("sender_webid", "")
        if not self._store or not room_id or not sender_webid:
            await websocket.send(json.dumps({"type": "sender_key", "key": None}))
            return
        key = self._store.get_sender_key(room_id, sender_webid)
        await websocket.send(json.dumps({"type": "sender_key", "room_id": room_id, "key": key}))

    async def _handle_distribute_sender_key(self, websocket, data: dict) -> None:
        """Distribute sealed sender keys to a list of room members.

        The client computes the sealed packets locally (via sender_keys.distribute_sender_key)
        and sends them in a dict {webid: sealed_b64}.  The gateway relays each sealed
        packet to the appropriate recipient sockets.
        """
        sender_webid = self._client_webids.get(websocket, "")
        room_id = data.get("room_id", "")
        distribution = data.get("distribution", {})
        if not sender_webid or not room_id or not distribution:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        delivered: list[str] = []
        for target_webid, sealed_b64 in distribution.items():
            event = json.dumps({
                "type": "sender_key_received",
                "room_id": room_id,
                "from_webid": sender_webid,
                "sealed_b64": sealed_b64,
            })
            for ws in self._sockets_for(target_webid):
                try:
                    await ws.send(event)
                    delivered.append(target_webid)
                except Exception:
                    pass
        await websocket.send(json.dumps({
            "type": "sender_key_distributed",
            "room_id": room_id,
            "delivered_to": delivered,
        }))

    async def _handle_ack_sender_key_rotation(self, websocket, data: dict) -> None:
        """Client confirms receipt of new sender key after a rotation event."""
        webid = self._client_webids.get(websocket, "")
        room_id = data.get("room_id", "")
        if not webid or not room_id:
            return
        await websocket.send(json.dumps({
            "type": "sender_key_rotation_acked",
            "room_id": room_id,
            "webid": webid,
        }))

    async def _trigger_sender_key_rotation(self, room_id: str, removed_webid: str) -> None:
        """Emit a sender_key_rotation event to all remaining members after a removal.

        Clears stored sender keys for the room so the next upload_sender_key from
        any member becomes the new key for the room.
        """
        if self._store:
            self._store.delete_sender_keys_for_room(room_id)
            asyncio.create_task(self._delete_sender_keys_for_room_from_pod(room_id))

        # Collect remaining member webids
        remaining: list[str] = []
        if self._store:
            remaining = [w for w in self._store.get_room_members(room_id) if w != removed_webid]
        elif room_id in self._local_rooms:
            remaining = [
                self._client_webids.get(ws, "")
                for ws in self._local_rooms[room_id].get("members", set())
            ]
            remaining = [w for w in remaining if w and w != removed_webid]

        # Determine the next expected epoch (1 since all keys were deleted; clients
        # should treat any key they distribute after rotation as epoch 1)
        next_epoch = 1

        rotation_event = json.dumps({
            "type": "sender_key_rotation",
            "room_id": room_id,
            "reason": "member_removed",
            "removed_webid": removed_webid,
            "remaining_members": remaining,
            "next_epoch": next_epoch,
        })
        for member_webid in remaining:
            for ws in self._sockets_for(member_webid):
                try:
                    await ws.send(rotation_event)
                except Exception:
                    pass

    async def _notify_new_member_sender_keys(self, room_id: str, new_member_webid: str) -> None:
        """Tell existing members and the new member to exchange sender keys."""
        existing_members: list[str] = []
        if self._store:
            existing_members = [w for w in self._store.get_room_members(room_id) if w != new_member_webid]
        elif room_id in self._local_rooms:
            existing_members = [
                self._client_webids.get(ws, "")
                for ws in self._local_rooms[room_id].get("members", set())
            ]
            existing_members = [w for w in existing_members if w and w != new_member_webid]

        # Tell existing members that the new member joined and needs their sender keys
        join_notice = json.dumps({
            "type": "new_member_joined",
            "room_id": room_id,
            "new_member_webid": new_member_webid,
        })
        for member_webid in existing_members:
            for ws in self._sockets_for(member_webid):
                try:
                    await ws.send(join_notice)
                except Exception:
                    pass

        # Tell the new member which existing senders' keys they need
        for ws in self._sockets_for(new_member_webid):
            try:
                await ws.send(json.dumps({
                    "type": "sender_key_distribution_needed",
                    "room_id": room_id,
                    "members": existing_members,
                }))
            except Exception:
                pass
