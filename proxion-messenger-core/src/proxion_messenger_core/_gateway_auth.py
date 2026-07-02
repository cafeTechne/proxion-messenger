"""AuthHandlerMixin — authentication and registration command handlers for ProxionGateway.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: _pending_auth, _auth_verified, _client_webids, _webid_sockets,
                  _session_meta, _relay_queue, _peer_gateway_urls, _user_presence,
                  _display_names, _local_rooms, _store, agent, config,
                  broadcast(), _make_turn_creds(), _backfill_rooms_from_pod(),
                  _sync_profile_to_pod(), process_command().
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone

logger = logging.getLogger("proxion_messenger_core.gateway")


class AuthHandlerMixin:

    async def _handle_auth_response(self, websocket, data: dict) -> None:
        _ip_ar = (self._session_meta.get(websocket) or {}).get("ip_addr", "?")
        logger.info("auth_response received from %s (has_pending=%s)", _ip_ar, websocket in self._pending_auth)
        pending = self._pending_auth.pop(websocket, None)
        if not pending:
            await websocket.send(json.dumps({"type": "auth_failed", "reason": "no_pending_challenge"}))
            return
        if time.time() > pending["expires_at"]:
            await websocket.send(json.dumps({"type": "auth_failed", "reason": "challenge_expired"}))
            return
        # Verify session-bound auth context (R7 — prevents challenge replay across sessions)
        if "auth_ctx" in pending:
            _ip_now = (self._session_meta.get(websocket) or {}).get("ip_addr", "") or ""
            _ua_hash_now = (self._session_meta.get(websocket) or {}).get("user_agent_hash", "") or ""
            _nonce_now = pending["nonce"]
            import hashlib as _hl_resp
            _ctx_now = _hl_resp.sha256(
                f"{_ip_now}|{_ua_hash_now}|{_nonce_now}".encode()
            ).hexdigest()
            if _ctx_now != pending["auth_ctx"]:
                logger.warning("Auth context mismatch — possible challenge replay for %s", _ip_now)
                if self._store:
                    self._store.save_security_event(
                        "auth_context_mismatch", "warning",
                        webid=None, ip=_ip_now,
                        details="auth context hash mismatch on response",
                    )
                await websocket.send(json.dumps({"type": "auth_failed", "reason": "context_mismatch"}))
                try:
                    await websocket.close(1008, "auth_failed")
                except Exception:
                    pass
                return
        did = pending["did"]
        if did.startswith("did:key:"):
            try:
                from .didkey import did_to_pub_key
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                pub_bytes = did_to_pub_key(did)
                pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
                sig_b64 = data.get("signature", "")
                sig = base64.urlsafe_b64decode(sig_b64 + "==")
                nonce_bytes = pending["nonce"].encode()
                pub_key.verify(sig, nonce_bytes)
            except Exception:
                # Auth failure: track and potentially lockout
                _ws_key = id(websocket)
                _ip = (self._session_meta.get(websocket) or {}).get("ip_addr", "")
                _fail_key = (_ws_key, _ip)
                _fail_counts = getattr(self, "_auth_fail_counts", {})
                _fail_entry = _fail_counts.get(_fail_key, {"count": 0, "first_at": time.time()})
                # Reset window if >10 minutes since first failure
                if time.time() - _fail_entry["first_at"] > 600:
                    _fail_entry = {"count": 0, "first_at": time.time()}
                _fail_entry["count"] += 1
                _fail_counts[_fail_key] = _fail_entry
                if hasattr(self, "_auth_fail_counts"):
                    self._auth_fail_counts[_fail_key] = _fail_entry
                if _fail_entry["count"] >= 5:
                    logger.warning("Auth lockout for %s after %d failures", _ip or "unknown", _fail_entry["count"])
                    if self._store:
                        self._store.save_security_event(
                            "auth_lockout", "warning",
                            webid=None, ip=_ip,
                            details=f"locked out after {_fail_entry['count']} failures",
                        )
                    try:
                        await websocket.close(1008, "auth_lockout")
                    except Exception:
                        pass
                    return
                logger.warning(
                    "auth_response signature verification FAILED for DID %s... from %s (attempt %d)",
                    did[:20], _ip or "?", _fail_entry["count"],
                )
                await websocket.send(json.dumps({"type": "auth_failed", "reason": "invalid_signature"}))
                return
        self._auth_verified.add(websocket)
        # Clear failure counter on successful auth
        _ws_key = id(websocket)
        _ip = (self._session_meta.get(websocket) or {}).get("ip_addr", "")
        if hasattr(self, "_auth_fail_counts"):
            self._auth_fail_counts.pop((_ws_key, _ip), None)
        await self.process_command(websocket, {
            "cmd": "register",
            "did": pending["did"],
            "webid": pending["webid"],
            "display_name": pending["display_name"],
            "gateway_url": pending["gateway_url"],
            "delegation_cert": pending.get("delegation_cert"),
        })

    async def _handle_register(self, websocket, data: dict) -> None:
        identity = data.get("did", "") or data.get("webid", "")
        _ip_reg = (self._session_meta.get(websocket) or {}).get("ip_addr", "?")
        logger.info("register received from %s — DID=%r", _ip_reg, identity[:40] if identity else "<empty>")
        gateway_url = data.get("gateway_url", "") or ""
        display_name = data.get("display_name", "")
        display_name = display_name[:100]
        if display_name:
            self._display_names[websocket] = display_name

        # Validate gateway_url before caching it
        if gateway_url:
            import urllib.parse as _up
            _gw_ok = False
            if len(gateway_url) <= 512:
                _parsed = _up.urlparse(gateway_url)
                if _parsed.scheme in ("ws", "wss") and not _parsed.username and not _parsed.password:
                    if os.environ.get("PROXION_ALLOW_PRIVATE_RELAY") == "1":
                        _gw_ok = True
                    else:
                        from .relay import _validate_relay_target
                        _gw_ok = _validate_relay_target(gateway_url)
            if not _gw_ok:
                gateway_url = ""

        _env_auth = os.environ.get("PROXION_REQUIRE_AUTH", "")
        if _env_auth == "1":
            require_auth = True
        elif _env_auth == "0":
            require_auth = False  # explicitly disabled
        else:
            # Auto-require when gateway is bound to a specific routable address (not loopback/wildcard).
            # Wildcard "0.0.0.0"/"::" requires explicit opt-in so local dev isn't affected.
            _host = (getattr(self, "config", None) and self.config.host) or ""
            # Only genuine loopback addresses skip auth.
            # Wildcard bindings (0.0.0.0 / :: / "") are externally routable
            # in production (Docker, VPS) so they must require auth.
            _loopback_only = _host in ("127.0.0.1", "localhost", "::1")
            require_auth = not _loopback_only
        already_verified = websocket in self._auth_verified
        if already_verified:
            self._auth_verified.discard(websocket)
        if require_auth and not already_verified:
            if not identity.startswith("did:key:"):
                _ip_dbg = (self._session_meta.get(websocket) or {}).get("ip_addr", "?")
                logger.warning(
                    "register rejected — unsupported_identity from %s (got %r, expected did:key:...)",
                    _ip_dbg, identity[:40] if identity else "<empty>",
                )
                await websocket.send(json.dumps({
                    "type": "auth_failed",
                    "reason": "unsupported_identity",
                    "detail": "Only did:key identities are supported; WebID via Solid OIDC is not yet implemented.",
                }))
                return
            nonce = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
            _ip_ctx = (self._session_meta.get(websocket) or {}).get("ip_addr", "") or ""
            _ua_hash_ctx = (self._session_meta.get(websocket) or {}).get("user_agent_hash", "") or ""
            import hashlib as _hl_ctx
            _auth_ctx = _hl_ctx.sha256(
                f"{_ip_ctx}|{_ua_hash_ctx}|{nonce}".encode()
            ).hexdigest()
            self._pending_auth[websocket] = {
                "did": identity,
                "webid": data.get("webid", ""),
                "display_name": display_name,
                "gateway_url": gateway_url,
                "nonce": nonce,
                "expires_at": time.time() + 30,
                "auth_ctx": _auth_ctx,
                "delegation_cert": data.get("delegation_cert"),
            }
            _ip_dbg2 = (self._session_meta.get(websocket) or {}).get("ip_addr", "?")
            logger.info("auth_challenge issued to %s for DID %s...", _ip_dbg2, identity[:20])
            await websocket.send(json.dumps({"type": "auth_challenge", "nonce": nonce}))
            return

        # R8: reject registration from revoked identities
        if identity and identity in getattr(self, "_revoked_dids", set()):
            logger.warning("Rejecting registration from revoked DID: %s", identity)
            try:
                await websocket.close(1008, "identity_revoked")
            except Exception:
                pass
            return

        # Multi-device delegation: a secondary device authenticates with its own
        # device_did (proven above via the auth challenge when auth is required)
        # plus a delegation_cert the account signed. If the cert is valid, this
        # connection acts AS the account_did — rooms/DMs/presence are shared
        # across all of the account's devices. No cert = ordinary single-device
        # session (backward compatible).
        delegation_cert = data.get("delegation_cert")
        if delegation_cert:
            from .device_cert import verify_device_cert
            device_did = identity
            account_did = verify_device_cert(delegation_cert, expected_device_did=device_did)
            if not account_did:
                logger.warning(
                    "register rejected — invalid delegation cert for device %s...",
                    device_did[:20] if device_did else "<empty>",
                )
                await websocket.send(json.dumps({
                    "type": "auth_failed", "reason": "invalid_delegation",
                }))
                return
            if account_did in getattr(self, "_revoked_dids", set()):
                await websocket.send(json.dumps({
                    "type": "auth_failed", "reason": "identity_revoked",
                }))
                return
            self._session_device_did[websocket] = device_did
            # Record the delegated device in the registry so the account's
            # "Linked Devices" list (list_devices) shows it and it can be revoked.
            if self._store:
                try:
                    import base64 as _b64d
                    from .didkey import did_to_pub_key
                    self._store.register_device(
                        device_did, account_did,
                        _b64d.b64encode(did_to_pub_key(device_did)).decode(),
                        "delegation",
                    )
                except Exception:
                    logger.debug("delegated device registry write failed", exc_info=True)
            identity = account_did

        if identity:
            self._client_webids[websocket] = identity
            self._webid_sockets.setdefault(identity, set()).add(websocket)
            # R7: single-session mode — revoke all other active sockets for this identity
            if os.environ.get("PROXION_SINGLE_SESSION") == "1":
                _other = [ws for ws in list(self._webid_sockets.get(identity, set())) if ws is not websocket]
                if _other:
                    asyncio.create_task(self._revoke_websockets(_other, "single_session_enforced"))
            # R13.13: track per-identity connections for aggregated presence
            self._presence_by_identity.setdefault(identity, set()).add(websocket)
            import uuid as _uuid_sess
            self._session_meta[websocket] = {
                "session_id": str(_uuid_sess.uuid4()),
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "ip_addr": getattr(websocket, 'remote_address', ('unknown',))[0]
                    if isinstance(getattr(websocket, 'remote_address', None), (tuple, list))
                    else str(getattr(websocket, 'remote_address', 'unknown')),
            }
            _E2E_KEYS = ("e2e", "nonce", "msg_num", "key_header",
                         "ratchet_pub", "pn", "x25519_pub")
            queued = self._relay_queue.pop(identity, [])
            for msg in queued:
                try:
                    cert_id = msg.get("cert_id")
                    event = {
                        "type": "message",
                        "source": "relay",
                        "from_webid": msg.get("from_webid", ""),
                        "from_display_name": msg.get("display_name") or msg.get("from_webid", "")[:12],
                        "content": msg.get("content", ""),
                        "timestamp": msg.get("timestamp", ""),
                        "message_id": msg.get("message_id", ""),
                        "thread_id": cert_id or msg.get("from_webid", ""),
                        "cert_id": cert_id,
                        "local": True,
                    }
                    for _k in _E2E_KEYS:
                        if _k in msg:
                            event[_k] = msg[_k]
                    await websocket.send(json.dumps(event))
                except Exception:
                    pass
            if gateway_url:
                self._peer_gateway_urls[identity] = gateway_url

            now = datetime.now(timezone.utc).isoformat()
            self._user_presence[identity] = {
                "status": "online",
                "status_message": self._user_presence.get(identity, {}).get("status_message", ""),
                "updated_at": now,
                "last_active_at": now,
            }
            await self.broadcast({
                "type": "presence_update",
                "webid": identity,
                "status": "online",
                "status_message": self._user_presence[identity]["status_message"],
                "updated_at": now,
                "last_active_at": now,
            })
            turn_creds = self._make_turn_creds(identity)
            registered_msg: dict = {"type": "registered", "webid": identity}
            if turn_creds:
                registered_msg["turn"] = turn_creds
            await websocket.send(json.dumps(registered_msg))

            if self._store:
                dn = self._display_names.get(websocket)
                if dn:
                    self._store.save_display_name(identity, dn)
                else:
                    saved_dn = self._store.get_display_name(identity)
                    if saved_dn:
                        self._display_names[websocket] = saved_dn

                x25519_pub = data.get("x25519_pub")
                if x25519_pub:
                    self._store.save_x25519_pub(identity, x25519_pub)
                    # Multi-device: also record this device's E2E key under the
                    # account so peers can fan a DM out to every device. device_id
                    # is the physical device's did (== account_did for the primary).
                    _dev_id = self._session_device_did.get(websocket) or identity
                    self._store.save_device_e2e_key(identity, _dev_id, x25519_pub)

                # Write operator profile to pod (fire-and-forget)
                _prof_dn = self._display_names.get(websocket, "")
                _prof_x25519 = data.get("x25519_pub") or ""
                if _prof_dn or _prof_x25519:
                    asyncio.create_task(
                        self._sync_profile_to_pod(identity, _prof_dn, _prof_x25519)
                    )

                restored_rooms = []
                for room_id in self._store.get_rooms_for_member(identity):
                    if room_id in self._local_rooms:
                        self._local_rooms[room_id]["members"].add(websocket)
                        r = self._local_rooms[room_id]
                        restored_rooms.append({
                            "id": room_id,
                            "name": r["name"],
                            "code": r["code"],
                            "invite_url": r.get("invite_url", ""),
                            "creator_webid": r.get("creator_webid", ""),
                            "local": True,
                        })
                # DID rotation recovery: if no SQLite membership exists but _local_rooms
                # has rooms (restored from pod at startup), adopt them all. This handles
                # the case where the operator's DID changed (e.g. after an IDB wipe) or
                # where SQLite was cold but the pod had the authoritative room list.
                if not restored_rooms and self._local_rooms:
                    _ip = (self._session_meta.get(websocket) or {}).get("ip_addr", "")
                    if _ip in ("127.0.0.1", "::1", "localhost"):
                        logger.info(
                            "DID rotation recovery: adopting %d local room(s) for %s from %s",
                            len(self._local_rooms), identity[:20], _ip,
                        )
                        for room_id, r in list(self._local_rooms.items()):
                            self._store.add_room_member(room_id, identity)
                            r["members"].add(websocket)
                            restored_rooms.append({
                                "id": room_id,
                                "name": r["name"],
                                "code": r["code"],
                                "invite_url": r.get("invite_url", ""),
                                "creator_webid": r.get("creator_webid", ""),
                                "local": True,
                            })

                if self._store and restored_rooms:
                    last_reads = self._store.get_all_last_reads(identity)
                    for r in restored_rooms:
                        r["last_read_ts"] = last_reads.get(r["id"], 0)
                if restored_rooms:
                    await websocket.send(json.dumps({"type": "rooms", "rooms": restored_rooms}))
                    asyncio.create_task(
                        self._backfill_rooms_from_pod([r["id"] for r in restored_rooms])
                    )

                local_dms = []
                for t in self._store.get_dm_threads(owner_webid=identity):
                    peer_dn = self._store.get_display_name(t["peer_webid"])
                    local_dms.append({
                        "id": t["thread_id"],
                        "name": peer_dn or t.get("display_name") or t["peer_webid"][:12],
                        "peer_webid": t["peer_webid"],
                        "local": True,
                    })
                if self._store and local_dms:
                    last_reads = self._store.get_all_last_reads(identity)
                    for d in local_dms:
                        d["last_read_ts"] = last_reads.get(d["id"], 0)
                if local_dms:
                    await websocket.send(json.dumps({"type": "local_dms", "dms": local_dms}))

                rel_list = []
                all_last_reads = self._store.get_all_last_reads(identity)
                for cert_dict in self._store.list_relationships():
                    cert_id = cert_dict.get("certificate_id")
                    peer_did = cert_dict.get("peer_did") or ""
                    lr_ts = all_last_reads.get(cert_id, 0)
                    unread = self._store.count_messages_after(cert_id, lr_ts)
                    rel_list.append({
                        "certificate_id": cert_id,
                        "peer_did":       peer_did,
                        "expires_at":     cert_dict.get("expires_at"),
                        "display_name":   self._store.get_display_name(peer_did) if peer_did else None,
                        "x25519_pub":     (self._store.get_e2e_key(peer_did) or self._store.get_x25519_pub(peer_did)) if peer_did else None,
                        "last_read_ts":   lr_ts,
                        "unread_count":   unread,
                    })
                if rel_list:
                    await websocket.send(json.dumps({"type": "relationships", "contacts": rel_list}))

                # Push pending friend requests (inbound invites awaiting acceptance)
                pending_invites = self._store.list_pending_invites("pending")
                if pending_invites:
                    pending_list = []
                    for inv in pending_invites:
                        issuer = inv.get("issuer", {})
                        from_did = issuer.get("did", "")
                        if not from_did:
                            pub_hex = issuer.get("public_key", "")
                            if pub_hex:
                                try:
                                    from .didkey import pub_key_to_did as _p2d
                                    from_did = _p2d(bytes.fromhex(pub_hex))
                                except Exception:
                                    pass
                        pending_list.append({
                            "invitation_id": inv.get("invitation_id"),
                            "from_did": from_did,
                            "display_name": issuer.get("display_name"),
                            "endpoint_hints": inv.get("endpoint_hints", []),
                        })
                    await websocket.send(json.dumps({
                        "type": "friend_requests",
                        "pending": pending_list,
                    }))
