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
                    for mws in list(ch.get("members", {}).values()):
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
            # Target on a different gateway — write invite to pod
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
        """Add caller to a named voice channel and signal existing members."""
        channel_id = data.get("channel_id", "")
        joiner_webid = self._client_webids.get(websocket, "")
        if not channel_id or not joiner_webid:
            return

        channel = self._voice_channels.setdefault(channel_id, {"members": {}})
        existing = dict(channel["members"])  # snapshot before adding self

        if len(existing) >= 6:
            await websocket.send(json.dumps({
                "type": "warning",
                "code": "voice_channel_crowded",
                "channel_id": channel_id,
                "message": f"Channel has {len(existing)} members. Mesh connections may be slow.",
            }))

        channel["members"][joiner_webid] = websocket

        # Notify existing members and inform joiner of who's already there
        for member_webid, member_ws in existing.items():
            try:
                await member_ws.send(json.dumps({
                    "type": "voice_peer_joined",
                    "channel_id": channel_id,
                    "peer_webid": joiner_webid,
                }))
            except Exception:
                pass
            # Tell joiner about this existing member (they will receive a voice_invite from them)
            await websocket.send(json.dumps({
                "type": "voice_peer_present",
                "channel_id": channel_id,
                "peer_webid": member_webid,
            }))

    async def _handle_leave_voice_channel(self, websocket, data: dict) -> None:
        """Remove caller from a voice channel and notify remaining members."""
        channel_id = data.get("channel_id", "")
        leaver_webid = self._client_webids.get(websocket, "")
        if not channel_id or not leaver_webid:
            return

        channel = self._voice_channels.get(channel_id)
        if not channel:
            return

        channel["members"].pop(leaver_webid, None)

        for member_ws in list(channel["members"].values()):
            try:
                await member_ws.send(json.dumps({
                    "type": "voice_peer_left",
                    "channel_id": channel_id,
                    "peer_webid": leaver_webid,
                }))
            except Exception:
                pass

        if not channel["members"]:
            self._voice_channels.pop(channel_id, None)
