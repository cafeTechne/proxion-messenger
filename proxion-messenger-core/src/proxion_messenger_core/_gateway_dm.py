"""DmHandlerMixin — direct-message command handlers for ProxionGateway.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: dm_clients, _client_webids, blocklist, agent, outbox, _store,
    _display_names, _webid_sockets, _did_pod_webids, _local_rooms, _any_socket,
    _name_for, broadcast, _record_peer_gateway, _resolve_peer_gateway, _gateway_http_url
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("proxion_messenger_core.gateway")


class DmHandlerMixin:

    async def _handle_send_dm(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        content = data.get("content")
        encrypt = data.get("encrypt", True)
        if not self._client_webids.get(websocket):
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        # R12.2.1: reject if target is revoked
        if self._store:
            cert_dict = self._store.get_relationship_by_cert_id(cert_id) if cert_id else None
            if cert_dict:
                peer_did = cert_dict.get("peer_did", "")
                if peer_did and peer_did in getattr(self, "_revoked_dids", set()):
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "contact_revoked",
                        "detail": "This contact has been revoked. You can no longer send messages.",
                    }))
                    return
        if cert_id in self.dm_clients:
            cert, client = self.dm_clients[cert_id]

            # Safety Catch: Blocked Recipients
            if self.blocklist.is_blocked(cert.subject):
                await websocket.send(json.dumps({"type": "error", "message": "Recipient is blocked"}))
                return

            from .messaging import compose, send
            msg = compose(self.agent.identity_key, cert, content, encrypt=encrypt, reply_to_id=data.get("reply_to_id"))
            try:
                send(msg, client)
                logger.info(f"Sent DM to {cert_id}")
            except Exception as e:
                logger.warning(f"Failed to send DM to {cert_id}, enqueuing: {e}")
                self.outbox.enqueue(msg, target_cert_id=cert_id)
                await websocket.send(json.dumps({"type": "info", "message": "Message queued (offline)"}))
        elif self._store:
            # No pod client — fall back to relay delivery if we know the peer's gateway
            cert_dict = self._store.get_relationship_by_cert_id(cert_id)
            if cert_dict:
                peer_did = cert_dict.get("peer_did")
                if peer_did:
                    _E2E_KEYS = ("e2e", "nonce", "msg_num", "key_header",
                                 "ratchet_pub", "pn", "x25519_pub")
                    await self._handle_local_dm(websocket, {
                        "target_webid": peer_did,
                        "content": content,
                        "thread_id": cert_id,
                        "message_id": data.get("message_id"),
                        "reply_to_id": data.get("reply_to_id"),
                        **{k: data[k] for k in _E2E_KEYS if k in data},
                    })
                    return
            await websocket.send(json.dumps({"type": "error", "message": f"Unknown DM recipient: {cert_id}"}))
        else:
            await websocket.send(json.dumps({"type": "error", "message": f"Unknown DM recipient: {cert_id}"}))

    async def _handle_edit_message(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        message_id = data.get("message_id")
        content = data.get("content")
        encrypt = data.get("encrypt", True)
        if not self._client_webids.get(websocket):
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        if cert_id in self.dm_clients:
            cert, client = self.dm_clients[cert_id]
            from .messaging import edit_message, send

            msg = edit_message(
                identity_key=self.agent.identity_key,
                cert=cert,
                original_message_id=message_id,
                new_content=content,
                encrypt=encrypt
            )
            try:
                send(msg, client)
                logger.info(f"Sent edit for {message_id} to {cert_id}")
                # Broadcast locally
                await self.broadcast({
                    "type": "message_edited",
                    "thread_id": cert_id,
                    "message_id": message_id,
                    "new_content": content
                })
            except Exception as e:
                logger.warning(f"Failed to send edit message, enqueuing: {e}")
                self.outbox.enqueue(msg, target_cert_id=cert_id)
                await websocket.send(json.dumps({"type": "info", "message": "Edit queued (offline)"}))
        else:
            await websocket.send(json.dumps({"type": "error", "message": f"Unknown DM recipient: {cert_id}"}))

    async def _handle_get_dms(self, websocket, data: dict) -> None:
        dms = []
        for cid, (cert, _) in self.dm_clients.items():
            if hasattr(cert, "subject"):
                dms.append({"cert_id": cid, "peer_webid": cert.subject})
        await websocket.send(json.dumps({"type": "dms", "dms": dms}))

    # MIME types accepted for file transfers
    _ALLOWED_FILE_MIMES = frozenset({
        "image/jpeg", "image/png", "image/gif", "image/webp",
        "audio/ogg", "audio/webm", "audio/mpeg", "audio/wav",
        "video/mp4", "video/webm",
        "application/pdf", "application/zip",
        "text/plain",
    })

    # Magic byte prefix → MIME (used to override/validate client-declared MIME)
    _MAGIC_BYTES: tuple = (
        (b"\xff\xd8\xff",    "image/jpeg"),
        (b"\x89PNG\r\n",     "image/png"),
        (b"GIF8",            "image/gif"),
        (b"RIFF",            "audio/wav"),   # RIFF container (WAV)
        (b"%PDF",            "application/pdf"),
        (b"PK\x03\x04",     "application/zip"),
        (b"ID3",             "audio/mpeg"),
        (b"\x1aE\xdf\xa3",  "video/webm"),
        (b"\x00\x00\x00",   "video/mp4"),   # ftyp box prefix
    )

    # Magic prefixes for executable/binary formats that are always blocked,
    # regardless of declared MIME type.
    _BLOCKED_MAGIC: tuple = (
        b"\x4d\x5a",        # MZ (Windows PE/DOS executables)
        b"\x7fELF",         # ELF (Linux executables / shared objects)
        b"\xce\xfa\xed\xfe",  # Mach-O 32-bit
        b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit
        b"\xca\xfe\xba\xbe",  # Mach-O fat binary
        b"#!",              # shell scripts
        b"#!/",             # shebang
    )

    async def _handle_send_file(self, websocket, data: dict) -> None:
        cert_id = data.get("cert_id")
        room_id = data.get("room_id")
        filename = data.get("filename", "file")
        data_b64 = data.get("data_b64", "")
        mime_type = data.get("mime_type", "application/octet-stream")
        import base64 as _b64
        import posixpath as _posix

        # Filename normalization: strip directory traversal, control chars
        filename = _posix.basename(str(filename).replace("\\", "/"))
        filename = "".join(c for c in filename if c.isprintable() and c not in '/<>:"|?*')[:128] or "file"

        # Pre-decode size guard: reject before attempting expensive decode
        import binascii as _binascii
        _max_b64_len = ((512 * 1024 + 2) // 3) * 4  # 699052 chars ≈ 512 KB decoded
        if len(data_b64) > _max_b64_len:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_file_encoding"}))
            return
        try:
            file_bytes = _b64.b64decode(data_b64, validate=True)
        except _binascii.Error:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_file_encoding"}))
            return

        # Reject zero-byte files
        if len(file_bytes) == 0:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_file_encoding"}))
            return

        # Normalize MIME: lowercase and strip parameters (e.g. "text/plain; charset=utf-8" → "text/plain")
        mime_type = mime_type.lower().split(";")[0].strip()

        # Blocked magic patterns — immediately rejected regardless of declared MIME
        for blocked_magic in self._BLOCKED_MAGIC:
            if file_bytes[:len(blocked_magic)] == blocked_magic:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "file_type_not_allowed: executable/script",
                }))
                return

        # Magic-byte sniffing to derive effective MIME (overrides declared MIME)
        sniffed_mime = None
        for magic, smime in self._MAGIC_BYTES:
            if file_bytes[:len(magic)] == magic:
                sniffed_mime = smime
                break
        effective_mime = sniffed_mime or mime_type

        # Reject text/* MIME with >20% NUL bytes (binary disguised as text).
        # Only applied when the effective MIME (after sniffing) is still text/*.
        if effective_mime.startswith("text/"):
            _nul_count = file_bytes.count(b"\x00")
            if _nul_count / len(file_bytes) > 0.20:
                await websocket.send(json.dumps({"type": "error", "message": "file_type_not_allowed: binary content in text file"}))
                return
        if effective_mime not in self._ALLOWED_FILE_MIMES:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"file_type_not_allowed: {effective_mime}",
            }))
            return
        mime_type = effective_mime

        if len(file_bytes) > 524288:
            await websocket.send(json.dumps({"type": "error", "message": "File too large (max 512 KB)"}))
        elif cert_id and cert_id in self.dm_clients:
            import tempfile, os as _os
            from .files import send_file as _send_file_pod
            cert, client = self.dm_clients[cert_id]
            with tempfile.NamedTemporaryFile(delete=False, suffix=_os.path.splitext(filename)[1]) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            try:
                _send_file_pod(cert, client, tmp_path, mime_type)
                logger.info(f"Sent file {filename} to pod DM {cert_id}")
            except Exception as exc:
                logger.warning(f"Pod DM file send failed: {exc}")
                await websocket.send(json.dumps({"type": "error", "message": f"File send failed: {exc}"}))
            finally:
                _os.unlink(tmp_path)
        elif cert_id and cert_id not in self.dm_clients:
            # Local DM file relay
            import uuid as _uuid_f
            message_id = "file-" + _uuid_f.uuid4().hex[:12]
            ts = datetime.now(timezone.utc).isoformat()
            sender_webid = self._client_webids.get(websocket, "unknown")
            sender_name = self._name_for(websocket, sender_webid)
            event = {
                "type": "message", "source": "local_dm",
                "thread_id": cert_id, "from_webid": sender_webid,
                "from_display_name": sender_name,
                "content": f"📎 {filename}", "timestamp": ts,
                "message_id": message_id, "local": True,
                "file": {"filename": filename, "mime_type": mime_type,
                         "size": len(file_bytes), "data_b64": data_b64},
            }
            if self._store:
                self._store.save_message(message_id, cert_id, "local_dm",
                                         sender_webid, sender_name, f"📎 {filename}", ts,
                                         seq_num=int(data.get("seq_num") or 0),
                                         prev_hash=str(data.get("prev_hash") or ""))
            # Echo to all sender tabs
            own_echo_file = json.dumps({**event, "own": True})
            for _ws in self._sockets_for(sender_webid) or [websocket]:
                try:
                    await _ws.send(own_echo_file)
                except Exception:
                    pass
            # Relay to peer (all tabs; cross-gateway fallback if offline)
            peer_webid = ""
            if self._store:
                threads = [t for t in self._store.get_dm_threads(owner_webid=sender_webid)
                           if t["thread_id"] == cert_id]
                if threads:
                    peer_webid = threads[0]["peer_webid"]
            if peer_webid:
                peer_sockets = self._sockets_for(peer_webid)
                if peer_sockets:
                    file_payload = json.dumps(event)
                    for ws in peer_sockets:
                        try:
                            await ws.send(file_payload)
                        except Exception:
                            pass
                else:
                    peer_gw = self._resolve_peer_gateway(peer_webid)
                    if peer_gw:
                        try:
                            from .relay import sign_relay_message, post_relay
                            from .didkey import pub_key_to_did
                            import secrets as _sec
                            gw_did = pub_key_to_did(self.agent.identity_pub_bytes)
                            summary = f"📎 {filename}"
                            relay_nonce = _sec.token_hex(8)
                            sig = sign_relay_message(
                                self.agent.identity_key,
                                gw_did, peer_webid, message_id, summary, ts, relay_nonce,
                            )
                            relay_payload = {
                                "from_webid": sender_webid,
                                "to_webid": peer_webid,
                                "message_id": message_id,
                                "content": summary,
                                "timestamp": ts,
                                "relay_nonce": relay_nonce,
                                "display_name": sender_name,
                                "signature": sig,
                                "file": {"filename": filename, "mime_type": mime_type,
                                         "size": len(file_bytes), "data_b64": data_b64},
                            }
                            http_base = peer_gw.replace("wss://", "https://").replace("ws://", "http://")
                            delivered = await post_relay(http_base.rstrip("/") + "/relay", relay_payload)
                            if not delivered and self._store:
                                self._store.enqueue_relay(message_id, peer_webid, http_base, relay_payload)
                                await websocket.send(json.dumps({
                                    "type": "relay_pending",
                                    "message_id": message_id,
                                    "message": "Peer gateway unreachable — file queued for retry.",
                                }))
                        except Exception as exc:
                            logger.warning(f"File cross-gateway relay failed: {exc}")
        elif room_id and room_id in self._local_rooms:
            if websocket not in self._local_rooms[room_id]["members"]:
                await websocket.send(json.dumps({"type": "error", "message": "Not a member of this room"}))
                return
            import uuid as _uuid_f
            message_id = "file-" + _uuid_f.uuid4().hex[:12]
            ts = datetime.now(timezone.utc).isoformat()
            sender_webid = self._client_webids.get(websocket, "unknown")
            sender_name = self._name_for(websocket, sender_webid)
            event = {
                "type": "message", "source": "room",
                "thread_id": room_id, "from_webid": sender_webid,
                "from_display_name": sender_name,
                "content": f"📎 {filename}", "timestamp": ts,
                "message_id": message_id, "local": True,
                "file": {"filename": filename, "mime_type": mime_type,
                         "size": len(file_bytes), "data_b64": data_b64},
            }
            if self._store:
                self._store.save_message(message_id, room_id, "room", sender_webid, sender_name, f"📎 {filename}", ts)
            room = self._local_rooms[room_id]
            for ws in list(room["members"]):
                try:
                    await ws.send(json.dumps({**event, "own": ws == websocket}))
                except Exception:
                    pass
        elif room_id and room_id in self.room_memberships:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "File sharing in federated pod rooms is not yet supported."
            }))

    async def _handle_link_pod(self, websocket, data: dict) -> None:
        # Associate a Solid Pod WebID with this client's DID.
        # Pod operations (CSS auth, resource sync) use the pod webid;
        # identity/presence/rooms use the DID.
        pod_webid = data.get("webid", "")
        client_did = self._client_webids.get(websocket)
        if pod_webid and client_did:
            self._did_pod_webids[client_did] = pod_webid
            await websocket.send(json.dumps({"type": "pod_linked", "pod_webid": pod_webid, "did": client_did}))

    async def _handle_local_dm(self, websocket, data: dict) -> None:
        # Direct WS relay for local testing — no pod required.
        # Both sender and target must have called "register" first.
        sender_webid = self._client_webids.get(websocket, "unknown")
        if sender_webid == "unknown":
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        target_webid = data.get("target_webid", "")
        # R12.2.1: reject if target is revoked
        if target_webid and target_webid in getattr(self, "_revoked_dids", set()):
            await websocket.send(json.dumps({
                "type": "error",
                "message": "contact_revoked",
                "detail": "This contact has been revoked. You can no longer send messages.",
            }))
            return
        content = data.get("content", "")
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
        # Relay-mode sealed-sender enforcement (Round 22)
        if self._store and target_webid:
            from .transport_policy import requires_sealed_sender
            if requires_sealed_sender(self._store, target_webid):
                if data.get("e2e_v") != 3:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "code": "relay_requires_sealed_sender",
                        "message": "Relay path is active — message must use sealed sender (e2e_v=3).",
                    }))
                    return
        thread_id = data.get("thread_id") or target_webid
        import uuid as _uuid_local
        message_id = data.get("message_id") or ("local-" + _uuid_local.uuid4().hex[:12])
        sender_name = self._name_for(websocket, sender_webid)
        ts = datetime.now(timezone.utc).isoformat()

        # Reject non-incrementing seq_num when provided (backward compatible)
        _provided_seq = data.get("seq_num")
        if _provided_seq is not None:
            _seq_int = int(_provided_seq)
            if _seq_int > 0 and self._store:
                _dm_thread_id = data.get("thread_id") or data.get("cert_id") or ""
                _max_seq = self._store.get_max_seq_num(_dm_thread_id)
                if _seq_int <= _max_seq:
                    await websocket.send(json.dumps({
                        "type": "error", "message": "invalid_sequence",
                    }))
                    return

        event = {
            "type": "message",
            "source": "local_dm",
            "thread_id": thread_id,
            "from_webid": sender_webid,
            "from_display_name": sender_name,
            "content": content,
            "timestamp": ts,
            "message_id": message_id,
            "reply_to_id": data.get("reply_to_id"),
            "local": True,
        }
        # E2E v2: X3DH session-encrypted DM
        if data.get("e2e_v") == 2 and self._store:
            _session_id = data.get("session_id")
            if _session_id and data.get("ciphertext_b64"):
                # Client already encrypted — relay as-is, annotate event
                event["e2e_v"] = 2
                event["session_id"] = _session_id
                for _ek in ("ciphertext_b64", "nonce_b64", "msg_num", "header"):
                    if _ek in data:
                        event[_ek] = data[_ek]
            else:
                # No active session — send peer's prekey bundle to client
                _bundle = self._store.get_prekey_bundle(target_webid) if target_webid else None
                await websocket.send(json.dumps({
                    "type": "dm_session_init_required",
                    "peer_webid": target_webid,
                    "prekey_bundle": _bundle,
                }))
                return
        else:
            # Forward legacy E2E fields if present (additive — no migration needed)
            for _k in ("e2e", "nonce", "msg_num", "key_header", "ratchet_pub", "pn", "x25519_pub"):
                if _k in data:
                    event[_k] = data[_k]

        # Persist to store
        if self._store:
            self._store.save_message(
                message_id, thread_id, "dm",
                sender_webid, sender_name, content, ts,
                reply_to_id=data.get("reply_to_id"),
                seq_num=int(data.get("seq_num") or 0),
                prev_hash=str(data.get("prev_hash") or ""),
            )
            target_dn = self._store.get_display_name(target_webid) if target_webid else None
            self._store.save_dm_thread(thread_id, target_webid, target_dn, owner_webid=sender_webid)
            self._store.set_last_read(sender_webid, thread_id)
            # R13.14: track both parties as known contacts
            if sender_webid and sender_name:
                self._store.upsert_contact(sender_webid, sender_name, source="dm")
            if target_webid:
                _tdn = target_dn or (target_webid[-8:] if target_webid else "")
                if _tdn:
                    self._store.upsert_contact(target_webid, _tdn, source="dm")
            # R13.4: metrics
            self._metrics["messages_total"] += 1

        # Write-through for gateway-relayed (non-federated) DMs
        asyncio.create_task(self._sync_local_dm_to_pod(thread_id, event))

        # Write-through to pod if a relationship cert exists for this peer.
        # dm_clients is keyed by cert_id (federated) OR webid (own pod) —
        # try both so cross-gateway DMs actually reach the recipient's pod.
        if self._store and self.dm_clients and target_webid:
            cert_dict = self._store.get_relationship_by_did(target_webid)
            if cert_dict:
                cert_id = cert_dict.get("certificate_id")
                client_entry = (
                    self.dm_clients.get(cert_id)
                    or self.dm_clients.get(target_webid)
                )
                if client_entry:
                    _, pod_client = client_entry
                    asyncio.create_task(
                        self._sync_message_to_pod(
                            pod_client, cert_dict, content, message_id, sender_webid
                        )
                    )

        # Echo to sender with own=True (all sender's tabs)
        own_echo = json.dumps({**event, "own": True})
        try:
            await websocket.send(own_echo)
        except Exception:
            pass
        for _ws in self._sockets_for(sender_webid):
            if _ws is not websocket:
                try:
                    await _ws.send(own_echo)
                except Exception:
                    pass

        # Store target's gateway URL if provided by browser (persist to SQLite)
        target_gateway_url = data.get("target_gateway_url", "")
        if target_gateway_url and target_webid:
            self._record_peer_gateway(target_webid, target_gateway_url)

        target_sockets = self._sockets_for(target_webid)
        if target_sockets:
            payload = json.dumps(event)
            for ws in target_sockets:
                try:
                    await ws.send(payload)
                except Exception as exc:
                    logger.warning(f"local_dm relay failed: {exc}")
        else:
            # Attempt WebPush for offline recipients
            if self._store and target_webid:
                _subs = self._store.get_push_subscriptions(target_webid)
                if _subs:
                    _vpk = getattr(self, "_vapid_private_pem", None)
                    _vsub = getattr(self, "_vapid_subject", None)
                    if _vpk and _vsub:
                        from .webpush import send_web_push
                        _sender_name = self._name_for(websocket, sender_webid)
                        for _sub in _subs:
                            send_web_push(
                                subscription={
                                    "endpoint": _sub["endpoint"],
                                    "keys": {
                                        "p256dh": _sub["p256dh_b64"],
                                        "auth": _sub["auth_b64"],
                                    },
                                },
                                payload={
                                    "type": "message",
                                    "thread_id": thread_id,
                                    "display_name": _sender_name,
                                },
                                vapid_private_pem=_vpk,
                                vapid_subject=_vsub,
                            )
            # Try cross-gateway relay
            peer_gw = self._resolve_peer_gateway(target_webid)
            if peer_gw:
                try:
                    from .relay import sign_relay_message, post_relay
                    from .didkey import pub_key_to_did
                    import secrets as _sec
                    gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
                    relay_nonce = _sec.token_hex(8)
                    sig = sign_relay_message(
                        self.agent.identity_key,
                        gateway_did, target_webid,
                        message_id, content, ts, relay_nonce,
                    )
                    my_http_url = self._gateway_http_url()
                    inner_payload = {
                        "from_webid": gateway_did,
                        "from_display_name": sender_name,
                        "to_webid": target_webid,
                        "message_id": message_id,
                        "content": content,
                        "timestamp": ts,
                        "relay_nonce": relay_nonce,
                        "display_name": sender_name,
                        "signature": sig,
                        "origin_gateway_url": my_http_url,
                    }
                    for _k in ("e2e", "nonce", "msg_num", "key_header",
                               "ratchet_pub", "pn", "x25519_pub"):
                        if _k in data:
                            inner_payload[_k] = data[_k]
                    # Attempt sealed relay if peer's x25519 pub is known
                    _peer_x25519 = self._resolve_peer_x25519_pub(target_webid)
                    if _peer_x25519:
                        try:
                            from .sealed_relay import seal_relay_payload as _seal
                            _sealed = _seal(inner_payload, _peer_x25519)
                            payload = {
                                "to_webid": target_webid,
                                "message_id": message_id,
                                "timestamp": ts,
                                "relay_nonce": relay_nonce,
                                "signature": sig,
                                "content": content,
                                "content_type": "sealed_dm",
                                "sealed_payload": _sealed,
                            }
                        except Exception as _se:
                            logger.debug("seal_relay_payload failed, sending plaintext: %s", _se)
                            payload = inner_payload
                    else:
                        payload = inner_payload
                    # Prefer gateway_http_url from discovery; fall back to ws→http heuristic
                    http_base = peer_gw.replace("wss://", "https://").replace("ws://", "http://")
                    relay_url = http_base.rstrip("/") + "/relay"
                    delivered = await post_relay(relay_url, payload)
                    if delivered:
                        logger.info(f"Relayed DM to {target_webid} via {peer_gw}")
                    else:
                        # Enqueue for retry; tell sender delivery is pending
                        if self._store:
                            self._store.enqueue_relay(message_id, target_webid, http_base, payload)
                        await websocket.send(json.dumps({
                            "type": "relay_pending",
                            "message_id": message_id,
                            "message": "Peer gateway unreachable — message queued for retry.",
                        }))
                except Exception as exc:
                    logger.warning(f"Cross-gateway relay failed: {exc}")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": f"Relay failed: {exc}",
                    }))
            else:
                await websocket.send(json.dumps({
                    "type": "info",
                    "message": f"Target {target_webid!r} not connected and no gateway URL known. "
                               "Share your Proxion address (DID@gateway-url) so peers can reach you.",
                }))

    async def _handle_upload_prekeys(self, websocket, data: dict) -> None:
        """Store caller's prekey bundle (signed prekey + one-time prekeys) in local DB."""
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return
        bundle = data.get("bundle", {})
        spk_id = bundle.get("signed_prekey_id")
        spk_pub = bundle.get("signed_prekey_pub_b64")
        spk_priv = bundle.get("signed_prekey_priv_b64", "")
        if spk_id and spk_pub:
            self._store.save_prekey(spk_id, owner_webid, spk_pub, spk_priv, one_time=False)
        for opk in bundle.get("one_time_prekeys", []):
            opk_id = opk.get("id")
            opk_pub = opk.get("pub_b64")
            opk_priv = opk.get("priv_b64", "")
            if opk_id and opk_pub:
                self._store.save_prekey(opk_id, owner_webid, opk_pub, opk_priv, one_time=True)
        await websocket.send(json.dumps({"type": "prekeys_uploaded", "owner_webid": owner_webid}))

    async def _handle_get_prekey_bundle(self, websocket, data: dict) -> None:
        """Return the public prekey bundle for a peer (one-time prekey is claimed and consumed)."""
        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no_store"}))
            return
        peer_webid = data.get("peer_webid", "")
        bundle = self._store.get_prekey_bundle(peer_webid) if peer_webid else None
        await websocket.send(json.dumps({
            "type": "prekey_bundle",
            "peer_webid": peer_webid,
            "bundle": bundle,
        }))

    async def _handle_session_unknown(self, websocket, data: dict) -> None:
        """Client received a DM with an unknown session_id (e.g. after app reinstall).

        Relay a reset request to the original peer so they can re-initiate X3DH.
        If the peer is offline, store a pending reset flag and deliver on next connect.
        """
        requester_webid = self._client_webids.get(websocket, "unknown")
        if requester_webid == "unknown":
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        session_id = data.get("session_id", "")
        if not session_id:
            await websocket.send(json.dumps({"type": "error", "message": "session_id required"}))
            return

        # Look up the original peer from the session record
        peer_webid = ""
        if self._store:
            sess = self._store.get_dm_session_by_id(session_id)
            if sess:
                # requester is the owner (the one who lost their session state)
                peer_webid = sess.get("peer_webid") or sess.get("owner_webid", "")
                # If the requester IS the owner, the peer is peer_webid; otherwise swap.
                if sess.get("owner_webid") == requester_webid:
                    peer_webid = sess.get("peer_webid", "")
                else:
                    peer_webid = sess.get("owner_webid", "")

        reset_event = json.dumps({
            "type": "session_reset_requested",
            "from_webid": requester_webid,
            "session_id": session_id,
        })

        peer_sockets = self._sockets_for(peer_webid) if peer_webid else []
        if peer_sockets:
            for ws in peer_sockets:
                try:
                    await ws.send(reset_event)
                except Exception:
                    pass
            await websocket.send(json.dumps({
                "type": "session_reset_pending",
                "session_id": session_id,
                "peer_webid": peer_webid,
            }))
        else:
            # Peer offline — store pending reset; deliver on next peer connect
            if not hasattr(self, "_pending_session_resets"):
                self._pending_session_resets = {}
            self._pending_session_resets.setdefault(peer_webid, []).append({
                "session_id": session_id,
                "requester_webid": requester_webid,
                "event": reset_event,
            })
            await websocket.send(json.dumps({
                "type": "session_reset_deferred",
                "session_id": session_id,
                "message": "Peer offline — reset request will be delivered on reconnect.",
            }))

    async def _handle_session_ready(self, websocket, data: dict) -> None:
        """Client confirms a new session is ready; clean up the stale old session."""
        owner_webid = self._client_webids.get(websocket, "unknown")
        old_session_id = data.get("old_session_id", "")
        if old_session_id and self._store:
            self._store.delete_dm_session(old_session_id)
        await websocket.send(json.dumps({
            "type": "session_ready_ack",
            "old_session_id": old_session_id,
        }))

    async def _handle_sealed_dm(self, websocket, data: dict) -> None:
        """Relay a sealed-sender (e2e_v=3) DM. Gateway sees only recipient WebID."""
        sender_webid = self._client_webids.get(websocket, "unknown")
        if sender_webid == "unknown":
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return
        target_webid = data.get("target_webid", "")
        sealed_b64 = data.get("sealed_b64", "")
        if not target_webid or not sealed_b64:
            await websocket.send(json.dumps({"type": "error", "message": "missing_fields"}))
            return
        if target_webid and target_webid in getattr(self, "_revoked_dids", set()):
            await websocket.send(json.dumps({"type": "error", "message": "contact_revoked"}))
            return

        event = {
            "type": "sealed_message",
            "e2e_v": 3,
            "sealed_b64": sealed_b64,
        }

        # Echo a minimal ack to sender
        await websocket.send(json.dumps({"type": "sealed_dm_sent", "target_webid": target_webid}))

        # Deliver to recipient sockets
        target_sockets = self._sockets_for(target_webid)
        if target_sockets:
            payload = json.dumps(event)
            for ws in target_sockets:
                try:
                    await ws.send(payload)
                except Exception as exc:
                    logger.warning("sealed_dm relay failed: %s", exc)
        else:
            # Attempt WebPush if recipient is offline and we have subscriptions
            if self._store:
                subs = self._store.get_push_subscriptions(target_webid)
                if subs:
                    _vapid_priv = getattr(self, "_vapid_private_pem", None)
                    _vapid_sub = getattr(self, "_vapid_subject", None)
                    if _vapid_priv and _vapid_sub:
                        from .webpush import send_web_push
                        for sub in subs:
                            send_web_push(
                                subscription={
                                    "endpoint": sub["endpoint"],
                                    "keys": {
                                        "p256dh": sub["p256dh_b64"],
                                        "auth": sub["auth_b64"],
                                    },
                                },
                                payload={"type": "sealed_message"},
                                vapid_private_pem=_vapid_priv,
                                vapid_subject=_vapid_sub,
                            )

    async def _handle_send_dm_fanout(self, websocket, data: dict) -> None:
        """Deliver per-device encrypted DM envelopes in one logical send.

        Expects:
            message_id: str
            from_webid:  str
            fanout: list[{to_webid, to_device_id, payload}]
        """
        message_id = data.get("message_id", "")
        from_webid = data.get("from_webid", "") or self._client_webids.get(websocket, "")
        fanout = data.get("fanout", [])
        if not message_id or not fanout:
            await websocket.send(json.dumps({"type": "error", "message": "message_id and fanout required"}))
            return

        delivered = []
        for entry in fanout:
            to_webid = entry.get("to_webid", "")
            to_device_id = entry.get("to_device_id", "")
            if not to_webid or not to_device_id:
                continue
            if self._store:
                self._store.record_dm_delivery(message_id, to_webid, to_device_id)
            target_sockets = self._sockets_for(to_webid) or []
            event = json.dumps({
                "type": "dm_fanout",
                "message_id": message_id,
                "from_webid": from_webid,
                "to_device_id": to_device_id,
                "payload": entry.get("payload"),
            })
            for ws in target_sockets:
                try:
                    await ws.send(event)
                    if self._store:
                        self._store.mark_dm_delivered(message_id, to_webid, to_device_id)
                except Exception as exc:
                    logger.warning("dm_fanout relay failed to %s/%s: %s", to_webid, to_device_id, exc)
            delivered.append({"to_webid": to_webid, "to_device_id": to_device_id})

        await websocket.send(json.dumps({
            "type": "send_dm_fanout_ack",
            "message_id": message_id,
            "delivered": delivered,
        }))

    async def _handle_dm_decrypt_failed(self, websocket, data: dict) -> None:
        """Client reports it could not decrypt a DM — initiate session recovery.

        The gateway is a sealed-DM relay and cannot decrypt; failure detection is
        client-triggered.  On receipt the gateway records the recovery attempt and
        emits ``session_recovery_required`` to the sender so they can re-send their
        ratchet state or kick off X3DH re-init.
        """
        reporter_webid = self._client_webids.get(websocket, "unknown")
        if reporter_webid == "unknown":
            await websocket.send(json.dumps({"type": "error", "message": "not_registered"}))
            return

        thread_id = data.get("thread_id", "")
        session_id = data.get("session_id", "")
        if not thread_id:
            await websocket.send(json.dumps({"type": "error", "message": "thread_id required"}))
            return

        # Record the attempt in the store (metrics + observability)
        attempt_no = 1
        if self._store:
            existing = self._store.get_recovery_attempts(thread_id, reporter_webid)
            attempt_no = len(existing) + 1
            self._store.record_recovery_attempt(thread_id, session_id, reporter_webid, attempt_no)
            self._metrics["dm_decrypt_errors_total"] = self._metrics.get("dm_decrypt_errors_total", 0) + 1
            self._metrics["session_recovery_attempts_total"] = (
                self._metrics.get("session_recovery_attempts_total", 0) + 1
            )

        # Determine the original sender (the peer this reporter was talking to)
        sender_webid = ""
        if self._store and session_id:
            sess = self._store.get_dm_session_by_id(session_id)
            if sess:
                owner = sess.get("owner_webid", "")
                peer = sess.get("peer_webid", "")
                sender_webid = peer if owner == reporter_webid else owner
        if not sender_webid and thread_id:
            sender_webid = thread_id  # thread_id is often the peer's webid

        recovery_event = json.dumps({
            "type": "session_recovery_required",
            "thread_id": thread_id,
            "session_id": session_id,
            "reporter_webid": reporter_webid,
        })

        sender_sockets = self._sockets_for(sender_webid) if sender_webid else []
        if sender_sockets:
            for ws in sender_sockets:
                try:
                    await ws.send(recovery_event)
                except Exception:
                    pass
            await websocket.send(json.dumps({
                "type": "session_recovery_initiated",
                "thread_id": thread_id,
                "session_id": session_id,
            }))
        else:
            await websocket.send(json.dumps({
                "type": "session_recovery_deferred",
                "thread_id": thread_id,
                "session_id": session_id,
                "message": "Sender offline — recovery request queued.",
            }))

    async def _handle_save_session_state(self, websocket, data: dict) -> None:
        """Client pushes E2E session state to gateway for backup.

        Called by the client after every 5 ratchet steps. The gateway stores
        the state in SQLite and checkpoints to the pod.
        """
        owner_webid = self._client_webids.get(websocket, "")
        if not owner_webid or not self._store:
            return
        session_id = data.get("session_id", "")
        if not session_id:
            return
        session = {
            "session_id": session_id,
            "peer_webid": data.get("peer_webid", ""),
            "owner_webid": owner_webid,
            "root_key": data.get("root_key_b64", ""),
            "send_chain_key": data.get("send_chain_key_b64", ""),
            "recv_chain_key": data.get("recv_chain_key_b64", ""),
            "send_count": int(data.get("send_count", 0)),
            "recv_count": int(data.get("recv_count", 0)),
        }
        if not session["root_key"] or not session["peer_webid"]:
            return
        try:
            self._store.save_dm_session(session)
            asyncio.create_task(self._checkpoint_e2e_session(session_id))
        except Exception as exc:
            logger.debug("_handle_save_session_state: %s", exc)
