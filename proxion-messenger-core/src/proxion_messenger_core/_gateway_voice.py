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

# Per-pair ring throttle: (caller_webid, target_webid) -> recent invite times
# (monotonic). At most _INVITE_BURST rings per pair per _INVITE_WINDOW seconds,
# and never a second ring while the first is still ringing. A burst rather than
# one ring per 30 s, so a redial after a decline or a cancelled call goes through.
_voice_invite_ts: dict = {}
_INVITE_WINDOW = 60.0
_INVITE_BURST = 3
_HANGUP_REASONS = ("declined", "busy")


class VoiceHandlerMixin:

    def _voice_session_binds(self, session_id, a, b) -> bool:
        """True when this gateway's 1:1 session `session_id` is between a and b.

        Two accounts on one gateway may have no stored relationship with each
        other, so answer/ICE addressed by webid must also route on the session.
        """
        sess = self._voice_sessions.get(session_id) if session_id else None
        if not sess or not a or not b or a == b:
            return False
        parties = {sess.get("caller_webid"), sess.get("target_webid")}
        return a in parties and b in parties

    async def _cleanup_voice_sessions(self, websocket, webid: str = "") -> None:
        """Notify the other party and remove any voice sessions the socket was in.

        ``webid`` is the socket's identity. handle_client pops _client_webids
        before calling this, so it passes the webid in; without it the voice
        channel cleanup below found no identity and left a ghost member."""
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

        # Also remove from any voice channels this socket is in. Only the entry
        # owned by THIS socket: another tab of the same account may be the one in
        # the call, and closing an idle tab must not drop it.
        leaver_webid = webid or self._client_webids.get(websocket, "")
        if leaver_webid:
            for ch_id in list(self._voice_channels.keys()):
                ch = self._voice_channels.get(ch_id)
                minfo = ch.get("members", {}).get(leaver_webid) if ch else None
                mws = minfo.get("ws") if isinstance(minfo, dict) else minfo
                if minfo is not None and mws is websocket:
                    await self._depart_voice_channel(ch_id, leaver_webid)

    async def _depart_voice_channel(self, channel_id: str, leaver_webid: str) -> None:
        """Remove a local member from a voice channel and tell everyone else:
        local members by socket, remote members' gateways by relay, and, for a
        channel hosted on another gateway, the host too. The host must hear it
        even when the leaver was alone in the call, or it keeps a ghost."""
        channel = self._voice_channels.get(channel_id)
        if not channel:
            return
        channel["members"].pop(leaver_webid, None)
        own_gw = self._gateway_http_url()
        leave_event = json.dumps({
            "type": "voice_peer_left",
            "channel_id": channel_id,
            "peer_webid": leaver_webid,
        })
        relay_to = []
        for member_info in list(channel["members"].values()):
            member_ws = member_info.get("ws") if isinstance(member_info, dict) else member_info
            member_gw = member_info.get("gateway_url") if isinstance(member_info, dict) else None
            if member_ws:
                try:
                    await member_ws.send(leave_event)
                except Exception:
                    pass
            elif member_gw and member_gw not in relay_to:
                relay_to.append(member_gw)
        host_gw = channel.get("host_gateway_url")
        if host_gw and host_gw != own_gw and host_gw not in relay_to:
            relay_to.append(host_gw)
        for gw_url in relay_to:
            asyncio.create_task(self._relay_ephemeral(gw_url, {
                "content_type": "voice_channel_leave",
                "channel_id": channel_id,
                "from_webid": leaver_webid,
                "origin_gateway_url": own_gw,
            }))
        # A channel mirrored for a room hosted elsewhere only exists for our local
        # users; once none is left, drop it with its remote routing entries.
        if not channel["members"] or (
            channel_id not in self._local_rooms
            and not any((m.get("ws") if isinstance(m, dict) else m)
                        for m in channel["members"].values())):
            self._voice_channels.pop(channel_id, None)
        await self._broadcast_voice_channel_state(channel_id)

    def _voice_channel_participants(self, channel_id: str) -> list:
        ch = self._voice_channels.get(channel_id) or {}
        return sorted(ch.get("members", {}).keys())

    async def _broadcast_voice_channel_state(self, channel_id: str) -> None:
        """Tell every connected member of a local room who is in its voice
        channel, so members outside the call can see it and join."""
        room = self._local_rooms.get(channel_id)
        if not room:
            return
        event = json.dumps({
            "type": "voice_channel_state", "room_id": channel_id,
            "participants": self._voice_channel_participants(channel_id),
        })
        for ws in list(room.get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass

    async def _handle_get_voice_channel_state(self, websocket, data: dict) -> None:
        """Who is in a room's voice channel. Room members only."""
        room_id = data.get("room_id", "")
        room = self._local_rooms.get(room_id)
        if not room or not self._check_room_permission(websocket, room_id, role="member"):
            return
        await websocket.send(json.dumps({
            "type": "voice_channel_state", "room_id": room_id,
            "participants": self._voice_channel_participants(room_id),
        }))

    def _voice_channel_peer_gateway(self, target_webid: str) -> "str | None":
        """Channel-scoped gateway lookup: find target's gateway_url from any voice
        channel it belongs to. Lets voice signaling reach a call co-member who is
        not a direct friend (hence absent from _peer_gateway_urls) WITHOUT writing
        into the global peer-gateway routing table (which a channel notification
        must never poison)."""
        for ch in getattr(self, "_voice_channels", {}).values():
            info = ch.get("members", {}).get(target_webid)
            gw = info.get("gateway_url") if isinstance(info, dict) else None
            if gw:
                return gw
        return None

    def _host_gateway_dids(self, gateway_url: str, channel_id: str = "") -> set:
        """DIDs known — from OUR trusted routing state, never an inbound payload — to
        reside at *gateway_url*.

        Used on the joiner side to pin a cross-gateway voice channel's accepted
        signer to the KNOWN host. voice_channel_peer_present/peer_joined carry no
        from_webid, so without a pin _voice_channel_gateway_ok TOFU-accepts the FIRST
        signer to POST — letting an attacker who wins the race become the trusted
        channel gateway and inject ghost peers (F10). The signer is a gateway's own
        identity did (== the peer's account did in the one-gateway-per-user model),
        which _peer_gateway_urls and federated room membership already map to the
        host URL from prior authenticated federation."""
        dids: set = set()
        if not gateway_url:
            return dids
        for _did, _gw in getattr(self, "_peer_gateway_urls", {}).items():
            if _gw == gateway_url and _did:
                dids.add(_did)
        if channel_id and self._store:
            try:
                for _m in (self._store.get_federated_room_members(channel_id) or []):
                    if _m.get("gateway_url") == gateway_url and _m.get("member_did"):
                        dids.add(_m["member_did"])
            except Exception:
                pass
        return dids

    def _joiner_channel_signer_ok(self, channel_id: str, data: dict) -> bool:
        """On the joiner side of a cross-gateway voice channel, confirm a
        peer_present/peer_joined relay was signed by the pinned host gateway.

        The channel records the host_gateway_url the joiner relayed its join to; the
        relay signer (relay_sig_did) must be one of the pinned host dids OR resolve —
        via OUR trusted routing state — to that same host URL. This blocks a non-host
        signer that won the TOFU race from injecting ghost peers. Returns True when
        there is no joiner pin to enforce (local-room / host side), leaving those
        paths to their own membership gates."""
        ch = getattr(self, "_voice_channels", {}).get(channel_id)
        host = ch.get("host_gateway_url") if ch else None
        if not host or channel_id in self._local_rooms:
            return True
        signer = data.get("relay_sig_did", "")
        if not signer:
            return False
        if signer in ch.get("gateway_dids", set()):
            return True
        return self._resolve_peer_gateway(signer) == host

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
        from .relay import sign_relay_envelope, post_relay
        from .didkey import pub_key_to_did

        gateway_url = self._resolve_peer_gateway(target_webid) or self._voice_channel_peer_gateway(target_webid)
        if not gateway_url:
            return False

        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        relay_nonce = _sec.token_hex(8)
        session_id = signal_data.get("session_id", "")
        # Synthetic message_id so relay dedup logic has a stable key
        message_id = f"vs:{signal_type}:{session_id}:{relay_nonce}"
        ts = _time.strftime("%Y-%m-%dT%H:%M:%S+00:00", _time.gmtime())

        my_http = self._gateway_http_url() if hasattr(self, "_gateway_http_url") else ""
        # from_webid IS this gateway's did (the voice actor is the gateway), so the
        # envelope signature both authenticates the payload and self-binds the
        # sender (relay_sig_did == from_webid) — DM-style. (R55)
        payload = {
            "from_webid": gateway_did,
            "to_webid": target_webid,
            "message_id": message_id,
            "content": signal_type,
            "timestamp": ts,
            "relay_nonce": relay_nonce,
            "origin_gateway_url": my_http,
            "content_type": "voice_signal",
            "signal_type": signal_type,
            "signal_data": signal_data,
            "session_id": session_id,
            "relay_sig_did": gateway_did,
            "relay_ts": ts,
        }
        try:
            payload["signature"] = sign_relay_envelope(self.agent.identity_key, payload)
        except Exception as exc:
            logger.debug("_relay_voice_signal sign failed: %s", exc)
            return False
        http_base = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
        try:
            return await post_relay(http_base.rstrip("/") + "/relay", payload)
        except Exception as exc:
            logger.debug("_relay_voice_signal post failed: %s", exc)
            return False

    def _push_missed_call(self, to_webid: str, from_webid: str = "") -> bool:
        """Web-push an offline account about a missed call, honoring mutes. Privacy-
        preserving: type only, no caller identity in the payload (R82 W2)."""
        if not self._store or not to_webid:
            return False
        if from_webid and self._store.is_thread_muted(to_webid, from_webid):
            return False
        subs = self._store.get_push_subscriptions(to_webid)
        vpk = getattr(self, "_vapid_private_pem", None)
        vsub = getattr(self, "_vapid_subject", None)
        if not (subs and vpk and vsub):
            return False
        from .webpush import send_web_push
        sent = False
        for sub in subs:
            try:
                ok = send_web_push(
                    subscription={"endpoint": sub["endpoint"],
                                  "keys": {"p256dh": sub["p256dh_b64"], "auth": sub["auth_b64"]}},
                    payload={"type": "missed_call", "thread_id": ""},
                    vapid_private_pem=vpk, vapid_subject=vsub,
                )
                sent = sent or bool(ok)
            except Exception:
                pass
        return sent

    async def _handle_voice_invite(self, websocket, data: dict) -> None:
        import secrets as _secrets
        import time as _time
        session_id = data.get("session_id") or _secrets.token_hex(16)
        if session_id in self._voice_sessions:
            # A caller-chosen id must be fresh; reusing a live one would replace
            # that session's endpoints.
            await websocket.send(json.dumps({"type": "error", "message": "voice_session_exists"}))
            return
        sdp_offer = data.get("sdp_offer", "")
        target_webid = data.get("target_webid")
        cert_id = data.get("cert_id")
        caller_webid = self._client_webids.get(websocket, self.agent.identity_pub_bytes.hex())

        _key = (caller_webid, target_webid)
        _now = _time.monotonic()
        _cutoff = _now - _INVITE_WINDOW
        # Evict stale entries before inserting to bound memory usage.
        # Without this, a flood of random target_webid values grows the dict without limit.
        for _k in [k for k, ts in _voice_invite_ts.items() if not ts or ts[-1] < _cutoff]:
            del _voice_invite_ts[_k]
        # A group-call mesh offer between two members of the same voice channel
        # is not a ring: it is auto-answered, and it is re-sent on every rejoin
        # and ICE recovery. Throttling it left the pair without audio.
        _ch = self._voice_channels.get(data.get("channel_id") or "") or {}
        _mesh = bool(target_webid) and target_webid in _ch.get("members", {}) and any(
            (m.get("ws") if isinstance(m, dict) else m) is websocket
            for m in _ch.get("members", {}).values())
        if not _mesh:
            _recent = [t for t in _voice_invite_ts.get(_key, []) if t >= _cutoff]
            _ringing = any(
                s.get("caller_webid") == caller_webid and s.get("target_webid") == target_webid
                and not s.get("answered")
                for s in self._voice_sessions.values())
            if _ringing or len(_recent) >= _INVITE_BURST:
                await websocket.send(json.dumps({"type": "error", "message": "call_too_frequent"}))
                return
            _voice_invite_ts[_key] = _recent + [_now]

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
                # Group call: the caller and target are co-members of a voice
                # channel (the target may be a remote peer with no local socket and
                # no direct relationship, so the room/relationship checks miss).
                _is_contact = any(
                    target_webid in ch.get("members", {})
                    and any((m.get("ws") if isinstance(m, dict) else m) is websocket
                            for m in ch.get("members", {}).values())
                    for ch in self._voice_channels.values()
                )
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
        # Ring timeout: drop the session if nobody answered within a minute. An
        # answered session lives until hangup or disconnect.
        def _expire_unanswered(sid=session_id):
            if not self._voice_sessions.get(sid, {}).get("answered", True):
                self._voice_sessions.pop(sid, None)
        asyncio.get_event_loop().call_later(60.0, _expire_unanswered)
        # R79: identity-bound DTLS fingerprint signature travels with the SDP so the
        # callee can detect a gateway that tampered with the media channel. Opaque to
        # the gateway; forwarded verbatim.
        fp_sig = data.get("fp_sig", "")
        fp_signer = data.get("fp_signer", "")
        # Device->account cert that binds a linked device's signing key to the account
        # (opaque to the gateway; forwarded verbatim so the callee can verify identity).
        fp_cert = data.get("fp_cert")
        event = {
            "type": "voice_invite",
            "session_id": session_id,
            "caller_webid": caller_webid,
            "sdp_offer": sdp_offer,
            "cert_id": cert_id,
            "fp_sig": fp_sig,
            "fp_signer": fp_signer,
            "fp_cert": fp_cert,
        }

        target_ws = self._any_socket(target_webid) if target_webid else None
        if target_ws and target_ws != websocket:
            self._voice_sessions[session_id]["callee_ws"] = target_ws
            await target_ws.send(json.dumps(event))
        else:
            # Target is on a different gateway.
            # First try: relay (fast, works without pod)
            _relayed = False
            _delivered_local = False
            if target_webid:
                try:
                    _relayed = await self._relay_voice_signal(
                        target_webid, "offer",
                        {
                            "session_id": session_id,
                            "sdp_offer": sdp_offer,
                            "caller_webid": caller_webid,
                            "fp_sig": fp_sig,
                            "fp_signer": fp_signer,
                            "fp_cert": fp_cert,
                        },
                    )
                except Exception:
                    pass
            # Second try: pod write (slower, requires federation cert). Look up the pod
            # client by the passed cert_id first, then by the target's relationship cert_id
            # (voice fallback is federated-only, so no own-pod webid fallback here).
            if not _relayed:
                try:
                    client_entry = self.dm_clients.get(cert_id) if cert_id else None
                    if not client_entry and target_webid and self._store:
                        _rel = self._store.get_relationship_by_did(target_webid)
                        if _rel:
                            client_entry = self.dm_clients.get(_rel.get("certificate_id"))
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

            # Nothing reached the callee's gateway: tell the caller it is unavailable
            # (so the UI stops ringing into the void) and try a local missed-call push
            # in case the callee is a known local user who is simply offline (R82 W1/W2).
            if not _relayed and target_webid:
                # Nothing rang, so this was no spam: let the caller try again as
                # soon as the callee comes online, and drop the dead session.
                _voice_invite_ts.pop(_key, None)
                self._voice_sessions.pop(session_id, None)
                try:
                    await websocket.send(json.dumps({
                        "type": "voice_unavailable", "session_id": session_id,
                        "target_webid": target_webid,
                    }))
                except Exception:
                    pass
                try:
                    asyncio.get_event_loop().run_in_executor(
                        None, self._push_missed_call, target_webid, caller_webid)
                except Exception:
                    pass

    async def _handle_voice_answer(self, websocket, data: dict) -> None:
        session_id = data.get("session_id", "")
        sdp_answer = data.get("sdp_answer", "")
        cert_id = data.get("cert_id")
        # R79: identity-bound fingerprint signature for the answer, forwarded verbatim.
        fp_sig = data.get("fp_sig", "")
        fp_signer = data.get("fp_signer", "")
        fp_cert = data.get("fp_cert")

        # Group voice: answer addressed directly to the calling peer by webid.
        target_webid = data.get("target_webid")
        if target_webid:
            sender_wid = self._client_webids.get(websocket, "")
            # Gate on either a voice-channel co-membership (group calls) OR a stored
            # relationship (1:1 across gateways route by webid too), mirroring the ICE
            # handler. Without it any registered user could push an SDP answer at any
            # webid. Return without delivering if it fails.
            _shares_channel = any(
                sender_wid in ch.get("members", {}) and target_webid in ch.get("members", {})
                for ch in self._voice_channels.values()
            )
            _related = bool(self._store and self._store.get_relationship_by_did(target_webid))
            _in_session = self._voice_session_binds(session_id, sender_wid, target_webid)
            if not (_shares_channel or _related or _in_session):
                return
            if _in_session:
                _s = self._voice_sessions[session_id]
                _s["answered"] = True
                if _s.get("callee_ws") is None:
                    _s["callee_ws"] = websocket
            answer_event = {
                "type": "voice_answer",
                "from_webid": sender_wid,
                "session_id": session_id,
                "sdp_answer": sdp_answer,
                "fp_sig": fp_sig,
                "fp_signer": fp_signer,
                "fp_cert": fp_cert,
            }
            target_ws = self._any_socket(target_webid)
            if target_ws and target_ws in self.clients:
                await target_ws.send(json.dumps(answer_event))
            else:
                # Relay to the target's gateway. Let _relay_voice_signal resolve it
                # (it falls back to the channel-scoped gateway for a group-call
                # co-member who isn't a direct friend, so _resolve_peer_gateway
                # alone would return None and drop the answer).
                asyncio.create_task(self._relay_voice_signal(
                    target_webid, "answer",
                    {"session_id": session_id, "sdp_answer": sdp_answer,
                     "caller_webid": sender_wid, "fp_sig": fp_sig, "fp_signer": fp_signer,
                     "fp_cert": fp_cert},
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
            event = {"type": "voice_answer", "session_id": session_id, "sdp_answer": sdp_answer,
                     "fp_sig": fp_sig, "fp_signer": fp_signer, "fp_cert": fp_cert}
            if caller_ws and caller_ws in self.clients:
                await caller_ws.send(json.dumps(event))

        if sess and not (sess["caller_ws"] and sess["caller_ws"] in self.clients):
            # Caller is on a different gateway — try direct relay before pod
            _target = sess.get("caller_webid", "")
            if _target:
                # Carry the fingerprint binding proof so the account-bound signature
                # survives this hop too (mirrors the target_webid relay above) (CF1).
                asyncio.create_task(self._relay_voice_signal(
                    _target, "answer", {"session_id": session_id, "sdp_answer": sdp_answer,
                     "fp_sig": fp_sig, "fp_signer": fp_signer, "fp_cert": fp_cert}
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
            # Gate on either a voice-channel co-membership (group calls) OR a stored
            # relationship (1:1 calls across gateways route by webid too, since the
            # callee's gateway holds no 1:1 session). Without a gate any registered
            # user could push ICE signaling at any webid (spam / probing vector).
            _shares_channel = any(
                sender_wid in ch.get("members", {}) and target_webid in ch.get("members", {})
                for ch in self._voice_channels.values()
            )
            _related = bool(self._store and self._store.get_relationship_by_did(target_webid))
            _in_session = self._voice_session_binds(session_id, sender_wid, target_webid)
            if not (_shares_channel or _related or _in_session):
                return
            event["from_webid"] = sender_wid
            target_ws = self._any_socket(target_webid)
            if target_ws and target_ws in self.clients:
                await target_ws.send(json.dumps(event))
            else:
                # _relay_voice_signal resolves the target's gateway with a
                # channel-scoped fallback (a group co-member isn't necessarily a
                # friend, so _resolve_peer_gateway alone would return None).
                asyncio.create_task(self._relay_voice_signal(
                    target_webid, "ice_candidate",
                    {"session_id": session_id, "candidate": candidate,
                     "sdp_mid": sdp_mid, "sdp_mline_index": sdp_mline_index,
                     "caller_webid": sender_wid},
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
        # Why the call ended, so the other side can say "declined" or "busy"
        # instead of ringing out. Fixed vocabulary; anything else is dropped.
        reason = data.get("reason") if data.get("reason") in _HANGUP_REASONS else ""
        sess = self._voice_sessions.get(session_id)
        if not sess:
            # Cross-gateway the callee's gateway holds no session (the offer came in
            # by relay), so a callee's decline or hangup has to be routed by webid
            # to the caller's gateway, or the caller keeps ringing / sits in a dead
            # call. Same gate as webid-routed answer/ICE: a stored relationship.
            target_webid = data.get("target_webid")
            sender_wid = self._client_webids.get(websocket, "")
            if (session_id and target_webid and sender_wid and target_webid != sender_wid
                    and self._store and self._store.get_relationship_by_did(target_webid)
                    and not self._any_socket(target_webid)):
                sig = {"session_id": session_id}
                if reason:
                    sig["reason"] = reason
                asyncio.create_task(self._relay_voice_signal(target_webid, "hangup", sig))
            return
        _sender_wid_hup = self._client_webids.get(websocket, "")
        _sess_caller_hup = sess.get("caller_webid", "")
        _sess_target_hup = sess.get("target_webid")
        if not (_sender_wid_hup and (_sender_wid_hup == _sess_caller_hup or (_sess_target_hup and _sender_wid_hup == _sess_target_hup))):
            await websocket.send(json.dumps({"type": "error", "message": "unauthorized"}))
            return
        self._voice_sessions.pop(session_id, None)
        if sess.get("answered"):
            # The call connected, so calling back right away is not spam: lift the
            # per-pair invite throttle, so a dropped call can always be redialed.
            _voice_invite_ts.pop((sess.get("caller_webid"), sess.get("target_webid")), None)
            _voice_invite_ts.pop((sess.get("target_webid"), sess.get("caller_webid")), None)
        event = {"type": "voice_hangup", "session_id": session_id}
        if reason:
            event["reason"] = reason
        other_ws = sess["callee_ws"] if websocket == sess["caller_ws"] else sess["caller_ws"]
        if other_ws and other_ws in self.clients:
            await other_ws.send(json.dumps(event))
        else:
            _target_hup = sess.get("target_webid") if websocket == sess.get("caller_ws") else sess.get("caller_webid")
            if _target_hup:
                _sig = {"session_id": session_id}
                if reason:
                    _sig["reason"] = reason
                asyncio.create_task(self._relay_voice_signal(
                    _target_hup, "hangup", _sig
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
                # Register the joiner in a LOCAL channel entry so the host's
                # inbound voice_channel_peer_present / peer_joined notifications
                # can be delivered to this client's socket. Previously we returned
                # early with no local channel, so those notifications hit
                # `if channel_id in self._voice_channels` = False and were dropped —
                # the joiner never learned who else was present and the mesh never
                # formed for cross-gateway group calls.
                channel = self._voice_channels.setdefault(channel_id, {"members": {}})
                channel["members"][joiner_webid] = {"ws": websocket, "gateway_url": None}
                channel["host_gateway_url"] = _host_gw
                # Pin the channel's accepted signer(s) to the KNOWN host gateway so an
                # attacker cannot win the peer_present/peer_joined TOFU race and inject
                # ghost peers (F10). Resolve the host gateway did(s) from OUR trusted
                # routing state (never from an inbound, forgeable payload); a non-empty
                # gateway_dids set stops _voice_channel_gateway_ok's blind first-signer
                # TOFU and admits only the pinned host. _host_gw was itself derived from
                # that routing state above, so at least its did is recovered here.
                channel.setdefault("gateway_dids", set()).update(
                    self._host_gateway_dids(_host_gw, channel_id))
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
        # Seed this local channel's participant-gateway set with our OWN did so its
        # slot is never empty. An empty set makes _voice_channel_gateway_ok TOFU-accept
        # ANY signer, letting a relayed peer_present/peer_joined inject a ghost peer
        # into a purely local channel (D1). Seeding here closes that bootstrap.
        channel.setdefault("gateway_dids", set()).add(self._own_gateway_did())
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
        await self._broadcast_voice_channel_state(channel_id)

    async def _handle_voice_channel_join_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relay: a user on another gateway joins our local voice channel."""
        channel_id   = data.get("channel_id", "")
        from_webid   = data.get("from_webid", "")
        origin_gw    = data.get("origin_gateway_url", "")
        if not channel_id or not from_webid or not origin_gw:
            return "400 Bad Request", '{"error":"missing_voice_channel_join_fields"}'

        # Authz: a voice channel is scoped to its room. A remote join must come
        # from a room member — otherwise anyone who knows the channel/room id
        # could join the call, join the ICE mesh, and see who's present. The
        # LOCAL join path checks this (e913357); the relay path didn't.
        if self._store and self._store.is_room_banned(channel_id, from_webid):
            return "403 Forbidden", '{"error":"banned_from_room"}'
        if channel_id in self._local_rooms and self._store:
            _members = set(self._store.get_room_members(channel_id))
            _members |= {m.get("member_did") for m in self._store.get_federated_room_members(channel_id)}
            _members.discard("")
            if _members and from_webid not in _members:
                return "403 Forbidden", '{"error":"not_a_room_member"}'

        channel = self._voice_channels.setdefault(channel_id, {"members": {}})
        existing = dict(channel["members"])
        channel["members"][from_webid] = {"ws": None, "gateway_url": origin_gw}
        # Record the joiner's gateway (proven by the envelope signature verified at
        # the /relay dispatch) as a channel participant, so its later
        # peer_joined/peer_present notifications are authorized (R55).
        _sig_did = data.get("relay_sig_did", "")
        if _sig_did:
            channel.setdefault("gateway_dids", set()).add(_sig_did)

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

        await self._broadcast_voice_channel_state(channel_id)
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
        await self._broadcast_voice_channel_state(channel_id)
        return "200 OK", '{"status":"ok"}'

    async def _handle_voice_channel_peer_joined_relay(self, data: dict) -> tuple[str, str]:
        """Deliver voice_peer_joined to a local user from a relay notification."""
        target_webid = data.get("target_webid", "")
        channel_id   = data.get("channel_id", "")
        peer_webid   = data.get("peer_webid", "")
        peer_gw      = data.get("peer_gateway_url", "")
        # Joiner-side host pin (F10): a channel this gateway created as a JOINER
        # records the known host_gateway_url. peer_present/peer_joined carry no
        # from_webid, so _voice_channel_gateway_ok would otherwise TOFU-accept the
        # first signer to POST. Require the signer to be the pinned host, blocking a
        # racing non-host gateway from injecting ghost peers.
        if not self._joiner_channel_signer_ok(channel_id, data):
            return "200 OK", '{"status":"received"}'
        # Membership gate (D1): for a channel scoped to a LOCAL room, the injected
        # peer must be a known room member. Otherwise a channel-bound signer could
        # inject a ghost peer the victim then opens WebRTC to. The joiner side (a
        # channel that is NOT a local room here) has no membership records; its
        # signer is authorized as a channel gateway at the /relay dispatch.
        if channel_id in self._local_rooms and not self._relay_from_is_room_member(channel_id, peer_webid):
            return "200 OK", '{"status":"received"}'
        # Record the peer + its gateway in the local channel roster so voice_signal
        # to that peer can resolve a gateway (channel-scoped, not global routing)
        # even when the two users aren't direct friends.
        if channel_id in self._voice_channels and peer_webid:
            _ch = self._voice_channels[channel_id]
            _existing = _ch["members"].get(peer_webid)
            if not (isinstance(_existing, dict) and _existing.get("ws")):
                _ch["members"][peer_webid] = {"ws": None, "gateway_url": peer_gw or None}
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
        # Joiner-side host pin (F10): a channel this gateway created as a JOINER
        # records the known host_gateway_url. peer_present/peer_joined carry no
        # from_webid, so _voice_channel_gateway_ok would otherwise TOFU-accept the
        # first signer to POST. Require the signer to be the pinned host, blocking a
        # racing non-host gateway from injecting ghost peers.
        if not self._joiner_channel_signer_ok(channel_id, data):
            return "200 OK", '{"status":"received"}'
        # Membership gate (D1): for a channel scoped to a LOCAL room, the injected
        # peer must be a known room member. Otherwise a channel-bound signer could
        # inject a ghost peer the victim then opens WebRTC to. The joiner side (a
        # channel that is NOT a local room here) has no membership records; its
        # signer is authorized as a channel gateway at the /relay dispatch.
        if channel_id in self._local_rooms and not self._relay_from_is_room_member(channel_id, peer_webid):
            return "200 OK", '{"status":"received"}'
        event = json.dumps({
            "type": "voice_peer_present",
            "channel_id": channel_id,
            "peer_webid": peer_webid,
            "gateway_url": peer_gw,
        })
        if channel_id in self._voice_channels:
            _ch = self._voice_channels[channel_id]
            # Record the existing peer + its gateway in the local roster so this
            # joiner can route voice_signal back to it (channel-scoped resolution).
            if peer_webid:
                _existing = _ch["members"].get(peer_webid)
                if not (isinstance(_existing, dict) and _existing.get("ws")):
                    _ch["members"][peer_webid] = {"ws": None, "gateway_url": peer_gw or None}
            for info in list(_ch["members"].values()):
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
        await self._depart_voice_channel(channel_id, leaver_webid)
