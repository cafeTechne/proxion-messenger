"""VoiceHandlerMixin — WebRTC signaling command handlers for ProxionGateway.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: _voice_sessions, _client_webids, clients, dm_clients,
                  _any_socket, _store, agent.
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger("proxion_messenger_core.gateway")

_voice_invite_ts: dict = {}  # (caller_webid, target_webid) -> float


class VoiceHandlerMixin:

    async def _cleanup_voice_sessions(self, websocket) -> None:
        """Notify the other party and remove any voice sessions the socket was in."""
        for sid, sess in list(self._voice_sessions.items()):
            if websocket not in (sess.get("caller_ws"), sess.get("callee_ws")):
                continue
            other = (
                sess.get("callee_ws")
                if sess.get("caller_ws") is websocket
                else sess.get("caller_ws")
            )
            if other:
                try:
                    await other.send(json.dumps({
                        "type": "voice_hangup",
                        "session_id": sid,
                        "reason": "peer_disconnected",
                    }))
                except Exception:
                    pass
            del self._voice_sessions[sid]

        # Also remove from any voice channels
        leaver_webid = self._client_webids.get(websocket, "")
        if leaver_webid:
            for ch_id in list(self._voice_channels.keys()):
                ch = self._voice_channels.get(ch_id)
                if ch and leaver_webid in ch.get("members", {}):
                    ch["members"].pop(leaver_webid, None)
                    for minfo in list(ch.get("members", {}).values()):
                        mws = minfo.get("ws") if isinstance(minfo, dict) else minfo
                        if mws:
                            try:
                                asyncio.create_task(mws.send(json.dumps({
                                    "type": "voice_peer_left",
                                    "channel_id": ch_id,
                                    "peer_webid": leaver_webid,
                                })))
                            except Exception:
                                pass
                    if not ch.get("members"):
                        self._voice_channels.pop(ch_id, None)

    async def _relay_voice_signal(
        self,
        target_webid: str,
        signal_type: str,
        signal_data: dict,
    ) -> bool:
        """Send a voice signal to a peer on a different gateway via HTTP relay.

        Uses POST /relay with content_type='voice_signal' so the receiving
        gateway delivers it immediately via WebSocket rather than queuing it.
        Returns True if the POST succeeded.
        """
        import secrets as _sec
        import time as _time
        from .relay import sign_relay_message, post_relay
        from .didkey import pub_key_to_did

        gateway_url = self._resolve_peer_gateway(target_webid)
        if not gateway_url:
            return False

        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        relay_nonce = _sec.token_hex(8)
        session_id = signal_data.get("session_id", "")
        # Synthetic message_id so relay dedup logic has a stable key
        message_id = f"vs:{signal_type}:{session_id}:{relay_nonce}"
        ts = _time.strftime("%Y-%m-%dT%H:%M:%S+00:00", _time.gmtime())

        try:
            sig = sign_relay_message(
                self.agent.identity_key,
                gateway_did, target_webid,
                message_id, signal_type, ts, relay_nonce,
            )
        except Exception as exc:
            logger.debug("_relay_voice_signal sign failed: %s", exc)
            return False

        my_http = self._gateway_http_url() if hasattr(self, "_gateway_http_url") else ""
        payload = {
            "from_webid": gateway_did,
            "to_webid": target_webid,
            "message_id": message_id,
            "content": signal_type,          # used by signature; receiver ignores
            "timestamp": ts,
            "relay_nonce": relay_nonce,
            "signature": sig,
            "origin_gateway_url": my_http,
            "content_type": "voice_signal",
            "signal_type": signal_type,
            "signal_data": signal_data,
            "session_id": session_id,
        }
        http_base = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
        try:
            return await post_relay(http_base.rstrip("/") + "/relay", payload)
        except Exception as exc:
            logger.debug("_relay_voice_signal post failed: %s", exc)
            return False

    async def _handle_voice_invite(self, websocket, data: dict) -> None:
        import secrets as _secrets
        import time as _time
        session_id = data.get("session_id") or _secrets.token_hex(16)
        sdp_offer = data.get("sdp_offer", "")
        target_webid = data.get("target_webid")
        cert_id = data.get("cert_id")
        caller_webid = self._client_webids.get(websocket, self.agent.identity_pub_bytes.hex())

        _key = (caller_webid, target_webid)
        _now = _time.monotonic()
        if _now - _voice_invite_ts.get(_key, 0.0) < 30.0:
            await websocket.send(json.dumps({"type": "error", "message": "call_too_frequent"}))
            return
        # Evict stale entries before inserting to bound memory usage.
        # Without this, a flood of random target_webid values grows the dict without limit.
        _cutoff = _now - 30.0
        for _k in [k for k, ts in _voice_invite_ts.items() if ts < _cutoff]:
            del _voice_invite_ts[_k]
        _voice_invite_ts[_key] = _now

        # Contact check: caller must share a room with or have a stored relationship
        # with the target. Prevents cold-calling strangers on the gateway.
        if target_webid and target_webid != caller_webid:
            _is_contact = bool(
                self._store and self._store.get_relationship_by_did(target_webid)
            )
            if not _is_contact:
                _shared_room = any(
                    websocket in room.get("members", set())
                    and self._any_socket(target_webid) in room.get("members", set())
                    for room in self._local_rooms.values()
                )
                _is_contact = _shared_room
            if not _is_contact:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "voice_invite_not_allowed",
                }))
                return

        # Global session cap — prevents _voice_sessions memory exhaustion.
        if len(self._voice_sessions) >= 1000:
            await websocket.send(json.dumps({"type": "error", "message": "voice_sessions_full"}))
            return

        # Per-caller cap: max 5 pending (unanswered) outbound invites.
        _caller_active = sum(
            1 for s in self._voice_sessions.values()
            if s.get("caller_ws") is websocket and not s.get("answered")
        )
        if _caller_active >= 5:
            await websocket.send(json.dumps({"type": "error", "message": "too_many_active_invites"}))
            return

        self._voice_sessions[session_id] = {
            "caller_ws": websocket,
            "callee_ws": None,
            "answered": False,
            "caller_webid": caller_webid,
            "target_webid": target_webid,
        }
        asyncio.get_event_loop().call_later(
            60.0, lambda: self._voice_sessions.pop(session_id, None)
        )
        event = {
            "type": "voice_invite",
            "session_id": session_id,
            "caller_webid": caller_webid,
            "sdp_offer": sdp_offer,
            "cert_id": cert_id,
        }

        target_ws = self._any_socket(target_webid) if target_webid else None
        if target_ws and target_ws != websocket:
            self._voice_sessions[session_id]["callee_ws"] = target_ws
            await target_ws.send(json.dumps(event))
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

    async def _handle_voice_answer(self, websocket, data: dict) -> None:
        session_id = data.get("session_id", "")
        sdp_answer = data.get("sdp_answer", "")
        cert_id = data.get("cert_id")

        # Group voice: answer addressed directly to the calling peer by webid.
        target_webid = data.get("target_webid")
        if target_webid:
            sender_wid = self._client_webids.get(websocket, "")
            answer_event = {
                "type": "voice_answer",
                "from_webid": sender_wid,
                "session_id": session_id,
                "sdp_answer": sdp_answer,
            }
            target_ws = self._any_socket(target_webid)
            if target_ws and target_ws in self.clients:
                await target_ws.send(json.dumps(answer_event))
            else:
                peer_gw = self._resolve_peer_gateway(target_webid)
                if peer_gw:
                    asyncio.create_task(self._relay_voice_signal(
                        target_webid, "answer",
                        {"session_id": session_id, "sdp_answer": sdp_answer},
                    ))
            return

        sess = self._voice_sessions.get(session_id)
        if sess:
            _sender_wid = self._client_webids.get(websocket, "")
            _sess_caller = sess.get("caller_webid", "")
            _sess_target = sess.get("target_webid")
            if not (_sender_wid and (_sender_wid == _sess_caller or (_sess_target and _sender_wid == _sess_target))):
                await websocket.send(json.dumps({"type": "error", "message": "unauthorized"}))
                return
            if sess.get("answered"):
                await websocket.send(json.dumps({"type": "error", "message": "Call already answered"}))
                return
            if websocket not in (sess["caller_ws"], sess["callee_ws"]) and sess["callee_ws"] is not None:
                return
            if sess["callee_ws"] is None:
                sess["callee_ws"] = websocket
            sess["answered"] = True
            caller_ws = sess["caller_ws"]
            event = {"type": "voice_answer", "session_id": session_id, "sdp_answer": sdp_answer}
            if caller_ws and caller_ws in self.clients:
                await caller_ws.send(json.dumps(event))

        if sess and not (sess["caller_ws"] and sess["caller_ws"] in self.clients):
            # Caller is on a different gateway — try direct relay before pod
            _target = sess.get("caller_webid", "")
            if _target:
                asyncio.create_task(self._relay_voice_signal(
                    _target, "answer", {"session_id": session_id, "sdp_answer": sdp_answer}
                ))
            else:
                try:
                    client_entry = self.dm_clients.get(cert_id) if cert_id else None
                    if client_entry:
                        cert, pod_client = client_entry
                        from .voice import signal_voice_answer
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, signal_voice_answer,
                            cert, pod_client, session_id, sdp_answer,
                        )
                except Exception as exc:
                    logger.debug("Pod voice_answer write skipped: %s", exc)

    async def _handle_ice_candidate(self, websocket, data: dict) -> None:
        session_id = data.get("session_id", "")
        candidate = data.get("candidate", "")
        sdp_mid = data.get("sdp_mid")
        sdp_mline_index = data.get("sdp_mline_index")
        cert_id = data.get("cert_id")

        event = {
            "type": "ice_candidate",
            "session_id": session_id,
            "candidate": candidate,
            "sdp_mid": sdp_mid,
            "sdp_mline_index": sdp_mline_index,
        }

        # Group voice: candidates are addressed directly to a peer webid.
        # Route by webid (local socket or cross-gateway relay) bypassing the
        # 1:1 session model.
        target_webid = data.get("target_webid")
        if target_webid:
            sender_wid = self._client_webids.get(websocket, "")
            # Gate on co-membership of a voice channel: without this any
            # registered user could push ICE signaling at any webid (spam /
            # probing vector). The 1:1 session path below already checks
            # session membership; this is the group-path equivalent.
            _shares_channel = any(
                sender_wid in ch.get("members", {}) and target_webid in ch.get("members", {})
                for ch in self._voice_channels.values()
            )
            if not _shares_channel:
                return
            event["from_webid"] = sender_wid
            target_ws = self._any_socket(target_webid)
            if target_ws and target_ws in self.clients:
                await target_ws.send(json.dumps(event))
            else:
                peer_gw = self._resolve_peer_gateway(target_webid)
                if peer_gw:
                    asyncio.create_task(self._relay_voice_signal(
                        target_webid, "ice_candidate",
                        {"session_id": session_id, "candidate": candidate,
                         "sdp_mid": sdp_mid, "sdp_mline_index": sdp_mline_index},
                    ))
            return

        sess = self._voice_sessions.get(session_id)
        if not sess:
            return
        _sender_wid_ice = self._client_webids.get(websocket, "")
        _sess_caller_ice = sess.get("caller_webid", "")
        _sess_target_ice = sess.get("target_webid")
        if not (_sender_wid_ice and (_sender_wid_ice == _sess_caller_ice or (_sess_target_ice and _sender_wid_ice == _sess_target_ice))):
            await websocket.send(json.dumps({"type": "error", "message": "unauthorized"}))
            return
        if websocket not in (sess["caller_ws"], sess["callee_ws"]):
            return
        other_ws = sess["callee_ws"] if websocket == sess["caller_ws"] else sess["caller_ws"]
        if other_ws and other_ws in self.clients:
            await other_ws.send(json.dumps(event))
        else:
            # Peer is on a different gateway — relay signal directly for low-latency ICE
            _sess_ice = self._voice_sessions.get(session_id, {})
            _target_ice = (
                _sess_ice.get("target_webid")
                if websocket == _sess_ice.get("caller_ws")
                else _sess_ice.get("caller_webid")
            )
            if _target_ice:
                asyncio.create_task(self._relay_voice_signal(
                    _target_ice, "ice_candidate",
                    {"session_id": session_id, "candidate": candidate,
                     "sdp_mid": sdp_mid, "sdp_mline_index": sdp_mline_index},
                ))
            else:
                try:
                    client_entry = self.dm_clients.get(cert_id) if cert_id else None
                    if client_entry:
                        cert, pod_client = client_entry
                        from .voice import signal_ice_candidate
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, signal_ice_candidate,
                            cert, pod_client, session_id, candidate, sdp_mid, sdp_mline_index,
                        )
                except Exception as exc:
                    logger.debug("Pod ice_candidate write skipped: %s", exc)

    async def _handle_voice_hangup(self, websocket, data: dict) -> None:
        session_id = data.get("session_id", "")
        cert_id = data.get("cert_id")
        sess = self._voice_sessions.get(session_id)
        if not sess:
            return
        _sender_wid_hup = self._client_webids.get(websocket, "")
        _sess_caller_hup = sess.get("caller_webid", "")
        _sess_target_hup = sess.get("target_webid")
        if not (_sender_wid_hup and (_sender_wid_hup == _sess_caller_hup or (_sess_target_hup and _sender_wid_hup == _sess_target_hup))):
            await websocket.send(json.dumps({"type": "error", "message": "unauthorized"}))
            return
        self._voice_sessions.pop(session_id, None)
        event = {"type": "voice_hangup", "session_id": session_id}
        other_ws = sess["callee_ws"] if websocket == sess["caller_ws"] else sess["caller_ws"]
        if other_ws and other_ws in self.clients:
            await other_ws.send(json.dumps(event))
        else:
            _target_hup = sess.get("target_webid") if websocket == sess.get("caller_ws") else sess.get("caller_webid")
            if _target_hup:
                asyncio.create_task(self._relay_voice_signal(
                    _target_hup, "hangup", {"session_id": session_id}
                ))
            else:
                try:
                    client_entry = self.dm_clients.get(cert_id) if cert_id else None
                    if client_entry:
                        cert, pod_client = client_entry
                        from .voice import signal_voice_hangup
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, signal_voice_hangup,
                            cert, pod_client, session_id,
                        )
                except Exception as exc:
                    logger.debug("Pod voice_hangup write skipped: %s", exc)

    async def _handle_join_voice_channel(self, websocket, data: dict) -> None:
        """Add caller to a named voice channel and signal existing members.

        If the channel belongs to a federated room hosted on another gateway,
        relay the join request there instead of handling it locally.
        """
        channel_id = data.get("channel_id", "") or data.get("room_id", "")
        joiner_webid = self._client_webids.get(websocket, "")
        if not channel_id or not joiner_webid:
            return

        # Authz: a voice channel is scoped to its room. Joining a LOCAL room's
        # channel requires membership — otherwise anyone who knows the room id
        # could join its call and exchange voice with its members.
        if channel_id in self._local_rooms and websocket not in self._local_rooms[channel_id].get("members", set()):
            await websocket.send(json.dumps({
                "type": "error", "message": "not_a_room_member", "channel_id": channel_id,
            }))
            return

        own_gw = self._gateway_http_url()

        # If the room is not local but is in room_federated_members, relay join to host gateway
        if channel_id not in self._local_rooms and self._store:
            # Look for the room's host gateway via peer_gateways or federated membership
            _host_gw = self._resolve_peer_gateway(channel_id)
            if not _host_gw:
                # Try finding any peer gateway that hosts this room
                for _did, _gw in list(self._peer_gateway_urls.items()):
                    if _gw != own_gw:
                        _host_gw = _gw
                        break
            if _host_gw and _host_gw != own_gw:
                asyncio.create_task(self._relay_ephemeral(_host_gw, {
                    "content_type": "voice_channel_join",
                    "channel_id": channel_id,
                    "from_webid": joiner_webid,
                    "origin_gateway_url": own_gw,
                }))
                await websocket.send(json.dumps({
                    "type": "voice_channel_join_relayed",
                    "channel_id": channel_id,
                }))
                return

        channel = self._voice_channels.setdefault(channel_id, {"members": {}})
        existing = dict(channel["members"])

        if len(existing) >= 6:
            await websocket.send(json.dumps({
                "type": "warning",
                "code": "voice_channel_crowded",
                "channel_id": channel_id,
                "message": f"Channel has {len(existing)} members. Mesh connections may be slow.",
            }))

        # Store local member with gateway_url=None
        channel["members"][joiner_webid] = {"ws": websocket, "gateway_url": None}

        # Notify existing members of new joiner; tell joiner about each existing member
        for member_webid, member_info in existing.items():
            member_ws = member_info.get("ws") if isinstance(member_info, dict) else member_info
            member_gw = member_info.get("gateway_url") if isinstance(member_info, dict) else None
            if member_ws:
                try:
                    await member_ws.send(json.dumps({
                        "type": "voice_peer_joined",
                        "channel_id": channel_id,
                        "peer_webid": joiner_webid,
                        "gateway_url": "",
                    }))
                except Exception:
                    pass
            elif member_gw:
                # Remote member — relay the notification
                asyncio.create_task(self._relay_ephemeral(member_gw, {
                    "content_type": "voice_channel_peer_joined",
                    "channel_id": channel_id,
                    "peer_webid": joiner_webid,
                    "peer_gateway_url": own_gw or "",
                }))
            # Tell joiner about this existing member
            try:
                await websocket.send(json.dumps({
                    "type": "voice_peer_present",
                    "channel_id": channel_id,
                    "peer_webid": member_webid,
                    "gateway_url": member_gw or "",
                }))
            except Exception:
                pass

    async def _handle_voice_channel_join_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relay: a user on another gateway joins our local voice channel."""
        channel_id   = data.get("channel_id", "")
        from_webid   = data.get("from_webid", "")
        origin_gw    = data.get("origin_gateway_url", "")
        if not channel_id or not from_webid or not origin_gw:
            return "400 Bad Request", '{"error":"missing_voice_channel_join_fields"}'

        channel = self._voice_channels.setdefault(channel_id, {"members": {}})
        existing = dict(channel["members"])
        channel["members"][from_webid] = {"ws": None, "gateway_url": origin_gw}

        own_gw = self._gateway_http_url()

        # Notify local members of the remote joiner
        for member_webid, member_info in existing.items():
            member_ws = member_info.get("ws") if isinstance(member_info, dict) else member_info
            if member_ws:
                try:
                    await member_ws.send(json.dumps({
                        "type": "voice_peer_joined",
                        "channel_id": channel_id,
                        "peer_webid": from_webid,
                        "gateway_url": origin_gw,
                    }))
                except Exception:
                    pass

        # Tell the remote joiner who is already in the channel
        for member_webid, member_info in existing.items():
            member_gw = (member_info.get("gateway_url") if isinstance(member_info, dict) else None) or own_gw
            asyncio.create_task(self._relay_ephemeral(origin_gw, {
                "content_type": "voice_channel_peer_present",
                "channel_id": channel_id,
                "peer_webid": member_webid,
                "peer_gateway_url": member_gw,
            }))

        return "200 OK", '{"status":"ok"}'

    async def _handle_voice_channel_leave_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relay: a remote user left a local voice channel."""
        channel_id  = data.get("channel_id", "")
        from_webid  = data.get("from_webid", "")
        if not channel_id or not from_webid:
            return "400 Bad Request", '{"error":"missing_fields"}'

        channel = self._voice_channels.get(channel_id)
        if not channel:
            return "200 OK", '{"status":"ok"}'

        channel["members"].pop(from_webid, None)
        for member_webid, member_info in list(channel["members"].items()):
            member_ws = member_info.get("ws") if isinstance(member_info, dict) else member_info
            if member_ws:
                try:
                    await member_ws.send(json.dumps({
                        "type": "voice_peer_left",
                        "channel_id": channel_id,
                        "peer_webid": from_webid,
                    }))
                except Exception:
                    pass
        if not channel["members"]:
            self._voice_channels.pop(channel_id, None)
        return "200 OK", '{"status":"ok"}'

    async def _handle_voice_channel_peer_joined_relay(self, data: dict) -> tuple[str, str]:
        """Deliver voice_peer_joined to a local user from a relay notification."""
        target_webid = data.get("target_webid", "")
        channel_id   = data.get("channel_id", "")
        peer_webid   = data.get("peer_webid", "")
        peer_gw      = data.get("peer_gateway_url", "")
        # Deliver to all sockets of the target (or broadcast to channel members)
        sockets = self._sockets_for(target_webid) if target_webid else []
        event = json.dumps({
            "type": "voice_peer_joined",
            "channel_id": channel_id,
            "peer_webid": peer_webid,
            "gateway_url": peer_gw,
        })
        if not sockets and channel_id in self._voice_channels:
            for info in self._voice_channels[channel_id]["members"].values():
                ws = info.get("ws") if isinstance(info, dict) else info
                if ws:
                    sockets.append(ws)
        for ws in sockets:
            try:
                await ws.send(event)
            except Exception:
                pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_voice_channel_peer_present_relay(self, data: dict) -> tuple[str, str]:
        """Deliver voice_peer_present to a local user from a relay notification."""
        channel_id = data.get("channel_id", "")
        peer_webid = data.get("peer_webid", "")
        peer_gw    = data.get("peer_gateway_url", "")
        event = json.dumps({
            "type": "voice_peer_present",
            "channel_id": channel_id,
            "peer_webid": peer_webid,
            "gateway_url": peer_gw,
        })
        if channel_id in self._voice_channels:
            for info in self._voice_channels[channel_id]["members"].values():
                ws = info.get("ws") if isinstance(info, dict) else info
                if ws:
                    try:
                        await ws.send(event)
                    except Exception:
                        pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_leave_voice_channel(self, websocket, data: dict) -> None:
        """Remove caller from a voice channel and notify remaining members."""
        channel_id = data.get("channel_id", "") or data.get("room_id", "")
        leaver_webid = self._client_webids.get(websocket, "")
        if not channel_id or not leaver_webid:
            return

        channel = self._voice_channels.get(channel_id)
        if not channel:
            return

        channel["members"].pop(leaver_webid, None)
        own_gw = self._gateway_http_url()

        for member_webid, member_info in list(channel["members"].items()):
            member_ws = member_info.get("ws") if isinstance(member_info, dict) else member_info
            member_gw = member_info.get("gateway_url") if isinstance(member_info, dict) else None
            leave_event = json.dumps({
                "type": "voice_peer_left",
                "channel_id": channel_id,
                "peer_webid": leaver_webid,
            })
            if member_ws:
                try:
                    await member_ws.send(leave_event)
                except Exception:
                    pass
            elif member_gw:
                asyncio.create_task(self._relay_ephemeral(member_gw, {
                    "content_type": "voice_channel_leave",
                    "channel_id": channel_id,
                    "from_webid": leaver_webid,
                    "origin_gateway_url": own_gw,
                }))

        if not channel["members"]:
            self._voice_channels.pop(channel_id, None)
