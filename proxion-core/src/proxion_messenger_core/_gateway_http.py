"""HTTP endpoint handlers (B2, R42 slice 1).

Cohesive POST-endpoint logic extracted from gateway.py's WS-centric body into a
mixin, matching the established _gateway_*.py pattern. No behavior change — the
methods are unchanged and still invoked as self._handle_*_post(...) from
_serve_http. Each method imports its own federation/crypto deps locally, so this
file needs only json/os/asyncio/logging at module scope.
"""
import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime

from .relay import _validate_relay_target as _is_safe_gateway_url

logger = logging.getLogger(__name__)


class HttpEndpointsMixin:
    async def _handle_invite_post(self, body: bytes) -> tuple[str, str]:
        """Handle POST /invite — receive federation invite."""
        # R9: federation quarantine mode
        if os.environ.get("PROXION_FEDERATION_QUARANTINE") == "1" and self._store:
            import uuid as _uuid_qi, hashlib as _hl_qi
            try:
                _data_qi = json.loads(body) if body else {}
            except Exception:
                _data_qi = {}
            _qi_sha256 = _hl_qi.sha256(body).hexdigest()
            if self._store.has_duplicate_quarantine_payload("invite", _qi_sha256):
                return "409 Conflict", '{"error":"duplicate_quarantine_payload"}'
            try:
                self._store.add_quarantine_item(
                    id=str(_uuid_qi.uuid4()),
                    item_type="invite",
                    source_identity=_data_qi.get("issuer", {}).get("did") if isinstance(_data_qi.get("issuer"), dict) else None,
                    payload_json=body.decode("utf-8", errors="replace"),
                    reason="federation_quarantine_mode",
                    created_at=time.time(),
                    payload_sha256=_qi_sha256,
                    source_ip="unknown",
                )
            except Exception:
                pass
            return "202 Accepted", '{"status":"quarantined"}'

        try:
            data = json.loads(body)
        except Exception:
            return "400 Bad Request", '{"error":"invalid JSON"}'

        # Check @type
        if data.get("@type") != "FederationInvite":
            return "400 Bad Request", '{"error":"wrong @type"}'
        
        # Deserialize
        from .federation import FederationInvite
        try:
            invite = FederationInvite.from_dict(data)
        except Exception as exc:
            logger.warning(f"Failed to deserialize invite: {exc}")
            return "400 Bad Request", '{"error":"invalid invite"}'
        
        # Verify signature
        from .handshake import _ed25519_verify
        if not invite.verify(_ed25519_verify):
            return "400 Bad Request", '{"error":"invalid signature"}'
        
        # Check not expired
        import time
        if invite.expires_at < time.time():
            return "400 Bad Request", '{"error":"expired"}'

        # Nonce replay check
        invite_nonce = getattr(invite, 'invitation_id', None) or data.get('invitation_id', '')
        if invite_nonce and self._store:
            if self._store.has_seen_invite_nonce(str(invite_nonce), ttl_seconds=86400):
                return "409 Conflict", '{"error":"replay_detected"}'
            self._store.record_invite_nonce(str(invite_nonce))
            self._store.prune_invite_nonces(time.time() - 86400)

        # R11: trust revocation check for invite issuer
        _inv_issuer_did = invite.issuer.get("did") if isinstance(invite.issuer, dict) else ""
        if self._store and _inv_issuer_did:
            if self._store.is_subject_revoked("peer_did", _inv_issuer_did):
                self._store.save_security_event(
                    "trust_revoked_invite_rejected", "warning",
                    details=f"peer_did={_inv_issuer_did}",
                )
                return "403 Forbidden", '{"error":"trust_revoked_peer"}'

        # R7: HTTPS enforcement for federation endpoint hints
        if not os.environ.get("PROXION_ALLOW_INSECURE_FEDERATION") == "1":
            for _hint in (invite.endpoint_hints or []):
                if isinstance(_hint, str) and _hint.startswith("http://"):
                    return "400 Bad Request", '{"error":"insecure_endpoint_hint"}'

        # R8: DID-pair invite flood control — max 10 pending invites per (from_did, to_did) per 24h
        _inv_from_did = invite.issuer.get("did") or ""
        if _inv_from_did and self._store:
            _inv_to_did = pub_key_to_did(self.agent.identity_pub_bytes)
            import time as _t_inv
            _inv_count = self._store.increment_invite_pair_counter(_inv_from_did, _inv_to_did, _t_inv.time())
            if _inv_count > 10:
                return "429 Too Many Requests", '{"error":"invite_rate_limited"}'
            self._store.prune_invite_counters(_t_inv.time())

        # Extract from_did
        from_did = invite.issuer.get("did")
        if not from_did:
            # Try to build from issuer public_key
            try:
                from .didkey import pub_key_to_did
                pub_hex = invite.issuer.get("public_key")
                if pub_hex:
                    pub_bytes = bytes.fromhex(pub_hex)
                    from_did = pub_key_to_did(pub_bytes)
            except Exception:
                from_did = None
        
        # Save pending invite if store is available
        if self._store:
            self._store.save_pending_invite(invite.to_dict(), from_did or "unknown")
        
        # Broadcast to all connected WebSocket clients
        event = {
            "type": "friend_request_received",
            "invitation_id": invite.invitation_id,
            "from_did": from_did,
            "display_name": invite.issuer.get("display_name"),
            "endpoint_hints": invite.endpoint_hints,
        }
        await self.broadcast(event)

        return "200 OK", '{"status":"received"}'

    async def _handle_invite_accept_post(self, body: bytes) -> tuple[str, str]:
        """Handle POST /invite/accept — receive acceptance from acceptor's gateway.

        Creates a symmetric RelationshipCertificate on the requester's side,
        persists it, and broadcasts contact_added to connected browsers.
        """
        try:
            data = json.loads(body)
        except Exception:
            return "400 Bad Request", '{"error":"invalid JSON"}'

        if data.get("@type") != "InviteAcceptance":
            return "400 Bad Request", '{"error":"wrong @type"}'

        invitation_id = data.get("invitation_id", "")
        acceptor_cert = data.get("certificate")
        acceptor_did = data.get("from_did", "")
        acceptor_pub_hex = data.get("from_pub_hex", "")

        if not invitation_id or not acceptor_pub_hex:
            return "400 Bad Request", '{"error":"missing fields"}'

        from .federation import RelationshipCertificate, Capability
        from .didkey import pub_key_to_did

        # Verify from_pub_hex is valid and matches from_did if provided
        try:
            expected_did = pub_key_to_did(bytes.fromhex(acceptor_pub_hex))
        except Exception:
            return "400 Bad Request", '{"error":"invalid from_pub_hex"}'
        if acceptor_did and expected_did != acceptor_did:
            return "400 Bad Request", '{"error":"from_did/from_pub_hex mismatch"}'

        # R11: trust revocation check for acceptor
        if self._store and acceptor_did:
            if self._store.is_subject_revoked("peer_did", acceptor_did):
                self._store.save_security_event(
                    "trust_revoked_invite_accept_rejected", "warning",
                    details=f"peer_did={acceptor_did}",
                )
                return "403 Forbidden", '{"error":"trust_revoked_peer"}'

        # Verify a pending invite actually exists for this invitation_id
        if not self._store:
            return "503 Service Unavailable", '{"error":"no store"}'
        if not self._store.get_pending_invite(invitation_id):
            return "400 Bad Request", '{"error":"no matching pending invite"}'

        my_pub_hex = self.agent.identity_pub_bytes.hex()

        if acceptor_cert:
            from .handshake import _ed25519_verify
            try:
                parsed_cert = RelationshipCertificate.from_dict(acceptor_cert)
            except Exception:
                return "400 Bad Request", '{"error":"invalid certificate"}'
            if parsed_cert.issuer != acceptor_pub_hex:
                return "400 Bad Request", '{"error":"certificate issuer mismatch"}'
            if parsed_cert.subject != my_pub_hex:
                return "400 Bad Request", '{"error":"certificate subject mismatch"}'
            if not parsed_cert.verify(_ed25519_verify):
                return "400 Bad Request", '{"error":"invalid certificate signature"}'

        # Nonce replay check
        if invitation_id and self._store:
            if self._store.has_seen_invite_nonce(str(invitation_id), ttl_seconds=86400):
                return "409 Conflict", '{"error":"replay_detected"}'
            self._store.record_invite_nonce(str(invitation_id))
            self._store.prune_invite_nonces(time.time() - 86400)

        # Build requester's symmetric cert (Alice is issuer, Bob is subject)
        cert = RelationshipCertificate(
            issuer=my_pub_hex,
            subject=acceptor_pub_hex,
            capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        )
        cert.sign(self.agent.identity_key)

        if self._store:
            self._store.save_relationship(cert.to_dict(), peer_did=acceptor_did)
            asyncio.create_task(self._sync_cert_to_pod(cert.to_dict()))
            if invitation_id:
                self._store.mark_invite_status(invitation_id, "accepted")

        # Register acceptor's gateway URL so relay routing works immediately
        acceptor_gw_http = data.get("from_gateway_http_url", "")
        if acceptor_gw_http and acceptor_did:
            self._record_peer_gateway(acceptor_did, acceptor_gw_http)

        pod_client = self._pod_client()
        if pod_client:
            from .federation import RelationshipCertificate as RC
            self.dm_clients[cert.certificate_id] = (cert, pod_client)

        await self.broadcast({
            "type": "contact_added",
            "certificate": cert.to_dict(),
            "peer_did": acceptor_did,
            "invitation_id": invitation_id,
        })

        return "200 OK", '{"status":"ok"}'

    async def _handle_relay_post(self, body: bytes, client_ip: str = "unknown") -> tuple[str, str]:
        """Handle POST /relay — verify signature, deliver to connected client."""
        try:
            data = json.loads(body)
        except Exception:
            return "400 Bad Request", '{"error":"invalid JSON"}'

        # R9: federation quarantine mode
        if os.environ.get("PROXION_FEDERATION_QUARANTINE") == "1" and self._store:
            import uuid as _uuid_q, hashlib as _hl_q
            _q_sha256 = _hl_q.sha256(body).hexdigest()
            if self._store.has_duplicate_quarantine_payload("relay", _q_sha256):
                return "409 Conflict", '{"error":"duplicate_quarantine_payload"}'
            try:
                self._store.add_quarantine_item(
                    id=str(_uuid_q.uuid4()),
                    item_type="relay",
                    source_identity=data.get("from_webid"),
                    payload_json=body.decode("utf-8", errors="replace"),
                    reason="federation_quarantine_mode",
                    created_at=time.time(),
                    payload_sha256=_q_sha256,
                    source_ip=client_ip,
                )
            except Exception:
                pass
            return "202 Accepted", '{"status":"quarantined"}'

        # R11: trust revocation check for relay sender
        _relay_from = (data.get("from_webid") or "") if isinstance(data, dict) else ""
        _relay_gw = (data.get("origin_gateway_url") or "") if isinstance(data, dict) else ""
        if self._store and _relay_from:
            if self._store.is_subject_revoked("peer_did", _relay_from):
                if self._store:
                    self._store.save_security_event(
                        "trust_revoked_relay_rejected", "warning",
                        details=f"peer_did={_relay_from}",
                    )
                return "403 Forbidden", '{"error":"trust_revoked_peer"}'
        if self._store and _relay_gw:
            if self._store.is_subject_revoked("gateway_url", _relay_gw):
                if self._store:
                    self._store.save_security_event(
                        "trust_revoked_relay_rejected", "warning",
                        details=f"gateway_url={_relay_gw}",
                    )
                return "403 Forbidden", '{"error":"trust_revoked_gateway"}'

        now = time.time()
        bucket = self._relay_rate_limiter.setdefault(client_ip, deque())
        while bucket and (now - bucket[0]) >= 60:
            bucket.popleft()
        if len(bucket) >= 60:
            return "429 Too Many Requests", '{"error":"rate limit exceeded"}'
        bucket.append(now)

        # Reject unknown top-level keys
        _ALLOWED_RELAY_KEYS = frozenset({
            "from_webid", "from_display_name", "to_webid", "message_id", "content", "timestamp",
            "signature", "relay_nonce", "display_name", "origin_gateway_url",
            "sender_webid", "message_scope",
            # E2E keys
            "e2e", "nonce", "msg_num", "key_header", "ratchet_pub", "pn", "x25519_pub",
            # chain-integrity fields
            "seq_num", "prev_hash",
            # voice signaling
            "content_type", "signal_type", "signal_data", "session_id",
            # sealed relay
            "sealed_payload",
            # file relay (≤96 KB base64; enforced below)
            "file",
            # room federation (R29)
            "room_id", "room_name", "status", "status_message", "updated_at", "from_display_name", "cert_id", "thread_id", "source", "local",
            # voice channel federation (R33)
            "channel_id", "peer_gateway_url", "peer_webid", "target_webid", "action",
            # chunked file transfer (R39)
            "file_id", "filename", "mime_type", "size_bytes", "total_chunks", "seq", "data", "reason",
            # room moderation federation (R-C1)
            "webid", "expires_at",
        })
        _unknown = set(data.keys()) - _ALLOWED_RELAY_KEYS
        if _unknown:
            return "400 Bad Request", '{"error":"unknown_relay_fields"}'

        # File relay: enforce per-field size limit (96 KB base64 ≈ 72 KB binary)
        if "file" in data:
            _file_field = data["file"]
            _b64_len = len(_file_field.get("data_b64", "")) if isinstance(_file_field, dict) else 0
            if _b64_len > 131072:  # 128 KiB base64
                return "413 Content Too Large", '{"error":"file_too_large_for_relay"}'

        # ── Voice signal relay — ephemeral, delivered immediately, not queued ──
        if data.get("content_type") == "voice_signal":
            return await self._handle_voice_signal_relay(data)

        # ── Room message relay — deliver to local members ──
        if data.get("content_type") == "room_message":
            return await self._handle_room_relay(data)

        # ── Room reaction relay — deliver to local members ──
        if data.get("content_type") == "room_reaction":
            return await self._handle_room_reaction_relay(data)

        # ── Room edit relay — update store and deliver to local members ──
        if data.get("content_type") == "room_edit":
            return await self._handle_room_edit_relay(data)

        # ── Room delete relay — remove from store and deliver to local members ──
        if data.get("content_type") == "room_delete":
            return await self._handle_room_delete_relay(data)

        # ── Room moderation relay — ban/mute propagation (R-C1) ──
        if data.get("content_type") == "room_moderation":
            return await self._handle_room_moderation_relay(data)

        # ── Voice channel relay (cross-gateway group voice) ──
        if data.get("content_type") == "voice_channel_join":
            return await self._handle_voice_channel_join_relay(data)
        if data.get("content_type") == "voice_channel_leave":
            return await self._handle_voice_channel_leave_relay(data)
        if data.get("content_type") == "voice_channel_peer_joined":
            return await self._handle_voice_channel_peer_joined_relay(data)
        if data.get("content_type") == "voice_channel_peer_present":
            return await self._handle_voice_channel_peer_present_relay(data)

        # ── Chunked file transfer relay (cross-gateway) ──
        if data.get("content_type") in (
            "file_offer", "file_accept", "file_reject", "file_chunk", "file_complete"
        ):
            return await self._handle_file_relay(data)

        # ── Presence relay — update cache and broadcast ──
        if data.get("content_type") == "presence":
            return await self._handle_presence_relay(data)

        # ── Typing relay — deliver to local DM peer ──
        if data.get("content_type") == "typing":
            return await self._handle_typing_relay(data)

        # ── Sealed DM relay — decrypt before processing ──
        if data.get("content_type") == "sealed_dm":
            sealed = data.get("sealed_payload", "")
            if not sealed or not self._own_x25519_priv:
                return "400 Bad Request", '{"error":"sealed_relay_not_supported"}'
            try:
                from .sealed_relay import unseal_relay_payload as _unseal
                data = _unseal(sealed, self._own_x25519_priv)
            except Exception as _ue:
                return "400 Bad Request", '{"error":"sealed_relay_decrypt_failed"}'

        from_webid  = data.get("from_webid", "")
        to_webid    = data.get("to_webid", "")
        message_id  = data.get("message_id", "")
        content     = data.get("content", "")
        timestamp   = data.get("timestamp", "")
        relay_nonce = data.get("relay_nonce", "")
        display_name = data.get("display_name", "")
        signature   = data.get("signature", "")
        origin_gateway = data.get("origin_gateway_url", "")

        # Strict payload bounds
        if message_id and len(message_id) > 128:
            return "400 Bad Request", '{"error":"message_id_too_long"}'
        if content and len(content.encode("utf-8", errors="replace")) > 16384:
            return "400 Bad Request", '{"error":"content_too_large"}'
        if display_name and len(display_name) > 64:
            display_name = display_name[:64]

        # Identity consistency: sender_webid must match from_webid (no delegation)
        _sender_webid = data.get("sender_webid", "")
        if _sender_webid and _sender_webid != from_webid:
            return "401 Unauthorized", '{"error":"sender_identity_mismatch"}'

        # Field character and format policy
        import re as _re
        _MSG_ID_RE = _re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
        _NONCE_RE = _re.compile(r"^[A-Fa-f0-9]{8,64}$")
        if message_id and not _MSG_ID_RE.match(message_id):
            return "400 Bad Request", '{"error":"invalid_relay_fields"}'
        if relay_nonce and not _NONCE_RE.match(relay_nonce):
            return "400 Bad Request", '{"error":"invalid_relay_fields"}'
        if timestamp:
            try:
                _ts_parsed = datetime.fromisoformat(timestamp)
                if _ts_parsed.tzinfo is None:
                    return "400 Bad Request", '{"error":"invalid_relay_fields"}'
            except (ValueError, TypeError):
                return "400 Bad Request", '{"error":"invalid_relay_fields"}'

        # R9.1.1 — deduplicate: drop if recently seen.
        # Use SQLite when available; in-memory deque as fallback.
        _relay_dedup_key = f"{from_webid}:{message_id}" if from_webid else message_id
        if message_id:
            if self._store and self._store.has_seen_relay_id(_relay_dedup_key, ttl_seconds=600):
                return "200 OK", '{"status":"duplicate"}'
            if _relay_dedup_key in self._seen_relay_ids:
                return "200 OK", '{"status":"duplicate"}'
            if self._store:
                self._store.record_relay_id(_relay_dedup_key)
                self._store.prune_seen_relay_ids(time.time() - 600)
            self._seen_relay_ids.append(_relay_dedup_key)

        # R10.4.1 — relay_nonce dedup (replay attack prevention).
        # Partition by sender: hash(from_webid + ":" + nonce) so one sender's
        # high-volume traffic cannot evict nonces belonging to other senders.
        if relay_nonce:
            import hashlib as _hashlib
            _nonce_key = _hashlib.sha256(
                f"{from_webid}:{relay_nonce}".encode()
            ).hexdigest()
            # Check SQLite first (durable across restarts), then in-memory fallback
            if self._store and self._store.seen_relay_nonce(_nonce_key, ttl_seconds=600):
                return "200 OK", '{"status":"duplicate"}'
            if _nonce_key in self._seen_relay_nonces:
                return "200 OK", '{"status":"duplicate"}'
            if self._store:
                self._store.record_relay_nonce(_nonce_key)
                # Prune entries older than 10 minutes
                self._store.prune_relay_nonces(time.time() - 600)
            self._seen_relay_nonces.append(_nonce_key)

        if not all([from_webid, to_webid, message_id, content, timestamp, signature]):
            return "400 Bad Request", '{"error":"missing fields"}'

        # Verify Ed25519 signature (include message_scope if present in payload)
        _message_scope = data.get("message_scope", "")
        from .relay import verify_relay_message
        if not verify_relay_message(
            from_webid, to_webid, message_id, content, timestamp, signature,
            relay_nonce, message_scope=_message_scope,
        ):
            return "400 Bad Request", '{"error":"invalid signature"}'
        try:
            from .didkey import did_to_pub_key, pub_key_to_did
            expected = pub_key_to_did(did_to_pub_key(from_webid))
            if expected != from_webid:
                return "401 Unauthorized", '{"error":"from_webid mismatch"}'
        except Exception:
            return "401 Unauthorized", '{"error":"from_webid mismatch"}'

        # R12.1.1 — reject relay from revoked senders
        if from_webid in self._revoked_dids:
            return "403 Forbidden", '{"error":"sender revoked"}'

        # Record the sender's gateway URL — validate first to prevent SSRF pinning
        if origin_gateway and from_webid:
            if _is_safe_gateway_url(origin_gateway):
                self._record_peer_gateway(from_webid, origin_gateway)
            else:
                logger.debug("relay: rejected unsafe origin_gateway_url from %s: %s",
                             from_webid, origin_gateway)

        # Resolve thread_id: prefer cert_id so the browser routes to the right thread
        cert_id = None
        if self._store:
            cert_dict = self._store.get_relationship_by_did(from_webid)
            if cert_dict:
                cert_id = cert_dict.get("certificate_id")
            # Cache sender's X25519 pub key for future E2E bootstrap
            if "x25519_pub" in data:
                self._store.save_x25519_pub(from_webid, data["x25519_pub"])

        _E2E_KEYS = ("e2e", "nonce", "msg_num", "key_header", "ratchet_pub", "pn", "x25519_pub", "file")
        # Deliver to all connected sockets of the target identity
        target_sockets = self._sockets_for(to_webid)
        if target_sockets:
            event = {
                "type": "message",
                "source": "relay",
                "from_webid": from_webid,
                "from_display_name": display_name or from_webid[:12],
                "content": content,
                "timestamp": timestamp,
                "message_id": message_id,
                "thread_id": cert_id or from_webid,
                "cert_id": cert_id,
                "local": True,
            }
            for _k in _E2E_KEYS:
                if _k in data:
                    event[_k] = data[_k]
            payload = json.dumps(event)
            delivered_any = False
            for ws in target_sockets:
                try:
                    await ws.send(payload)
                    delivered_any = True
                except Exception:
                    pass
            if delivered_any:
                if self._store:
                    # Message-ID collision guard
                    _existing = self._store.get_message_identity_binding(message_id)
                    if _existing and (
                        _existing["from_webid"] != from_webid or
                        _existing["thread_id"] != (cert_id or from_webid)
                    ):
                        self._store.save_security_event(
                            "message_id_conflict", "warning",
                            details=f"msg_id={message_id} existing_from={_existing['from_webid']} new_from={from_webid}",
                        )
                        try:
                            self._store.append_relay_delivery_event(message_id, from_webid, "rejected")
                        except Exception:
                            pass
                        return "409 Conflict", '{"error":"message_id_conflict"}'
                    self._store.save_message(
                        message_id, cert_id or from_webid, "relay",
                        from_webid, display_name or None, content, timestamp,
                        seq_num=int(data.get("seq_num") or 0),
                        prev_hash=str(data.get("prev_hash") or ""),
                    )
                    try:
                        self._store.append_relay_delivery_event(message_id, from_webid, "delivered")
                    except Exception:
                        pass
                return "200 OK", '{"status":"delivered"}'

        # WebPush for offline DM relay target
        _vpk_dm  = getattr(self, "_vapid_private_pem", None)
        _vsub_dm = getattr(self, "_vapid_subject", None)
        if self._store and _vpk_dm and _vsub_dm and to_webid:
            from .webpush import send_web_push as _swp
            _dm_subs = self._store.get_push_subscriptions(to_webid)
            for _sub in (_dm_subs or []):
                try:
                    _swp(
                        subscription={
                            "endpoint": _sub["endpoint"],
                            "keys": {
                                "p256dh": _sub["p256dh_b64"],
                                "auth":   _sub["auth_b64"],
                            },
                        },
                        payload={
                            "type": "message",
                            "thread_id": cert_id or from_webid,
                            "display_name": display_name,
                        },
                        vapid_private_pem=_vpk_dm,
                        vapid_subject=_vsub_dm,
                    )
                except Exception:
                    pass

        # Target not connected — queue + store for when they reconnect.
        # Two-level budget: max 500 unique recipients AND max 5000 total messages
        # globally. Both limits bound worst-case memory even when messages are large.
        if to_webid not in self._relay_queue and len(self._relay_queue) >= 500:
            return "507 Insufficient Storage", '{"error":"relay queue full"}'
        _total_queued = sum(len(q) for q in self._relay_queue.values())
        if _total_queued >= 5000:
            return "507 Insufficient Storage", '{"error":"relay queue full"}'
        queue = self._relay_queue.setdefault(to_webid, [])
        if len(queue) >= 100:
            queue.pop(0)
        queue_entry = {
            "from_webid": from_webid,
            "to_webid": to_webid,
            "message_id": message_id,
            "content": content,
            "timestamp": timestamp,
            "display_name": display_name,
            "cert_id": cert_id,
        }
        for _k in _E2E_KEYS:
            if _k in data:
                queue_entry[_k] = data[_k]
        queue.append(queue_entry)
        if self._store:
            self._store.save_message(
                message_id, cert_id or to_webid, "relay",
                from_webid, display_name or None, content, timestamp,
                seq_num=int(data.get("seq_num") or 0),
                prev_hash=str(data.get("prev_hash") or ""),
            )
            try:
                self._store.append_relay_delivery_event(message_id, from_webid, "accepted")
            except Exception:
                pass
        return "202 Accepted", '{"status":"stored"}'

    # _handle_invite_post / _handle_invite_accept_post: moved to
    # _gateway_http.py (HttpEndpointsMixin).
