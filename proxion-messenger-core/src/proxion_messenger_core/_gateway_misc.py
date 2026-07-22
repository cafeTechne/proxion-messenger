"""MiscHandlerMixin — presence, identity, session, pod-mgmt, and utility command handlers.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: _client_webids, _user_presence, _display_names, _store,
                  _webid_sockets, _session_meta, _voice_sessions, _pod_webid,
                  _pod_url, dm_clients, room_memberships, agent, config, stash,
                  read_state, blocklist, broadcast(), _any_socket(),
                  _ws_public_url(), _proxion_address(), _make_turn_creds(),
                  _connect_css_sync(), _reconnect_stored_pod_sync(),
                  _ensure_pod_room_containers().
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("proxion_messenger_core.gateway")


class MiscHandlerMixin:

    async def _handle_set_presence(self, websocket, data: dict) -> None:
        status = data.get("status", "online")
        status_message = data.get("status_message", "").strip()
        if len(status_message) > 100:
            status_message = status_message[:100]

        webid = self._client_webids.get(websocket)

        if webid and status in ("online", "away", "busy", "offline"):
            now = datetime.now(timezone.utc).isoformat()
            old_presence = self._user_presence.get(webid, {})
            last_active = old_presence.get("last_active_at", now) if status != "offline" else now

            self._user_presence[webid] = {
                "status": status,
                "status_message": status_message,
                "updated_at": now,
                "last_active_at": last_active
            }
            await self.broadcast({
                "type": "presence_update",
                "webid": webid,
                "status": status,
                "status_message": status_message,
                "updated_at": now,
                "last_active_at": last_active
            })

        from .presence import set_presence
        if self.dm_clients:
            _, client = next(iter(self.dm_clients.values()))
            try:
                set_presence(client, status, webid if webid else "unknown")
                logger.info(f"Presence set to: {status}")
            except Exception as e:
                logger.debug(f"Failed to set presence on pod: {e}")

        # Relay presence to all known federated peers (fire-and-forget)
        if webid and self._peer_gateway_urls:
            _now_iso = datetime.now(timezone.utc).isoformat()
            _pres_payload = {
                "content_type": "presence",
                "from_webid": webid,
                "status": status,
                "status_message": status_message,
                "updated_at": _now_iso,
            }
            for _peer_gw in set(self._peer_gateway_urls.values()):
                asyncio.create_task(self._relay_ephemeral(_peer_gw, _pres_payload))

        await websocket.send(json.dumps({"type": "presence_set", "status": status, "status_message": status_message}))

    async def _handle_resolve_did(self, websocket, data: dict) -> None:
        did = data.get("did", "").strip()
        try:
            from .didkey import did_to_pub_key
            did_to_pub_key(did)
            await websocket.send(json.dumps({
                "type": "did_resolved",
                "did": did,
                "webid": did,
            }))
        except Exception as exc:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Cannot resolve DID: {exc}",
            }))

    async def _handle_get_presence(self, websocket, data: dict) -> None:
        webid = data.get("webid")
        if webid and webid in self._user_presence:
            presence = self._user_presence[webid]
            await websocket.send(json.dumps({
                "type": "presence",
                "webid": webid,
                "status": presence["status"],
                "status_message": presence.get("status_message", ""),
                "updated_at": presence["updated_at"],
                "last_active_at": presence.get("last_active_at", presence["updated_at"])
            }))
        else:
            now = datetime.now(timezone.utc).isoformat()
            await websocket.send(json.dumps({
                "type": "presence",
                "webid": webid,
                "status": "offline",
                "status_message": "",
                "updated_at": now,
                "last_active_at": now
            }))

    async def _handle_get_all_presence(self, websocket, data: dict) -> None:
        caller_webid = self._client_webids.get(websocket, "")
        # Collect the caller's known contacts from the relationship store.
        allowed: set = {caller_webid}
        if self._store and caller_webid:
            try:
                for rel in (self._store.list_relationships(caller_webid) or []):
                    # LocalStore.list_relationships returns dicts with 'peer_did'
                    peer = rel.get("peer_did") or ""
                    if peer:
                        allowed.add(peer)
            except Exception as exc:
                logger.debug("Failed to fetch relationships for presence filter: %s", exc)
                pass
        filtered = {wid: data for wid, data in self._user_presence.items() if wid in allowed}
        await websocket.send(json.dumps({
            "type": "all_presence",
            "presence": filtered
        }, default=str))

    # NOTE: the real _handle_join_voice_channel lives in VoiceHandlerMixin
    # (_gateway_voice.py) and wins via MRO (VoiceHandlerMixin is first in the
    # ProxionGateway bases). A duplicate stub used to sit here that broadcast a
    # presence event with the GATEWAY's DID (not the joiner's) and tracked no
    # membership — dead, shadowed, and a latent hazard if the mixin order ever
    # changed. Removed.

    async def _handle_get_identity(self, websocket, data: dict) -> None:
        client_did = self._client_webids.get(websocket, "")
        resp = {
            "type": "identity",
            "webid": self.agent.identity_pub_bytes.hex(),
            "pub_hex": self.agent.identity_pub_bytes.hex(),
            "did": client_did,
            "turn_url": self.config.turn_url,
        }
        # Send time-limited TURN credentials instead of the raw shared secret
        turn_creds = self._make_turn_creds(client_did or "anon")
        if turn_creds:
            resp["turn"] = turn_creds
        await websocket.send(json.dumps(resp))

    async def _handle_block(self, websocket, data: dict) -> None:
        webid = data.get("webid")
        if not webid:
            return
        owner = self._client_webids.get(websocket, "")
        # Per-owner (send-path isolation) + mirror into the global file so the
        # not-yet-scoped receive path keeps enforcing (union of all owners).
        if owner and self._store:
            self._store.set_block(owner, webid, True)
        self.blocklist.block(webid)
        logger.info(f"Blocked WebID: {webid}")

    async def _handle_unblock(self, websocket, data: dict) -> None:
        webid = data.get("webid")
        if not webid:
            return
        owner = self._client_webids.get(websocket, "")
        if owner and self._store:
            self._store.set_block(owner, webid, False)
        # Drop from the global file only if no other owner still blocks them,
        # so the file stays the exact union used by the receive path.
        if not (self._store and self._store.is_blocked_by_anyone(webid)):
            self.blocklist.unblock(webid)
        logger.info(f"Unblocked WebID: {webid}")

    async def _handle_list_blocks(self, websocket, data: dict) -> None:
        """Return the caller-owner's block list so the client can show block
        state, render a manage-blocks list, and mirror it to the pod."""
        owner = self._client_webids.get(websocket, "")
        webids = self._store.get_blocked_by(owner) if (owner and self._store) else []
        await websocket.send(json.dumps({"type": "blocks", "webids": webids}))

    async def _handle_set_thread_mute(self, websocket, data: dict) -> None:
        """Record a thread mute server-side so OFFLINE push respects it. mute_key is
        the peer's webid (DM) or room_id (room)."""
        owner = self._client_webids.get(websocket, "")
        mute_key = data.get("mute_key", "")
        if not owner or not mute_key or not self._store:
            return
        self._store.set_thread_mute(owner, mute_key, bool(data.get("muted", False)))

    async def _handle_get_message(self, websocket, data: dict) -> None:
        message_id = data.get("message_id", "")
        if self._store and message_id:
            rows = self._store.get_messages_by_ids([message_id])
            if rows:
                msg = rows[0]
                thread_id = msg.get("thread_id")
                # Verify the caller is authorized to read this message
                if thread_id:
                    caller_webid = self._client_webids.get(websocket, "")
                    # Check room membership first
                    is_room = self._strip_thread_prefix(thread_id)
                    if self._check_room_permission(websocket, is_room):
                        pass  # Authorized: room member
                    elif msg.get("from_webid") == caller_webid:
                        pass  # Authorized: message sender
                    elif caller_webid and self._store and any(
                        t["thread_id"] == thread_id
                        for t in self._store.get_dm_threads(owner_webid=caller_webid)
                    ):
                        pass  # Authorized: DM thread participant
                    else:
                        logger.warning("Rejecting unauthorized get_message for %s from %s", message_id, websocket)
                        await websocket.send(json.dumps({"type": "error", "message": "Unauthorized"}))
                        return

                # R7: Single-message integrity check against stored thread checkpoint
                _msg_integrity_warning = None
                if self._store and thread_id:
                    _checkpoint = self._store.get_thread_integrity_state(thread_id)
                    if _checkpoint:
                        _msg_seq = msg.get("seq_num") or 0
                        _msg_prev = msg.get("prev_hash") or ""
                        _ckpt_seq = _checkpoint.get("last_seq_num", 0)
                        _ckpt_hash = _checkpoint.get("last_prev_hash", "")
                        if _msg_seq and _ckpt_seq and _msg_seq < _ckpt_seq:
                            _msg_integrity_warning = {
                                "type": "seq_num",
                                "first_offending_message_id": message_id,
                            }
                            self._store.save_security_event(
                                "thread_integrity_break", "warning",
                                webid=caller_webid or None,
                                ip=None,
                                details=f"get_message: seq_num={_msg_seq} < checkpoint={_ckpt_seq} thread={thread_id}",
                            )

                _fetched_payload = {
                    "type": "message_fetched",
                    "message": msg,
                }
                if _msg_integrity_warning:
                    _fetched_payload["integrity_warning"] = _msg_integrity_warning
                await websocket.send(json.dumps(_fetched_payload))
            else:
                await websocket.send(json.dumps({
                    "type": "message_fetched",
                    "message": None,
                    "message_id": message_id,
                }))

    async def _handle_get_receipts(self, websocket, data: dict) -> None:
        thread_id = data.get("thread_id")
        message_id = data.get("message_id")

        try:
            from . import receipts
            receipt_list = await receipts.get_read_receipts(
                self.read_state.pod_client,
                thread_id,
                message_id
            )
            await websocket.send(json.dumps({
                "type": "receipts",
                "thread_id": thread_id,
                "message_id": message_id,
                "receipts": [
                    {
                        "message_id": r.message_id,
                        "reader_webid": r.reader_webid,
                        "read_at": r.read_at
                    }
                    for r in receipt_list
                ]
            }))
        except Exception as exc:
            logger.warning(f"Failed to get receipts: {exc}")
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Failed to get receipts: {exc}"
            }))

    async def _handle_create_invite(self, websocket, data: dict) -> None:
        room_id = data.get("room_id")
        if not room_id or room_id not in self.room_memberships:
            await websocket.send(json.dumps({"type": "error", "message": "Unknown room"}))
        else:
            try:
                from .invites import create_invite as _create_invite
                rec = await _create_invite(
                    self.stash,
                    room_id,
                    str(self.agent.webid),
                    expires_hours=max(1, min(int(data.get("expires_hours") or 24), 720)),
                    max_uses=max(0, int(data.get("max_uses") or 0)),
                )
                await websocket.send(json.dumps({
                    "type": "invite_created",
                    "code": rec.code,
                    "expires_iso": rec.expires_iso,
                    "room_id": rec.room_id,
                }))
            except (TypeError, ValueError):
                await websocket.send(json.dumps({"type": "error", "message": "Invalid invite parameters"}))

    async def _handle_join_by_invite(self, websocket, data: dict) -> None:
        code = data.get("code", "")
        from .invites import use_invite as _use_invite
        rec = await _use_invite(self.stash, code)
        if rec is None:
            await websocket.send(json.dumps({"type": "error", "message": "Invalid or expired invite"}))
        else:
            await websocket.send(json.dumps({"type": "invite_accepted", "room_id": rec.room_id}))

    async def _handle_get_notifications(self, websocket, data: dict) -> None:
        if not self._client_webids.get(websocket):
            await websocket.send(json.dumps({"type": "notifications", "notifications": []}))
            return
        from .notifications import get_notifications as _get_notifs
        unread_only = bool(data.get("unread_only", False))
        try:
            limit = min(int(data.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        notifs = await _get_notifs(self.stash, unread_only=unread_only, limit=limit)
        await websocket.send(json.dumps({
            "type": "notifications",
            "notifications": [
                {"id": n.id, "event_type": n.event_type, "title": n.title,
                 "body": n.body, "created_iso": n.created_iso, "read": n.read}
                for n in notifs
            ],
        }))

    async def _handle_mark_notification_read(self, websocket, data: dict) -> None:
        if not self._client_webids.get(websocket):
            return
        from .notifications import mark_notification_read as _mark_notif
        nid = data.get("notification_id", "")
        found = await _mark_notif(self.stash, nid)
        if found:
            await websocket.send(json.dumps({"type": "notification_read", "id": nid}))
        else:
            await websocket.send(json.dumps({"type": "error", "message": "Notification not found"}))

    async def _handle_set_identity(self, websocket, data: dict) -> None:
        display_name = data.get("display_name", "").strip()[:100]
        if display_name:
            self._display_names[websocket] = display_name
            webid = self._client_webids.get(websocket)
            if webid and self._store:
                self._store.save_display_name(webid, display_name)

    async def _handle_connect_css(self, websocket, data: dict) -> None:
        # Only the gateway owner (whose identity matches self.agent) may reconfigure pod connectivity.
        caller_did = self._client_webids.get(websocket, "")
        from .didkey import pub_key_to_did
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({
                "type": "css_error",
                "message": "Only the gateway owner can configure pod connectivity.",
            }))
            return
        css_url  = data.get("css_url", "").rstrip("/")
        email    = data.get("email", "")
        password = data.get("password", "")
        if not (css_url and email and password):
            await websocket.send(json.dumps({
                "type": "css_error",
                "message": "css_url, email, and password are required",
            }))
            return
        from .relay import _validate_relay_target
        if not _validate_relay_target(css_url):
            await websocket.send(json.dumps({
                "type": "css_error",
                "message": (
                    "css_url resolves to a private or disallowed address. "
                    "Set PROXION_ALLOW_PRIVATE_RELAY=1 to connect to a local pod."
                ),
            }))
            return
        else:
            try:
                creds, pod_url, webid = await asyncio.get_event_loop().run_in_executor(
                    None, self._connect_css_sync, css_url, email, password
                )
                await websocket.send(json.dumps({
                    "type": "css_connected",
                    "pod_url": pod_url,
                    "webid": webid,
                    "proxion_address": self._proxion_address(),
                }))
                asyncio.create_task(self._ensure_pod_room_containers())
            except Exception as exc:
                logger.warning(f"connect_css failed: {exc}")
                await websocket.send(json.dumps({
                    "type": "css_error",
                    "message": str(exc),
                }))

    async def _handle_disconnect_pod(self, websocket, data: dict) -> None:
        self._pod_url = None
        self._pod_webid = None
        self.dm_clients.clear()
        # Delete persisted credentials so the gateway does not auto-reconnect
        # to the pod after a browser reload or gateway restart (sign-out).
        if self.config.db_path:
            from pathlib import Path
            cred_path = Path(self.config.db_path).parent / "pod_creds.json"
            cred_path.unlink(missing_ok=True)
        await websocket.send(json.dumps({"type": "pod_disconnected"}))

    async def _handle_reconnect_pod(self, websocket, data: dict) -> None:
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._reconnect_stored_pod_sync
            )
            if result:
                creds, pod_url, webid = result
                await websocket.send(json.dumps({
                    "type": "css_connected",
                    "pod_url": pod_url,
                    "webid": webid,
                    "proxion_address": self._proxion_address(),
                }))
            else:
                await websocket.send(json.dumps({
                    "type": "css_error",
                    "message": "No stored pod credentials found.",
                }))
        except Exception as exc:
            logger.warning(f"reconnect_pod failed: {exc}")
            await websocket.send(json.dumps({"type": "css_error", "message": str(exc)}))

    async def _handle_get_my_address(self, websocket, data: dict) -> None:
        from .didkey import pub_key_to_did
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        proxion_addr = self._proxion_address()
        http_url = self._gateway_http_url()
        import urllib.parse
        invite_link = f"{http_url}/invite?from={urllib.parse.quote(proxion_addr)}" if http_url else ""
        short_token = getattr(self, "_short_invite_token", "")
        short_invite_url = f"{http_url}/i/{short_token}" if (http_url and short_token) else ""
        await websocket.send(json.dumps({
            "type": "my_address",
            "did": gateway_did,
            "gateway_url": self._ws_public_url(),
            "gateway_http_url": http_url,
            "proxion_address": proxion_addr,
            "invite_link": invite_link,
            "short_invite_url": short_invite_url,
        }))

    async def _handle_get_relationships(self, websocket, data: dict) -> None:
        rels = []
        caller_webid = self._client_webids.get(websocket, "")
        if self._store:
            for cert_dict in self._store.list_relationships(owner_webid=caller_webid):
                cert_id = cert_dict.get("certificate_id") or cert_dict.get("id")
                peer_did = cert_dict.get("peer_did") or ""
                rels.append({
                    "certificate_id": cert_id,
                    "peer_did": peer_did,
                    "expires_at": cert_dict.get("expires_at"),
                    "display_name": self._store.get_display_name(peer_did) if peer_did else None,
                    # Prefer the peer's browser E2E key over the gateway store key so the
                    # client caches the right key for content encryption (the store key
                    # is for sealed-sender only).
                    "x25519_pub": (self._store.get_e2e_key(peer_did) or self._store.get_x25519_pub(peer_did)) if peer_did else None,
                })
        await websocket.send(json.dumps({"type": "relationships", "contacts": rels}))

    async def _handle_pod_status(self, websocket, data: dict) -> None:
        connected = bool(self._pod_webid and self._pod_url)
        try:
            from .solid_migration import migration_store
            snap = migration_store.snapshot()
            auth_mode_active = snap.get("auth_mode_active", "legacy")
            auth_mode_fallback_count = snap.get("auth_mode_fallback_count", 0)
            auth_mode_last_failure_code = snap.get("auth_mode_last_failure_code")
        except Exception:
            auth_mode_active = "legacy"
            auth_mode_fallback_count = 0
            auth_mode_last_failure_code = None
        await websocket.send(json.dumps({
            "type": "pod_status",
            "connected": connected,
            "pod_url": self._pod_url or "",
            "webid": self._pod_webid or "",
            "auth_mode_active": auth_mode_active,
            "auth_mode_fallback_count": auth_mode_fallback_count,
            "auth_mode_last_failure_code": auth_mode_last_failure_code,
        }))

    async def _handle_screenshare_started(self, websocket, data: dict) -> None:
        session_id = data.get("session_id", "")
        sess = self._voice_sessions.get(session_id)
        if not sess:
            return
        other = sess.get("callee_ws") if sess.get("caller_ws") is websocket else sess.get("caller_ws")
        if other:
            try:
                await other.send(json.dumps({
                    "type": "screenshare_started",
                    "session_id": session_id,
                    "from_webid": self._client_webids.get(websocket, ""),
                }))
            except Exception:
                pass

    async def _handle_screenshare_stopped(self, websocket, data: dict) -> None:
        session_id = data.get("session_id", "")
        sess = self._voice_sessions.get(session_id)
        if not sess:
            return
        other = sess.get("callee_ws") if sess.get("caller_ws") is websocket else sess.get("caller_ws")
        if other:
            try:
                await other.send(json.dumps({
                    "type": "screenshare_stopped",
                    "session_id": session_id,
                }))
            except Exception:
                pass

    async def _handle_list_sessions(self, websocket, data: dict) -> None:
        webid = self._client_webids.get(websocket)
        if not webid:
            return
        sessions = []
        for ws in list(self._webid_sockets.get(webid, set())):
            meta = self._session_meta.get(ws, {})
            sessions.append({
                "session_id": meta.get("session_id", ""),
                "connected_at": meta.get("connected_at", ""),
                "ip_addr": meta.get("ip_addr", ""),
                "user_agent_hash": meta.get("user_agent_hash", ""),
                "first_seen_ip": meta.get("first_seen_ip", meta.get("ip_addr", "")),
                "last_seen_at": meta.get("last_seen_at", meta.get("connected_at", "")),
                "is_current": ws is websocket,
            })
        await websocket.send(json.dumps({"type": "session_list", "sessions": sessions}))

    async def _revoke_websockets(self, sockets, reason: str) -> None:
        """Add sockets to the revoked set and close them. Prevents race-condition commands."""
        for ws in sockets:
            self._revoked_sessions.add(ws)
            try:
                await ws.send(json.dumps({"type": "session_revoked", "reason": reason}))
                await ws.close()
            except Exception:
                pass

    async def _handle_revoke_session(self, websocket, data: dict) -> None:
        webid = self._client_webids.get(websocket)
        target_session_id = data.get("session_id", "")
        if not webid or not target_session_id:
            return
        target_ws = next(
            (ws for ws in list(self._webid_sockets.get(webid, set()))
             if self._session_meta.get(ws, {}).get("session_id") == target_session_id),
            None
        )
        if not target_ws:
            return
        await self._revoke_websockets([target_ws], "revoked_by_other_session")

    async def _handle_logout_all_devices(self, websocket, data: dict) -> None:
        """Close all other sessions for the calling identity. R11.3.1."""
        webid = self._client_webids.get(websocket)
        if not webid:
            return
        other_sockets = [ws for ws in list(self._webid_sockets.get(webid, set()))
                         if ws is not websocket]
        await self._revoke_websockets(other_sockets, "logout_all")
        await websocket.send(json.dumps({
            "type": "logout_all_complete",
            "revoked_count": len(other_sockets),
        }))

    async def _handle_get_audit_logs(self, websocket, data: dict) -> None:
        """Return recent audit log entries. Restricted to the gateway owner."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "Only the gateway owner can access audit logs.",
            }))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "audit_logs", "logs": []}))
            return
        event_type = data.get("event_type") or None
        try:
            limit = min(int(data.get("limit", 100)), 500)
        except (TypeError, ValueError):
            limit = 100
        logs = self._store.get_audit_logs(event_type=event_type, limit=limit)
        result = {"type": "audit_logs", "logs": logs}
        if data.get("verify_chain"):
            chain_result = self._store.verify_audit_chain(limit=5000)
            result["chain_ok"] = chain_result["ok"]
            result["chain_break_at"] = chain_result["break_at"]
            result["chain_error"] = chain_result["error"]
        await websocket.send(json.dumps(result, default=str))

    async def _handle_get_security_events(self, websocket, data: dict) -> None:
        """Return recent security events. Restricted to the gateway owner."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({
                "type": "error",
                "code": "E_FORBIDDEN",
                "message": "Only the gateway owner can access security events.",
            }))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "security_events", "events": []}))
            return
        event_type = data.get("event_type") or None
        try:
            limit = min(int(data.get("limit", 100)), 500)
        except (TypeError, ValueError):
            limit = 100
        events = self._store.get_security_events(event_type=event_type, limit=limit)
        await websocket.send(json.dumps({"type": "security_events", "events": events}, default=str))

    async def _handle_get_security_summary(self, websocket, data: dict) -> None:
        """Owner-only: return security event rollups for the past N hours (Round 6)."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({"type": "error", "code": "E_FORBIDDEN", "message": "Owner only"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "security_summary", "hours": 24,
                                              "rate_limits_triggered": 0, "schema_rejects": 0,
                                              "relay_replay_rejects": 0, "auth_lockouts": 0, "webhook_failures": 0}))
            return
        try:
            hours = max(1, min(int(data.get("hours", 24)), 168))
        except (TypeError, ValueError):
            hours = 24
        summary = self._store.get_security_summary(hours=hours)
        await websocket.send(json.dumps({"type": "security_summary", **summary}))

    async def _handle_get_runtime_security_state(self, websocket, data: dict) -> None:
        """Owner-only: return runtime security state for operator diagnostics."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({"type": "error", "code": "E_FORBIDDEN", "message": "Owner only"}))
            return
        import os as _os
        safe_mode = _os.environ.get("PROXION_SAFE_MODE", "0") == "1"
        schema_version = self._store._SCHEMA_VERSION if self._store else None
        relay_nonce_count = 0
        relay_id_count = 0
        if self._store:
            try:
                import sqlite3 as _sq
                conn = _sq.connect(self._store.db_path)
                relay_nonce_count = conn.execute("SELECT COUNT(*) FROM relay_seen_nonces").fetchone()[0]
                relay_id_count = conn.execute("SELECT COUNT(*) FROM relay_seen_ids").fetchone()[0]
                conn.close()
            except Exception:
                pass
        last_purge = getattr(self, "_last_retention_purge_at", None)
        _lockout_limit = getattr(self, "_AUTH_FAIL_LIMIT", 5)
        auth_lockout_count = sum(
            1 for v in getattr(self, "_auth_fail_counts", {}).values()
            if v.get("count", 0) >= _lockout_limit
        )
        await websocket.send(json.dumps({
            "type": "runtime_security_state",
            "safe_mode": safe_mode,
            "schema_version": schema_version,
            "relay_nonce_cache_size": relay_nonce_count,
            "relay_id_cache_size": relay_id_count,
            "last_retention_purge_at": last_purge,
            "auth_lockout_active_count": auth_lockout_count,
        }, default=str))

    async def _handle_get_degraded_mode_state(self, websocket, data: dict) -> None:
        """Owner-only: return degraded mode state for operator diagnostics (Round 7)."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({"type": "error", "code": "E_FORBIDDEN", "message": "Owner only"}))
            return
        _breakers = getattr(self, "_webhook_breakers", {})
        _open_breakers = {wh_id: b for wh_id, b in _breakers.items() if b.get("opened_at") is not None}
        import time as _t_dg
        await websocket.send(json.dumps({
            "type": "degraded_mode_state",
            "degraded": bool(_open_breakers),
            "open_webhook_breakers": list(_open_breakers.keys()),
            "breaker_details": {
                wh_id: {
                    "failures": b.get("failures", 0),
                    "opened_at": b.get("opened_at"),
                    "open_seconds": round(_t_dg.time() - b["opened_at"], 1) if b.get("opened_at") else None,
                }
                for wh_id, b in _open_breakers.items()
            },
        }, default=str))

    async def _handle_get_realtime_abuse_signals(self, websocket, data: dict) -> None:
        """Owner-only: return 1h and 24h abuse signal rollups with severity score."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        rollup_1h = self._store.get_abuse_signal_rollups(hours=1)
        rollup_24h = self._store.get_abuse_signal_rollups(hours=24)

        def _severity(r: dict) -> str:
            auth = r.get("auth_lockouts", 0)
            integrity = r.get("db_integrity_events", 0)
            replay = r.get("replay_rejects", 0)
            if integrity > 0 or auth >= 10:
                return "critical"
            if auth >= 5 or replay >= 20:
                return "high"
            if auth >= 2 or replay >= 5 or r.get("relay_failed", 0) >= 50:
                return "medium"
            return "low"

        await websocket.send(json.dumps({
            "type": "realtime_abuse_signals",
            "1h": rollup_1h,
            "24h": rollup_24h,
            "severity_1h": _severity(rollup_1h),
            "severity_24h": _severity(rollup_24h),
        }, default=str))

    async def _handle_approve_peer_gateway_change(self, websocket, data: dict) -> None:
        """Owner-only: approve a pending peer gateway URL change."""
        peer_did = data.get("peer_did", "")
        if not peer_did:
            await websocket.send(json.dumps({"type": "error", "message": "peer_did required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        approved = self._store.approve_peer_gateway_change(peer_did)
        if approved:
            # Update the in-memory cache to reflect the newly approved URL
            pin = self._store.get_peer_gateway_pin(peer_did)
            if pin:
                self._peer_gateway_urls[peer_did] = pin["pinned_gateway_url"]
        await websocket.send(json.dumps({
            "type": "peer_gateway_change_approved",
            "peer_did": peer_did,
            "approved": approved,
        }))

    async def _handle_prepare_recovery_operation(self, websocket, data: dict) -> None:
        """Owner-only: create a short-lived recovery operation ID for two-person control."""
        import time as _t_ro, uuid as _uuid_ro, hashlib as _hl_ro
        op_type = data.get("op_type", "")
        if op_type not in ("restore", "import"):
            await websocket.send(json.dumps({"type": "error", "message": "op_type must be restore or import"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        from .didkey import pub_key_to_did as _ptd_ro
        op_id = str(_uuid_ro.uuid4())
        now = _t_ro.time()
        requested_by = _ptd_ro(self.agent.identity_pub_bytes)
        # R9: compute fingerprint binding this op to the requester's source IP
        _ip_ro = (self._session_meta.get(websocket) or {}).get("ip_addr", "")
        _fp_ro = _hl_ro.sha256(f"{_ip_ro}|{op_type}".encode()).hexdigest()
        self._store.create_recovery_operation(
            op_id=op_id,
            op_type=op_type,
            requested_by=requested_by,
            requested_at=now,
            expires_at=now + 300,  # 5-minute window
            requester_fingerprint=_fp_ro,
        )
        self._store.prune_recovery_operations(now)
        await websocket.send(json.dumps({
            "type": "recovery_operation_prepared",
            "op_id": op_id,
            "op_type": op_type,
            "expires_at": now + 300,
        }))

    async def _handle_confirm_recovery_operation(self, websocket, data: dict) -> None:
        """Owner-only: confirm a previously prepared recovery operation (second factor)."""
        import time as _t_cro
        op_id = data.get("op_id", "")
        if not op_id:
            await websocket.send(json.dumps({"type": "error", "message": "op_id required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        now = _t_cro.time()
        confirmed = self._store.confirm_recovery_operation(op_id, now)
        await websocket.send(json.dumps({
            "type": "recovery_operation_confirmed",
            "op_id": op_id,
            "confirmed": confirmed,
        }))

    # ------------------------------------------------------------------
    # R9 handlers
    # ------------------------------------------------------------------

    async def _handle_export_security_snapshot(self, websocket, data: dict) -> None:
        """Owner-only: build and return a signed security snapshot."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        try:
            snap = await self._build_security_snapshot()
            await websocket.send(json.dumps({"type": "security_snapshot", "snapshot": snap}, default=str))
        except Exception as exc:
            await websocket.send(json.dumps({"type": "error", "message": str(exc)}))

    async def _handle_resolve_peer_trust_dispute(self, websocket, data: dict) -> None:
        """Owner-only: resolve a peer trust dispute with accept/keep/revoke."""
        import time as _t_rtd
        dispute_id = data.get("dispute_id", "")
        resolution = data.get("resolution", "")
        if not dispute_id or resolution not in ("accept_new_value", "keep_old_value", "revoke_peer_temporarily"):
            await websocket.send(json.dumps({
                "type": "error",
                "message": "dispute_id and valid resolution (accept_new_value|keep_old_value|revoke_peer_temporarily) required",
            }))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        dispute = self._store.get_peer_trust_dispute(dispute_id)
        if not dispute:
            await websocket.send(json.dumps({"type": "error", "message": "dispute not found"}))
            return
        peer_did = dispute.get("peer_did", "")
        self._store.resolve_peer_trust_dispute(dispute_id, _t_rtd.time())
        if resolution == "accept_new_value":
            pin = self._store.get_peer_gateway_pin(peer_did)
            if pin:
                self._peer_gateway_urls[peer_did] = pin["pinned_gateway_url"]
        elif resolution == "revoke_peer_temporarily":
            self._peer_gateway_urls.pop(peer_did, None)
        await websocket.send(json.dumps({
            "type": "peer_trust_dispute_resolved",
            "dispute_id": dispute_id,
            "resolution": resolution,
            "peer_did": peer_did,
        }))

    async def _handle_list_quarantine_items(self, websocket, data: dict) -> None:
        """Owner-only: list pending federation quarantine items."""
        if not self._store:
            await websocket.send(json.dumps({"type": "quarantine_items", "items": []}))
            return
        try:
            limit = min(int(data.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        items = self._store.list_quarantine_items(limit=limit)
        await websocket.send(json.dumps({"type": "quarantine_items", "items": items}, default=str))

    async def _handle_release_quarantine_item(self, websocket, data: dict) -> None:
        """Owner-only: release a quarantined federation item for delivery."""
        item_id = data.get("item_id", "")
        if not item_id:
            await websocket.send(json.dumps({"type": "error", "message": "item_id required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        released = self._store.release_quarantine_item(item_id)
        await websocket.send(json.dumps({
            "type": "quarantine_item_released",
            "item_id": item_id,
            "released": released,
        }))

    async def _handle_drop_quarantine_item(self, websocket, data: dict) -> None:
        """Owner-only: permanently drop a quarantined federation item."""
        item_id = data.get("item_id", "")
        if not item_id:
            await websocket.send(json.dumps({"type": "error", "message": "item_id required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        dropped = self._store.drop_quarantine_item(item_id)
        await websocket.send(json.dumps({
            "type": "quarantine_item_dropped",
            "item_id": item_id,
            "dropped": dropped,
        }))

    async def _handle_ack_checksum_mismatch(self, websocket, data: dict) -> None:
        """Owner-only: acknowledge a checksum mismatch and take a new baseline snapshot."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        self._checksum_mismatch = False
        self._checksum_mismatch_tables = []
        _CRITICAL_TABLES = ["relationships", "peer_gateway_pins", "audit_logs", "security_events"]
        try:
            self._store.snapshot_security_checksums(_CRITICAL_TABLES)
        except Exception:
            pass
        await websocket.send(json.dumps({"type": "checksum_mismatch_acked"}))

    # ------------------------------------------------------------------
    # R10 handlers
    # ------------------------------------------------------------------

    async def _handle_set_security_tier(self, websocket, data: dict) -> None:
        """Owner-only: manually set the adaptive security tier with optional TTL."""
        import time as _t_st
        try:
            tier = int(data.get("tier", 0))
        except (TypeError, ValueError):
            await websocket.send(json.dumps({"type": "error", "message": "tier must be integer 0-3"}))
            return
        if tier not in (0, 1, 2, 3):
            await websocket.send(json.dumps({"type": "error", "message": "tier must be 0-3"}))
            return
        ttl_s = None
        try:
            _ttl_raw = data.get("ttl_seconds")
            if _ttl_raw is not None:
                ttl_s = float(_ttl_raw)
        except (TypeError, ValueError):
            pass
        reason = str(data.get("reason", "manual_override"))[:200]
        from .security_policy import get_policy as _get_pol_st
        _get_pol_st().set_tier(tier, override_ttl_s=ttl_s, reason=reason)
        await websocket.send(json.dumps({
            "type": "security_tier_set",
            "tier": tier,
            "ttl_seconds": ttl_s,
            "reason": reason,
        }))

    async def _handle_get_security_tier_state(self, websocket, data: dict) -> None:
        """Owner-only: return current adaptive security tier state."""
        from .security_policy import get_policy as _get_pol_gts
        state = _get_pol_gts().get_tier_state()
        await websocket.send(json.dumps({"type": "security_tier_state", **state}, default=str))

    async def _handle_set_retention_lock(self, websocket, data: dict) -> None:
        """Owner-only: set a retention lock for audit/security data."""
        import time as _t_rl
        lock_name = data.get("lock_name", "").strip()
        if not lock_name:
            await websocket.send(json.dumps({"type": "error", "message": "lock_name required"}))
            return
        try:
            hours = max(1, min(int(data.get("hours", 24)), 8760))  # max 1 year
        except (TypeError, ValueError):
            hours = 24
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        locked_until = _t_rl.time() + hours * 3600
        self._store.set_retention_lock(lock_name, locked_until)
        await websocket.send(json.dumps({
            "type": "retention_lock_set",
            "lock_name": lock_name,
            "locked_until": locked_until,
            "hours": hours,
        }))

    async def _handle_list_retention_locks(self, websocket, data: dict) -> None:
        """Owner-only: list active retention locks."""
        if not self._store:
            await websocket.send(json.dumps({"type": "retention_locks", "locks": []}))
            return
        locks = self._store.list_retention_locks()
        await websocket.send(json.dumps({"type": "retention_locks", "locks": locks}, default=str))

    async def _handle_clear_retention_lock(self, websocket, data: dict) -> None:
        """Owner-only: clear a retention lock (requires confirmation token)."""
        import hashlib as _hl_rl
        lock_name = data.get("lock_name", "").strip()
        confirmation_token = data.get("confirmation_token", "").strip()
        if not lock_name:
            await websocket.send(json.dumps({"type": "error", "message": "lock_name required"}))
            return
        # Require confirmation token = sha256("clear:" + lock_name) first 16 chars
        expected = _hl_rl.sha256(f"clear:{lock_name}".encode()).hexdigest()[:16]
        if confirmation_token != expected:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "confirmation_token required",
                "hint": f"confirmation_token must be sha256('clear:{lock_name}')[:16]",
            }))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        cleared = self._store.clear_retention_lock(lock_name)
        await websocket.send(json.dumps({
            "type": "retention_lock_cleared",
            "lock_name": lock_name,
            "cleared": cleared,
        }))

    async def _handle_run_security_self_test(self, websocket, data: dict) -> None:
        """Owner-only: run security self-test and return signed report."""
        try:
            report = await self._build_security_self_test_report()
            await websocket.send(json.dumps({"type": "security_self_test_report", "report": report}, default=str))
        except Exception as exc:
            await websocket.send(json.dumps({"type": "error", "message": str(exc)}))

    # ------------------------------------------------------------------
    # R11 handlers
    # ------------------------------------------------------------------

    async def _handle_request_admin_action(self, websocket, data: dict) -> None:
        """Owner-only: create a pending dual-control admin action."""
        import time as _t_ra
        import uuid as _uuid_ra
        import json as _json_ra
        action_type = str(data.get("action_type", "")).strip()
        payload = data.get("payload", {})
        if not action_type:
            await websocket.send(json.dumps({"type": "error", "message": "action_type required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        caller = (self._session_meta.get(websocket) or {}).get("webid", "")
        action_id = str(_uuid_ra.uuid4())
        expires_at = _t_ra.time() + 600  # 10-minute window
        self._store.create_pending_admin_action(
            action_id=action_id,
            action_type=action_type,
            payload_json=_json_ra.dumps(payload),
            requested_by=caller,
            expires_at=expires_at,
        )
        await websocket.send(json.dumps({
            "type": "admin_action_requested",
            "action_id": action_id,
            "action_type": action_type,
            "expires_at": expires_at,
        }))

    async def _handle_confirm_admin_action(self, websocket, data: dict) -> None:
        """Owner-only: confirm a pending dual-control admin action."""
        import time as _t_ca
        import hashlib as _hl_ca
        action_id = str(data.get("action_id", "")).strip()
        challenge_signature = str(data.get("challenge_signature", "")).strip()
        if not action_id:
            await websocket.send(json.dumps({"type": "error", "message": "action_id required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        action = self._store.get_pending_admin_action(action_id)
        if action is None:
            await websocket.send(json.dumps({"type": "error", "message": "action not found"}))
            return
        if action.get("consumed"):
            await websocket.send(json.dumps({"type": "error", "message": "action already consumed"}))
            return
        if action.get("expires_at", 0) < _t_ca.time():
            await websocket.send(json.dumps({"type": "error", "message": "action expired"}))
            return
        # Validate challenge_signature = sha256("confirm:{action_id}")[:16]
        expected = _hl_ca.sha256(f"confirm:{action_id}".encode()).hexdigest()[:16]
        if challenge_signature != expected:
            await websocket.send(json.dumps({"type": "error", "message": "invalid challenge_signature"}))
            return
        caller = (self._session_meta.get(websocket) or {}).get("webid", "")
        confirmed = self._store.confirm_admin_action(action_id, confirmed_by=caller)
        await websocket.send(json.dumps({
            "type": "admin_action_confirmed",
            "action_id": action_id,
            "confirmed": confirmed,
        }))

    async def _handle_simulate_incident_policy(self, websocket, data: dict) -> None:
        """Owner-only: simulate incident policy replay on recent security events."""
        try:
            hours = int(data.get("hours", 24))
        except (TypeError, ValueError):
            hours = 24
        tier_profile = data.get("tier_profile") or {}
        if not isinstance(tier_profile, dict):
            tier_profile = {}
        try:
            from .incident_sim import simulate_incident_policy
            report = simulate_incident_policy(
                store=self._store,
                hours=hours,
                tier_profile=tier_profile,
            )
            await websocket.send(json.dumps({"type": "incident_simulation_report", "report": report}, default=str))
        except Exception as exc:
            await websocket.send(json.dumps({"type": "error", "message": str(exc)}))

    async def _handle_create_trust_revocation(self, websocket, data: dict) -> None:
        """Owner-only: create a trust revocation entry."""
        import time as _t_tr
        import uuid as _uuid_tr
        subject_type = str(data.get("subject_type", "")).strip()
        subject_id = str(data.get("subject_id", "")).strip()
        reason = str(data.get("reason", "")).strip()
        expires_at = data.get("expires_at")
        if not subject_type or not subject_id or not reason:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "subject_type, subject_id, and reason are required",
            }))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        caller = (self._session_meta.get(websocket) or {}).get("webid", "")
        rev_id = str(_uuid_tr.uuid4())
        now = _t_tr.time()
        self._store.create_trust_revocation(
            id=rev_id,
            subject_type=subject_type,
            subject_id=subject_id,
            reason=reason,
            revoked_by=caller or "gateway_owner",
            revoked_at=now,
            expires_at=float(expires_at) if expires_at is not None else None,
        )
        if self._store:
            self._store.save_security_event(
                "trust_revocation_created", "warning",
                details=f"type={subject_type} id={subject_id} reason={reason}",
            )
        await websocket.send(json.dumps({
            "type": "trust_revocation_created",
            "revocation_id": rev_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
        }))

    async def _handle_list_trust_revocations(self, websocket, data: dict) -> None:
        """Owner-only: list active trust revocations."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        revocations = self._store.list_active_trust_revocations(limit=500)
        await websocket.send(json.dumps({
            "type": "trust_revocations",
            "revocations": revocations,
        }, default=str))

    # ------------------------------------------------------------------
    # R12: Compromise recovery handlers
    # ------------------------------------------------------------------

    async def _handle_start_compromise_recovery(self, websocket, data: dict) -> None:
        """Owner-only: start a new key compromise recovery session."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        reason = data.get("reason", "unspecified")
        initiated_by = getattr(self, "_owner_did", "") or data.get("initiated_by", "")
        from .compromise_recovery import start_compromise_recovery as _scr
        session_id = _scr(self._store, reason=reason, initiated_by=initiated_by)
        if self._store:
            self._store.save_security_event(
                "compromise_recovery_started", "critical",
                details=f"session_id={session_id} reason={reason}",
            )
        await websocket.send(json.dumps({
            "type": "compromise_recovery_started",
            "session_id": session_id,
            "reason": reason,
        }))

    async def _handle_get_compromise_recovery_status(self, websocket, data: dict) -> None:
        """Owner-only: get status of a recovery session."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        session_id = data.get("session_id", "")
        if not session_id:
            await websocket.send(json.dumps({"type": "error", "message": "session_id required"}))
            return
        session = self._store.get_compromise_recovery_session(session_id)
        if not session:
            await websocket.send(json.dumps({"type": "error", "code": "not_found"}))
            return
        await websocket.send(json.dumps({
            "type": "compromise_recovery_status",
            "session": session,
        }, default=str))

    async def _handle_resume_compromise_recovery(self, websocket, data: dict) -> None:
        """Owner-only: resume a paused recovery session."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        session_id = data.get("session_id", "")
        if not session_id:
            await websocket.send(json.dumps({"type": "error", "message": "session_id required"}))
            return
        from .compromise_recovery import resume_compromise_recovery as _rcr
        result = _rcr(self._store, session_id)
        await websocket.send(json.dumps({
            "type": "compromise_recovery_resumed",
            **result,
        }, default=str))

    async def _handle_abort_compromise_recovery(self, websocket, data: dict) -> None:
        """Owner-only: abort a recovery session."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        session_id = data.get("session_id", "")
        if not session_id:
            await websocket.send(json.dumps({"type": "error", "message": "session_id required"}))
            return
        from .compromise_recovery import abort_compromise_recovery as _acr
        ok = _acr(self._store, session_id)
        if ok and self._store:
            self._store.save_security_event(
                "compromise_recovery_aborted", "warning",
                details=f"session_id={session_id}",
            )
        await websocket.send(json.dumps({
            "type": "compromise_recovery_aborted",
            "session_id": session_id,
            "success": ok,
        }))

    # ------------------------------------------------------------------
    # R12: Signed event stream handler
    # ------------------------------------------------------------------

    async def _handle_get_solid_migration_errors(self, websocket, data: dict) -> None:
        """Owner-only: return Solid SDK migration error metrics grouped by normalised code and mode."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({
                "type": "error",
                "code": "E_FORBIDDEN",
                "message": "Only the gateway owner can access migration error metrics.",
            }))
            return
        try:
            from .solid_migration import migration_store
            snapshot = migration_store.snapshot()
        except Exception as _exc:
            snapshot = {"error": str(_exc)}
        await websocket.send(json.dumps({
            "type": "solid_migration_errors",
            **snapshot,
        }, default=str))

    async def _handle_get_access_grants_policy_state(self, websocket, data: dict) -> None:
        """Owner-only: return access grants policy state and violation counters."""
        from .didkey import pub_key_to_did
        caller_did = self._client_webids.get(websocket, "")
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if caller_did != gateway_did:
            await websocket.send(json.dumps({
                "type": "error",
                "code": "E_FORBIDDEN",
                "message": "Only the gateway owner can access access grants policy state.",
            }))
            return

        import os as _os_ag
        import hashlib as _hl_ag
        enabled = _os_ag.environ.get("PROXION_ENABLE_ACCESS_GRANTS") == "1"
        issuer_allowlist_raw = _os_ag.environ.get("PROXION_ACCESS_GRANTS_ISSUER_ALLOWLIST", "")
        scope_allowlist_raw = _os_ag.environ.get("PROXION_ACCESS_GRANTS_SCOPE_ALLOWLIST", "")
        issuer_hash = _hl_ag.sha256(issuer_allowlist_raw.encode()).hexdigest()[:16] if issuer_allowlist_raw else ""
        scope_hash = _hl_ag.sha256(scope_allowlist_raw.encode()).hexdigest()[:16] if scope_allowlist_raw else ""

        violation_count_24h = 0
        if self._store:
            try:
                import sqlite3 as _sq_ag
                import time as _t_ag
                conn_ag = _sq_ag.connect(self._store.db_path)
                since = _t_ag.time() - 86400
                violation_count_24h = conn_ag.execute(
                    "SELECT COUNT(*) FROM security_events WHERE event_type='access_grant_scope_violation' AND created_at > ?",
                    (since,),
                ).fetchone()[0]
                conn_ag.close()
            except Exception:
                pass

        await websocket.send(json.dumps({
            "type": "access_grants_policy_state",
            "enabled": enabled,
            "issuer_allowlist_hash": issuer_hash,
            "scope_allowlist_hash": scope_hash,
            "violation_count_24h": violation_count_24h,
        }))

    async def _handle_get_security_event_stream(self, websocket, data: dict) -> None:
        """Owner-only: fetch signed security event stream with cursor pagination."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no store"}))
            return
        cursor = data.get("cursor", "")
        limit = min(int(data.get("limit", 100)), 1000)
        from .event_stream import get_events_after as _gea
        result = _gea(
            self._store, cursor, limit,
            self.agent.identity_key, self.agent.identity_pub_bytes,
        )
        await websocket.send(json.dumps({
            "type": "security_event_stream",
            **result,
        }, default=str))

    async def _handle_get_security_exit_gate_status(self, websocket, data: dict) -> None:
        """Owner-only: evaluate all security exit gates and return pass/fail status."""
        from .security_exit_gates import evaluate_all_gates as _eval_gates
        result = _eval_gates(self._store)
        await websocket.send(json.dumps({
            "type": "security_exit_gate_status",
            **result,
        }, default=str))

    async def _handle_list_recovery_drill_templates(self, websocket, data: dict) -> None:
        """Owner-only: list available recovery drill templates."""
        from .recovery_drill_runner import list_drill_templates
        await websocket.send(json.dumps({
            "type": "recovery_drill_templates",
            "templates": list_drill_templates(),
        }))

    async def _handle_run_recovery_drill(self, websocket, data: dict) -> None:
        """Owner-only: execute a named recovery drill scenario."""
        template_id = data.get("template_id", "")
        dry_run = bool(data.get("dry_run", False))
        if not template_id:
            await websocket.send(json.dumps({"type": "error", "message": "template_id required"}))
            return
        from .recovery_drill_runner import run_drill
        result = run_drill(template_id, store=self._store, dry_run=dry_run)
        await websocket.send(json.dumps({"type": "recovery_drill_result", **result}, default=str))

    async def _handle_get_recovery_drill_report(self, websocket, data: dict) -> None:
        """Owner-only: retrieve recent drill results from the store."""
        import time as _t
        window_days = min(int(data.get("window_days", 30)), 365)
        if not self._store:
            await websocket.send(json.dumps({"type": "recovery_drill_report", "drills": []}))
            return
        drills = self._store.get_drill_results_in_window(
            _t.time() - window_days * 86400, _t.time()
        )
        await websocket.send(json.dumps({
            "type": "recovery_drill_report",
            "window_days": window_days,
            "drills": drills,
        }, default=str))

    # ------------------------------------------------------------------
    # R17: Delivery and read receipts
    # ------------------------------------------------------------------

    async def _handle_ack_delivered(self, websocket, data: dict) -> None:
        """Client signals that a message was delivered. Persists and notifies sender."""
        from datetime import datetime, timezone
        message_id = data.get("message_id", "")
        sender_webid = data.get("sender_webid", "")
        receiver_webid = self._client_webids.get(websocket, "")
        if not message_id or not receiver_webid:
            await websocket.send(json.dumps({"type": "error", "message": "message_id required"}))
            return
        delivered_at = datetime.now(timezone.utc).isoformat()
        if self._store:
            self._store.save_receipt(message_id, receiver_webid, delivered_at=delivered_at)
        # Notify original sender if online
        if sender_webid:
            for ws in self._sockets_for(sender_webid):
                try:
                    await ws.send(json.dumps({
                        "type": "msg_delivered",
                        "message_id": message_id,
                        "receiver_webid": receiver_webid,
                        "delivered_at": delivered_at,
                    }))
                except Exception:
                    pass
        await websocket.send(json.dumps({"type": "ack_delivered_ok", "message_id": message_id}))

    async def _handle_ack_read(self, websocket, data: dict) -> None:
        """Client signals that a message was read. Persists and notifies sender."""
        from datetime import datetime, timezone
        message_id = data.get("message_id", "")
        sender_webid = data.get("sender_webid", "")
        receiver_webid = self._client_webids.get(websocket, "")
        if not message_id or not receiver_webid:
            await websocket.send(json.dumps({"type": "error", "message": "message_id required"}))
            return
        read_at = datetime.now(timezone.utc).isoformat()
        if self._store:
            self._store.save_receipt(message_id, receiver_webid, read_at=read_at)
        if sender_webid:
            for ws in self._sockets_for(sender_webid):
                try:
                    await ws.send(json.dumps({
                        "type": "msg_read",
                        "message_id": message_id,
                        "receiver_webid": receiver_webid,
                        "read_at": read_at,
                    }))
                except Exception:
                    pass
        await websocket.send(json.dumps({"type": "ack_read_ok", "message_id": message_id}))

    # ------------------------------------------------------------------
    # Safety numbers / contact verification (R18)
    # ------------------------------------------------------------------

    async def _handle_verify_contact(self, websocket, data: dict) -> None:
        """Client provides safety numbers for a peer; gateway confirms and persists."""
        caller_webid = self._client_webids.get(websocket, "")
        peer_webid = data.get("peer_webid", "")
        provided_numbers = data.get("safety_numbers", "")
        if not caller_webid or not peer_webid or not provided_numbers:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no_store"}))
            return
        # Persist the provided safety numbers (trust-on-first-use or explicit comparison)
        self._store.save_contact_verification(peer_webid, provided_numbers, verified_by=caller_webid)
        asyncio.create_task(self._sync_verification_to_pod(peer_webid, provided_numbers, caller_webid))
        await websocket.send(json.dumps({
            "type": "contact_verified",
            "peer_webid": peer_webid,
            "safety_numbers": provided_numbers,
        }))

    async def _handle_get_contact_verification(self, websocket, data: dict) -> None:
        """Return the stored contact verification record for a peer."""
        peer_webid = data.get("peer_webid", "")
        if not self._store or not peer_webid:
            await websocket.send(json.dumps({"type": "contact_verification", "record": None}))
            return
        record = self._store.get_contact_verification(peer_webid)
        await websocket.send(json.dumps({"type": "contact_verification", "record": record}))

    async def _handle_list_verified_contacts(self, websocket, data: dict) -> None:
        """Return all verified contact records."""
        records = self._store.list_verified_contacts() if self._store else []
        await websocket.send(json.dumps({"type": "verified_contacts", "contacts": records}))

    # ------------------------------------------------------------------
    # WebPush subscription management (R18)
    # ------------------------------------------------------------------

    async def _handle_subscribe_push(self, websocket, data: dict) -> None:
        """Store a browser push subscription for offline notifications."""
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        import secrets as _sec
        subscription_id = data.get("subscription_id") or _sec.token_hex(12)
        endpoint = data.get("endpoint", "")
        p256dh_b64 = data.get("p256dh_b64", "")
        auth_b64 = data.get("auth_b64", "")
        if not endpoint or not p256dh_b64 or not auth_b64:
            await websocket.send(json.dumps({"type": "error", "message": "missing_subscription_fields"}))
            return
        self._store.save_push_subscription(subscription_id, owner_webid, endpoint, p256dh_b64, auth_b64)
        asyncio.create_task(self._sync_push_subscription_to_pod(subscription_id, owner_webid, endpoint, p256dh_b64, auth_b64))
        await websocket.send(json.dumps({
            "type": "push_subscribed",
            "subscription_id": subscription_id,
        }))

    async def _handle_unsubscribe_push(self, websocket, data: dict) -> None:
        """Remove a push subscription."""
        subscription_id = data.get("subscription_id", "")
        if self._store and subscription_id:
            self._store.delete_push_subscription(subscription_id)
            asyncio.create_task(self._delete_push_subscription_from_pod(subscription_id))
        await websocket.send(json.dumps({"type": "push_unsubscribed", "subscription_id": subscription_id}))

    # ------------------------------------------------------------------
    # DM session lifecycle (R18)
    # ------------------------------------------------------------------

    async def _handle_list_dm_sessions(self, websocket, data: dict) -> None:
        """Return active DM sessions for the caller."""
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "dm_sessions", "sessions": []}))
            return
        sessions = self._store.list_dm_sessions(owner_webid)
        await websocket.send(json.dumps({"type": "dm_sessions", "sessions": sessions}))

    async def _handle_expire_dm_session(self, websocket, data: dict) -> None:
        """Force-expire a specific DM session (e.g. 'forget this device')."""
        owner_webid = self._client_webids.get(websocket, "")
        session_id = data.get("session_id", "")
        if not session_id or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "session_id required"}))
            return
        session = self._store.get_dm_session_by_id(session_id)
        if not session or session.get("owner_webid") != owner_webid:
            await websocket.send(json.dumps({"type": "error", "message": "not_found"}))
            return
        self._store.delete_dm_session(session_id)
        # Notify any open sockets for this owner
        for ws in self._sockets_for(owner_webid):
            try:
                await ws.send(json.dumps({
                    "type": "session_expired",
                    "session_id": session_id,
                    "peer_webid": session.get("peer_webid"),
                }))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Device key registration (R19, schema v45)
    # ------------------------------------------------------------------

    async def _handle_register_device(self, websocket, data: dict) -> None:
        """Register a new device key with Ed25519 attestation proof."""
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        device_id = data.get("device_id", "")
        device_pub_b64 = data.get("device_pub_b64", "")
        attestation_b64 = data.get("attestation_b64", "")
        timestamp = float(data.get("timestamp", 0))
        if not device_id or not device_pub_b64 or not attestation_b64 or not timestamp:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        from .device_registry import verify_device_attestation
        if not verify_device_attestation(device_pub_b64, owner_webid, device_id, timestamp, attestation_b64):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_attestation"}))
            return
        self._store.register_device(device_id, owner_webid, device_pub_b64, attestation_b64)
        asyncio.create_task(self._sync_device_to_pod(device_id, owner_webid, device_pub_b64, attestation_b64))
        await websocket.send(json.dumps({
            "type": "device_registered",
            "device_id": device_id,
            "owner_webid": owner_webid,
        }))

    async def _handle_list_devices(self, websocket, data: dict) -> None:
        """Return all registered devices for the caller."""
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "devices", "devices": []}))
            return
        devices = self._store.list_devices(owner_webid)
        # Strip attestation bytes from the public response
        safe = [{k: v for k, v in d.items() if k != "attestation_b64"} for d in devices]
        await websocket.send(json.dumps({"type": "devices", "devices": safe}))

    async def _handle_unregister_device(self, websocket, data: dict) -> None:
        """Remove a device registration (owner can only remove their own devices)."""
        owner_webid = self._client_webids.get(websocket, "")
        device_id = data.get("device_id", "")
        if not owner_webid or not device_id or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        device = self._store.get_device(device_id)
        if not device or device.get("owner_webid") != owner_webid:
            await websocket.send(json.dumps({"type": "error", "message": "not_found"}))
            return
        self._store.unregister_device(device_id)
        asyncio.create_task(self._delete_device_from_pod(device_id))
        await websocket.send(json.dumps({"type": "device_unregistered", "device_id": device_id}))

    async def _handle_rotate_spk(self, websocket, data: dict) -> None:
        """Client uploads a fresh signed prekey after receiving spk_rotation_needed."""
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        bundle = data.get("bundle", {})
        spk_id = bundle.get("signed_prekey_id")
        spk_pub = bundle.get("signed_prekey_pub_b64")
        spk_priv = bundle.get("signed_prekey_priv_b64", "")
        spk_created_at = float(bundle.get("spk_created_at", 0))
        old_prekey_id = data.get("old_prekey_id")
        if not spk_id or not spk_pub:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        # Store the new SPK with its creation timestamp
        self._store.save_prekey_with_timestamp(
            spk_id, owner_webid, spk_pub, spk_priv, one_time=False,
            spk_created_at=spk_created_at or __import__("time").time(),
        )
        # Mark the old SPK expired (retained 48h for in-flight sessions)
        if old_prekey_id and self._store:
            self._store.mark_prekey_expired(int(old_prekey_id))
        await websocket.send(json.dumps({
            "type": "spk_rotated",
            "signed_prekey_id": spk_id,
        }))

    async def _handle_get_peer_devices(self, websocket, data: dict) -> None:
        """Return device list for a peer (public key refs only, no attestation)."""
        peer_webid = data.get("peer_webid", "")
        if not peer_webid:
            await websocket.send(json.dumps({"type": "error", "message": "peer_webid required"}))
            return
        if not self._store:
            await websocket.send(json.dumps({
                "type": "peer_devices", "peer_webid": peer_webid, "devices": []
            }))
            return
        devices = self._store.list_devices(peer_webid)
        result = [
            {
                "device_id": d["device_id"],
                "device_pub_b64": d["device_pub_b64"],
                "last_seen_at": d.get("last_seen_at"),
            }
            for d in devices
        ]
        await websocket.send(json.dumps({
            "type": "peer_devices", "peer_webid": peer_webid, "devices": result
        }))

    async def _handle_get_peer_device_keys(self, websocket, data: dict) -> None:
        """Return a peer account's per-device E2E x25519 keys for DM fanout.

        Response: {type: peer_device_keys, peer_webid, devices:[{device_id, pub_b64u}]}.
        """
        peer_webid = data.get("peer_webid", "")
        if not peer_webid or not self._store:
            await websocket.send(json.dumps({
                "type": "peer_device_keys", "peer_webid": peer_webid, "devices": [],
            }))
            return
        devices = self._store.list_device_e2e_keys(peer_webid)
        # Cross-gateway peer: their devices aren't in OUR store — fetch the
        # roster from their gateway (signed, relationship-gated /devices) so a
        # multi-device peer gets per-device fanout across gateways too. Own
        # account and local peers never hit this (they resolve locally above).
        if not devices and peer_webid != self._client_webids.get(websocket, ""):
            devices = await self._fetch_remote_device_keys(peer_webid)
        await websocket.send(json.dumps({
            "type": "peer_device_keys", "peer_webid": peer_webid, "devices": devices,
        }))

    async def _handle_sync_contact_verifications(self, websocket, data: dict) -> None:
        """Return contact verification records for the requesting user since a timestamp."""
        owner_webid = self._client_webids.get(websocket, "")
        since = float(data.get("since", 0))
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({
                "type": "contact_verifications_sync", "records": []
            }))
            return
        records = self._store.list_contact_verifications(owner_webid)
        result = [r for r in records if r.get("verified_at", 0) > since]
        await websocket.send(json.dumps({
            "type": "contact_verifications_sync",
            "owner_webid": owner_webid,
            "records": result,
        }))

    async def _handle_apply_contact_verification_sync(self, websocket, data: dict) -> None:
        """Upsert a contact verification record from a peer device; higher version wins."""
        record = data.get("record", {})
        if not record or not self._store:
            await websocket.send(json.dumps({
                "type": "contact_verification_sync_ack", "ok": False
            }))
            return
        self._store.apply_contact_verification_sync(record)
        await websocket.send(json.dumps({
            "type": "contact_verification_sync_ack", "ok": True
        }))

    async def _handle_set_primary_device(self, websocket, data: dict) -> None:
        """Mark one device as primary for the requesting user."""
        owner_webid = self._client_webids.get(websocket, "")
        device_id = data.get("device_id", "")
        if not owner_webid or not device_id or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_request"}))
            return
        self._store.set_device_primary(device_id, owner_webid)
        await websocket.send(json.dumps({
            "type": "primary_device_set",
            "device_id": device_id,
            "owner_webid": owner_webid,
        }))

    async def _handle_revoke_device_and_rekey(self, websocket, data: dict) -> None:
        """Unregister a device and notify remaining devices to rekey."""
        owner_webid = self._client_webids.get(websocket, "")
        device_id = data.get("device_id", "")
        if not owner_webid or not device_id or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_request"}))
            return
        # Refuse to revoke the account's own primary identity through this path —
        # device_id == owner_webid would put the account DID itself in the
        # revocation set and lock the whole account out.
        if device_id == owner_webid:
            await websocket.send(json.dumps({"type": "error", "message": "cannot_revoke_primary"}))
            return
        existing = self._store.get_device(device_id)
        if not existing or existing.get("owner_webid") != owner_webid:
            await websocket.send(json.dumps({"type": "error", "message": "device_not_found"}))
            return
        self._store.unregister_device(device_id)
        # Delegation-linked devices use their did:key as device_id. Deleting the
        # registry row is NOT revocation — the delegation cert stays valid for its
        # TTL, so persist the device DID as revoked (checked in the register path)
        # and drop its fanout key so peers stop encrypting copies to it.
        if device_id.startswith("did:key:"):
            self._revoked_dids.add(device_id)
            try:
                self._store.mark_revoked(f"device:{device_id}", device_id)
            except Exception:
                logger.warning("could not persist device revocation for %s", device_id[:24])
            try:
                self._store.delete_device_e2e_key(owner_webid, device_id)
            except Exception:
                pass
        revoke_event = json.dumps({
            "type": "device_revoked",
            "device_id": device_id,
            "owner_webid": owner_webid,
        })
        revoked_sockets = []
        for ws in self._sockets_for(owner_webid):
            if ws is websocket:
                continue
            # The revoked device's own live session gets cut, not just notified.
            if self._session_device_did.get(ws) == device_id:
                revoked_sockets.append(ws)
                continue
            try:
                await ws.send(revoke_event)
            except Exception:
                pass
        for ws in revoked_sockets:
            try:
                await ws.send(json.dumps({"type": "session_revoked", "reason": "device_revoked"}))
                await ws.close(1008, "device_revoked")
            except Exception:
                pass
        await websocket.send(json.dumps({
            "type": "device_revoked_ack",
            "device_id": device_id,
        }))

    # ---- Multi-device pairing relay (delegation cert distribution) -----------
    # A primary starts a pairing session and shows a QR carrying the pairing_code
    # (+ its gateway URL). The new device connects, submits its freshly-generated
    # device_did against the code; the gateway forwards that to the primary, which
    # signs a delegation cert and relays it back through the gateway. The cert is
    # NON-secret (public authorization), so the relay carries no key material.
    _PAIRING_TTL = 300           # seconds a pairing session stays claimable
    _PAIRING_MAX = 64            # cap concurrent sessions (DoS guard)

    def _prune_pairing_sessions(self) -> None:
        import time as _t
        now = _t.time()
        for _c in [c for c, s in self._pairing_sessions.items() if s.get("expires_at", 0) <= now]:
            self._pairing_sessions.pop(_c, None)

    @staticmethod
    def _pairing_safety_code(device_did: str) -> str:
        """Short human-verifiable code so the user can confirm the right device."""
        import hashlib
        h = hashlib.sha256(device_did.encode()).hexdigest()
        return f"{int(h[:8], 16) % 1000000:06d}"

    async def _handle_pair_start(self, websocket, data: dict) -> None:
        """Authenticated primary opens a pairing session; returns a pairing_code."""
        import secrets
        import time as _t
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        self._prune_pairing_sessions()
        if len(self._pairing_sessions) >= self._PAIRING_MAX:
            await websocket.send(json.dumps({"type": "error", "message": "pairing_busy"}))
            return
        code = secrets.token_urlsafe(12)
        self._pairing_sessions[code] = {
            "account_webid": owner_webid,
            "primary_ws": websocket,
            "device_ws": None,
            "device_did": None,
            "created_at": _t.time(),
            "expires_at": _t.time() + self._PAIRING_TTL,
            "status": "started",
        }
        await websocket.send(json.dumps({
            "type": "pairing_started",
            "pairing_code": code,
            "expires_in": self._PAIRING_TTL,
        }))

    async def _handle_pair_submit(self, websocket, data: dict) -> None:
        """New (unregistered) device submits its device_did against a pairing_code."""
        code = data.get("pairing_code", "")
        device_did = data.get("device_did", "")
        if not code or not device_did.startswith("did:key:"):
            await websocket.send(json.dumps({"type": "pairing_invalid", "reason": "missing_fields"}))
            return
        self._prune_pairing_sessions()
        sess = self._pairing_sessions.get(code)
        if not sess or sess["status"] != "started":
            # Unknown, already-claimed, expired, or already-approved.
            await websocket.send(json.dumps({"type": "pairing_invalid", "reason": "no_such_session"}))
            return
        sess["device_ws"] = websocket
        sess["device_did"] = device_did
        sess["status"] = "submitted"
        safety = self._pairing_safety_code(device_did)
        try:
            await sess["primary_ws"].send(json.dumps({
                "type": "pairing_request",
                "pairing_code": code,
                "device_did": device_did,
                "safety_code": safety,
            }))
        except Exception:
            self._pairing_sessions.pop(code, None)
            await websocket.send(json.dumps({"type": "pairing_invalid", "reason": "primary_gone"}))
            return
        await websocket.send(json.dumps({
            "type": "pairing_submitted", "pairing_code": code, "safety_code": safety,
        }))

    async def _handle_pair_approve(self, websocket, data: dict) -> None:
        """Primary approves: relays the signed delegation cert to the new device."""
        from .device_cert import verify_device_cert
        owner_webid = self._client_webids.get(websocket, "")
        code = data.get("pairing_code", "")
        cert = data.get("delegation_cert")
        sess = self._pairing_sessions.get(code)
        if not owner_webid or not sess or sess.get("primary_ws") is not websocket:
            await websocket.send(json.dumps({"type": "error", "message": "pairing_not_found"}))
            return
        if sess["status"] != "submitted":
            await websocket.send(json.dumps({"type": "error", "message": "pairing_not_ready"}))
            return
        # Defense in depth: the cert must authorize exactly this device for this account.
        acct = verify_device_cert(
            cert, expected_device_did=sess["device_did"], expected_account_did=owner_webid,
        )
        if not acct:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_cert"}))
            return
        device_ws = sess.get("device_ws")
        self._pairing_sessions.pop(code, None)  # single-use
        if device_ws is not None:
            approved = {
                "type": "pairing_approved",
                "delegation_cert": cert,
                "account_did": owner_webid,
            }
            # E5 slice 1: relay the primary's recent DM-history bundle so the new
            # device starts populated. It rides this authenticated channel; drop
            # it if oversized (WS max is 4 MiB) so the cert always gets through.
            _bundle = data.get("history_bundle")
            if _bundle is not None:
                try:
                    if len(json.dumps(_bundle)) <= 1_048_576:
                        approved["history_bundle"] = _bundle
                except Exception:
                    pass
            try:
                await device_ws.send(json.dumps(approved))
            except Exception:
                pass
        await websocket.send(json.dumps({"type": "pairing_approve_ack", "pairing_code": code}))

    async def _handle_pair_cancel(self, websocket, data: dict) -> None:
        """Either party cancels; the other side is notified if still present."""
        code = data.get("pairing_code", "")
        sess = self._pairing_sessions.get(code)
        if not sess or websocket not in (sess.get("primary_ws"), sess.get("device_ws")):
            await websocket.send(json.dumps({"type": "pairing_cancelled", "pairing_code": code}))
            return
        self._pairing_sessions.pop(code, None)
        other = sess["device_ws"] if websocket is sess.get("primary_ws") else sess["primary_ws"]
        if other is not None:
            try:
                await other.send(json.dumps({"type": "pairing_cancelled", "pairing_code": code}))
            except Exception:
                pass
        await websocket.send(json.dumps({"type": "pairing_cancelled", "pairing_code": code}))

    async def _handle_device_recovery_code_generate(self, websocket, data: dict) -> None:
        """Generate a single-use device recovery code for the requesting user."""
        import hashlib
        import secrets
        import uuid
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        plaintext = secrets.token_hex(16)  # 32 hex chars
        code_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        code_id = str(uuid.uuid4())
        self._store.save_device_recovery_code(code_id, owner_webid, code_hash)
        await websocket.send(json.dumps({
            "type": "device_recovery_code_generated",
            "code_id": code_id,
            "code": plaintext,
            "owner_webid": owner_webid,
        }))

    async def _handle_device_recovery_code_use(self, websocket, data: dict) -> None:
        """Validate a recovery code. Marks it used if valid; rejects if used or wrong."""
        import hashlib
        code_id = data.get("code_id", "")
        plaintext = data.get("code", "")
        if not code_id or not plaintext or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        record = self._store.get_device_recovery_code(code_id)
        if not record:
            await websocket.send(json.dumps({"type": "device_recovery_code_invalid", "reason": "not_found"}))
            return
        if record.get("used_at") is not None:
            await websocket.send(json.dumps({"type": "device_recovery_code_invalid", "reason": "already_used"}))
            return
        expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        if expected_hash != record["code_hash"]:
            await websocket.send(json.dumps({"type": "device_recovery_code_invalid", "reason": "wrong_code"}))
            return
        self._store.use_device_recovery_code(code_id)
        await websocket.send(json.dumps({
            "type": "device_recovery_code_accepted",
            "code_id": code_id,
            "owner_webid": record["owner_webid"],
        }))

    async def _handle_get_connect_id(self, websocket, data: dict) -> None:
        """Encode the caller's DID + gateway URL as a Connect ID for sharing."""
        from .connect_id import encode_connect_id
        own_did = self._client_webids.get(websocket, "") or getattr(self.agent, "webid", "")
        gateway_url = data.get("gateway_url") or getattr(self, "_gateway_http_url", "")
        if not own_did:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        connect_id = encode_connect_id(own_did, gateway_url or "")
        await websocket.send(json.dumps({
            "type": "connect_id",
            "connect_id": connect_id,
            "did": own_did,
            "gateway_url": gateway_url,
        }))

    async def _handle_resolve_connect_id(self, websocket, data: dict) -> None:
        """Decode a Connect ID and return the embedded DID + gateway URL."""
        from .connect_id import decode_connect_id, is_valid_connect_id
        connect_id = data.get("connect_id", "")
        if not is_valid_connect_id(connect_id):
            await websocket.send(json.dumps({
                "type": "error",
                "code": "invalid_connect_id",
                "message": "Connect ID is invalid or has a bad checksum.",
            }))
            return
        resolved = decode_connect_id(connect_id)
        await websocket.send(json.dumps({
            "type": "connect_id_resolved",
            "connect_id": connect_id,
            "did": resolved.get("did", ""),
            "gateway_url": resolved.get("url", ""),
        }))

    async def _handle_request_hole_punch(self, websocket, data: dict) -> None:
        """Initiate a UDP hole punch with a peer.

        The endpoint used in the offer is sourced from the most recent valid
        STUN session cached in the store for the caller.  If no cached session
        exists, the client-supplied local_ip/local_port is used after endpoint
        validation.  The gateway fans out a ``hole_punch_offer`` to the peer
        (if online) and returns a generic ack — without leaking whether the peer
        is currently connected.
        """
        from .hole_punch import HolePunchCoordinator, HolePunchForbidden
        from .stun_client import validate_stun_endpoint
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "store_unavailable"}))
            return

        actor_webid = self._client_webids.get(websocket, "")
        if not actor_webid:
            await websocket.send(json.dumps({"type": "error", "code": "unauthenticated"}))
            return

        peer_webid = data.get("peer_webid", "")
        attempt_nonce = data.get("attempt_nonce", "")
        if not peer_webid:
            await websocket.send(json.dumps({
                "type": "error",
                "code": "missing_fields",
                "message": "peer_webid is required.",
            }))
            return

        coordinator = HolePunchCoordinator(self._store)

        # Authorization: actor must share a room or have a wg_peer record with peer
        if not coordinator.can_attempt_hole_punch(actor_webid, peer_webid):
            if self._store:
                self._store.save_security_event(
                    "hole_punch_authz_denied", "warning", webid=actor_webid,
                    details=f"peer_webid={peer_webid}",
                )
            await websocket.send(json.dumps({
                "type": "error",
                "code": "hole_punch_forbidden",
                "message": "No established relationship with peer.",
            }))
            return

        # Endpoint: prefer store-cached STUN session; fall back to client-supplied
        cached = self._store.get_latest_stun_session_for_owner(actor_webid)
        if cached:
            local_ip = cached["external_ip"]
            local_port = cached["external_port"]
        else:
            local_ip = data.get("local_ip", "")
            local_port = data.get("local_port")
            if not local_ip or local_port is None:
                await websocket.send(json.dumps({
                    "type": "error",
                    "code": "missing_fields",
                    "message": "No cached STUN session found; provide local_ip and local_port.",
                }))
                return
            valid, reason = validate_stun_endpoint(local_ip, int(local_port))
            if not valid:
                if self._store:
                    self._store.save_security_event(
                        "hole_punch_endpoint_rejected", "warning", webid=actor_webid,
                        details=reason,
                    )
                await websocket.send(json.dumps({
                    "type": "error",
                    "code": "invalid_endpoint",
                    "message": reason,
                }))
                return

        attempt_id = coordinator.initiate(
            initiator_webid=actor_webid,
            responder_webid=peer_webid,
            local_ip=local_ip,
            local_port=int(local_port),
            attempt_nonce=attempt_nonce,
        )
        coordinator.record_offer(attempt_id)
        self._metrics["hole_punch_attempts_total"] += 1

        # Fan out hole_punch_offer to all online sockets for peer_webid
        peer_sockets = self._webid_sockets.get(peer_webid, set())
        offer_msg = json.dumps({
            "type": "hole_punch_offer",
            "attempt_id": attempt_id,
            "from_webid": actor_webid,
            "local_ip": local_ip,
            "local_port": local_port,
        })
        for peer_ws in list(peer_sockets):
            try:
                await peer_ws.send(offer_msg)
            except Exception:
                pass

        # Return generic ack — no peer_online leak
        await websocket.send(json.dumps({
            "type": "hole_punch_initiated",
            "attempt_id": attempt_id,
        }))

    async def _handle_hole_punch_complete_notify(self, websocket, data: dict) -> None:
        """Record the result of a hole punch attempt.

        The client reports ``success`` or ``failure`` for a given *attempt_id*.
        The caller must be the initiator or responder of the attempt.  On success
        the transport layer is promoted to direct; on failure the relay path
        remains active.
        """
        from .hole_punch import HolePunchCoordinator, HolePunchForbidden, InvalidPunchTransition
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "store_unavailable"}))
            return

        actor_webid = self._client_webids.get(websocket, "")
        if not actor_webid:
            await websocket.send(json.dumps({"type": "error", "code": "unauthenticated"}))
            return

        attempt_id = data.get("attempt_id", "")
        result = data.get("result", "")  # "success" or "failure"
        peer_ip = data.get("peer_ip")
        peer_port = data.get("peer_port")
        provided_nonce = data.get("attempt_nonce", "")

        if not attempt_id or result not in ("success", "failure"):
            await websocket.send(json.dumps({
                "type": "error",
                "code": "missing_fields",
                "message": "attempt_id and result ('success'|'failure') are required.",
            }))
            return

        # Actor authorization: must be initiator or responder
        attempt = self._store.get_hole_punch_attempt_for_actor(attempt_id, actor_webid)
        if attempt is None:
            self._store.save_security_event(
                "hole_punch_authz_denied", "warning", webid=actor_webid,
                details=f"attempt_id={attempt_id}",
            )
            await websocket.send(json.dumps({
                "type": "error",
                "code": "hole_punch_forbidden",
                "message": "Not authorized to complete this attempt.",
            }))
            return

        # Nonce defense-in-depth: enforce only when both stored and provided are non-empty
        stored_nonce = attempt.get("attempt_nonce", "")
        if stored_nonce and provided_nonce and stored_nonce != provided_nonce:
            self._store.save_security_event(
                "hole_punch_nonce_mismatch", "warning", webid=actor_webid,
                details=f"attempt_id={attempt_id}",
            )
            await websocket.send(json.dumps({
                "type": "error",
                "code": "hole_punch_nonce_mismatch",
                "message": "Attempt nonce does not match.",
            }))
            return

        coordinator = HolePunchCoordinator(self._store)
        try:
            if result == "success":
                if peer_ip and peer_port is not None:
                    coordinator.record_peer_endpoint(attempt_id, actor_webid, peer_ip, int(peer_port))
                coordinator.mark_succeeded(attempt_id, actor_webid)
                self._store.save_security_event(
                    "hole_punch_direct_promotion", "info", webid=actor_webid,
                    details=f"attempt_id={attempt_id} peer={attempt.get('peer_webid','')}",
                )
                self._metrics["hole_punch_succeeded_total"] += 1
                self._metrics["relay_to_direct_recovery_total"] += 1
            else:
                coordinator.mark_failed(attempt_id, actor_webid)
                self._metrics["hole_punch_failed_total"] += 1
        except (HolePunchForbidden, InvalidPunchTransition) as exc:
            self._store.save_security_event(
                "hole_punch_state_invalid", "warning", webid=actor_webid,
                details=f"attempt_id={attempt_id} error={exc}",
            )
            await websocket.send(json.dumps({
                "type": "error",
                "code": "invalid_hole_punch_state_transition",
                "message": str(exc),
            }))
            return

        await websocket.send(json.dumps({
            "type": "hole_punch_complete_ack",
            "attempt_id": attempt_id,
            "result": result,
        }))

    async def _handle_discover_peer(self, websocket, data: dict) -> None:
        """Resolve a peer's Proxion address to their gateway info."""
        address = data.get("address", "").strip()
        if not address:
            await websocket.send(json.dumps({
                "type": "error", "code": "missing_address", "message": "address required",
            }))
            return

        result = await self._discover_peer_gateway(address)
        if not result:
            await websocket.send(json.dumps({
                "type": "error", "code": "peer_not_found",
                "message": "Could not reach peer gateway — check the address and try again.",
            }))
            return

        discovered_did = result.get("did", "")
        fp = ""
        try:
            from .pop import fingerprint as _fp
            from .didkey import did_to_pub_key as _d2pk
            fp = _fp(_d2pk(discovered_did))
        except Exception:
            pass

        await websocket.send(json.dumps({
            "type": "peer_discovered",
            "did": discovered_did,
            "gateway_http_url": result.get("gateway_http_url", ""),
            "display_name": result.get("display_name") or discovered_did[:20],
            "fingerprint": fp,
            "x25519_pub": result.get("x25519_pub", ""),
        }))

    async def _handle_announce_room_join(self, websocket, data: dict) -> None:
        """Remote user announces they want to federate a local room to their home gateway."""
        room_id = data.get("room_id", "")
        code = data.get("code", "")
        home_gateway = data.get("home_gateway", "").strip()
        caller_webid = self._client_webids.get(websocket, "")

        if not room_id or not caller_webid or not home_gateway:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return

        room = self._local_rooms.get(room_id)
        if not room:
            await websocket.send(json.dumps({"type": "error", "message": "room_not_found"}))
            return

        # Validate code OR caller must already be a member
        stored_code = room.get("code", "")
        import hmac as _hmac
        code_ok = stored_code and _hmac.compare_digest(
            str(code).encode(), str(stored_code).encode()
        )
        already_member = websocket in room.get("members", set()) or (
            self._store and caller_webid in (self._store.get_room_members(room_id) or [])
        )
        if not code_ok and not already_member:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_code"}))
            return

        if self._store and caller_webid and self._store.is_room_banned(room_id, caller_webid):
            await websocket.send(json.dumps({"type": "error", "message": "banned_from_room"}))
            return

        own_http = self._gateway_http_url()
        if home_gateway == own_http or home_gateway == self._ws_public_url():
            # Same gateway — no federation needed
            await websocket.send(json.dumps({"type": "federated_room_joined", "room_id": room_id, "same_gateway": True}))
            return

        # Reject private/loopback URLs to prevent SSRF via relay fanout
        from .relay import _validate_relay_target as _vrt
        _gw_for_check = home_gateway.replace("wss://", "https://").replace("ws://", "http://")
        if not _vrt(_gw_for_check):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_home_gateway"}))
            return

        if self._store:
            self._store.add_federated_room_member(room_id, caller_webid, home_gateway)
            # R59G: replay the room's custom-emoji set to the new member's
            # gateway (per-emoji deltas — the /relay body cap forbids one blob).
            try:
                self._sync_room_emoji_to_gateway(room_id, home_gateway)
            except Exception:
                pass

        # Notify locally connected members that a federated peer has joined
        _join_event = json.dumps({
            "type": "room_member_joined",
            "room_id": room_id,
            "webid": caller_webid,
            "display_name": self._name_for(websocket, caller_webid),
            "federated": True,
            "gateway": home_gateway,
        })
        for _ws in list(self._local_rooms.get(room_id, {}).get("members", set())):
            try:
                await _ws.send(_join_event)
            except Exception:
                pass

        await websocket.send(json.dumps({
            "type": "federated_room_joined",
            "room_id": room_id,
            "gateway": home_gateway,
        }))

        # Send recent room history so the federated member isn't starting blank
        if self._store:
            _history = self._store.get_messages(room_id, limit=50)
            if _history:
                await websocket.send(json.dumps({
                    "type": "room_history",
                    "room_id": room_id,
                    "messages": _history,
                }))

