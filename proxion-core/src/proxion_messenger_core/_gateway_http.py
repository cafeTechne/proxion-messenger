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
