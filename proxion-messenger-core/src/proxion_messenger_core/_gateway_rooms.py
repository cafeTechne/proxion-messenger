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
import re
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from .command_validation import seq_int
from typing import Optional

from .room import RoomMembership

logger = logging.getLogger("proxion_messenger_core.gateway")

# Per-room member cap for local (pod-free) rooms. Joins over this are rejected;
# existing members reconnecting are unaffected.
_MAX_ROOM_MEMBERS = 500

# Per-message edit-history cap. Each edit appends a message_edits row holding the
# previous and new content, so an unbounded edit loop is a disk-growth vector.
_MAX_EDITS_PER_MESSAGE = 100
# In-memory history kept per room when the gateway runs without a store.
_ROOM_MEMORY_CAP = 1000

# Upper bound on a disappearing-message timer, in milliseconds (365 days). The
# 30s expiry sweep builds datetime.now() - timedelta(milliseconds=ms); an
# unbounded ms raises OverflowError and would abort the whole tick, so both set
# entry points clamp to this ceiling, which stays well within a representable
# timedelta while covering every legitimate seconds-to-days timer.
_MAX_DISAPPEAR_MS = 365 * 24 * 60 * 60 * 1000


def _expire_tick_seconds() -> float:
    """Seconds between disappearing-message sweeps (PROXION_EXPIRE_TICK_S, for
    tests that wait on an expiry; default 30)."""
    import os
    try:
        return min(max(float(os.environ.get("PROXION_EXPIRE_TICK_S", "30")), 0.5), 300.0)
    except ValueError:
        return 30.0


def _disappear_cutoff(ms) -> Optional[str]:
    """Return the ISO cutoff for a disappearing timer, or None to skip.

    Clamps ms to _MAX_DISAPPEAR_MS and swallows an overflow so a single poisoned
    (already-persisted) timer row can never raise inside the expiry sweep and
    abort the whole tick — that one thread is skipped, the rest still expire."""
    from datetime import timedelta
    try:
        ms = min(int(ms), _MAX_DISAPPEAR_MS)
        if ms <= 0:
            return None
        return (datetime.now(timezone.utc) - timedelta(milliseconds=ms)).isoformat()
    except (OverflowError, TypeError, ValueError, OSError):
        return None

# Real message ids are UUIDs, "local-<hex>", or urlsafe tokens, so they live
# within [word chars, dot, hyphen]. A client-supplied id reaches a pod URI
# (stash://pod/rooms/<rid>/messages/<message_id>.json), so an id containing a
# slash or a bare dot segment could climb out of the messages container and
# delete or rewrite room.json. Reject anything outside the strict charset before
# any pod call.
_MESSAGE_ID_RE = re.compile(r"^[\w.-]{1,128}$")


