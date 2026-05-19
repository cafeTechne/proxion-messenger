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
            # Peer is on a different gateway — write ICE candidate to pod.
            # TURN relay is strongly recommended for cross-gateway calls to avoid
            # ICE timeout (trickle ICE via 3-second pod poll is marginal).
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