def _is_safe_message_id(message_id) -> bool:
    """True when *message_id* is a plain id with no path-traversal potential."""
    if not message_id or not isinstance(message_id, str):
        return False
    if message_id in (".", ".."):
        return False
    return bool(_MESSAGE_ID_RE.match(message_id))


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

    def _forget_room_on_pod(self, room_id: str) -> None:
        """Mark a deleted room as deleted on the pod so the next start does not
        restore it (with its invite code still live).

        The tombstone is queued as a pending pod op before anything is
        attempted, so a pod that is down or not yet connected still gets it on
        the next flush, and _restore_rooms_from_pod skips a room whose
        tombstone is still queued."""
        if not self._store:
            return
        try:
            from .pod_room_store import PodRoomStore
            uri = PodRoomStore(None)._room_meta_uri(room_id)
        except ValueError:
            return
        body = json.dumps(PodRoomStore.tombstone_meta(
            room_id, datetime.now(timezone.utc).isoformat()))
        self._store.record_pending_pod_op(uri, "put", body)
        if self._pod_client():
            asyncio.create_task(self._tombstone_room_on_pod(room_id, uri, body))

    async def _tombstone_room_on_pod(self, room_id: str, uri: str, body: str) -> None:
        client = self._pod_client()
        if not client:
            return
        from .pod_room_store import PodRoomStore
        loop = asyncio.get_event_loop()
        async with self._pod_sync_sem:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: client.put(uri, body.encode("utf-8"), content_type="application/json"),
                )
                self._store.clear_pending_pod_op(uri)
            except Exception as exc:
                logger.debug("room tombstone write failed [%s]: %s", room_id, exc)
                self._store.bump_pending_pod_op_attempt(uri)
                return
        try:
            await loop.run_in_executor(None, PodRoomStore(client).purge_room_contents, room_id)
        except Exception as exc:
            logger.debug("room pod purge failed [%s]: %s", room_id, exc)

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

    async def _delete_room_message_on_pod(self, room_id: str, message_id: str) -> bool:
        """Remove a room message from the pod after a local delete.

        Returns True when the pod object is gone (deleted or already absent) and
        False when the pod is unreachable, so the expiry sweep can keep the local
        row until the pod delete actually succeeds (avoids orphaning an object
        that a later backfill would re-import)."""
        client = self._pod_client()
        if not client:
            return False
        try:
            from .pod_room_store import PodRoomStore
            store = PodRoomStore(client)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, store.delete_message, room_id, message_id)
            return True
        except Exception as exc:
            logger.debug(f"pod room delete failed [{room_id}/{message_id}]: {exc}")
            return False

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

    def _is_scheduled_dm_thread(self, thread_id: str, actor: str) -> bool:
        """True when *thread_id* is a DM (cert or local) rather than a local room.
        Local DM threads are keyed by the peer identity, so a did: id is a DM."""
        if not thread_id or thread_id in self._local_rooms:
            return False
        if thread_id in self.dm_clients or thread_id.startswith("did:"):
            return True
        return self._actor_participates_in_dm(actor, thread_id)

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
        # Bound the per-tick pod-delete work so a thread with many expired ids
        # cannot wedge the loop; the remainder is handled on the next tick.
        _POD_EXPIRE_BATCH = 200
        _consecutive_failures = 0
        while True:
            await asyncio.sleep(_expire_tick_seconds())
            try:
                # --- Rooms ---
                for room_id, ms in list(self._room_disappear_timers.items()):
                    if ms <= 0:
                        continue
                    room = self._local_rooms.get(room_id)
                    if not room:
                        continue
                    cutoff = _disappear_cutoff(ms)
                    if cutoff is None:
                        continue
                    since = self._disappear_since_iso(room_id)

                    def _gone(m, cutoff=cutoff, since=since):
                        ts = m.get("timestamp", "")
                        return ts < cutoff and (not since or ts >= since)
                    expired = [m.get("message_id") for m in room.get("messages", []) if _gone(m)]
                    room["messages"] = [m for m in room.get("messages", []) if not _gone(m)]
                    # Purge the STORE unconditionally by cutoff. This was gated on
                    # the in-memory `expired` list, but a room only keeps messages
                    # in memory when history_mode=="all" — so for normal rooms
                    # `expired` was always empty and disappearing messages were
                    # NEVER deleted from persistence (they reappeared on reload).
                    if self._store and self._pod_client():
                        # Messages are written through to the pod (and re-imported
                        # on restart), so the pod object must be deleted too or the
                        # message reappears. Enumerate a bounded batch, purge each
                        # pod object best-effort, then delete exactly those rows so
                        # nothing is orphaned; the rest expires on later ticks.
                        _expired_ids = self._store.select_message_ids_before(
                            room_id, cutoff, limit=_POD_EXPIRE_BATCH, since_iso=since)
                        store_deleted = 0
                        for _mid in _expired_ids:
                            # Only drop the local row once the pod object is gone.
                            # A failed pod delete (pod unreachable) leaves the row,
                            # which the next tick re-selects and retries — so the
                            # object is never orphaned for a backfill to resurrect.
                            _pod_ok = False
                            try:
                                _pod_ok = await self._delete_room_message_on_pod(room_id, _mid)
                            except Exception:
                                _pod_ok = False
                            if _pod_ok:
                                try:
                                    await self._delete_pin_from_pod(room_id, _mid)
                                except Exception:
                                    pass
                                self._store.delete_message(_mid)
                                store_deleted += 1
                    else:
                        store_deleted = (self._store.delete_messages_before(room_id, cutoff, since)
                                         if self._store else 0)
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
                    # Store-only rooms (no in-memory copies to name individually):
                    # tell members to purge by cutoff so the feed/cache clears too.
                    if store_deleted and not expired:
                        for ws in list(room.get("members", set())):
                            try:
                                await ws.send(json.dumps({
                                    "type": "dm_messages_expired",
                                    "thread_id": room_id,
                                    "before_timestamp": cutoff,
                                    **({"since_timestamp": since} if since else {}),
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
                    cutoff = _disappear_cutoff(ms)
                    if cutoff is None:
                        continue
                    if self._pod_client():
                        # DM messages are written through to the pod (local_dms/…)
                        # and re-imported on restart; delete the pod object too.
                        _expired_ids = self._store.select_message_ids_before(
                            cert_id, cutoff, limit=_POD_EXPIRE_BATCH,
                            since_iso=self._disappear_since_iso(cert_id))
                        deleted = 0
                        for _mid in _expired_ids:
                            # Gate the local delete on pod success (see rooms
                            # branch) so a failed pod delete cannot orphan the
                            # object for a later restore to resurrect.
                            _pod_ok = False
                            try:
                                _pod_ok = await self._delete_local_dm_on_pod(cert_id, _mid)
                            except Exception:
                                _pod_ok = False
                            if _pod_ok:
                                self._store.delete_message(_mid)
                                deleted += 1
                    else:
                        deleted = self._store.delete_messages_before(
                            cert_id, cutoff, self._disappear_since_iso(cert_id))
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
        since = self._disappear_since_iso(cert_id)
        payload = json.dumps({
            "type": "dm_messages_expired",
            "thread_id": cert_id,
            "before_timestamp": cutoff,
            **({"since_timestamp": since} if since else {}),
        })
        # Only the thread's local participants, not every session on the gateway:
        # the account holding the relationship cert, and the owner/peer of a
        # did-keyed local DM thread.
        participants = set()
        try:
            _owner = self._store.get_relationship_owner_by_cert_id(cert_id)
            if _owner:
                participants.add(_owner)
            elif self._store.get_relationship_by_cert_id(cert_id):
                # Older relationship rows carry no owner: the gateway's own user.
                participants.add(self._own_gateway_did())
            for _t in self._store.get_all_dm_threads():
                if _t.get("thread_id") == cert_id:
                    participants.update(w for w in (_t.get("owner_webid"), _t.get("peer_webid")) if w)
        except Exception:
            logger.debug("resolving DM expiry participants failed", exc_info=True)
        if str(cert_id).startswith("did:key:"):
            participants.add(cert_id)
        seen = set()
        for wid in participants:
            for ws in self._sockets_for(wid):
                if id(ws) in seen:
                    continue
                seen.add(id(ws))
                try:
                    await ws.send(payload)
                except Exception:
                    pass

    async def process_link_previews(self, content: str, source: str, message_id: str,
                                    recipients: Optional[list] = None):
        """Fetch previews for URLs in a message and deliver them to the message's
        recipients (same scoping as the message itself); broadcast only as the
        unattributable fallback (single-user-safe)."""
        from .linkpreview import extract_urls, fetch_link_preview
        urls = extract_urls(content)
        for url in urls[:3]:
            preview = await fetch_link_preview(url)
            if preview:
                event = {
                    "type": "link_preview",
                    "source": source,
                    "message_id": message_id,
                    "preview": preview
                }
                if recipients:
                    payload = json.dumps(event)
                    for r in set(recipients):
                        await self._send_to_identity(r, payload)
                else:
                    await self.broadcast(event)

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
                self._prune_room_rate_counters(now)
                if key not in self._rate_counters:
                    self._rate_counters[key] = deque()

                while self._rate_counters[key] and self._rate_counters[key][0] < now - rate_limit:
                    self._rate_counters[key].popleft()

                if len(self._rate_counters[key]) > 0:
                    return f"Rate limit exceeded (min {rate_limit}s between messages)"

                self._rate_counters[key].append(now)

        return None

    # Slow-mode slots are keyed (sender, room) and were never reclaimed; relayed
    # senders make the key space remote-driven. Drop idle ones once it grows.
    _ROOM_RATE_PRUNE_AT = 1024
    _ROOM_RATE_IDLE_S = 86400

    def _prune_room_rate_counters(self, now: float) -> None:
        rc = self._rate_counters
        if len(rc) < self._ROOM_RATE_PRUNE_AT:
            return
        stale = [k for k, v in rc.items()
                 if isinstance(k, tuple) and isinstance(v, deque)
                 and (not v or now - v[-1] >= self._ROOM_RATE_IDLE_S)]
        for k in stale:
            rc.pop(k, None)

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
            _fed_owner = getattr(getattr(membership, "room", None), "owner_webid", None)
            if role == "owner":
                return _fed_owner == caller_webid
            if role == "admin":
                # Was 'any member' — same hole as local rooms (any federated
                # member could ban/mute). Restrict to the owner or a granted
                # admin until federated admin-role delegation is modeled.
                if _fed_owner == caller_webid:
                    return True
                return bool(self._store and self._store.get_room_role(room_id, caller_webid) == "admin")
            return True # non-privileged (member-level) actions: any member

        # For local (pod-free) rooms
        room = self._local_rooms.get(room_id)
        if room:
            if role == "owner":
                return room.get("creator_webid") == caller_webid
            if role == "admin":
                # Owner OR an explicitly-granted admin. Previously ANY member
                # passed here, so any member could ban/mute/unban anyone. A
                # granted admin must ALSO be a current member — a role row alone
                # survives removal, so without this a kicked/banned ex-admin who
                # reconnected could still run admin ops.
                if room.get("creator_webid") == caller_webid:
                    return True
                return bool(
                    self._store
                    and self._store.get_room_role(room_id, caller_webid) == "admin"
                    and caller_webid in self._store.get_room_members(room_id)
                )
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
                # Blocking pod write: run it off the event loop.
                await asyncio.get_running_loop().run_in_executor(None, send, msg, client)
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
                    seq_num=seq_int(data.get("seq_num") or 0),
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
            if room.get("history_mode") == "all" and not self._store:
                # Storeless rooms keep recent history in memory for late joiners
                # and search; with a store those read SQLite, so nothing is kept.
                _mem = room.setdefault("messages", [])
                _mem.append(event)
                if len(_mem) > _ROOM_MEMORY_CAP:
                    del _mem[:len(_mem) - _ROOM_MEMORY_CAP]
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
                "topic": room.get("topic", ""),
                "local": True,
            })
        if _caller_wid and self._store:
            self._attach_unread_counts(_caller_wid, [r for r in rooms if r.get("local")])
        await websocket.send(json.dumps({"type": "rooms", "rooms": rooms}))

    def _attach_unread_counts(self, webid: str, entries: list, last_reads: dict | None = None) -> None:
        """Set unread_count on each room/DM list entry for *webid*.

        The client used to send get_local_history for every thread on connect
        just to count unread messages, which with a few dozen rooms exhausted
        the per-connection command budget.
        """
        if not (self._store and webid and entries):
            return
        try:
            if last_reads is None:
                last_reads = self._store.get_all_last_reads(webid)
            counts = self._store.count_unread_by_thread(webid, [e["id"] for e in entries], last_reads)
        except Exception as exc:
            logger.debug("unread counts skipped: %s", exc)
            return
        for e in entries:
            e["unread_count"] = counts.get(e["id"], 0)

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
        elif thread_id and self._auth_enforced():
            # DM / pod-backed-room branch: get_messages(thread_id) has no scoping,
            # so gate on DM participation or room membership (the _local_rooms
            # branch above already handles live local rooms).
            _caller_wid = self._client_webids.get(websocket)
            if not self._actor_can_read_thread(websocket, _caller_wid, thread_id):
                await websocket.send(json.dumps({
                    "type": "local_history", "thread_id": thread_id, "messages": [],
                }))
                return
        # A same-gateway DM thread is readable only by the two accounts in it,
        # with or without auth enforcement (other local accounts are strangers).
        if thread_id and thread_id not in self._local_rooms and self._store:
            _parties = self._store.dm_thread_parties(thread_id)
            if _parties and self._client_webids.get(websocket) not in _parties:
                await websocket.send(json.dumps({
                    "type": "local_history", "thread_id": thread_id, "messages": [],
                }))
                return
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
                                    seq_num=seq_int(m.get("seq_num") or 0),
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
                    # Still sealed: the browser shows its own plaintext copy or
                    # an undecryptable notice, never this ciphertext.
                    **({"e2e": True} if m.get("content_type") == "e2e" else {}),
                }
                for m in raw
            ]
            self._attach_history_files(thread_id, msgs, is_room)
            # DM threads too: their reactions are stored under the thread id
            # (a sealed DM reaction as its token), and leaving them out made
            # every DM reaction vanish on reload. Access was checked above.
            # Only the reactions on this page's messages: the whole thread's
            # set made every page (and every room open) grow with its history.
            reactions = self._store.get_reactions(
                thread_id, [m["message_id"] for m in msgs]) if self._store else []

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
        # F2: a crafted message_id ("../room") reaches a pod URI, so validate the
        # charset before anything else — a bad id is rejected outright.
        if not _is_safe_message_id(message_id):
            await websocket.send(json.dumps({"type": "error", "message": "Invalid message id"}))
            return
        if thread_id in self._local_rooms and websocket not in self._local_rooms[thread_id]["members"]:
            return
        sender = self._store.get_message_sender(message_id) if self._store else None
        if sender is not None and sender != caller_webid:
            await websocket.send(json.dumps({"type": "error", "message": "Cannot delete another user's message"}))
            return
        # F2: an id with no row (get_message_sender is None) must NOT reach a pod
        # delete — that was the ownership bypass, since the guard only blocked a
        # DIFFERENT existing sender. Only touch the pod for a row the caller owns.
        _owned = sender is not None and sender == caller_webid
        if message_id and self._store:
            self._store.delete_message(message_id)
        if _owned and thread_id in self._local_rooms:
            asyncio.create_task(self._delete_room_message_on_pod(thread_id, message_id))
            # F11: a pinned message keeps a pod-side pin object holding its
            # content; drop it too so a delete cannot be undone by pin restore.
            asyncio.create_task(self._delete_pin_from_pod(thread_id, message_id))
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
            # DM thread: deliver to the two participants — locally OR, for a
            # cross-gateway peer, RELAY the delete (was _send_to_identity(peer)
            # only, so a federated peer never saw the deletion).
            # Resolve the peer: a cert-DM thread (federated) is keyed by a UUID
            # cert_id, so get_dm_threads(owner=browser_did) won't find it (dm
            # threads/relationships are keyed by the gateway did) — resolve via
            # the relationship. A local_dm thread is keyed by the peer's did.
            peer = ""
            if self._store:
                _rel = self._store.get_relationship_by_cert_id(thread_id)
                if _rel:
                    peer = _rel.get("peer_did", "")
            if not peer and str(thread_id).startswith("did:key:"):
                peer = thread_id
            payload = json.dumps(event)
            await self._send_to_identity(caller_webid, payload)
            if peer and peer != caller_webid:
                if self._sockets_for(peer):
                    await self._send_to_identity(peer, payload)
                else:
                    _pg = self._resolve_peer_gateway(peer)
                    if _pg:
                        from .didkey import pub_key_to_did as _p2d_del
                        asyncio.create_task(self._relay_ephemeral(_pg, {
                            "content_type": "dm_delete",
                            "from_webid": _p2d_del(self.agent.identity_pub_bytes),
                            "to_webid": peer,
                            "message_id": message_id,
                        }))

    async def _handle_edit_local_message(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        thread_id  = data.get("thread_id", "")
        new_content = (data.get("content") or "").strip()
        caller_webid_edit = self._client_webids.get(websocket)
        if not caller_webid_edit:
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        # E2E-sealed DM edit (content is ratchet ciphertext). Rooms are not E2E.
        from .command_validation import sealed_fields, sealed_header
        _sealed = sealed_fields(data)
        if _sealed is None or (_sealed and thread_id in self._local_rooms):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_e2e_fields"}))
            return
        # F2: a crafted id reaches the pod read-modify-write, so validate first.
        if not _is_safe_message_id(message_id):
            await websocket.send(json.dumps({"type": "error", "message": "Invalid message id"}))
            return
        if thread_id in self._local_rooms and websocket not in self._local_rooms[thread_id]["members"]:
            return
        # A muted member must not edit their own message to inject new content
        # that broadcasts + federates. Same gate as _handle_send_room.
        if (thread_id in self._local_rooms and self._store
                and self._store.is_room_muted(thread_id, caller_webid_edit)):
            await websocket.send(json.dumps({"type": "error", "message": "you_are_muted"}))
            return
        sender = self._store.get_message_sender(message_id) if self._store else None
        if sender is not None and sender != caller_webid_edit:
            await websocket.send(json.dumps({"type": "error", "message": "Cannot edit another user's message"}))
            return
        # F2: only a row the caller owns may reach the pod read-modify-write; an
        # unknown id must not be able to rewrite an arbitrary pod object.
        _owned_edit = sender is not None and sender == caller_webid_edit
        # Every edit appends a message_edits row (prev+new content), so an
        # unbounded edit loop would grow that table without limit. Refuse once a
        # message has reached the per-message edit ceiling. get_edits is a read.
        if self._store and message_id and new_content:
            if len(self._store.get_edits(message_id)) >= _MAX_EDITS_PER_MESSAGE:
                await websocket.send(json.dumps({"type": "error", "message": "edit_history_full"}))
                return
        if message_id and new_content:
            edited_at = datetime.now(timezone.utc).isoformat()
            if self._store:
                self._store.update_message(message_id, new_content, edited_at,
                                           editor_webid=caller_webid_edit or "")
            if _owned_edit and thread_id in self._local_rooms:
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
                # DM thread: deliver only to the two participants. The editor
                # id lets the peer pick the ratchet session for a sealed edit.
                event["from_webid"] = caller_webid_edit
                event.update(sealed_header(_sealed))
                participants_edit: set = {caller_webid_edit}
                if self._store:
                    dm_threads_edit = [t for t in self._store.get_dm_threads(owner_webid=caller_webid_edit)
                                       if t["thread_id"] == thread_id]
                    if dm_threads_edit:
                        participants_edit.add(dm_threads_edit[0]["peer_webid"])
                payload_edit = json.dumps(event)
                for identity in participants_edit:
                    await self._send_to_identity(identity, payload_edit)

    def _actor_participates_in_dm(self, actor: str, thread_id: str) -> bool:
        """True when *actor* is a party to DM *thread_id*.

        A local_dm thread is keyed by the PEER IDENTITY (did/webid) and each
        participant owns a dm_threads row for it; a cert-DM thread is keyed by a
        relationship certificate_id. Without this check an authenticated user
        could inject a message into another account's DM just by naming that
        account's identity as target_thread_id (rooms are already membership-
        checked; only the DM branch was missing a guard)."""
        if not actor or not thread_id or not self._store:
            return False
        # local_dm: actor owns a dm_threads row keyed by the peer identity.
        for t in self._store.get_dm_threads(owner_webid=actor):
            if t.get("thread_id") == thread_id:
                return True
        # ...or is the peer another local account opened the thread with (the
        # gateway keeps a thread id to one pair, see resolve_local_dm_thread).
        _parties = self._store.dm_thread_parties(thread_id)
        if _parties and actor in _parties:
            return True
        # explicit participant binding (thread_participant_bindings)
        if self._store.is_thread_participant_binding(thread_id, actor):
            return True
        # cert-DM: the relationship for this cert_id belongs to the actor, or
        # the actor holds a relationship with this peer identity.
        if self._store.get_relationship_owner_by_cert_id(thread_id) == actor:
            return True
        if self._store.get_relationship_owner(thread_id) == actor:
            return True
        return False

    def _local_dm_pair(self, actor: str, peer: str) -> bool:
        """True when *actor* and the did *peer* have a local_dm thread on this
        gateway, opened by either side. A DM reaction names the thread by the
        peer's did, which has no relationship row for two local accounts."""
        if not self._store or not actor or not str(peer).startswith("did:key:"):
            return False
        return (any(t.get("peer_webid") == peer for t in self._store.get_dm_threads(owner_webid=actor))
                or any(t.get("peer_webid") == actor for t in self._store.get_dm_threads(owner_webid=peer)))

    def _actor_can_read_thread(self, websocket, actor: str, thread_id: str) -> bool:
        """True when *actor* may READ thread *thread_id* — a DM party or a room
        member. Gate for read-only handlers (history/pins/timer) under
        ``_auth_enforced()``. Room membership is accepted via the live
        ``_local_rooms`` websocket set, the federated ``room_memberships`` map,
        and persistent store membership so a pod-backed room absent from
        ``_local_rooms`` is covered rather than dropped."""
        if not actor or not thread_id:
            return False
        if self._actor_participates_in_dm(actor, thread_id):
            return True
        room_id = self._strip_thread_prefix(thread_id)
        room = self._local_rooms.get(room_id) or self._local_rooms.get(thread_id)
        if room is not None and websocket in room.get("members", set()):
            return True
        if room_id in self.room_memberships or thread_id in self.room_memberships:
            return True
        if self._store and (actor in self._store.get_room_members(room_id)
                            or actor in self._store.get_room_members(thread_id)):
            return True
        return False

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

        # The actor must be able to READ the source message's thread, else they
        # could exfiltrate a message from a thread they were never party to just
        # by naming its message_id here. Gated on _auth_enforced() like the other
        # read-authz handlers: only meaningful when strangers can register.
        _source_thread = source_row.get("thread_id", "")
        if (self._auth_enforced() and _source_thread
                and not self._actor_can_read_thread(websocket, actor, _source_thread)):
            await websocket.send(json.dumps({"type": "error", "message": "Cannot read source message"}))
            return

        target_room = self._local_rooms.get(target_thread_id)
        if target_room and websocket not in target_room.get("members", set()):
            await websocket.send(json.dumps({"type": "error", "message": "Not a member of target thread"}))
            return

        # The forwarded content: the store holds only CIPHERTEXT for an E2E DM,
        # so forwarding one used to post an undecryptable blob. The forwarder's
        # client has the plaintext (it rendered it); prefer that. The actor is
        # attributed as the author of the forwarded copy, so trusting their text
        # is no different from them sending a normal message.
        _client_content = data.get("content")
        _content_is_client = isinstance(_client_content, str) and bool(_client_content)
        content = _client_content if _content_is_client else source_row.get("content", "")
        # The source author's display name may only ride along with the source
        # row's own content. Pinning it to client-supplied text would let a
        # forwarder post attacker-chosen content UNDER the original author's name.
        # When the content is client-supplied we still mark it forwarded, but with
        # no borrowed author identity.
        _forwarded_from_name = "" if _content_is_client else source_row.get("from_display_name", "")
        if len(content.encode("utf-8")) > 16_384:
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "content_too_large"}))
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
            "content": content,
            "content_type": "text",
            "forwarded": True,
            "forwarded_from_name": _forwarded_from_name,
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
            # DM target: target_thread_id is a peer identity / cert_id, NOT a
            # room. Require the actor to actually participate in that DM thread,
            # else an authenticated user could inject into another account's DM.
            if not self._actor_participates_in_dm(actor, target_thread_id):
                await websocket.send(json.dumps({
                    "type": "error", "message": "Not a participant in target thread"}))
                return
            # A block does not tear down a pre-existing DM thread, so a forward is
            # a parallel delivery a normal DM would drop. Match _handle_local_dm:
            # a blocked actor delivers nothing into the target's DM.
            if self._is_blocked_for(target_thread_id, actor):
                return
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
                seq_num=seq_int(data.get("seq_num") or 0),
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

    def _pick_successor_owner(self, room: dict, room_id: str, leaver_did: str) -> str:
        """Choose who inherits a room when its owner leaves.

        Membership is the stored member list, not the live sockets: a member
        who has the app closed is still a member and must not lose the room.
        Admins win over mods over members; ties go to the longest-standing
        member (join order). Live members missing from the store come next and
        federated members are the last resort. Returns "" when nobody is left.
        """
        stored = [w for w in (self._store.get_room_members(room_id) if self._store else [])
                  if w and w != leaver_did]
        live = []
        for ws in list(room.get("members", ())):
            d = self._client_webids.get(ws, "")
            if d and d != leaver_did and d not in stored and d not in live:
                live.append(d)
        candidates = stored + live
        if candidates:
            _role_priority = {"admin": 0, "mod": 1, "member": 2}
            _roles = self._store.get_all_room_roles(room_id) if self._store else {}
            order = {d: i for i, d in enumerate(candidates)}
            candidates.sort(key=lambda d: (_role_priority.get(_roles.get(d, "member"), 2), order[d]))
            return candidates[0]
        if self._store:
            for fm in self._store.get_federated_room_members(room_id):
                if fm.get("member_did") and fm["member_did"] != leaver_did:
                    return fm["member_did"]
        return ""

    async def _handle_leave_local_room(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if room_id not in self._local_rooms:
            await websocket.send(json.dumps({"type": "left_room", "room_id": room_id}))
        else:
            room = self._local_rooms[room_id]
            caller_did = self._client_webids.get(websocket, "")
            is_creator = room.get("creator_webid") == caller_did and caller_did

            # Evict ALL of the leaver's sockets, not just the one that sent the
            # command, so a second device does not keep receiving room traffic.
            if caller_did:
                for _ws in self._sockets_for(caller_did):
                    room["members"].discard(_ws)
            room["members"].discard(websocket)
            if caller_did and self._store:
                self._store.remove_room_member(room_id, caller_did)
                self._store.delete_room_role(room_id, caller_did)
            # Tell whoever is still in the room, so their member lists drop
            # the leaver (the counterpart of room_member_joined).
            if caller_did:
                _left_evt = json.dumps({
                    "type": "room_member_left", "room_id": room_id, "webid": caller_did,
                })
                for ws in list(room["members"]):
                    try:
                        await ws.send(_left_evt)
                    except Exception:
                        pass

            if is_creator:
                new_owner_did = self._pick_successor_owner(room, room_id, caller_did)
                if not new_owner_did:
                    self._room_codes.pop(room.get("code"), None)
                    del self._local_rooms[room_id]
                    if self._store:
                        self._store.delete_room(room_id)
                    self._forget_room_on_pod(room_id)
                    await websocket.send(json.dumps({
                        "type": "left_room", "room_id": room_id,
                        "deleted": True,
                        "reason": "Room deleted — no remaining members.",
                    }))
                else:
                    new_owner_name = self._name_for(self._any_socket(new_owner_did), new_owner_did)
                    room["creator_webid"] = new_owner_did
                    if self._store:
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
                self._forget_room_on_pod(room_id)

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
                        "from_did": caller_did,
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

    def _drop_ownership_offer(self, room_id: str, target_did: str) -> None:
        """Forget a pending ownership offer to *target_did* (kicked or banned)."""
        pending = self._pending_ownership_transfers.get(room_id)
        if pending and pending.get("to_did") == target_did:
            self._pending_ownership_transfers.pop(room_id, None)

    async def _handle_accept_ownership(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        pending = self._pending_ownership_transfers.get(room_id)
        if not pending or pending["to_ws"] != websocket:
            await websocket.send(json.dumps({"type": "error", "message": "No pending transfer for this room"}))
        else:
            room = self._local_rooms.get(room_id)
            _to = pending.get("to_did", "")
            # The offer is only good while both sides are where it found them: a
            # kicked, banned or departed target (or an owner who already handed the
            # room on) must not be able to take ownership with a stale offer.
            _stale = bool(room) and (
                websocket not in room.get("members", set())
                or self._client_webids.get(websocket, "") != _to
                or ("from_did" in pending and room.get("creator_webid") != pending["from_did"])
                or (self._store is not None and self._store.is_room_banned(room_id, _to)))
            if _stale:
                self._pending_ownership_transfers.pop(room_id, None)
                await websocket.send(json.dumps({"type": "error", "message": "No pending transfer for this room"}))
            elif not room:
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

    async def _deliver_dm_reaction(self, sender_webid: str, cert_id: str, peer_did: str,
                                   event: dict, action: str, message_id: str, emoji: str,
                                   sealed: dict | None = None) -> None:
        """Deliver a DM reaction to the peer: local sessions if here, else RELAY
        to their gateway. Federated DM reactions were silently lost — branch 2
        did _send_to_identity(cert_id) where cert_id is a UUID (federated) that
        matches no socket, with no relay. target = the cert's peer_did (federated)
        or cert_id itself (local DM threads are keyed by the peer's did)."""
        target = peer_did or (cert_id if str(cert_id).startswith("did:key:") else "")
        if not target or target == sender_webid:
            return
        if self._sockets_for(target):
            await self._send_to_identity(target, json.dumps(event))
            return
        peer_gw = self._resolve_peer_gateway(target)
        if peer_gw:
            # Cross-gateway: identify the sender by OUR GATEWAY did, which is how
            # the peer's relationship keys us (the Proxion-address did) — the
            # browser session did wouldn't match on their side. Same model as the
            # plain-DM relay.
            from .didkey import pub_key_to_did as _p2d
            asyncio.create_task(self._relay_ephemeral(peer_gw, {
                "content_type": "dm_reaction",
                "from_webid": _p2d(self.agent.identity_pub_bytes),
                "to_webid": target,
                "message_id": message_id,
                "emoji": emoji,
                "action": action,
                **(sealed or {}),
            }))

    async def _handle_add_reaction(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        room_id = data.get("room_id")
        message_id = data.get("message_id")
        emoji = data.get("emoji")
        sender_webid = self._client_webids.get(websocket, self.agent.identity_pub_bytes.hex())
        # E2E-sealed DM reaction: `emoji` is an opaque token and the sealed
        # header (forwarded to the peer, never parsed) maps it to the emoji.
        from .command_validation import sealed_fields
        _sealed = sealed_fields(data, max_content=1024)
        if _sealed is None or (_sealed and not cert_id):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_e2e_fields"}))
            return

        if room_id and room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id]["members"]:
                return
            # A muted member's reaction must not broadcast + federate. Same gate
            # as _handle_send_room (mute does not evict the socket).
            if sender_webid and self._store and self._store.is_room_muted(room_id, sender_webid):
                await websocket.send(json.dumps({"type": "error", "message": "you_are_muted"}))
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
            # Cert-DM (relay-only) reaction. The local participant of a cert DM is
            # this gateway's OWNER (account DID = the gateway/Proxion-address DID).
            _peer_did = ""
            if self._store:
                _cert_dict = self._store.get_relationship_by_cert_id(cert_id)
                if _cert_dict:
                    _peer_did = _cert_dict.get("peer_did", "")
            # Authorize the caller. When auth is enforced, _client_webids is the
            # proven account DID (== the owner/gateway DID for the owner's own
            # devices), so require the caller to be a participant. When auth is off
            # (loopback single-user dev) the check is skipped: the DID is an
            # unauthenticated self-claim AND the browser registers under a session
            # DID ≠ the gateway DID, so the check would wrongly reject the real
            # local user (this is why federated DM reactions never worked). See
            # _auth_enforced().
            # A same-gateway local_dm is keyed by the peer's did and has no
            # relationship row: allow it when the two accounts have a DM here.
            if (self._auth_enforced() and sender_webid not in (self._own_gateway_did(), _peer_did)
                    and not self._local_dm_pair(sender_webid, cert_id)):
                await websocket.send(json.dumps({"type": "error", "message": "Not a participant in this DM"}))
                return
            if self._store and message_id and emoji:
                saved = self._store.save_reaction(cert_id, message_id, emoji, sender_webid)
                if not saved:
                    await websocket.send(json.dumps({"type": "error", "message": "reaction_limit_reached"}))
                    return
            event = {
                "type": "reaction_added", "thread_id": cert_id,
                "message_id": message_id, "emoji": emoji, "from_webid": sender_webid,
                **_sealed,
            }
            await websocket.send(json.dumps(event))
            # Deliver to the peer — local sessions OR cross-gateway relay (was
            # _send_to_identity(cert_id), which silently dropped federated DM
            # reactions since cert_id is a UUID there).
            await self._deliver_dm_reaction(sender_webid, cert_id, _peer_did, event, "add", message_id, emoji,
                                             sealed=_sealed)
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
                    **_sealed,
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
            # A muted member's reaction removal must not broadcast + federate.
            # Same gate as _handle_send_room.
            if sender_webid and self._store and self._store.is_room_muted(room_id, sender_webid):
                await websocket.send(json.dumps({"type": "error", "message": "you_are_muted"}))
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
            # Cert-DM reaction removal. Same auth-gated participant check as
            # _handle_add_reaction: enforced ⇒ caller must be owner or peer.
            _peer_did_r = ""
            if self._store:
                _cert_dict_r = self._store.get_relationship_by_cert_id(cert_id)
                if _cert_dict_r:
                    _peer_did_r = _cert_dict_r.get("peer_did", "")
            if (self._auth_enforced() and sender_webid not in (self._own_gateway_did(), _peer_did_r)
                    and not self._local_dm_pair(sender_webid, cert_id)):
                await websocket.send(json.dumps({"type": "error", "message": "Not a participant in this DM"}))
                return
            if self._store and message_id and emoji:
                self._store.remove_reaction(cert_id, message_id, emoji, sender_webid)
            event = {
                "type": "reaction_removed", "thread_id": cert_id,
                "message_id": message_id, "emoji": emoji, "from_webid": sender_webid,
            }
            await websocket.send(json.dumps(event))
            await self._deliver_dm_reaction(sender_webid, cert_id, _peer_did_r, event, "remove", message_id, emoji)
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

        # F6: this handler writes a receipt (injecting the caller's WebID into the
        # readers list), writes read rows, and broadcasts/relays a read receipt.
        # Its read siblings gate on thread access; without the same guard a client
        # could mark-read an arbitrary thread it is not a party to. Reject (no-op)
        # when the actor cannot read the thread. Gated on _auth_enforced() like the
        # other read-authz handlers.
        if (self._auth_enforced() and thread_id
                and not self._actor_can_read_thread(websocket, reader_webid, thread_id)):
            return

        if self._store and reader_webid and thread_id:
            self._store.set_last_read(reader_webid, thread_id)
            await self._notify_thread_read(websocket, reader_webid, thread_id)
            # Same pod flush as update_last_read, so opening a thread needs one command.
            _rp = getattr(self, "_dirty_read_positions", None)
            if _rp is not None:
                _rp[(reader_webid, thread_id)] = time.time()
            # R13.12: update room read receipt table
            if thread_id in self._local_rooms and message_id_read:
                self._store.mark_room_read(thread_id, reader_webid, message_id_read,
                                           datetime.now(timezone.utc).isoformat())
            # R32: save per-message receipt if receipts enabled
            if reader_webid and message_id_read and self._client_receipts_prefs.get(reader_webid, True):
                self._store.save_message_receipt(message_id_read, reader_webid,
                                                  datetime.now(timezone.utc).isoformat())

        # Opening a contact's thread sends mark_read without a message id: the
        # receipt then covers the newest message the contact sent in it.
        if (reader_webid and thread_id and not message_id_read and self._store
                and self._store.get_relationship_by_cert_id(thread_id)):
            try:
                for _m in reversed(self._store.get_messages(thread_id, limit=20)):
                    if _m.get("from_webid") and _m.get("from_webid") != reader_webid:
                        message_id_read = _m.get("message_id") or ""
                        break
            except Exception:
                message_id_read = ""

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
            await self._notify_thread_read(websocket, reader_webid, channel_id)
            # Mark dirty for pod flush (flushed every 30 s by _read_position_flush_loop)
            _rp = getattr(self, "_dirty_read_positions", None)
            if _rp is not None:
                _rp[(reader_webid, channel_id)] = time.time()

    async def _handle_read_dm(self, websocket, data: dict) -> None:
        caller = self._client_webids.get(websocket)
        if not caller:
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        cert_id = data.get("cert_id", "")
        # IDOR guard: get_messages(cert_id) is WHERE thread_id=? only, so without
        # a participant check any registered account could read another's DM by
        # naming its thread id. Return the same empty-history shape (not a
        # distinct error) so thread existence is not leaked.
        if self._auth_enforced() and not self._actor_participates_in_dm(caller, cert_id):
            await websocket.send(json.dumps({"type": "history", "thread_id": cert_id, "messages": []}))
            return
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
                    "edited_at":        m.get("edited_at"),
                    "local":            True,
                    **({"imported": True} if m.get("imported") else {}),
                }
                for m in raw
            ]
        # Reactions ride with the history (a sealed one as its token, which the
        # browser maps back to the emoji it saw); without them every DM
        # reaction vanished on reload.
        reactions = self._store.get_reactions(
            cert_id, [m["message_id"] for m in messages]) if self._store else []
        await websocket.send(json.dumps({"type": "history", "thread_id": cert_id,
                                         "messages": messages, "reactions": reactions}))

    async def _handle_read_room(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id]["members"]:
                await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
                return
        elif self._auth_enforced():
            # Room not tracked locally (pod/federated): membership was only enforced
            # for the local case, so a non-member local account could read the room's
            # cached history by naming its id. Gate on the same read-authz helper the
            # DM/history paths use.
            _actor = self._client_webids.get(websocket, "")
            if not self._actor_can_read_thread(websocket, _actor, room_id):
                await websocket.send(json.dumps({"type": "history", "thread_id": room_id, "messages": []}))
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
        _base = self._gateway_http_url()
        invite_url = f"{_base}/?join={code}" if _base else code
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

    async def _handle_rehost_room(self, websocket, data: dict) -> None:
        """Reconstruct a room this gateway has no state for, from the host-owned
        descriptor the client read from its pod (PLAN_ROUND_71 B2, client-driven
        rehydration). Owner-only and idempotent: an already-hosted room is not
        clobbered. B3 will additionally verify the descriptor's signature so a
        spoofed webid cannot rehost; here the check is that the requester's session
        webid IS the room owner.
        """
        import re as _re
        from .room_descriptor import verify_descriptor
        desc = data.get("descriptor") or {}
        room_id = str(desc.get("room_id") or "")
        owner = str(desc.get("owner") or "")
        if not room_id or not _re.fullmatch(r"[\w-]{1,128}", room_id):
            await websocket.send(json.dumps({"type": "error", "code": "E_REHOST", "message": "bad room id"}))
            return
        # B3: the descriptor must carry a valid signature, and the signer must be
        # this connection's auth-verified identity. This is what makes rehost safe:
        # a fabricated or tampered descriptor fails the signature, and a descriptor
        # signed by someone else cannot be replayed by a different session. The
        # signer did is the room's cryptographic authority (owner webid is
        # descriptive).
        signer = verify_descriptor(desc)
        requester = self._client_webids.get(websocket, "")
        # R73: a pod session's display identity is its webid, but its descriptors are
        # signed by its did, retained as the connection's proven signing did. Accept
        # either, so a pod-connected owner can rehost.
        signing_did = self._session_signing_did.get(websocket, "")
        if not signer:
            await websocket.send(json.dumps({"type": "error", "code": "E_REHOST", "message": "descriptor not signed / bad signature"}))
            return
        if signer != requester and signer != signing_did:
            await websocket.send(json.dumps({"type": "error", "code": "E_REHOST", "message": "signer is not the requester"}))
            return

        # Already hosted: adding the caller to the live member set is what grants
        # send/history/members/reactions/pins, so it must be gated. A valid
        # SELF-signed descriptor proves only "I am who I say"; it does NOT prove
        # room membership. Rooms are hydrated into _local_rooms at startup, so
        # without this any authenticated caller who learned the room id (a former
        # member, a BANNED member, or anyone who saw it) could self-sign a
        # descriptor and be added to the live members. Require a genuine
        # membership tie: the room creator, or a current stored member, and never
        # banned. Mirrors _handle_join_room's ban gate. Never clobber the room.
        if room_id in self._local_rooms:
            _ids = {requester, signing_did, signer}
            _ids.discard("")
            _creator = self._local_rooms[room_id].get("creator_webid", "")
            _is_creator = bool(_creator) and _creator in _ids
            _members = set(self._store.get_room_members(room_id)) if self._store else set()
            _is_member = bool(_ids & _members)
            _is_banned = bool(self._store) and any(
                self._store.is_room_banned(room_id, _i) for _i in _ids
            )
            if not (_is_creator or _is_member) or _is_banned:
                await websocket.send(json.dumps({
                    "type": "error", "code": "E_REHOST",
                    "message": "not a room member",
                }))
                return
            self._local_rooms[room_id]["members"].add(websocket)
            await websocket.send(json.dumps({
                "type": "room_rehosted", "room_id": room_id,
                "name": self._local_rooms[room_id].get("name", ""), "already": True,
            }))
            return

        name = (str(desc.get("title") or desc.get("name") or "Room")).strip()[:100]
        history_mode = str(desc.get("history_mode") or "none")
        # Restore the invite code if the descriptor carried the plaintext one.
        code_plain = str(desc.get("code") or "")
        code_hash = self._hmac_invite_code(code_plain) if code_plain else ""
        _base = self._gateway_http_url()
        invite_url = f"{_base}/?join={code_plain}" if (_base and code_plain) else code_plain

        self._local_rooms[room_id] = {
            "name": name,
            "code": code_hash,
            "members": {websocket},
            "invite_url": invite_url,
            "history_mode": history_mode,
            "messages": [],
            "creator_webid": signer,
        }
        if code_hash:
            self._room_codes[code_hash] = room_id
        if self._store:
            self._store.save_room(room_id, name, code_hash, invite_url, history_mode, signer)
            self._store.add_room_member(room_id, signer)
            # The descriptor's members list is client-supplied, so cap it at
            # _MAX_ROOM_MEMBERS (the signer already counts) the way _handle_join_room
            # caps live joins. Without this a crafted descriptor could seed an
            # arbitrarily large membership.
            _rehost_added = 1
            for m in (desc.get("members") or []):
                if _rehost_added >= _MAX_ROOM_MEMBERS:
                    break
                wid = m.get("webid") if isinstance(m, dict) else m
                if isinstance(wid, str) and wid and wid != signer:
                    self._store.add_room_member(room_id, wid)
                    _rehost_added += 1
        logger.info("Room rehosted from pod descriptor: %r (%s) by %s", name, room_id, signer)
        await websocket.send(json.dumps({
            "type": "room_rehosted", "room_id": room_id, "name": name,
            "code": code_plain, "invite_url": invite_url,
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
            # A4: reject joins over the per-room member cap (existing members
            # reconnecting are exempt so they are never locked out).
            _current_members = (self._store.get_room_members(room_id)
                                if self._store else [])
            _already_member = bool(joiner_webid and joiner_webid in _current_members)
            _member_count = (len(_current_members) if self._store
                             else len(self._local_rooms[room_id].get("members", set())))
            if not _already_member and _member_count >= _MAX_ROOM_MEMBERS:
                await websocket.send(json.dumps({"type": "error", "message": "room_full"}))
                return
            # A3: enforce invite expiry / max-uses. Only invite-table codes are
            # gated; a legacy plaintext code (no invite row) falls through so
            # pre-existing joins keep working.
            if self._store and code_hash and self._store.room_invite_exists(code_hash):
                if self._store.consume_room_invite(code_hash) is None:
                    await websocket.send(json.dumps({
                        "type": "error", "message": "invite_expired_or_exhausted"}))
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
                "topic": room.get("topic", ""),
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
                "display_name": self._name_for(websocket, self._client_webids.get(websocket, "")),
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
        # The owner cannot be kicked (mirrors _handle_set_member_role).
        _room_obj = self._local_rooms.get(room_id, {})
        if target_webid and target_webid == _room_obj.get("creator_webid"):
            await websocket.send(json.dumps({"type": "error", "message": "Cannot kick the room owner"}))
            return
        if room_id and target_webid:
            if self._store:
                self._store.remove_room_member(room_id, target_webid)
                self._store.delete_room_role(room_id, target_webid)
            self._drop_ownership_offer(room_id, target_webid)
            # Evict ALL of the target's sockets and notify each — a single-socket
            # discard left a second device connected and still receiving.
            _target_sockets = self._sockets_for(target_webid)
            if room_id in self._local_rooms:
                for _ws in _target_sockets:
                    self._local_rooms[room_id]["members"].discard(_ws)
            for _ws in _target_sockets:
                try:
                    await _ws.send(json.dumps({
                        "type": "kicked_from_room",
                        "room_id": room_id,
                    }))
                except Exception:
                    pass
            # Notify ALL remaining members (like ban) so their member list updates
            # live — not just the kicker.
            _kick_event = json.dumps({
                "type": "member_kicked",
                "room_id": room_id,
                "webid": target_webid,
            })
            _members_after = list(self._local_rooms.get(room_id, {}).get("members", set()))
            if _members_after:
                for ws in _members_after:
                    try:
                        await ws.send(_kick_event)
                    except Exception:
                        pass
            else:
                await websocket.send(_kick_event)
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
        # The owner cannot be banned (mirrors _handle_set_member_role).
        room = self._local_rooms.get(room_id, {})
        if target_webid == room.get("creator_webid"):
            await websocket.send(json.dumps({"type": "error", "message": "Cannot ban the room owner"}))
            return
        self._store.ban_room_member(room_id, target_webid, caller_webid, reason)
        self._store.remove_room_member(room_id, target_webid)
        self._store.delete_room_role(room_id, target_webid)
        self._drop_ownership_offer(room_id, target_webid)
        # Evict ALL of the target's sockets (a single-socket discard left a second
        # device connected and still able to decrypt future messages) and notify each.
        _target_sockets = self._sockets_for(target_webid)
        for _ws in _target_sockets:
            room.get("members", set()).discard(_ws)
        for _ws in _target_sockets:
            try:
                await _ws.send(json.dumps({"type": "kicked_from_room", "room_id": room_id,
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
        # Rotate room + sender keys so a banned user's surviving session cannot
        # decrypt future E2E group messages (parity with the kick path).
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
            logger.warning("room_rekey after ban failed: %s", _rk_exc)
        try:
            await self._trigger_sender_key_rotation(room_id, target_webid)
        except Exception as _sk_exc:
            logger.warning("sender_key_rotation after ban failed: %s", _sk_exc)
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
        # The owner cannot be muted (mirrors _handle_set_member_role).
        if target_webid == self._local_rooms.get(room_id, {}).get("creator_webid"):
            await websocket.send(json.dumps({"type": "error", "message": "Cannot mute the room owner"}))
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
                               caller_webid: str, reason: str = "", expires_at=None,
                               extra_gateways=(), exclude_gateways=(),
                               display_name: str = "") -> None:
        """Fan a moderation action out to federated member gateways (R-C1).

        *extra_gateways* are reached too: a kicked or banned member's gateway
        is no longer in the federated member list by the time this runs, and
        it is the one that most needs to hear about it."""
        if not self._store or not target_webid:
            return
        _seen: set = {self._norm_gw_url(u) for u in exclude_gateways if u}
        _targets = [_fm.get("gateway_url", "")
                    for _fm in (self._store.get_federated_room_members(room_id) or [])]
        for _gw in _targets + [u for u in extra_gateways if u]:
            if not _gw or self._norm_gw_url(_gw) in _seen:
                continue
            _seen.add(self._norm_gw_url(_gw))
            _payload = {
                "content_type": "room_moderation", "action": action,
                "room_id": room_id, "webid": target_webid, "from_webid": caller_webid,
            }
            if reason:
                _payload["reason"] = reason
            if expires_at is not None:
                _payload["expires_at"] = expires_at
            if display_name:
                _payload["display_name"] = display_name[:64]
            asyncio.create_task(self._relay_ephemeral(_gw, _payload))

    def _member_gateways(self, room_id: str, member_did: str) -> list:
        """The gateway URLs recorded for a federated member of *room_id*."""
        if not self._store or not member_did:
            return []
        return [m.get("gateway_url", "") for m in
                (self._store.get_federated_room_members(room_id) or [])
                if m.get("member_did") == member_did and m.get("gateway_url")]

    def _room_is_hosted_here(self, room_id: str) -> bool:
        room = self._local_rooms.get(room_id)
        creator = room.get("creator_webid", "") if room else ""
        return bool(creator) and self._relay_identity_is_local(creator, room_id)

    def _room_host_gateway(self, room_id: str) -> str:
        """For a mirror of a room hosted elsewhere, the host gateway's URL."""
        room = self._local_rooms.get(room_id)
        creator = room.get("creator_webid", "") if room else ""
        if not creator or self._room_is_hosted_here(room_id):
            return ""
        urls = self._member_gateways(room_id, creator)
        return urls[0] if urls else ""

    def _forward_room_relay(self, data: dict, result: tuple) -> tuple:
        """Hub fan-out: on the room's host, pass a member's relayed message,
        reaction, edit or delete on to every OTHER member gateway.

        A member gateway only relays to the host, so without this members on
        different gateways never see each other. The host has already checked
        the sender's gateway binding and membership (the handler accepted it);
        receivers accept the host's signature for members it told them about
        (_relay_room_host_vouches). Never sent back to the sender's gateway,
        and a mirror never forwards, so nothing loops. Returns *result*."""
        try:
            if not str(result[0]).startswith("200") or not self._store:
                return result
            room_id = data.get("room_id", "")
            if room_id not in self._local_rooms or not self._room_is_hosted_here(room_id):
                return result
            origin = {self._norm_gw_url(u)
                      for u in self._member_gateways(room_id, data.get("from_webid", ""))}
            if not origin:
                return result
            origin.add(self._norm_gw_url(self._gateway_http_url()))
            fwd = {k: v for k, v in data.items() if not k.startswith("_") and k not in (
                "signature", "relay_sig_did", "relay_ts", "relay_nonce", "origin_gateway_url")}
            ct = data.get("content_type", "")
            for _fm in (self._store.get_federated_room_members(room_id) or []):
                gw = _fm.get("gateway_url", "")
                if not gw or self._norm_gw_url(gw) in origin:
                    continue
                origin.add(self._norm_gw_url(gw))
                if ct == "room_message":
                    asyncio.create_task(self._relay_room_message(gw, room_id, dict(fwd)))
                else:
                    asyncio.create_task(self._relay_ephemeral(gw, dict(fwd)))
        except Exception:
            logger.debug("room relay fan-out failed", exc_info=True)
        return result

    async def _drop_mirror_room(self, room_id: str, event: dict) -> None:
        """Remove this gateway's copy of a room hosted elsewhere, telling the
        local members still in it with *event*."""
        room = self._local_rooms.get(room_id)
        if not room:
            return
        payload = json.dumps(event)
        for ws in list(room.get("members", set())):
            try:
                await ws.send(payload)
            except Exception:
                pass
        self._room_codes.pop(room.get("code"), None)
        self._local_rooms.pop(room_id, None)
        if self._store:
            self._store.delete_room(room_id)
        self._forget_room_on_pod(room_id)

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
        if not message_id or not self._store:
            await websocket.send(json.dumps({"type": "message_readers", "message_id": message_id, "readers": []}))
            return
        # Authz: resolve the message's REAL thread from the store (never trust the
        # client-supplied room_id) and gate on read access. The old check only ran
        # when room_id was a known local room, so an empty/DM/federated room_id
        # skipped it and leaked readers for ANY message_id. Mirrors
        # _handle_get_pins / _handle_get_disappear_timer.
        _binding = self._store.get_message_identity_binding(message_id)
        _thread_id = _binding.get("thread_id", "") if _binding else ""
        if self._auth_enforced() and not self._actor_can_read_thread(
                websocket, self._client_webids.get(websocket), _thread_id):
            await websocket.send(json.dumps({"type": "message_readers", "message_id": message_id, "readers": []}))
            return
        readers = self._store.get_message_readers(message_id)
        # In a room, reading any later message also means this one was seen.
        if _thread_id and _thread_id in self._local_rooms:
            _seen = {r["receiver_webid"] for r in readers}
            readers += [r for r in self._store.get_room_readers_since(_thread_id, message_id)
                        if r["receiver_webid"] not in _seen]
        for r in readers:
            r["display_name"] = self._store.get_display_name(r["receiver_webid"]) or r["receiver_webid"][:12]
        await websocket.send(json.dumps({"type": "message_readers", "message_id": message_id, "readers": readers}))

    async def _handle_pin_message(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        thread_id = data.get("thread_id", "")
        room_id = self._strip_thread_prefix(thread_id)
        pinner_webid = self._client_webids.get(websocket, "")
        # DM pin: pinning a DM used to ALWAYS error ("Only the room owner can
        # pin") — _check_room_permission returns False for a non-room — AND was
        # never persisted or sent to the peer. A DM has no owner; either
        # participant may pin. Persist + deliver/relay like the other DM ops.
        if room_id not in self._local_rooms and thread_id not in self.room_memberships:
            await self._pin_dm(websocket, pinner_webid, thread_id, message_id,
                               data.get("content", ""), pin=True)
            return
        if not self._check_room_permission(websocket, room_id, role="owner"):
            await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can pin messages"}))
            return
        if room_id in self._local_rooms and self._store:
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
        # Notify ALL room members, not just the pinner — otherwise a pin doesn't
        # appear live for anyone else (unpin already broadcasts; pin didn't).
        event = json.dumps({"type": "message_pinned", "message_id": message_id, "thread_id": thread_id})
        if room_id in self._local_rooms:
            for ws in list(self._local_rooms[room_id]["members"]):
                try:
                    await ws.send(event)
                except Exception:
                    pass
        else:
            await websocket.send(event)

    async def _pin_dm(self, websocket, pinner_webid: str, thread_id: str,
                      message_id: str, content: str, pin: bool) -> None:
        """Pin/unpin a message in a DM thread. Either participant may pin; the
        pin persists keyed by the thread_id and is delivered to the peer (local
        or cross-gateway relay)."""
        if not pinner_webid or not thread_id or not message_id:
            return
        # The client sends "dm:<id>"; key by the bare id so the peer lookup below
        # finds the relationship and the pin is relayed, not kept local.
        _raw_thread_id = thread_id
        thread_id = self._strip_thread_prefix(thread_id)
        # Resolve the peer via the relationship (cert DM) or the thread id (local
        # did-keyed DM).
        peer = ""
        if self._store:
            _cd = self._store.get_relationship_by_cert_id(thread_id)
            if _cd:
                peer = _cd.get("peer_did", "")
        if not peer:
            peer = thread_id if str(thread_id).startswith("did:key:") else ""
        # Auth-gated participant check (see ProxionGateway._auth_enforced): when
        # auth is enforced, pinner_webid is a proven identity and must be the owner
        # (account/gateway DID) or the peer; auth-off skips it (the local user
        # registers under a session DID ≠ the gateway DID).
        if self._auth_enforced() and pinner_webid not in (self._own_gateway_did(), peer):
            await websocket.send(json.dumps({"type": "error", "message": "Not a participant in this DM"}))
            return
        if self._store:
            try:
                if pin:
                    _mr = self._store.get_messages_by_ids([message_id])
                    _c = (_mr[0].get("content", "") if _mr else content)
                    # A sealed DM's stored text is ciphertext: keep no copy.
                    if _mr and _mr[0].get("content_type") == "e2e":
                        _c = ""
                    self._store.save_pin(thread_id, message_id, pinner_webid, _c)
                else:
                    self._store.remove_pin(thread_id, message_id)
                    if _raw_thread_id != thread_id:
                        self._store.remove_pin(_raw_thread_id, message_id)
            except Exception:
                pass
        event = json.dumps({
            "type": "message_pinned" if pin else "unpinned",
            "message_id": message_id, "thread_id": thread_id,
        })
        await websocket.send(event)
        if peer and peer != pinner_webid:
            if self._sockets_for(peer):
                await self._send_to_identity(peer, event)
            else:
                _pg = self._resolve_peer_gateway(peer)
                if _pg:
                    from .didkey import pub_key_to_did as _p2d_pin
                    asyncio.create_task(self._relay_ephemeral(_pg, {
                        "content_type": "dm_pin",
                        "from_webid": _p2d_pin(self.agent.identity_pub_bytes),
                        "to_webid": peer,
                        "message_id": message_id,
                        "action": "pin" if pin else "unpin",
                    }))

    async def _handle_get_pins(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id", "")
        room_id = self._strip_thread_prefix(thread_id)
        # Pins carry content and pinned_by; gate on DM participation / room
        # membership so a non-party cannot enumerate another thread's pins.
        if self._auth_enforced() and not self._actor_can_read_thread(
                websocket, self._client_webids.get(websocket), thread_id):
            await websocket.send(json.dumps({"type": "pins", "thread_id": thread_id, "pins": []}))
            return
        pins = []
        if room_id in self._local_rooms and self._store:
            stored = self._store.get_pins(room_id)
            pins = [{"message_id": p["message_id"], "pinned_by": p["pinned_by"],
                     "pinned_at": p["pinned_at"], "content": p.get("content", "")}
                    for p in stored]
        elif thread_id not in self.dm_clients and thread_id not in self.room_memberships and self._store:
            # DM pins are persisted in the store keyed by the bare thread id (were
            # never read back — get_pins for a DM returned []). Older pins were
            # saved under the client's "dm:" form, so read both.
            stored = self._store.get_pins(room_id)
            if room_id != thread_id:
                _seen = {p["message_id"] for p in stored}
                stored = stored + [p for p in self._store.get_pins(thread_id)
                                   if p["message_id"] not in _seen]
            # A pin of a sealed DM carries no text (older pins kept the
            # ciphertext); the browser fills in its own plaintext copy.
            _sealed = {r["message_id"] for r in self._store.get_messages_by_ids(
                [p["message_id"] for p in stored]) if r.get("content_type") == "e2e"}
            pins = [{"message_id": p["message_id"], "pinned_by": p["pinned_by"],
                     "pinned_at": p["pinned_at"],
                     **({"content": "", "e2e": True} if p["message_id"] in _sealed
                        else {"content": p.get("content", "")})}
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
        # DM unpin: same as pin — participant-authz'd, persisted, relayed.
        if room_id not in self._local_rooms and thread_id not in self.room_memberships:
            await self._pin_dm(websocket, self._client_webids.get(websocket, ""),
                               thread_id, message_id, "", pin=False)
            return
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

    _ROOM_TOPIC_MAX = 300

    async def _handle_update_room(self, websocket, data: dict) -> None:
        """Rename a room and/or set its topic. Owner or admin; local rooms only.
        Every member is sent the new name and topic."""
        room_id = data.get("room_id", "")
        room = self._local_rooms.get(room_id)
        if not room:
            await websocket.send(json.dumps({"type": "error", "message": "room_not_found"}))
            return
        if not self._check_room_permission(websocket, room_id, role="admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        name = room["name"]
        topic = room.get("topic", "")
        if "name" in data:
            _n = data.get("name")
            _n = _n.strip() if isinstance(_n, str) else ""
            if not _n or len(_n) > 64:
                await websocket.send(json.dumps({"type": "error", "message": "invalid_room_name"}))
                return
            name = _n
        if "topic" in data:
            _t = data.get("topic")
            _t = _t.strip() if isinstance(_t, str) else ""
            if len(_t) > self._ROOM_TOPIC_MAX:
                await websocket.send(json.dumps({"type": "error", "message": "room_topic_too_long"}))
                return
            topic = _t
        caller = self._client_webids.get(websocket, "")
        await self._apply_room_update(room_id, name, topic, caller, extra_ws=websocket)
        # Members on other gateways see the change too (the receiver re-checks
        # that the caller is the owner or an admin).
        if self._store:
            _seen: set = set()
            for _fm in (self._store.get_federated_room_members(room_id) or []):
                _gw = _fm.get("gateway_url", "")
                if not _gw or _gw in _seen:
                    continue
                _seen.add(_gw)
                asyncio.create_task(self._relay_ephemeral(_gw, {
                    "content_type": "room_update", "room_id": room_id,
                    "name": name, "topic": topic, "from_webid": caller,
                }))

    async def _apply_room_update(self, room_id: str, name: str, topic: str, by: str,
                                 extra_ws=None) -> None:
        """Store a room's new name and topic and tell every local member."""
        room = self._local_rooms.get(room_id)
        if not room:
            return
        room["name"] = name
        room["topic"] = topic
        if self._store:
            self._store.update_room_info(room_id, name, topic)
        event = json.dumps({
            "type": "room_updated", "room_id": room_id, "name": name, "topic": topic,
            "by": by,
        })
        _targets = set(room.get("members", set()))
        if self._store:
            # Exact lookup: an offline member whose id is the gateway DID (often the
            # owner) must not fall back to every connected socket.
            for _wid in self._store.get_room_members(room_id):
                _targets.update(self._sockets_for_exact(_wid))
        if extra_ws is not None:
            _targets.add(extra_ws)
        for ws in list(_targets):
            try:
                await ws.send(event)
            except Exception:
                pass

    async def _handle_room_update_relay(self, data: dict) -> tuple[str, str]:
        """A room rename/topic change from a member gateway. Re-checks on this
        side that the sender is the room's owner or an admin, and the limits."""
        room_id = data.get("room_id", "")
        caller = data.get("from_webid", "")
        room = self._local_rooms.get(room_id)
        if not room or not caller or not self._store:
            return "404 Not Found", '{"error":"room_not_found"}'
        _is_admin = self._store.get_room_role(room_id, caller) == "admin"
        if caller != room.get("creator_webid") and not _is_admin:
            return "403 Forbidden", '{"error":"not_authorized_for_room_update"}'
        # An admin role only counts while its holder is still in the room (a stale
        # role row must not outlive a leave or a removal).
        if caller != room.get("creator_webid"):
            _members = set(self._store.get_room_members(room_id))
            _members |= {m.get("member_did") for m in self._store.get_federated_room_members(room_id)}
            if caller not in _members or self._store.is_room_banned(room_id, caller):
                return "403 Forbidden", '{"error":"not_authorized_for_room_update"}'
        name = data.get("name")
        topic = data.get("topic", "")
        name = name.strip() if isinstance(name, str) else ""
        topic = topic.strip() if isinstance(topic, str) else ""
        if not name or len(name) > 64 or len(topic) > self._ROOM_TOPIC_MAX:
            return "400 Bad Request", '{"error":"invalid_room_update"}'
        await self._apply_room_update(room_id, name, topic, caller)
        return "200 OK", '{"status":"ok"}'

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
        # Clamp to a representable ceiling so the expiry sweep's timedelta cannot
        # overflow and abort the whole tick (a gateway-wide DoS).
        ms = min(ms, _MAX_DISAPPEAR_MS)

        if thread_id in self._local_rooms:
            # Room: only the owner may change it.
            if not self._check_room_permission(websocket, thread_id, role="owner"):
                await websocket.send(json.dumps({"type": "error", "message": "Only the room owner can set the disappear timer"}))
                return
            self._note_disappear_since(thread_id, self._room_disappear_timers.get(thread_id, 0), ms)
            self._room_disappear_timers[thread_id] = ms
            if self._store:
                self._store.set_room_disappear_timer(thread_id, ms)
            event = json.dumps({"type": "disappear_timer_updated", "room_id": thread_id, "ms": ms,
                                **self._disappear_since_field(thread_id)})
            for ws in list(self._local_rooms.get(thread_id, {}).get("members", set())):
                try:
                    await ws.send(event)
                except Exception:
                    pass
            return

        # DM thread: a locally-registered caller (the gateway's user) may set it.
        # Resolve the peer via the relationship for a cert DM (keyed by UUID —
        # get_dm_threads(owner=browser_did) can't find it), or the thread id for
        # a local did-keyed DM.
        peer = None
        if self._store:
            _reld = self._store.get_relationship_by_cert_id(thread_id)
            if _reld:
                peer = _reld.get("peer_did", "")
            else:
                for _t in self._store.get_dm_threads(owner_webid=caller):
                    if _t.get("thread_id") == thread_id:
                        peer = _t.get("peer_webid")
                        break
        if not peer and str(thread_id).startswith("did:key:"):
            peer = thread_id
        if not peer:
            await websocket.send(json.dumps({"type": "error", "message": "Not a participant in this thread"}))
            return
        # Auth-gated participant check (see ProxionGateway._auth_enforced): when
        # auth is enforced, caller is a proven identity and must be the owner
        # (account/gateway DID) or the peer; auth-off skips it (the local user
        # registers under a session DID ≠ the gateway DID).
        if self._auth_enforced() and caller not in (self._own_gateway_did(), peer):
            await websocket.send(json.dumps({"type": "error", "message": "Not a participant in this thread"}))
            return
        self._note_disappear_since(thread_id, self._dm_disappear_timers.get(thread_id, 0), ms)
        self._dm_disappear_timers[thread_id] = ms
        if self._store:
            self._store.set_dm_disappear_timer(thread_id, ms)  # dedicated DM table (survives restart)
        event = json.dumps({"type": "disappear_timer_updated", "room_id": thread_id, "ms": ms,
                            **self._disappear_since_field(thread_id)})
        await self._send_to_identity(caller, event)
        # Deliver to the peer — and for a cross-gateway peer, RELAY the timer so
        # THEIR gateway also expires the shared messages (disappearing messages
        # are mutual, like Signal). Without this the timer applied only on the
        # setter's side and the peer's copy never disappeared.
        if peer and peer != caller:
            if self._sockets_for(peer):
                await self._send_to_identity(peer, event)
            else:
                _pg = self._resolve_peer_gateway(peer)
                if _pg:
                    from .didkey import pub_key_to_did as _p2d_dis
                    asyncio.create_task(self._relay_ephemeral(_pg, {
                        "content_type": "dm_disappear_timer",
                        "from_webid": _p2d_dis(self.agent.identity_pub_bytes),
                        "to_webid": peer,
                        "ms": ms,
                    }))

    def _note_disappear_since(self, thread_id: str, old_ms, new_ms) -> None:
        """Turning a timer on starts it now: messages sent before stay. Changing
        the duration keeps the start; turning it off clears it."""
        since_map = self.__dict__.setdefault("_disappear_since", {})
        try:
            old_ms, new_ms = int(old_ms or 0), int(new_ms or 0)
        except (TypeError, ValueError):
            return
        if new_ms <= 0:
            since = None
        elif old_ms <= 0:
            since = datetime.now(timezone.utc).isoformat()
        else:
            return
        if since:
            since_map[thread_id] = since
        else:
            since_map.pop(thread_id, None)
        if self._store:
            try:
                self._store.set_disappear_since(thread_id, since)
            except Exception:
                logger.debug("could not persist disappear start", exc_info=True)

    def _disappear_since_iso(self, thread_id: str) -> str:
        return self.__dict__.get("_disappear_since", {}).get(thread_id, "") or ""

    def _disappear_since_field(self, thread_id: str) -> dict:
        """{"since": ...} for timer events, so the browser counts down only the
        messages the timer applies to."""
        since = self._disappear_since_iso(thread_id)
        return {"since": since} if since else {}

    async def _handle_get_disappear_timer(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if self._auth_enforced() and not self._actor_can_read_thread(
                websocket, self._client_webids.get(websocket), room_id):
            await websocket.send(json.dumps({
                "type": "disappear_timer", "room_id": room_id, "ms": 0,
            }))
            return
        ms = self._room_disappear_timers.get(room_id, 0) or self._dm_disappear_timers.get(room_id, 0)
        if not ms and self._store:
            ms = self._store.get_room_disappear_timer(room_id) or self._store.get_dm_disappear_timer(room_id)
        await websocket.send(json.dumps({
            "type": "disappear_timer",
            "room_id": room_id,
            "ms": ms,
            **self._disappear_since_field(room_id),
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
        import re as _re_voice
        # Honor the client's message_id: the client uploads the pod audio copy
        # keyed by ITS id, so minting a fresh uuid here orphaned that upload
        # from the stored/rendered message.
        _client_mid = str(data.get("message_id") or "")
        if _client_mid and _re_voice.match(r"^[A-Za-z0-9:_-]{1,128}$", _client_mid):
            message_id = _client_mid
        else:
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
            # Room voice notes carry the same authorization as text room-sends
            # (_handle_send_room): a non-member, banned, or muted account, or one
            # writing to a read-only room, must not be able to push audio to every
            # live member and into history just by knowing the room id.
            if not self._check_room_permission(websocket, thread_id):
                await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
                return
            if self._store and self._store.is_room_banned(thread_id, sender):
                await websocket.send(json.dumps({"type": "error", "message": "you_are_banned"}))
                return
            if self._store and self._store.is_room_muted(thread_id, sender):
                await websocket.send(json.dumps({"type": "error", "message": "you_are_muted"}))
                return
            _vroom = self._local_rooms[thread_id]
            if _vroom.get("read_only") and _vroom.get("creator_webid") != sender:
                await websocket.send(json.dumps({"type": "error", "message": "Room is read-only"}))
                return
            for ws in list(_vroom.get("members", set())):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass
        else:
            # DM voice note. This branch previously delivered to NOBODY — the
            # event was built, saved, and dropped: the sender saw nothing after
            # recording (no echo) and the peer only found it in history after a
            # reload. Deliver: echo to the sender's sessions/devices, then to
            # the peer (typing-pattern resolution: the sender's DM thread, or a
            # did:key thread id IS the peer).
            peer_webid = ""
            _from_owned_thread = False
            if self._store:
                for t in self._store.get_dm_threads(owner_webid=sender):
                    if t.get("thread_id") == thread_id:
                        peer_webid = t.get("peer_webid", "")
                        _from_owned_thread = True
                        break
            if not peer_webid and str(thread_id).startswith("did:key:"):
                peer_webid = thread_id
            # Authorization mirrors the DM send path (_handle_send_dm /
            # _handle_local_dm): never deliver to a peer who blocked this sender,
            # and, when auth is enforced, only to an established contact. Without
            # this a blocked user could keep pushing voice notes and any account
            # could push an unsolicited note to an arbitrary did:key. A DM thread
            # the sender already owns is itself the established conversation, so it
            # stands in for the relationship the way _handle_local_dm treats one.
            if peer_webid and peer_webid != sender:
                if self._is_blocked_for(peer_webid, sender):
                    await websocket.send(json.dumps({"type": "error", "message": "Recipient is blocked"}))
                    return
                if (self._auth_enforced() and self._store and not _from_owned_thread
                        and not self._store.get_relationship_by_did(peer_webid)):
                    await websocket.send(json.dumps({
                        "type": "error",
                        "code": "no_relationship",
                        "message": "No relationship with this contact.",
                    }))
                    return
            _vpayload = json.dumps(event)
            await self._send_to_identity(sender, _vpayload)
            if peer_webid and peer_webid != sender:
                if self._sockets_for(peer_webid):
                    await self._send_to_identity(peer_webid, _vpayload)
                elif self._resolve_peer_gateway(peer_webid):
                    # Cross-gateway voice notes need chunked transport (audio can
                    # exceed the 128 KiB relay cap) — not built yet. Tell the
                    # sender instead of silently pretending it delivered.
                    await websocket.send(json.dumps({
                        "type": "error",
                        "code": "voice_note_remote_unsupported",
                        "message": "Voice messages to contacts on another gateway aren't supported yet.",
                        "message_id": message_id,
                    }))
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

        # Authz: search_messages IGNORES the member-thread scope when a thread_id
        # filter is given, so a client-supplied thread_id must be one the caller
        # actually belongs to — otherwise search leaks any thread's messages.
        if filter_thread_id and filter_thread_id not in member_threads:
            await websocket.send(json.dumps({
                "type": "search_results", "query": query, "results": [], "next_offset": 0,
            }))
            return

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
            # A muted member must not push "X is typing" to the whole room. Same
            # gate as _handle_send_room.
            _typing_sender = self._client_webids.get(websocket, "")
            if _typing_sender and self._store and self._store.is_room_muted(room_id, _typing_sender):
                return
            for ws in list(self._local_rooms[room_id]["members"]):
                if ws != websocket:
                    try:
                        await ws.send(json.dumps(typing_event))
                    except Exception:
                        pass
        elif cert_id:
            # Resolve the DM peer. Bug: this used get_dm_threads() with NO
            # owner_webid → 'WHERE owner_webid = ""' → always empty, so DM typing
            # never reached the peer. Use the sender's threads; and for local DMs
            # cert_id IS the peer's DID directly.
            sender = self._client_webids.get(websocket, "")
            peer_webid = ""
            if self._store:
                for t in self._store.get_dm_threads(owner_webid=sender):
                    if t.get("thread_id") == cert_id:
                        peer_webid = t.get("peer_webid", "")
                        break
            if not peer_webid and str(cert_id).startswith("did:key:"):
                peer_webid = cert_id
            # A friend's DM is keyed by our relationship cert_id, which has no
            # dm_threads row until this side has sent a message in it.
            if not peer_webid and self._store:
                _rel = self._store.get_relationship_by_cert_id(cert_id)
                if _rel:
                    peer_webid = _rel.get("peer_did", "") or ""
            if peer_webid:
                # Authz: only deliver DM typing when the sender is a party to this
                # DM thread (or holds a relationship with the peer). The cross-gateway
                # relay path already enforces this via _authorized_relationship; the
                # same-gateway path did not, so any authenticated user could push
                # "X is typing" to an arbitrary did:key peer. Gated on _auth_enforced()
                # to match the other DM handlers (auth-off registers a session DID
                # that owns the thread rows).
                if self._auth_enforced() and not (
                        self._actor_participates_in_dm(sender, cert_id)
                        or self._authorized_relationship(peer_webid)):
                    return
                # A blocked sender must not push "X is typing" (nor have it relayed
                # cross-gateway). Match _handle_local_dm block gating (recipient's POV).
                if self._is_blocked_for(peer_webid, sender):
                    return
                peer_ws = self._any_socket(peer_webid)
                if peer_ws and peer_ws != websocket:
                    await peer_ws.send(json.dumps(typing_event))
                else:
                    # Peer is on a different gateway — relay ephemeral typing event
                    peer_gw = self._resolve_peer_gateway(peer_webid)
                    if peer_gw:
                        # The peer's relationship keys us by the gateway did, not
                        # this browser's session did (same as the dm_reaction relay).
                        asyncio.create_task(self._relay_ephemeral(peer_gw, {
                            "content_type": "typing",
                            "from_webid": self._own_gateway_did(),
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
        # The gateway sends a scheduled message itself, as plaintext. DMs are
        # end-to-end encrypted, so only schedule into one when the client says
        # the user opted this conversation in to unencrypted messages.
        if self._is_scheduled_dm_thread(thread_id, actor) and data.get("plaintext_ok") is not True:
            await websocket.send(json.dumps({
                "type": "error", "code": "E_SCHEDULE_E2E", "message": "schedule_dm_encrypted",
            }))
            return
        _MAX_PENDING_SCHEDULED = 100
        if self._store and self._store.count_pending_scheduled(actor) >= _MAX_PENDING_SCHEDULED:
            await websocket.send(json.dumps({"type": "error", "message": "Too many pending scheduled messages"}))
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
            # Roles are only for people in the room: a role row for an outsider
            # would be honored later (e.g. a relayed room_update) without them
            # ever having joined.
            _members = set(self._store.get_room_members(room_id))
            _members |= {m.get("member_did") for m in self._store.get_federated_room_members(room_id)}
            if target_webid not in _members or self._store.is_room_banned(room_id, target_webid):
                await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
                return
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
        # Membership gate — mirror _handle_get_room_members. Without it, any
        # authenticated client could enumerate a room's admins/mods (and their
        # webids) for an arbitrary room_id, bypassing the members-list privacy
        # control that get_room_members enforces.
        if room_id in self._local_rooms:
            _in_live = websocket in self._local_rooms[room_id]["members"]
            _in_store = bool(self._store and actor in self._store.get_room_members(room_id))
            if not _in_live and not _in_store:
                await websocket.send(json.dumps({
                    "type": "room_roles", "room_id": room_id, "roles": {},
                }))
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
            # /webhook/ is served by the HTTP server. A ws(s):// public_url names
            # the websocket port, so only an http(s) one (a tunnel) is used here.
            _pub = (self.config.public_url or "").rstrip("/")
            if self.config.http_public_url or not _pub.startswith(("https://", "http://")):
                _pub = ""
            http_base = _pub or self._gateway_http_url() or "http://localhost:8080"
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
        # Require room membership so an attacker cannot write save_sender_key rows
        # for arbitrary room ids they do not belong to (unbounded DB growth).
        if self._auth_enforced() and not self._check_room_permission(websocket, room_id):
            await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
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
        # This returns the raw group chain key, so gate it like the write siblings
        # (_handle_upload_sender_key / _handle_distribute_sender_key): the caller
        # must be a current room member AND may fetch only its OWN sender key. A
        # kicked-but-still-authenticated account could otherwise pull the fresh
        # key, negating rotation-on-kick.
        caller_webid = self._client_webids.get(websocket, "")
        if self._auth_enforced():
            if not self._check_room_permission(websocket, room_id):
                await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
                return
            if sender_webid != caller_webid:
                await websocket.send(json.dumps({"type": "error", "message": "Cannot fetch another member's sender key"}))
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
        # Require room membership so this cannot be used to push sender_key_received
        # events at arbitrary accounts for a room the caller does not belong to.
        if self._auth_enforced() and not self._check_room_permission(websocket, room_id):
            await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
            return
        # Only distribute to accounts that are actually members of the room, so a
        # member cannot inject key events at arbitrary targets.
        _room_members = (
            set(self._store.get_room_members(room_id))
            if (self._auth_enforced() and self._store) else None
        )
        delivered: list[str] = []
        for target_webid, sealed_b64 in distribution.items():
            if _room_members is not None and target_webid not in _room_members:
                continue
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

    # ------------------------------------------------------------------
    # Custom room emoji (R59G)
    # ------------------------------------------------------------------
    # Small images scoped to a room, rendered for :name: tokens. Admin/owner
    # manage; every member can list. Changes federate as per-emoji relay
    # deltas ("room_emoji" content_type, member-signed like room_moderation)
    # -- a full 50x64KB set would blow the 128 KiB /relay body cap.
    _EMOJI_MAX_BYTES = 64 * 1024
    _EMOJI_MAX_PER_ROOM = 50
    _EMOJI_ALLOWED_MIMES = frozenset({"image/png", "image/webp", "image/gif"})

    @staticmethod
    def _valid_emoji_name(name: str) -> bool:
        import re as _re
        return bool(_re.fullmatch(r"[a-z0-9_]{2,32}", name or ""))

    def _validate_emoji_payload(self, name: str, mime: str, data_b64: str) -> Optional[str]:
        """Returns an error string, or None if the payload is acceptable."""
        if not self._valid_emoji_name(name):
            return "invalid_emoji_name"
        if (mime or "").lower() not in self._EMOJI_ALLOWED_MIMES:
            return "emoji_type_not_allowed"
        try:
            raw = base64.b64decode(data_b64 or "", validate=True)
        except Exception:
            return "invalid_emoji_data"
        if not raw or len(raw) > self._EMOJI_MAX_BYTES:
            return "emoji_too_large"
        # Magic-byte sanity: content must match one of the allowed image types
        # (RIFF kind checked for WebP -- see the R59B sniffing fix).
        _is_png = raw[:6] == b"\x89PNG\r\n"
        _is_gif = raw[:4] == b"GIF8"
        _is_webp = raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
        if not (_is_png or _is_gif or _is_webp):
            return "emoji_content_mismatch"
        return None

    async def _broadcast_room_emoji(self, room_id: str) -> None:
        """Push the room's full emoji list to locally-connected members."""
        room = self._local_rooms.get(room_id)
        if not room or not self._store:
            return
        payload = json.dumps({
            "type": "room_emoji", "room_id": room_id,
            "emoji": self._store.get_room_emoji(room_id),
        })
        for ws in list(room.get("members", set())):
            try:
                await ws.send(payload)
            except Exception:
                pass

    def _relay_room_emoji(self, room_id: str, action: str, name: str,
                          caller_webid: str, mime: str = "", data_b64: str = "",
                          only_gateway: str = "") -> None:
        """Fan an emoji add/remove out to federated member gateways (delta)."""
        if not self._store:
            return
        _seen: set = set()
        for _fm in (self._store.get_federated_room_members(room_id) or []):
            _gw = _fm.get("gateway_url", "")
            if not _gw or _gw in _seen:
                continue
            if only_gateway and _gw != only_gateway:
                continue
            _seen.add(_gw)
            _payload = {
                "content_type": "room_emoji", "action": action,
                "room_id": room_id, "name": name, "from_webid": caller_webid,
            }
            if action == "add":
                _payload["mime"] = mime
                _payload["data_b64"] = data_b64
            asyncio.create_task(self._relay_ephemeral(_gw, _payload))

    def _sync_room_emoji_to_gateway(self, room_id: str, gateway_url: str) -> None:
        """New federated member gateway: replay the existing set as add deltas."""
        if not self._store:
            return
        room = self._local_rooms.get(room_id)
        _owner = room.get("creator_webid", "") if room else ""
        for e in self._store.get_room_emoji(room_id):
            self._relay_room_emoji(room_id, "add", e["name"], _owner,
                                   mime=e["mime"], data_b64=e["data_b64"],
                                   only_gateway=gateway_url)

    async def _handle_add_room_emoji(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        name = (data.get("name", "") or "").lower()
        mime = (data.get("mime", "") or "").lower()
        data_b64 = data.get("data_b64", "")
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        err = self._validate_emoji_payload(name, mime, data_b64)
        if err:
            await websocket.send(json.dumps({"type": "error", "message": err}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no_store"}))
            return
        _existing = {e["name"] for e in self._store.get_room_emoji(room_id)}
        if name not in _existing and len(_existing) >= self._EMOJI_MAX_PER_ROOM:
            await websocket.send(json.dumps({"type": "error", "message": "emoji_limit_reached"}))
            return
        caller = self._client_webids.get(websocket, "")
        self._store.save_room_emoji(room_id, name, mime, data_b64, caller)
        await self._broadcast_room_emoji(room_id)
        self._relay_room_emoji(room_id, "add", name, caller, mime=mime, data_b64=data_b64)

    async def _handle_remove_room_emoji(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        name = (data.get("name", "") or "").lower()
        if not self._check_room_permission(websocket, room_id, "admin"):
            await websocket.send(json.dumps({"type": "error", "message": "insufficient_permissions"}))
            return
        if not self._valid_emoji_name(name) or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_emoji_name"}))
            return
        self._store.remove_room_emoji(room_id, name)
        await self._broadcast_room_emoji(room_id)
        self._relay_room_emoji(room_id, "remove", name, self._client_webids.get(websocket, ""))

    async def _handle_list_room_emoji(self, websocket, data: dict) -> None:
        room_id = data.get("room_id", "")
        if not self._check_room_permission(websocket, room_id, "member"):
            await websocket.send(json.dumps({"type": "error", "message": "not_a_room_member"}))
            return
        emoji = self._store.get_room_emoji(room_id) if self._store else []
        await websocket.send(json.dumps({"type": "room_emoji", "room_id": room_id, "emoji": emoji}))

    async def _handle_room_emoji_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed emoji delta -- validate, apply, notify local members.

        Same authz stance as _handle_room_moderation_relay: only the room's
        owner or an admin may change the set, and the receiver re-verifies
        (never trust the wire) including name/mime/size/magic revalidation.
        """
        room_id = data.get("room_id", "")
        action = data.get("action", "")
        name = (data.get("name", "") or "").lower()
        caller = data.get("from_webid", "")
        if not room_id or action not in ("add", "remove") or not self._store:
            return "400 Bad Request", '{"error":"missing_emoji_fields"}'
        _room = self._local_rooms.get(room_id)
        _owner = _room.get("creator_webid") if _room else None
        _is_admin = bool(caller) and self._store.get_room_role(room_id, caller) == "admin"
        if not caller or (caller != _owner and not _is_admin):
            return "403 Forbidden", '{"error":"not_authorized_for_emoji"}'
        if action == "add":
            mime = (data.get("mime", "") or "").lower()
            data_b64 = data.get("data_b64", "")
            err = self._validate_emoji_payload(name, mime, data_b64)
            if err:
                return "400 Bad Request", json.dumps({"error": err})
            _existing = {e["name"] for e in self._store.get_room_emoji(room_id)}
            if name not in _existing and len(_existing) >= self._EMOJI_MAX_PER_ROOM:
                return "400 Bad Request", '{"error":"emoji_limit_reached"}'
            self._store.save_room_emoji(room_id, name, mime, data_b64, caller)
        else:
            if not self._valid_emoji_name(name):
                return "400 Bad Request", '{"error":"invalid_emoji_name"}'
            self._store.remove_room_emoji(room_id, name)
        await self._broadcast_room_emoji(room_id)
        return "200 OK", '{"status":"ok"}'

    # ── Cross-gateway room join ──────────────────────────────────────────────
    #
    # An invite link names the host gateway (http(s)://host/?join=CODE). A user
    # on another gateway joins THROUGH their own gateway: it asks the host over a
    # signed POST /room-join, the host records them as a federated member, and the
    # joiner's gateway keeps a mirror of the room so messages relay both ways.

    @staticmethod
    def _norm_gw_url(u: str) -> str:
        return (u or "").replace("wss://", "https://").replace("ws://", "http://").rstrip("/")

    async def _handle_join_remote_room(self, websocket, data: dict) -> None:
        from urllib.parse import urlsplit, parse_qs
        raw = str(data.get("url") or "").strip()
        try:
            parts = urlsplit(raw)
            code = (parse_qs(parts.query).get("join") or [""])[0].strip()
        except ValueError:
            parts, code = None, ""
        if (not parts or parts.scheme not in ("http", "https") or not parts.netloc
                or "@" in parts.netloc or not code):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_invite_link"}))
            return
        origin = f"{parts.scheme}://{parts.netloc}"
        own = self._norm_gw_url(self._gateway_http_url())
        if origin == own or origin == self._norm_gw_url(self._ws_public_url()):
            await self._handle_join_room(websocket, {"code": code})
            return
        caller = self._client_webids.get(websocket, "")
        if not caller or not own:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        from .relay import _validate_relay_target, sign_relay_envelope
        if not _validate_relay_target(origin):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_remote_gateway"}))
            return
        # "@<url>" is the address form with no expected did (a bare URL is not
        # parsed as a gateway address).
        info = await self._discover_peer_gateway("@" + origin)
        if not info:
            await websocket.send(json.dumps({"type": "error", "message": "remote_gateway_unreachable"}))
            return
        host_did = info.get("did", "")
        host_http = self._norm_gw_url(info.get("gateway_http_url", ""))
        if host_did == self._own_gateway_did() or host_http == own:
            await self._handle_join_room(websocket, {"code": code})
            return
        payload = {
            "code": code[:256],
            "member_did": caller,
            "display_name": (self._display_names.get(websocket) or "")[:64],
            "origin_gateway_url": own,
            "relay_sig_did": self._own_gateway_did(),
            "relay_ts": datetime.now(timezone.utc).isoformat(),
            "relay_nonce": secrets.token_hex(8),
        }
        payload["signature"] = sign_relay_envelope(self.agent.identity_key, payload)
        from .network import async_safe_post_content, NetworkError
        try:
            resp = json.loads(await async_safe_post_content(
                host_http + "/room-join", payload, timeout=10))
        except (NetworkError, ValueError) as exc:
            logger.info("remote room join via %s failed: %s", host_http, exc)
            await websocket.send(json.dumps({"type": "error", "message": "remote_gateway_unreachable"}))
            return
        if not isinstance(resp, dict) or not resp.get("ok"):
            err = str(resp.get("error") or "invalid_code") if isinstance(resp, dict) else "invalid_code"
            await websocket.send(json.dumps({"type": "error", "message": err[:64]}))
            return
        room_id = str(resp.get("room_id") or "")
        creator = str(resp.get("creator_webid") or "")
        if (not re.fullmatch(r"[\w-]{1,128}", room_id) or not creator or len(creator) > 256
                or creator == caller or creator in self._webid_sockets
                or creator == self._own_gateway_did()):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_remote_room"}))
            return
        name = str(resp.get("name") or "Room").strip()[:100] or "Room"
        topic = str(resp.get("topic") or "")[:500]
        history_mode = "all" if resp.get("history_mode") == "all" else "none"
        # Every open session of the joiner (other tabs, linked devices) gets the room.
        _socks = {websocket} | set(self._sockets_for(caller))
        existing = self._local_rooms.get(room_id)
        if existing is not None:
            # Only an existing mirror of THIS host's room may be reused; a host
            # must not be able to graft a member onto an unrelated local room.
            _mirrored = existing.get("creator_webid") == creator and bool(self._store) and any(
                m.get("member_did") == creator
                and self._norm_gw_url(m.get("gateway_url", "")) == host_http
                for m in self._store.get_federated_room_members(room_id))
            if not _mirrored:
                await websocket.send(json.dumps({"type": "error", "message": "room_id_conflict"}))
                return
            existing["members"].update(_socks)
        else:
            self._local_rooms[room_id] = {
                "name": name, "code": "", "members": set(_socks), "invite_url": raw,
                "history_mode": history_mode, "messages": [], "creator_webid": creator,
                "topic": topic,
            }
            if self._store:
                self._store.save_room(room_id, name, "", raw, history_mode, creator)
        if self._store:
            self._store.add_room_member(room_id, caller)
            # The host gateway's key, so traffic it forwards for members on
            # other gateways is accepted for this room (_relay_room_host_vouches).
            try:
                from .didkey import did_to_pub_key
                self._store.record_identity_key_seen(
                    "roomhost:" + room_id, did_to_pub_key(host_did).hex(), trusted=True)
            except Exception:
                logger.debug("could not record the room host key", exc_info=True)
            members = [m for m in (resp.get("members") or []) if isinstance(m, dict)]
            if not any(m.get("webid") == creator for m in members):
                members.insert(0, {"webid": creator})
            for m in members[:_MAX_ROOM_MEMBERS]:
                wid = m.get("webid")
                if not isinstance(wid, str) or not wid or len(wid) > 256 or wid == caller:
                    continue
                # An identity that lives on this gateway is never accepted over a relay.
                if wid == self._own_gateway_did() or wid in self._webid_sockets or (
                        wid != creator and self._relay_identity_is_local(wid, room_id)):
                    continue
                self._store.add_federated_room_member(room_id, wid, host_http,
                                                      max_members=_MAX_ROOM_MEMBERS)
                dn = m.get("display_name")
                if isinstance(dn, str) and dn.strip():
                    self._store.save_display_name(wid, dn.strip()[:64])
            if history_mode == "all":
                for msg in (resp.get("messages") or [])[:50]:
                    if not isinstance(msg, dict):
                        continue
                    mid = str(msg.get("message_id") or "")
                    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", mid):
                        continue
                    content = msg.get("content")
                    if not isinstance(content, str) or len(content) > 16384:
                        continue
                    _from = str(msg.get("from_webid") or "")[:256]
                    # The host speaks for its own members, never for anyone here.
                    if _from == caller or (_from != creator
                                           and self._relay_identity_is_local(_from, room_id)):
                        continue
                    self._store.save_message(
                        mid, room_id, "relay", _from,
                        str(msg.get("from_display_name") or "")[:64], content,
                        str(msg.get("timestamp") or ""))
        logger.info("Joined remote room %s hosted at %s", room_id, host_http)
        _joined = json.dumps({
            "type": "room_joined", "room_id": room_id, "name": name, "topic": topic,
            "code": "", "invite_url": raw, "federated": True,
        })
        await websocket.send(_joined)
        for ws in _socks - {websocket}:
            try:
                await ws.send(_joined)
            except Exception:
                pass
        _evt = json.dumps({"type": "room_member_joined", "room_id": room_id,
                           "name": name, "webid": caller,
                           "display_name": self._name_for(websocket, caller)})
        for ws in list(self._local_rooms[room_id]["members"]):
            if ws not in _socks and ws in self.clients:
                try:
                    await ws.send(_evt)
                except Exception:
                    pass

    async def _handle_room_join_post(self, body: bytes, client_ip: str = "") -> tuple[str, str]:
        """POST /room-join: a member's own gateway joins them to a room hosted here.

        Signed by the joiner's gateway (full-payload envelope). The home gateway
        URL is bound to the signer by fetching its /.well-known/proxion, so relays
        for the new member only go to the gateway that vouched for them. Admission
        mirrors _handle_join_room: rate limit, invite code, ban, member cap and
        invite expiry/max-uses. Refusals are 200 {"ok": false, "error": ...}.
        """
        from .relay import verify_relay_envelope, _validate_relay_target
        try:
            data = json.loads(body)
        except Exception:
            return "400 Bad Request", '{"error":"invalid JSON"}'
        if not isinstance(data, dict) or not verify_relay_envelope(data):
            return "401 Unauthorized", '{"error":"invalid signature"}'
        signer = data.get("relay_sig_did", "")
        nonce = str(data.get("relay_nonce") or "")
        if not re.fullmatch(r"[A-Fa-f0-9]{8,64}", nonce):
            return "400 Bad Request", '{"error":"invalid nonce"}'
        import hashlib as _hl
        _nk = _hl.sha256(f"roomjoin:{signer}:{nonce}".encode()).hexdigest()
        if self._store:
            if self._store.seen_relay_nonce(_nk, ttl_seconds=600):
                return "409 Conflict", '{"error":"replay"}'
            self._store.record_relay_nonce(_nk)

        def refuse(err: str) -> tuple[str, str]:
            return "200 OK", json.dumps({"ok": False, "error": err})

        code = str(data.get("code") or "").strip()
        member = str(data.get("member_did") or "")
        origin = self._norm_gw_url(str(data.get("origin_gateway_url") or ""))
        if not code or len(code) > 256 or not member or len(member) > 256:
            return refuse("invalid_code")
        if not member.startswith(("did:key:", "https://", "http://")):
            return refuse("invalid_member")
        if not origin or not _validate_relay_target(origin):
            return refuse("invalid_home_gateway")
        code_hash = self._hmac_invite_code(code)
        if self._store and client_ip:
            if self._store.count_recent_join_attempts(code_hash, client_ip, window_s=60.0) >= 10:
                return refuse("join_rate_limited")
            self._store.record_join_attempt(code_hash, client_ip)
        room_id = self._room_codes.get(code_hash) or self._room_codes.get(code)
        room = self._local_rooms.get(room_id) if room_id else None
        creator = room.get("creator_webid", "") if room else ""
        # Only the host answers: in a mirror of someone else's room the creator
        # is a federated member, not a local identity.
        if not room or not creator or not self._relay_identity_is_local(creator, room_id):
            return refuse("invalid_code")
        # The joiner's gateway vouches for its own users only: never for an
        # identity that lives here, nor for one already bound to another gateway.
        if member == creator or self._relay_identity_is_local(member, room_id):
            return refuse("invalid_member")
        if self._store:
            try:
                from .didkey import did_to_pub_key
                _gw_hex = did_to_pub_key(signer).hex()
                _hist = self._store.get_identity_key_history("relaygw:" + member)
                if _hist and not any(h.get("pubkey_hex") == _gw_hex for h in _hist):
                    return refuse("invalid_member")
                _seed_binding = not _hist
            except Exception:
                return refuse("invalid_member")
        info = await self._discover_peer_gateway(f"{signer}@{origin}")
        if not info:
            return refuse("invalid_home_gateway")
        home = self._norm_gw_url(info.get("gateway_http_url", ""))
        if not home or home == self._norm_gw_url(self._gateway_http_url()) \
                or not _validate_relay_target(home):
            return refuse("invalid_home_gateway")
        if self._store:
            if self._store.is_room_banned(room_id, member):
                return refuse("banned_from_room")
            _fed = self._store.get_federated_room_members(room_id) or []
            _is_fed = any(m.get("member_did") == member for m in _fed)
            _local = self._store.get_room_members(room_id) or []
            if not _is_fed and len(_local) + len(_fed) >= _MAX_ROOM_MEMBERS:
                return refuse("room_full")
            if self._store.room_invite_exists(code_hash):
                if self._store.consume_room_invite(code_hash) is None:
                    return refuse("invite_expired_or_exhausted")
            if not self._store.add_federated_room_member(
                    room_id, member, home, max_members=_MAX_ROOM_MEMBERS):
                return refuse("room_full")
            # Bind the member to the gateway that vouched for them now, so no other
            # gateway can seed the binding with the member's first relay.
            if _seed_binding:
                self._store.record_identity_key_seen("relaygw:" + member, _gw_hex, trusted=True)
            dn = str(data.get("display_name") or "").strip()[:64]
            if dn:
                self._store.save_display_name(member, dn)
            try:
                self._sync_room_emoji_to_gateway(room_id, home)
            except Exception:
                pass
        _stored = self._store.get_room_members(room_id) if self._store else []
        # Members on other gateways too: the joiner reaches them through us.
        _stored = _stored + [m.get("member_did", "") for m in (
            self._store.get_federated_room_members(room_id) if self._store else [])]
        members = []
        for wid in list(dict.fromkeys([creator] + [w for w in _stored if w != creator]))[:_MAX_ROOM_MEMBERS]:
            if wid and wid != member:
                members.append({"webid": wid, "display_name":
                                (self._store.get_display_name(wid) if self._store else None) or ""})
        messages = []
        if room.get("history_mode") == "all" and self._store:
            for m in self._store.get_messages(room_id, limit=50):
                messages.append({k: m.get(k) for k in (
                    "message_id", "from_webid", "from_display_name", "content", "timestamp")})
        _evt = json.dumps({
            "type": "room_member_joined", "room_id": room_id, "name": room.get("name", ""),
            "webid": member,
            "display_name": (self._store.get_display_name(member) if self._store else "") or "",
            "federated": True, "gateway": home,
        })
        for ws in list(room.get("members", set())):
            try:
                await ws.send(_evt)
            except Exception:
                pass
        # And to the other member gateways, so their member lists and relay
        # checks know the newcomer.
        self._relay_room_moderation(
            room_id, "member_joined", member, creator, exclude_gateways=(home,),
            display_name=(self._store.get_display_name(member) if self._store else "") or "")
        logger.info("Federated member joined room %s from %s", room_id, home)
        return "200 OK", json.dumps({
            "ok": True, "room_id": room_id, "name": room.get("name", ""),
            "topic": room.get("topic", ""), "history_mode": room.get("history_mode", "none"),
            "creator_webid": creator, "members": members, "messages": messages,
        })
