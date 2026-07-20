"""HTTP endpoint handlers (B2, R42 slice 1).

Cohesive POST-endpoint logic extracted from gateway.py's WS-centric body into a
mixin, matching the established _gateway_*.py pattern. No behavior change — the
methods are unchanged and still invoked as self._handle_*_post(...) from
_serve_http. Each method imports its own federation/crypto deps locally, so this
file needs only json/os/asyncio/logging at module scope.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

from .relay import _validate_relay_target as _is_safe_gateway_url
from ._gateway_mailbox import relay_node_enabled, relay_fallback_url

logger = logging.getLogger(__name__)


async def _read_http_body(reader, n: int, timeout: float = 10.0) -> bytes:
    """Read exactly *n* bytes of request body (or until EOF/timeout).

    asyncio ``reader.read(n)`` returns as soon as ANY data is available, so a
    body spanning multiple TCP segments (backups, relay payloads over a real
    network — anything past one segment) gets silently truncated. Loop until we
    have *n* bytes or the peer stops sending.
    """
    if n <= 0:
        return b""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = await asyncio.wait_for(reader.read(n - len(buf)), timeout=timeout)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


_INVITE_DOWNLOAD_URL = "https://github.com/cafeTechne/proxion-messenger"


class HttpEndpointsMixin:
    def _render_invite_landing(self, from_addr: str) -> bytes:
        """A4.1: the /i/<token> landing page — branch app-installed vs download,
        carrying the inviter (`from_addr`) through every path. Self-contained
        (no external assets); from_addr is the gateway's own Proxion address so
        it's server-controlled, but we still HTML-escape / URL-encode it."""
        import html as _html
        import urllib.parse as _up
        addr_txt = _html.escape(from_addr) if from_addr else ""
        addr_q = _up.quote(from_addr, safe="") if from_addr else ""
        web_href = "/?from=" + addr_q if addr_q else "/"
        app_href = "proxion://invite?from=" + addr_q if addr_q else "proxion://invite"
        from_block = (
            f'<p class="from">from <code>{addr_txt}</code></p>' if addr_txt else ""
        )
        html_doc = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>You're invited to Proxion</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    background:#1a1a2e; color:#e1e1e1; font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; padding:24px; }}
  .card {{ background:#16213e; border-radius:12px; padding:32px 28px; max-width:380px; width:100%; text-align:center;
    box-shadow:0 8px 32px rgba(0,0,0,.4); }}
  h1 {{ margin:0 0 4px; font-size:1.6em; letter-spacing:.02em; }}
  .tag {{ color:#94a3b8; margin:0 0 16px; font-size:.95em; }}
  .from {{ color:#94a3b8; font-size:.82em; word-break:break-all; margin:0 0 20px; }}
  .from code {{ background:#0f172a; padding:2px 6px; border-radius:4px; }}
  .btn {{ display:block; text-decoration:none; padding:11px 16px; border-radius:6px; margin:8px 0;
    font-weight:600; font-size:.95em; }}
  .btn.primary {{ background:#e94560; color:#fff; }}
  .btn.secondary {{ background:#0f3460; color:#e1e1e1; }}
  .btn.ghost {{ background:transparent; color:#94a3b8; border:1px solid #334155; font-weight:500; }}
  .hint {{ color:#64748b; font-size:.8em; margin:16px 0 0; }}
</style></head><body>
  <div class="card">
    <h1>Proxion</h1>
    <p class="tag">You've been invited to a private, sovereign chat.</p>
    {from_block}
    <a class="btn primary" href="{web_href}">Open in browser</a>
    <a class="btn secondary" href="{app_href}">Open the desktop app</a>
    <a class="btn ghost" href="{_INVITE_DOWNLOAD_URL}" target="_blank" rel="noopener">Get Proxion for desktop</a>
    <p class="hint">No account or install needed - "Open in browser" just works.</p>
  </div>
</body></html>"""
        return html_doc.encode("utf-8")

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
            from .didkey import pub_key_to_did
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

        # Scope contact_added to the INVITER (the local user whose invite was
        # accepted) rather than broadcasting the new cert + peer E2E key to every
        # session on the gateway. The pending invite's issuer.did is that user.
        _contact_event = {
            "type": "contact_added",
            "certificate": cert.to_dict(),
            "peer_did": acceptor_did,
            "invitation_id": invitation_id,
            # Acceptor's browser E2E key (from the acceptance) so our client caches the
            # right key for the first outgoing DM, overriding the gateway key cached at
            # discover time. (The sealed-relay layer still uses the gateway store key.)
            "x25519_pub": data.get("from_e2e_key") or None,
        }
        _inviter_did = ""
        if invitation_id and self._store:
            _pend = self._store.get_pending_invite(invitation_id)
            if _pend:
                _inviter_did = (_pend.get("issuer") or {}).get("did") or ""
        # Only narrow when the inviter is confidently matched to live sockets;
        # otherwise fall back to broadcast so the "new contact" event is never
        # silently dropped (e.g. inviter offline, or an identity/delegation
        # mismatch we can't resolve here). Broadcast is single-user-safe.
        _targets = self._sockets_for(_inviter_did) if _inviter_did else []
        if _targets:
            _payload = json.dumps(_contact_event)
            for _ws in _targets:
                try:
                    await _ws.send(_payload)
                except Exception:
                    pass
        else:
            await self.broadcast(_contact_event)
        # Persist the acceptor's browser E2E key (separate from the gateway store key)
        # so it survives a contacts-list refresh and isn't clobbered by the store key.
        _acc_e2e = data.get("from_e2e_key")
        if _acc_e2e and acceptor_did and self._store:
            self._store.save_e2e_key(acceptor_did, _acc_e2e)

        return "200 OK", '{"status":"ok"}'

    async def _handle_devices_post(self, body: bytes) -> tuple[str, str]:
        """POST /devices — a RELATED peer gateway fetches this user's device E2E
        roster for cross-gateway multi-device DM fanout.

        Gated so strangers cannot enumerate devices: the request is signed with
        the requester's did:key Ed25519 over f"{requester}|{target}|{ts}|{nonce}"
        AND the requester must already hold a (non-revoked) relationship with
        this gateway's user. Returns [{device_id, pub_b64u}] — public keys only.
        Replay of a fresh request is harmless (read-only, same response), so
        freshness is a timestamp window rather than a nonce ledger.
        """
        try:
            data = json.loads(body)
        except Exception:
            return "400 Bad Request", '{"error":"invalid JSON"}'
        requester = data.get("requester_did", "")
        target = data.get("target_did", "")
        ts = data.get("ts", "")
        nonce = data.get("nonce", "")
        sig_b64 = data.get("signature", "")
        if not all([requester, target, ts, nonce, sig_b64]):
            return "400 Bad Request", '{"error":"missing fields"}'
        # Freshness: +-5 min.
        try:
            _t = datetime.fromisoformat(ts)
            if _t.tzinfo is None:
                return "400 Bad Request", '{"error":"invalid_ts"}'
            if abs((datetime.now(timezone.utc) - _t).total_seconds()) > 300:
                return "400 Bad Request", '{"error":"stale_request"}'
        except (ValueError, TypeError):
            return "400 Bad Request", '{"error":"invalid_ts"}'
        # Only answer for OUR user (no directory service).
        from .didkey import pub_key_to_did, did_to_pub_key
        own_did = pub_key_to_did(self.agent.identity_pub_bytes)
        if target != own_did:
            return "404 Not Found", '{"error":"unknown_target"}'
        # Signature by the requester's did:key.
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            pub = Ed25519PublicKey.from_public_bytes(did_to_pub_key(requester))
            import base64 as _b64
            raw_sig = _b64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
            pub.verify(raw_sig, f"{requester}|{target}|{ts}|{nonce}".encode())
        except Exception:
            return "401 Unauthorized", '{"error":"invalid signature"}'
        # Relationship gate: only contacts may fetch the roster.
        if not self._store or requester in getattr(self, "_revoked_dids", set()):
            return "403 Forbidden", '{"error":"not_related"}'
        if not self._store.get_relationship_by_did(requester):
            return "403 Forbidden", '{"error":"not_related"}'
        # Roster: prefer the account that owns the relationship with the
        # requester; fall back to the one-gateway-per-user union (older rows
        # were saved without an owner) — mirrors the _sockets_for fallback.
        owner = self._store.get_relationship_owner(requester)
        devices = self._store.list_device_e2e_keys(owner) if owner else []
        if not devices:
            devices = [{"device_id": d["device_id"], "pub_b64u": d["pub_b64u"]}
                       for d in self._store.list_all_device_e2e_keys()]
        return "200 OK", json.dumps({"devices": devices[:16]})

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
        # Blocked sender: silently ACCEPT (200) but do not deliver anything —
        # DMs, room messages, reactions, voice signals. Returning 200 (not 403)
        # avoids revealing to the sender that they've been blocked. Previously the
        # relay receive path never checked the blocklist, so a blocked user's
        # messages still reached the recipient (block only worked on send + pod).
        if _relay_from and self.blocklist.is_blocked(_relay_from):
            logger.info("Dropped relay content from blocked sender %s", _relay_from[:24])
            return "200 OK", '{"status":"received"}'

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
            # reaction relay (room_reaction had emoji un-whitelisted -> 400 in
            # production; masked because tests call the handler directly) + dm_reaction
            "emoji",
            # dm_edit relay
            "new_content",
            # dm_disappear_timer relay
            "ms",
            # ephemeral relay envelope signature (R55): full-payload Ed25519 sig by
            # the relaying gateway, verified against relay_sig_did over relay_ts.
            "relay_sig_did", "relay_ts",
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

        # ── R55: signed-envelope enforcement for ephemeral content_type relays ──
        # Runs BEFORE any content_type dispatch. Require a valid full-payload
        # envelope signature (binds op-specific fields like emoji/action/ms/
        # new_content, not just the fixed set) and bind from_webid to the signing
        # gateway so a peer gateway can't forge another's from_webid. Unknown/
        # invalid/forged → 200 no-reveal (don't act).
        #   Self-signed (from_webid IS the signing gateway → from_webid == signer):
        #     DM secondary ops + voice_signal.
        #   Member-signed (from_webid is a MEMBER did → TOFU continuity binding):
        #     room ops + file transfer + voice channel join/leave.
        #   Channel-bound (no from_webid; bind the SIGNER to a channel participant):
        #     voice_channel_peer_joined/peer_present — the signing gateway must
        #     participate in the channel, else it could inject a fake peer.
        _SELF_SIGNED_TYPES = (
            "dm_reaction", "dm_edit", "dm_delete", "dm_pin", "dm_disappear_timer",
            "voice_signal")
        _MEMBER_SIGNED_TYPES = (
            "room_message", "room_reaction", "room_edit", "room_delete", "room_moderation",
            "room_emoji",
            "file_offer", "file_accept", "file_reject", "file_chunk", "file_complete",
            "voice_channel_join", "voice_channel_leave")
        _CHANNEL_SIGNED_TYPES = (
            "voice_channel_peer_joined", "voice_channel_peer_present")
        _ct = data.get("content_type")
        if _ct in _SELF_SIGNED_TYPES or _ct in _MEMBER_SIGNED_TYPES or _ct in _CHANNEL_SIGNED_TYPES:
            from .relay import verify_relay_envelope
            _from = data.get("from_webid", "")
            _signer = data.get("relay_sig_did", "")
            if _ct in _SELF_SIGNED_TYPES:
                _bound = (_signer == _from)
            elif _ct in _MEMBER_SIGNED_TYPES:
                _bound = self._relay_sender_gateway_ok(_from, _signer)
            else:  # _CHANNEL_SIGNED_TYPES — bind the signer to a channel participant
                _bound = self._voice_channel_gateway_ok(data.get("channel_id", ""), _signer)
            if not _bound or not verify_relay_envelope(data):
                return "200 OK", '{"status":"received"}'
            # Replay guard: dedup on the signed nonce (partitioned by signer).
            _env_nonce = data.get("relay_nonce", "")
            if _env_nonce:
                import hashlib as _h_env
                _ek = _h_env.sha256(f"{_signer}:{_env_nonce}".encode()).hexdigest()
                if self._store and self._store.seen_relay_nonce(_ek, ttl_seconds=600):
                    return "200 OK", '{"status":"duplicate"}'
                if _ek in self._seen_relay_nonces:
                    return "200 OK", '{"status":"duplicate"}'
                if self._store:
                    self._store.record_relay_nonce(_ek)
                self._seen_relay_nonces.append(_ek)

        # ── Voice signal relay — ephemeral, delivered immediately, not queued ──
        if data.get("content_type") == "voice_signal":
            return await self._handle_voice_signal_relay(data)

        # ── Room message relay — deliver to local members ──
        if data.get("content_type") == "room_message":
            return await self._handle_room_relay(data)

        # ── Room reaction relay — deliver to local members ──
        if data.get("content_type") == "room_reaction":
            return await self._handle_room_reaction_relay(data)

        # ── DM reaction relay — deliver reaction_added/removed to a local peer ──
        if data.get("content_type") == "dm_reaction":
            return await self._handle_dm_reaction_relay(data)

        # ── DM edit relay — deliver message_edited to a local peer ──
        if data.get("content_type") == "dm_edit":
            return await self._handle_dm_edit_relay(data)

        # ── DM delete relay — deliver message_deleted to a local peer ──
        if data.get("content_type") == "dm_delete":
            return await self._handle_dm_delete_relay(data)

        # ── DM pin relay — deliver message_pinned/unpinned to a local peer ──
        if data.get("content_type") == "dm_pin":
            return await self._handle_dm_pin_relay(data)

        # ── DM disappear-timer relay — mutual disappearing messages ──
        if data.get("content_type") == "dm_disappear_timer":
            return await self._handle_dm_disappear_relay(data)

        # ── Room edit relay — update store and deliver to local members ──
        if data.get("content_type") == "room_edit":
            return await self._handle_room_edit_relay(data)

        # ── Room delete relay — remove from store and deliver to local members ──
        if data.get("content_type") == "room_delete":
            return await self._handle_room_delete_relay(data)

        # ── Room moderation relay — ban/mute propagation (R-C1) ──
        if data.get("content_type") == "room_moderation":
            return await self._handle_room_moderation_relay(data)
        if data.get("content_type") == "room_emoji":
            return await self._handle_room_emoji_relay(data)

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

        # ── Multi-device DM fanout relay — per-device E2E envelope from a peer
        # gateway. Verified + deduped here (it bypasses the plain-DM path), then
        # emitted as the same dm_fanout event local fanout uses; each device
        # picks the envelope addressed to it.
        if data.get("content_type") == "dm_fanout":
            return await self._handle_dm_fanout_relay(data)

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
            # Re-dispatch sealed content types: a sealed dm_fanout envelope must
            # route to the fanout handler, not fall through to the plain-DM path.
            if isinstance(data, dict) and data.get("content_type") == "dm_fanout":
                return await self._handle_dm_fanout_relay(data)

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
            # Cache sender's BROWSER X25519 pub for E2E bootstrap — into the
            # e2e_key store, NOT save_x25519_pub. save_x25519_pub is the GATEWAY
            # seal key used by _resolve_peer_x25519_pub for sealed-sender relay;
            # overwriting it with the peer's browser key made every reply seal to
            # the wrong key (recipient gateway can't unseal -> 400 -> reply lost).
            # Discovery already stored the peer's gateway seal key; keep it.
            if "x25519_pub" in data:
                self._store.save_e2e_key(from_webid, data["x25519_pub"])

        _E2E_KEYS = ("e2e", "nonce", "msg_num", "key_header", "ratchet_pub", "pn", "x25519_pub", "file")
        # Deliver to all connected sockets of the target identity. _sockets_for now
        # centrally handles the one-gateway-per-user fallback (a relay addressed to
        # this gateway's own identity reaches the local browser, which registered
        # under its own client DID).
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

        # No live socket for the recipient — fall back to offline delivery. Log it
        # (H4): a relay returning 202 while the recipient's browser silently never
        # received the DM (registered under a different DID than to_webid) was the
        # exact blindness behind the cross-gateway delivery bug.
        logger.debug("relay: no live socket for to_webid=%s — offline fallback (push/mailbox)", to_webid)

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

    async def _serve_http(self, web_dir: str, http_port: int):
        """Serve the web UI as static HTTP(S), injecting the WS gateway URL.
        Also handles POST /relay for cross-gateway message delivery.
        """
        import ssl as _ssl
        from pathlib import Path
        web_path = Path(web_dir) if web_dir is not None else None

        ws_url = self._ws_public_url()
        _api_token = os.environ.get("PROXION_API_TOKEN", "")
        _meta_parts = [f'<meta name="x-gateway-url" content="{ws_url}">']
        if self.config.css_default_url:
            _meta_parts.append(f'<meta name="x-css-default-url" content="{self.config.css_default_url}">')
        if _api_token:
            _meta_parts.append(f'<meta name="x-api-token" content="{_api_token}">')
        inject = "".join(_meta_parts).encode()

        ssl_ctx_http = None
        if self.config.ssl_certfile and self.config.ssl_keyfile:
            ssl_ctx_http = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx_http.load_cert_chain(self.config.ssl_certfile, self.config.ssl_keyfile)

        _SEC_HDR = (
            b"X-Content-Type-Options: nosniff\r\n"
            b"Referrer-Policy: no-referrer\r\n"
            b"Content-Security-Policy: default-src 'self'; connect-src 'self' ws: wss:; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data: blob:; media-src 'self' data: blob:; "
            b"object-src 'none'; frame-ancestors 'none'; base-uri 'self'\r\n"
            b"Cross-Origin-Opener-Policy: same-origin\r\n"
            b"Cross-Origin-Resource-Policy: same-origin\r\n"
            b"Permissions-Policy: microphone=(self), camera=(self), geolocation=()\r\n"
        )
        _NO_STORE_HDR = b"Cache-Control: no-store\r\n"

        # Per-IP rate counters for high-risk HTTP endpoints
        # Structure: {(ip, group): [count, window_start]}
        _http_ip_rate: dict = {}
        _HTTP_RATE_LIMITS = {
            "relay":   60,   # /relay: 60/min
            "invite":  20,   # /invite, /invite/accept: 20/min
            "backup":   5,   # /backup, /restore, /import: 5/min
        }

        async def handle(reader, writer):
            try:
                req = await asyncio.wait_for(reader.readline(), timeout=5.0)
                # Collect all request headers
                headers_raw = {}
                content_length = 0
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if line.strip() == b"":
                        break
                    if b":" in line:
                        k, _, v = line.partition(b":")
                        headers_raw[k.strip().lower()] = v.strip()
                        if k.strip().lower() == b"content-length":
                            try:
                                content_length = int(v.strip())
                            except ValueError:
                                pass

                origin_header = headers_raw.get(b"origin", b"")
                parts = req.decode(errors="replace").split()
                method = parts[0] if parts else "GET"
                _raw_target = parts[1] if len(parts) > 1 else "/"
                path = _raw_target.split("?")[0]
                _query_string = _raw_target.split("?", 1)[1] if "?" in _raw_target else ""
                _peer_info = writer.get_extra_info("peername")
                peer_ip = _peer_info[0] if isinstance(_peer_info, tuple) and _peer_info else ""

                def _check_http_rate(ip: str, group: str) -> bool:
                    """Returns True if rate limit exceeded."""
                    if not ip:
                        return False
                    limit = _HTTP_RATE_LIMITS.get(group, 60)
                    now_t = time.monotonic()
                    key = (ip, group)
                    entry = _http_ip_rate.get(key)
                    if entry is None or now_t - entry[1] > 60:
                        _http_ip_rate[key] = [1, now_t]
                        return False
                    entry[0] += 1
                    if entry[0] > limit:
                        return True
                    return False

                _429_BODY = b'{"error":"rate_limit_exceeded","retry_after":60}'
                def _write_429(writer):
                    writer.write(
                        b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\n"
                        b"Retry-After: 60\r\n"
                        b"Content-Length: " + str(len(_429_BODY)).encode() + b"\r\n\r\n" + _429_BODY
                    )

                async def _write_json(writer, code, obj):
                    """Write a JSON HTTP response with an explicit status code.

                    Restores a helper referenced ~15x in cold error/recovery
                    response paths but never defined (latent NameError landmine,
                    predating the B2 extraction). Matches the _write_429 framing.
                    """
                    import http as _http
                    try:
                        _reason = _http.HTTPStatus(code).phrase
                    except ValueError:
                        _reason = ""
                    _wj_body = json.dumps(obj).encode()
                    writer.write(
                        f"HTTP/1.1 {code} {_reason}\r\nContent-Type: application/json\r\n".encode()
                        + b"Content-Length: " + str(len(_wj_body)).encode() + b"\r\n\r\n" + _wj_body
                    )

                # Per-endpoint POST body size limits.
                _ENDPOINT_SIZE_LIMITS = {
                    "/relay":         128 * 1024,        # 128 KiB
                    "/invite":        128 * 1024,        # 128 KiB
                    "/invite/accept": 128 * 1024,        # 128 KiB
                    "/devices":       8 * 1024,          # 8 KiB (signed roster request)
                    "/restore":       5 * 1024 * 1024,   # 5 MiB
                    "/import":        20 * 1024 * 1024,  # 20 MiB
                }
                _POST_MAX = 2 * 1024 * 1024  # 2 MB default for unlisted endpoints
                _IMPORT_MAX = _ENDPOINT_SIZE_LIMITS["/import"]  # cap the /import body read (was undefined)
                if method == "POST" and content_length > 0:
                    _limit = _ENDPOINT_SIZE_LIMITS.get(path, _POST_MAX)
                    if content_length > _limit:
                        _413_body = b'{"error":"payload_too_large"}'
                        writer.write(
                            b"HTTP/1.1 413 Content Too Large\r\nContent-Type: application/json\r\n"
                            b"Content-Length: " + str(len(_413_body)).encode() + b"\r\n\r\n" + _413_body
                        )
                        await writer.drain()
                        return

                # Content-Type enforcement for JSON POST endpoints
                _JSON_POST_PATHS = {"/relay", "/invite", "/invite/accept", "/devices", "/restore", "/import"}
                if method == "POST" and path in _JSON_POST_PATHS:
                    _ct = headers_raw.get(b"content-type", b"").decode("utf-8", errors="replace").lower()
                    _ct_base = _ct.split(";")[0].strip()
                    if _ct_base not in ("application/json", "application/ld+json"):
                        _415_body = b'{"error":"unsupported_media_type"}'
                        writer.write(
                            b"HTTP/1.1 415 Unsupported Media Type\r\nContent-Type: application/json\r\n"
                            b"Content-Length: " + str(len(_415_body)).encode() + b"\r\n\r\n" + _415_body
                        )
                        await writer.drain()
                        return

                # R8: DB integrity guard — block mutating POST endpoints when DB is corrupt
                _MUTATING_POST_PATHS = {"/relay", "/invite", "/invite/accept", "/restore", "/import"}
                if method == "POST" and path in _MUTATING_POST_PATHS:
                    _store_ok = not self._store or getattr(self._store, "_integrity_ok", True)
                    if not _store_ok:
                        await _write_json(writer, 503, {"error": "db_integrity_failed"})
                        await writer.drain()
                        return
                    # R14: drift protection blocks high-risk mutations
                    try:
                        from .security_policy import get_policy as _get_pol_http
                        if _get_pol_http().is_drift_protection_active():
                            await _write_json(writer, 503, {"error": "spec_drift_protection_active"})
                            await writer.drain()
                            return
                    except Exception:
                        pass

                # ── POST /relay — cross-gateway message delivery ──
                if method == "POST" and path == "/relay":
                    if _check_http_rate(peer_ip, "relay"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 65536), 10.0)
                    peer = writer.get_extra_info("peername")
                    client_ip = peer[0] if isinstance(peer, tuple) and peer else "unknown"
                    status, response = await self._handle_relay_post(body, client_ip=client_ip)
                    resp_bytes = response.encode()
                    writer.write(
                        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n".encode()
                        + _SEC_HDR + _NO_STORE_HDR
                        + f"Content-Length: {len(resp_bytes)}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode()
                        + resp_bytes
                    )
                    await writer.drain()
                    return

                # ── POST /devices — related peer gateway fetches the device roster ──
                if method == "POST" and path == "/devices":
                    if _check_http_rate(peer_ip, "devices"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 8192), 10.0)
                    status, response = await self._handle_devices_post(body)
                    resp_bytes = response.encode()
                    writer.write(
                        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n".encode()
                        + _SEC_HDR + _NO_STORE_HDR
                        + f"Content-Length: {len(resp_bytes)}\r\n\r\n".encode()
                        + resp_bytes
                    )
                    await writer.drain()
                    return

                # ── OPTIONS /relay — CORS preflight ──
                if method == "OPTIONS" and path == "/relay":
                    writer.write(
                        b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                        b"Access-Control-Allow-Methods: POST, OPTIONS\r\n"
                        b"Access-Control-Allow-Headers: Content-Type\r\n\r\n"
                    )
                    await writer.drain()
                    return

                # ── GET /.well-known/proxion — discovery endpoint (R8.1) ──
                if method == "GET" and path == "/.well-known/proxion":
                    discovery_data = self._build_discovery_data()
                    resp_bytes = json.dumps(discovery_data).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(resp_bytes)).encode() + b"\r\n\r\n" + resp_bytes
                    )
                    await writer.drain()
                    return

                # ── GET /turn-credentials — coturn HMAC credentials ──
                if method == "GET" and path == "/turn-credentials":
                    from .didkey import pub_key_to_did as _p2d_turn
                    _turn_gw_did = _p2d_turn(self.agent.identity_pub_bytes)
                    _turn_creds = self._make_turn_creds(_turn_gw_did)
                    _turn_body = json.dumps(_turn_creds if _turn_creds else {"urls": []}).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\n"
                        b"Cache-Control: no-store\r\n"
                        b"Content-Length: " + str(len(_turn_body)).encode() + b"\r\n\r\n" + _turn_body
                    )
                    await writer.drain()
                    return

                # ── GET /fingerprint/<did> — R11.2.1: safety number endpoint ──
                if method == "GET" and path.startswith("/fingerprint/"):
                    raw_did = path[len("/fingerprint/"):]
                    try:
                        import urllib.parse as _up
                        did_param = _up.unquote(raw_did)
                        from .didkey import did_to_pub_key as _d2pk, fingerprint_words as _fw
                        from .pop import fingerprint as _fp
                        pub = _d2pk(did_param)
                        fp = _fp(pub)
                        words = _fw(pub)
                        fp_body = json.dumps({
                            "did": did_param,
                            "fingerprint": fp,
                            "safety_words": words,
                        }).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            b"Access-Control-Allow-Origin: *\r\n"
                            b"Content-Length: " + str(len(fp_body)).encode() + b"\r\n\r\n" + fp_body
                        )
                    except Exception:
                        err = b'{"error":"invalid_did"}'
                        writer.write(
                            b"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n"
                            b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err
                        )
                    await writer.drain()
                    return

                # ── GET /health — R11.4.1: liveness check ──
                if method == "GET" and path == "/health":
                    health_body = json.dumps({
                        "status": "ok",
                        "connected_clients": len(self.clients),
                        "pod_available": self._pod_available,
                        "uptime_s": int(time.time() - self._start_time),
                        "turn_configured": bool(self.config.turn_url and self.config.turn_secret),
                        "relay_capable": bool(self.config.public_url),
                        "public_url_set": bool(self.config.public_url),
                    }).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + _SEC_HDR + _NO_STORE_HDR
                        + b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(health_body)).encode() + b"\r\n\r\n" + health_body
                    )
                    await writer.drain()
                    return

                # ── GET /connectivity — NAT/reachability status for setup guide ──
                if method == "GET" and path == "/connectivity":
                    conn_body = json.dumps({
                        "public_url_set": bool(self.config.public_url),
                        "upnp_mapped":    self.config.upnp_mapped,
                        "relay_capable":  bool(self.config.public_url),
                        "local_ip":       getattr(self, "_local_ip", "127.0.0.1"),
                        "local_port":     self.config.http_port or 8080,
                        "turn_configured": bool(self.config.turn_url and self.config.turn_secret),
                        "pod_available":  self._pod_available,
                        "relay_fallback_active": bool(relay_fallback_url()),
                        "relay_node": relay_node_enabled(),
                    }).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + _SEC_HDR + _NO_STORE_HDR
                        + b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(conn_body)).encode() + b"\r\n\r\n" + conn_body
                    )
                    await writer.drain()
                    return

                # ── Mailbox endpoints (relay-node sealed store-and-forward; R38) ──
                if path.startswith("/mailbox/") and method in ("POST", "GET"):
                    if _check_http_rate(peer_ip, "mailbox"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    from urllib.parse import unquote as _mb_unq, parse_qs as _mb_qs
                    _mb_did = _mb_unq(path[len("/mailbox/"):])
                    if method == "POST":
                        _mb_body = b""
                        if content_length > 0:
                            _mb_body = await _read_http_body(reader, min(content_length, 262144 + 4096), 10.0)
                        _mb_status, _mb_resp = await self._handle_mailbox_store(_mb_did, _mb_body)
                    else:
                        _q = _mb_qs(_query_string)
                        _mb_status, _mb_resp = await self._handle_mailbox_drain(
                            _mb_did, _q.get("sig", [""])[0], _q.get("ts", [""])[0],
                            _q.get("nonce", [""])[0])
                    _mb_bytes = _mb_resp.encode()
                    writer.write(
                        f"HTTP/1.1 {_mb_status}\r\n".encode()
                        + b"Content-Type: application/json\r\n" + _SEC_HDR + _NO_STORE_HDR
                        + b"Content-Length: " + str(len(_mb_bytes)).encode() + b"\r\n\r\n" + _mb_bytes
                    )
                    await writer.drain()
                    return

                # ── GET /room-history/{room_id} — fetch room message history ──
                if method == "GET" and path.startswith("/room-history/"):
                    if _check_http_rate(peer_ip, "room_history"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    from urllib.parse import unquote as _rh_unquote, parse_qs as _rh_parse_qs
                    _rh_room_id = _rh_unquote(path[len("/room-history/"):])
                    _rh_qs = _rh_parse_qs(_query_string)
                    _rh_code = _rh_qs.get("code", [""])[0]
                    try:
                        _rh_limit = min(int(_rh_qs.get("limit", ["50"])[0]), 200)
                    except ValueError:
                        _rh_limit = 50
                    _rh_before = _rh_qs.get("before", [""])[0] or None

                    _rh_room = self._local_rooms.get(_rh_room_id)
                    if not _rh_room:
                        writer.write(b"HTTP/1.1 404 Not Found\r\n" + _SEC_HDR +
                                     b"Content-Length: 9\r\n\r\nNot found")
                        await writer.drain()
                        return
                    import hmac as _rh_hmac
                    _rh_stored_code = _rh_room.get("code", "")
                    if not (_rh_stored_code and _rh_code and
                            _rh_hmac.compare_digest(_rh_code.encode(), _rh_stored_code.encode())):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\n" + _SEC_HDR +
                                     b"Content-Length: 9\r\n\r\nForbidden")
                        await writer.drain()
                        return
                    _rh_messages = []
                    if self._store:
                        _rh_messages = self._store.get_messages(
                            _rh_room_id, before_timestamp=_rh_before, limit=_rh_limit
                        )
                    _rh_body = json.dumps({
                        "room_id": _rh_room_id, "messages": _rh_messages,
                    }).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + _SEC_HDR + _NO_STORE_HDR
                        + b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(_rh_body)).encode() + b"\r\n\r\n" + _rh_body
                    )
                    await writer.drain()
                    return

                # ── GET /profile/{did} — contact profile lookup ──
                if method == "GET" and path.startswith("/profile/"):
                    _prof_did = path[len("/profile/"):]
                    if not _prof_did:
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    from .didkey import pub_key_to_did as _p2d, did_to_pub_key as _d2p
                    from .pop import fingerprint as _fp
                    try:
                        _d2p(_prof_did)  # validate DID format
                    except Exception:
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    _own_did = _p2d(self.agent.identity_pub_bytes)
                    _profile: dict = {"did": _prof_did}
                    if self._store:
                        _dn = self._store.get_display_name(_prof_did)
                        if _dn:
                            _profile["display_name"] = _dn
                        _x25 = self._store.get_x25519_pub(_prof_did)
                        if _x25:
                            _profile["x25519_pub"] = _x25
                        _rel = self._store.get_relationship_by_did(_prof_did)
                        if _rel:
                            _profile["gateway_url"] = _rel.get("pod_url") or ""
                    if _prof_did in self._user_presence:
                        _pr = self._user_presence[_prof_did]
                        _profile["status"] = _pr.get("status", "offline")
                        _profile["status_message"] = _pr.get("status_message", "")
                        _profile["last_active_at"] = _pr.get("last_active_at", "")
                    else:
                        _profile["status"] = "offline"
                    _peer_gw = self._peer_gateway_urls.get(_prof_did)
                    if _peer_gw:
                        _profile["gateway_url"] = _peer_gw
                    try:
                        _pk = _d2p(_prof_did)
                        from cryptography.hazmat.primitives import serialization as _ser
                        _raw = _pk.public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
                        _profile["fingerprint"] = _fp(_raw)
                    except Exception:
                        pass
                    if not _profile.get("display_name") and _prof_did == _own_did:
                        _dn_own = os.environ.get("PROXION_DISPLAY_NAME", "")
                        if _dn_own:
                            _profile["display_name"] = _dn_own
                    _prof_bytes = json.dumps(_profile).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + _SEC_HDR + _NO_STORE_HDR
                        + b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(_prof_bytes)).encode() + b"\r\n\r\n" + _prof_bytes
                    )
                    await writer.drain()
                    return

                # ── POST /invite — federation invite endpoint ──
                if method == "POST" and path == "/invite":
                    if _check_http_rate(peer_ip, "invite"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 65536), 10.0)
                    status, response = await self._handle_invite_post(body)
                    resp_bytes = response.encode()
                    writer.write(
                        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(resp_bytes)}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode()
                        + resp_bytes
                    )
                    await writer.drain()
                    return

                # ── OPTIONS /invite — CORS preflight ──
                if method == "OPTIONS" and path == "/invite":
                    writer.write(
                        b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                        b"Access-Control-Allow-Methods: POST, OPTIONS\r\n"
                        b"Access-Control-Allow-Headers: Content-Type\r\n\r\n"
                    )
                    await writer.drain()
                    return

                # ── GET /invite — deep-link (R8.2.1) ──
                if method == "GET" and path == "/invite":
                    from_addr = ""
                    if "?" in parts[1] if len(parts) > 1 else "":
                        qs = parts[1].split("?", 1)[1] if len(parts) > 1 else ""
                        for kv in qs.split("&"):
                            if kv.startswith("from="):
                                import urllib.parse
                                from_addr = urllib.parse.unquote_plus(kv[5:])
                    # URL-encode to prevent XSS via injected JS in the from parameter
                    from_addr_safe = urllib.parse.quote(from_addr, safe="") if from_addr else ""
                    html = (
                        b"<!DOCTYPE html><html><head><title>Proxion Invite</title>"
                        b"<meta http-equiv='refresh' content='0;url=/"
                        + (b"?from=" + from_addr_safe.encode() if from_addr_safe else b"")
                        + b"'></head><body>"
                        b"<script>window.location.href='/"
                        + (b"?from=" + from_addr_safe.encode() if from_addr_safe else b"")
                        + b"';</script></body></html>"
                    )
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                        b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(html)).encode() + b"\r\n\r\n" + html
                    )
                    await writer.drain()
                    return

                # ── GET /i/<token> — R17.2.2: short invite link redirect ──
                if method == "GET" and path.startswith("/i/"):
                    # Rate limit token enumeration: 20 attempts per minute per IP
                    import time as _time
                    _client_ip = writer.get_extra_info("peername", ("unknown", 0))[0]
                    _enum_key = ("invite_enum", _client_ip)
                    _now_enum = _time.monotonic()
                    _enum_entry = self._rate_counters.get(_enum_key)
                    if _enum_entry is None:
                        self._rate_counters[_enum_key] = [1, _now_enum]
                    elif _now_enum - _enum_entry[1] >= 60.0:
                        self._rate_counters[_enum_key] = [1, _now_enum]
                    elif _enum_entry[0] >= 20:
                        writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    else:
                        _enum_entry[0] += 1
                    token = path[3:]
                    if token == self._short_invite_token:
                        # A4.1: serve the invite landing page (branch app/web/download)
                        # instead of bouncing straight into the web app, carrying the
                        # inviter's address through every path.
                        landing = self._render_invite_landing(self._proxion_address())
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                            b"Access-Control-Allow-Origin: *\r\n"
                            b"Content-Length: " + str(len(landing)).encode() + b"\r\n\r\n" + landing
                        )
                    else:
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()
                    return

                # ── POST /relay/receipt — relay read receipt (R10.1.2) ──
                if method == "POST" and path == "/relay/receipt":
                    # Rate limit: 60 receipt POSTs per minute per IP
                    _rr_now = time.time()
                    _rr_bucket = self._relay_rate_limiter.setdefault(peer_ip, deque())
                    while _rr_bucket and (_rr_now - _rr_bucket[0]) >= 60:
                        _rr_bucket.popleft()
                    if len(_rr_bucket) >= 60:
                        err = b'{"error":"rate limit exceeded"}'
                        writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    _rr_bucket.append(_rr_now)
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 4096), 5.0)
                    try:
                        rdata = json.loads(body)
                        from_did = rdata.get("from_did", "")
                        to_did = rdata.get("to_did", "")
                        message_id = rdata.get("message_id", "")
                        thread_id = rdata.get("thread_id", "")
                        timestamp = rdata.get("timestamp", "")
                        signature = rdata.get("signature", "")
                        if not all([from_did, to_did, message_id, timestamp, signature]):
                            err = b'{"error":"missing fields"}'
                            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: " +
                                         str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                        # Reject receipts from revoked senders
                        if from_did in self._revoked_dids:
                            err = b'{"error":"sender revoked"}'
                            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: " +
                                         str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                        # Verify Ed25519 signature over (from_did, to_did, message_id, "", timestamp)
                        from .relay import verify_relay_message
                        if not verify_relay_message(from_did, to_did, message_id, "", timestamp, signature):
                            err = b'{"error":"invalid signature"}'
                            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: " +
                                         str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                        receipt_event = json.dumps({
                            "type": "read_receipt",
                            "from_did": from_did,
                            "to_did": to_did,
                            "message_id": message_id,
                            "thread_id": thread_id,
                            "timestamp": timestamp,
                        })
                        await self._send_to_identity(to_did, receipt_event)
                        ok = b'{"status":"ok"}'
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            b"Access-Control-Allow-Origin: *\r\n"
                            b"Content-Length: " + str(len(ok)).encode() + b"\r\n\r\n" + ok
                        )
                    except Exception:
                        err = b'{"error":"bad request"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                    await writer.drain()
                    return

                # ── OPTIONS /relay/receipt — CORS preflight ──
                if method == "OPTIONS" and path == "/relay/receipt":
                    writer.write(
                        b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                        b"Access-Control-Allow-Methods: POST, OPTIONS\r\n"
                        b"Access-Control-Allow-Headers: Content-Type\r\n\r\n"
                    )
                    await writer.drain()
                    return

                # ── POST /invite/accept — federation acceptance callback ──
                if method == "POST" and path == "/invite/accept":
                    if _check_http_rate(peer_ip, "invite"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    # R8: source-IP invite-accept flood control — max 50 accepts per IP per hour
                    if self._store:
                        import time as _t_iac
                        _iac_count = self._store.increment_invite_source_counter(peer_ip, _t_iac.time())
                        if _iac_count > 50:
                            await _write_json(writer, 429, {"error": "invite_rate_limited"})
                            await writer.drain()
                            return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 65536), 10.0)
                    status, response = await self._handle_invite_accept_post(body)
                    resp_bytes = response.encode()
                    writer.write(
                        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(resp_bytes)}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode()
                        + resp_bytes
                    )
                    await writer.drain()
                    return

                # ── GET /export — data export (R14.1) ──
                if method == "GET" and path == "/export":
                    _peer = writer.get_extra_info("peername")
                    if (_peer and _peer[0] not in ("127.0.0.1", "::1")):
                        _err = b'{"error":"forbidden"}'
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: " +
                                     str(len(_err)).encode() + b"\r\n\r\n" + _err)
                        await writer.drain()
                        return
                    if not self._store:
                        err = b'{"error":"no store"}'
                        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    try:
                        # R8: default to minimized export; require full=1 query param for full payload
                        _qs_exp = parts[1].split("?", 1)[1] if (len(parts) > 1 and "?" in parts[1]) else ""
                        _full_export = "full=1" in _qs_exp
                        export_data = self._store.export_all(minimize=not _full_export)
                        resp_bytes = json.dumps(export_data).encode()
                        # R14.1.3: chunked transfer encoding — avoids holding Content-Length
                        CHUNK = 65536
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            b"Content-Disposition: attachment; filename=proxion-export.json\r\n"
                            b"Transfer-Encoding: chunked\r\n\r\n"
                        )
                        for i in range(0, len(resp_bytes), CHUNK):
                            chunk = resp_bytes[i:i + CHUNK]
                            writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                            await writer.drain()
                        writer.write(b"0\r\n\r\n")
                    except Exception as exc:
                        err = json.dumps({"error": str(exc)}).encode()
                        writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                    await writer.drain()
                    return

                # ── GET /security-events/stream — R12: signed event stream for SIEM ──
                if method == "GET" and path.startswith("/security-events/stream"):
                    _peer_ses = writer.get_extra_info("peername")
                    if _peer_ses and _peer_ses[0] not in ("127.0.0.1", "::1"):
                        await _write_json(writer, 403, {"error": "forbidden"})
                        await writer.drain()
                        return
                    if not self._store:
                        await _write_json(writer, 503, {"error": "no store"})
                        await writer.drain()
                        return
                    try:
                        from .event_stream import get_events_after as _get_ev
                        _qs = path.split("?", 1)[1] if "?" in path else ""
                        _params = dict(p.split("=", 1) for p in _qs.split("&") if "=" in p)
                        _ev_cursor = _params.get("cursor", "")
                        _ev_limit = min(int(_params.get("limit", "100")), 1000)
                        _ev_result = _get_ev(
                            self._store, _ev_cursor, _ev_limit,
                            self.agent.identity_key, self.agent.identity_pub_bytes,
                        )
                        _ev_bytes = json.dumps(_ev_result, default=str).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            b"Content-Length: " + str(len(_ev_bytes)).encode() + b"\r\n\r\n" + _ev_bytes
                        )
                    except Exception as exc:
                        await _write_json(writer, 500, {"error": str(exc)})
                    await writer.drain()
                    return

                # ── GET /security-snapshot — R9: signed security telemetry export ──
                if method == "GET" and path == "/security-snapshot":
                    _peer_ss = writer.get_extra_info("peername")
                    if _peer_ss and _peer_ss[0] not in ("127.0.0.1", "::1"):
                        await _write_json(writer, 403, {"error": "forbidden"})
                        await writer.drain()
                        return
                    if not self._store:
                        await _write_json(writer, 503, {"error": "no store"})
                        await writer.drain()
                        return
                    try:
                        _snap = await self._build_security_snapshot()
                        _snap_bytes = json.dumps(_snap, default=str).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            b"Content-Disposition: attachment; filename=security-snapshot.json\r\n"
                            b"Content-Length: " + str(len(_snap_bytes)).encode() + b"\r\n\r\n" + _snap_bytes
                        )
                    except Exception as exc:
                        await _write_json(writer, 500, {"error": str(exc)})
                    await writer.drain()
                    return

                # ── GET /security-self-test — R10: loopback-only self-test endpoint ──
                if method == "GET" and path == "/security-self-test":
                    _peer_sst = writer.get_extra_info("peername")
                    if _peer_sst and _peer_sst[0] not in ("127.0.0.1", "::1"):
                        await _write_json(writer, 403, {"error": "forbidden"})
                        await writer.drain()
                        return
                    try:
                        _sst_report = await self._build_security_self_test_report()
                        _sst_bytes = json.dumps(_sst_report, default=str).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            b"Content-Length: " + str(len(_sst_bytes)).encode() + b"\r\n\r\n" + _sst_bytes
                        )
                    except Exception as exc:
                        await _write_json(writer, 500, {"error": str(exc)})
                    await writer.drain()
                    return

                # ── GET /setup/pod — R16.2.3: current pod connection status ──
                if method == "GET" and path == "/setup/pod":
                    status_data = json.dumps({
                        "connected": bool(self._pod_available and self._pod_url),
                        "pod_url": self._pod_url or None,
                        "css_url": getattr(self.config, "css_url", None),
                    }).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                        + str(len(status_data)).encode() + b"\r\n\r\n" + status_data
                    )
                    await writer.drain()
                    return

                # ── POST /setup/pod — R16.2.1: wizard pod credential intake ──
                if method == "POST" and path == "/setup/pod":
                    if not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        fb = b'{"error":"forbidden origin"}'
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                                     b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                     + str(len(fb)).encode() + b"\r\n\r\n" + fb)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 8192), 10.0)
                    try:
                        req = json.loads(body) if body else {}
                    except Exception:
                        req = {}
                    css_url = req.get("css_url", "").strip().rstrip("/")
                    email = req.get("email", "").strip()
                    password = req.get("password", "")
                    if not (css_url and email and password):
                        err = b'{"status":"error","message":"css_url, email, and password are required"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                                     b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                     + str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    try:
                        await asyncio.get_event_loop().run_in_executor(
                            None, self._connect_css_sync, css_url, email, password
                        )
                        self._pod_available = True
                        self.config.css_url = css_url
                        self.config.css_email = email
                        self.config.css_password = password
                        await self.broadcast({"type": "pod_status", "available": True,
                                              "pod_url": self._pod_url or ""})
                        ok = json.dumps({"status": "ok", "pod_url": self._pod_url or ""}).encode()
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                     b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                     + str(len(ok)).encode() + b"\r\n\r\n" + ok)
                    except Exception as exc:
                        msg = str(exc)
                        if "401" in msg or "Unauthorized" in msg or "Invalid credentials" in msg.lower():
                            human = "Incorrect email or password."
                        elif "connect" in msg.lower() or "refused" in msg.lower():
                            human = f"Could not reach {css_url} — check the URL and try again."
                        else:
                            human = f"Connection failed: {msg[:120]}"
                        err = json.dumps({"status": "error", "message": human}).encode()
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                     b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                     + str(len(err)).encode() + b"\r\n\r\n" + err)
                    await writer.drain()
                    return

                # ── OPTIONS /setup/pod — CORS preflight ──
                if method == "OPTIONS" and path == "/setup/pod":
                    if not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    else:
                        acao = (origin_header or b"null")
                        writer.write(
                            b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: " + acao + b"\r\n"
                            b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                            b"Access-Control-Allow-Headers: Content-Type\r\n\r\n"
                        )
                    await writer.drain()
                    return

                # ── POST /admin/revoke_contact — R12.3.2: CLI/pod-sync pushes revocation ──
                if method == "POST" and path == "/admin/revoke_contact":
                    _peer = writer.get_extra_info("peername")
                    if (_peer and _peer[0] not in ("127.0.0.1", "::1")):
                        _err = b'{"error":"forbidden"}'
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: " +
                                     str(len(_err)).encode() + b"\r\n\r\n" + _err)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 4096), 5.0)
                    try:
                        req = json.loads(body) if body else {}
                    except Exception:
                        req = {}
                    cert_id = req.get("cert_id", "")
                    peer_did = req.get("peer_did", "")
                    if not self._store or not (cert_id or peer_did):
                        err = b'{"error":"cert_id or peer_did required"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    if cert_id and not peer_did:
                        row = self._store.get_relationship_by_cert_id(cert_id)
                        peer_did = row.get("peer_did", "") if row else ""
                    if peer_did not in self._revoked_dids:
                        self._store.mark_revoked(cert_id or "", peer_did)
                        self._revoked_dids.add(peer_did)
                        await self._broadcast_to_owner({
                            "type": "contact_revoked",
                            "cert_id": cert_id,
                            "peer_did": peer_did,
                        })
                    ok = json.dumps({"status": "ok", "peer_did": peer_did}).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                 b"Content-Length: " + str(len(ok)).encode() + b"\r\n\r\n" + ok)
                    await writer.drain()
                    return

                # ── GET /backup — R12.1.2: identity backup download ──
                if method == "GET" and path == "/backup":
                    if _check_http_rate(peer_ip, "backup"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    # Admin token check (Round 6)
                    _admin_token = os.environ.get("PROXION_ADMIN_API_TOKEN", "")
                    if _admin_token:
                        _auth_header = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                        _req_token = _auth_header.removeprefix("Bearer ").strip() if _auth_header else ""
                        import hmac as _hmac_admin
                        if not _req_token or not _hmac_admin.compare_digest(_req_token, _admin_token):
                            err = b'{"error":"admin_token_required"}'
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                                         b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    # Legacy PROXION_API_TOKEN check (fallback)
                    _api_token_env = os.environ.get("PROXION_API_TOKEN", "")
                    _auth_header = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                    if _api_token_env and not _admin_token:
                        if _auth_header != f"Bearer {_api_token_env}":
                            err = b'{"error":"unauthorized"}'
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                                         b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    elif not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        fb = b'{"error":"forbidden origin"}'
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: " + str(len(fb)).encode() + b"\r\n\r\n" + fb)
                        await writer.drain()
                        return
                    # R11: per-day operation budget (max 20 backup exports/day)
                    if self._store and not self._store.check_operation_budget("backup_export", 20):
                        await _write_json(writer, 429, {"error": "recovery_budget_exceeded"})
                        await writer.drain()
                        return
                    if self._store:
                        self._store.increment_operation_budget("backup_export")
                    # R10: check backup mode
                    import urllib.parse as _urlparse_bk
                    _qs_bk = parts[1].split("?", 1)[1] if (len(parts) > 1 and "?" in parts[1]) else ""
                    _qs_params_bk: dict = {}
                    for _kv_bk in _qs_bk.split("&"):
                        if "=" in _kv_bk:
                            _k_bk, _v_bk = _kv_bk.split("=", 1)
                            _qs_params_bk[_k_bk] = _urlparse_bk.unquote_plus(_v_bk)
                    _backup_mode = _qs_params_bk.get("mode", "passphrase")
                    _recipient_pubkey = _qs_params_bk.get("recipient_pubkey", "").strip()
                    if _backup_mode not in ("passphrase", "recipient_key"):
                        await _write_json(writer, 400, {"error": "invalid_backup_mode",
                                                         "detail": "mode must be passphrase or recipient_key"})
                        await writer.drain()
                        return
                    if _backup_mode == "passphrase" and _recipient_pubkey:
                        await _write_json(writer, 400, {"error": "invalid_backup_mode",
                                                         "detail": "passphrase and recipient_key modes are mutually exclusive"})
                        await writer.drain()
                        return
                    if _backup_mode == "recipient_key":
                        if not _recipient_pubkey:
                            await _write_json(writer, 400, {"error": "invalid_backup_mode",
                                                             "detail": "recipient_pubkey required for recipient_key mode"})
                            await writer.drain()
                            return
                        try:
                            if len(bytes.fromhex(_recipient_pubkey)) != 32:
                                raise ValueError("must be 32 bytes")
                        except Exception:
                            await _write_json(writer, 400, {"error": "invalid_backup_mode",
                                                             "detail": "recipient_pubkey must be 32-byte X25519 key hex"})
                            await writer.drain()
                            return
                        try:
                            backup_bytes = self.agent.export_backup(recipient_pubkey_hex=_recipient_pubkey)
                            writer.write(
                                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                + _NO_STORE_HDR
                                + b"Content-Disposition: attachment; filename=\"proxion-backup.json\"\r\n"
                                b"Content-Length: " + str(len(backup_bytes)).encode() + b"\r\n\r\n" + backup_bytes
                            )
                        except Exception as exc:
                            err = json.dumps({"error": str(exc)}).encode()
                            writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
                                         + _NO_STORE_HDR
                                         + b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    # Passphrase mode
                    _pp = headers_raw.get(b"x-proxion-passphrase", b"").decode("utf-8", errors="replace")
                    if not _pp:
                        _pp = _qs_params_bk.get("passphrase", "")
                    if not _pp:
                        err = b'{"error":"passphrase required"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    try:
                        backup_bytes = self.agent.export_backup(passphrase=_pp.encode("utf-8"))
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            + _NO_STORE_HDR
                            + b"Content-Disposition: attachment; filename=\"proxion-backup.json\"\r\n"
                            b"Content-Length: " + str(len(backup_bytes)).encode() + b"\r\n\r\n" + backup_bytes
                        )
                    except Exception as exc:
                        err = json.dumps({"error": str(exc)}).encode()
                        writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
                                     + _NO_STORE_HDR
                                     + b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                    await writer.drain()
                    return

                # ── POST /restore — R12.1.4: identity restore ──
                if method == "POST" and path == "/restore":
                    if os.environ.get("PROXION_SAFE_MODE") == "1":
                        await _write_json(writer, 503, {"error": "safe_mode_enabled"})
                        return
                    if _check_http_rate(peer_ip, "backup"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    # R11: per-day operation budget (max 3 restore ops/day)
                    if self._store and not self._store.check_operation_budget("restore", 3):
                        await _write_json(writer, 429, {"error": "recovery_budget_exceeded"})
                        await writer.drain()
                        return
                    # Admin token check (Round 6)
                    _admin_token = os.environ.get("PROXION_ADMIN_API_TOKEN", "")
                    if _admin_token:
                        _auth_header = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                        _req_token = _auth_header.removeprefix("Bearer ").strip() if _auth_header else ""
                        import hmac as _hmac_admin
                        if not _req_token or not _hmac_admin.compare_digest(_req_token, _admin_token):
                            err = b'{"error":"admin_token_required"}'
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                                         b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    # Legacy PROXION_API_TOKEN check (fallback)
                    _api_token_env = os.environ.get("PROXION_API_TOKEN", "")
                    _auth_header = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                    if _api_token_env and not _admin_token:
                        if _auth_header != f"Bearer {_api_token_env}":
                            err = b'{"error":"unauthorized"}'
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                                         b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    elif not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        fb = b'{"error":"forbidden origin"}'
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: " + str(len(fb)).encode() + b"\r\n\r\n" + fb)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 4 * 1024 * 1024), 30.0)
                    # R7: Restore manifest verification
                    _manifest_hdr_r = headers_raw.get(b"x-proxion-import-manifest", b"").decode("utf-8", errors="replace")
                    _require_manifest_r = os.environ.get("PROXION_REQUIRE_IMPORT_MANIFEST") == "1"
                    _manifest_source_r = None
                    _manifest_sha256_r = None
                    if _manifest_hdr_r:
                        try:
                            _manifest_r = json.loads(_manifest_hdr_r)
                            _manifest_source_r = _manifest_r.get("source")
                            _manifest_sha256_r = _manifest_r.get("sha256")
                            if _manifest_sha256_r:
                                import hashlib as _hl_rst
                                _actual_sha256_r = _hl_rst.sha256(body).hexdigest()
                                if _actual_sha256_r != _manifest_sha256_r:
                                    err = b'{"error":"import_manifest_hash_mismatch"}'
                                    writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: " +
                                                 str(len(err)).encode() + b"\r\n\r\n" + err)
                                    await writer.drain()
                                    return
                        except Exception:
                            err = b'{"error":"invalid_import_manifest"}'
                            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: " +
                                         str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    elif _require_manifest_r:
                        err = b'{"error":"import_manifest_required"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    # R8: Two-person recovery control
                    if os.environ.get("PROXION_REQUIRE_RECOVERY_APPROVAL") == "1" and self._store:
                        _recovery_op_id_r = headers_raw.get(b"x-proxion-recovery-op-id", b"").decode("utf-8", errors="replace").strip()
                        if not _recovery_op_id_r:
                            await _write_json(writer, 403, {"error": "recovery_approval_required"})
                            await writer.drain()
                            return
                        _r_op = self._store.get_recovery_operation(_recovery_op_id_r)
                        if not _r_op or not _r_op.get("confirmed") or _r_op.get("used") or _r_op.get("expires_at", 0) < time.time():
                            await _write_json(writer, 403, {"error": "invalid_or_expired_recovery_op"})
                            await writer.drain()
                            return
                        if _r_op.get("op_type") != "restore":
                            await _write_json(writer, 403, {"error": "recovery_op_type_mismatch"})
                            await writer.drain()
                            return
                        # R9: fingerprint binding check
                        _stored_fp_r = _r_op.get("requester_fingerprint")
                        if _stored_fp_r:
                            _req_fp_r = hashlib.sha256(
                                f"{peer_ip}|restore".encode()
                            ).hexdigest()
                            if _req_fp_r != _stored_fp_r:
                                await _write_json(writer, 403, {"error": "recovery_fingerprint_mismatch"})
                                await writer.drain()
                                return
                        self._store.consume_recovery_operation(_recovery_op_id_r)

                    # Extract passphrase: prefer X-Proxion-Passphrase header, fall back to query string
                    _pp_r = headers_raw.get(b"x-proxion-passphrase", b"").decode("utf-8", errors="replace")
                    if not _pp_r:
                        import urllib.parse as _urlparse_r
                        _qs_r = parts[1].split("?", 1)[1] if (len(parts) > 1 and "?" in parts[1]) else ""
                        for _kv_r in _qs_r.split("&"):
                            if _kv_r.startswith("passphrase="):
                                _pp_r = _urlparse_r.unquote_plus(_kv_r[len("passphrase="):])
                    if not _pp_r:
                        err = b'{"error":"passphrase required"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    # Check for dry_run query parameter (Round 6)
                    _dry_run_r = "dry_run=1" in parts[1] if len(parts) > 1 else False
                    try:
                        from .persist import AgentState as _AS
                        _pp_bytes = _pp_r.encode("utf-8")
                        new_agent = _AS.import_backup(body, _pp_bytes)
                        if _dry_run_r:
                            # Dry-run mode: validate but don't persist
                            dry_resp = json.dumps({"dry_run": True, "valid": True}).encode()
                            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                         + _NO_STORE_HDR
                                         + b"Content-Length: " + str(len(dry_resp)).encode() + b"\r\n\r\n" + dry_resp)
                        else:
                            self.agent = new_agent
                            # R13.3: always persist agent.json, creating it if needed
                            if self.config.db_path:
                                from pathlib import Path as _P
                                _agent_path = _P(self.config.db_path).parent / "agent.json"
                                _agent_path.parent.mkdir(parents=True, exist_ok=True)
                                new_agent.save(str(_agent_path), _pp_bytes)
                            await self.broadcast({"type": "identity_restored"})
                            # R7: Save restore provenance
                            if self._store:
                                import uuid as _uuid_rst, time as _t_rst
                                _summary_rst = json.dumps({"type": "identity_restore"})
                                self._store.save_import_provenance(
                                    id=str(_uuid_rst.uuid4()),
                                    source=_manifest_source_r,
                                    body_sha256=_manifest_sha256_r,
                                    imported_by=peer_ip,
                                    imported_at=_t_rst.time(),
                                    dry_run=False,
                                    summary_json=_summary_rst,
                                )
                                # R11: record restore budget usage
                                self._store.increment_operation_budget("restore")
                            ok = b'{"status":"ok"}'
                            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                         + _NO_STORE_HDR
                                         + b"Content-Length: " + str(len(ok)).encode() + b"\r\n\r\n" + ok)
                    except Exception as exc:
                        err = json.dumps({"error": str(exc)}).encode()
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                                     + _NO_STORE_HDR
                                     + b"Content-Length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
                    await writer.drain()
                    return

                # ── POST /import — data import (R14.2) ──
                if method == "POST" and path == "/import":
                    if os.environ.get("PROXION_SAFE_MODE") == "1":
                        await _write_json(writer, 503, {"error": "safe_mode_enabled"})
                        return
                    if _check_http_rate(peer_ip, "backup"):
                        await _write_429(writer)
                        await writer.drain()
                        return
                    # R11: per-day operation budget (max 10 import ops/day)
                    if self._store and not self._store.check_operation_budget("import", 10):
                        await _write_json(writer, 429, {"error": "recovery_budget_exceeded"})
                        await writer.drain()
                        return
                    # Admin token check (Round 6)
                    _admin_token = os.environ.get("PROXION_ADMIN_API_TOKEN", "")
                    if _admin_token:
                        _auth_header = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                        _req_token = _auth_header.removeprefix("Bearer ").strip() if _auth_header else ""
                        import hmac as _hmac_admin
                        if not _req_token or not _hmac_admin.compare_digest(_req_token, _admin_token):
                            err = b'{"error":"admin_token_required"}'
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                                         b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                         + str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    # Legacy PROXION_API_TOKEN check (fallback)
                    _api_token_env = os.environ.get("PROXION_API_TOKEN", "")
                    _auth_header = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                    if _api_token_env and not _admin_token:
                        if _auth_header != f"Bearer {_api_token_env}":
                            err = b'{"error":"unauthorized"}'
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                                         b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                         + str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    elif not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        # Drain body before responding so Windows doesn't send a TCP RST
                        # (unread body on close causes ConnectionAbortedError on the client).
                        if content_length > 0:
                            try:
                                await _read_http_body(reader, min(content_length, _IMPORT_MAX), 5.0)
                            except Exception:
                                pass
                        fb = b'{"error":"forbidden origin"}'
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                                     b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                                     + str(len(fb)).encode() + b"\r\n\r\n" + fb)
                        await writer.drain()
                        return
                    if not self._store:
                        err = b'{"error":"no store"}'
                        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    body = b""
                    if content_length > 0:
                        body = await _read_http_body(reader, min(content_length, 10 * 1024 * 1024), 30.0)
                    # R8: Two-person recovery control for /import
                    if os.environ.get("PROXION_REQUIRE_RECOVERY_APPROVAL") == "1":
                        _recovery_op_id_i = headers_raw.get(b"x-proxion-recovery-op-id", b"").decode("utf-8", errors="replace").strip()
                        if not _recovery_op_id_i:
                            await _write_json(writer, 403, {"error": "recovery_approval_required"})
                            await writer.drain()
                            return
                        _i_op = self._store.get_recovery_operation(_recovery_op_id_i)
                        if not _i_op or not _i_op.get("confirmed") or _i_op.get("used") or _i_op.get("expires_at", 0) < time.time():
                            await _write_json(writer, 403, {"error": "invalid_or_expired_recovery_op"})
                            await writer.drain()
                            return
                        if _i_op.get("op_type") != "import":
                            await _write_json(writer, 403, {"error": "recovery_op_type_mismatch"})
                            await writer.drain()
                            return
                        # R9: fingerprint binding check for /import
                        _stored_fp_i = _i_op.get("requester_fingerprint")
                        if _stored_fp_i:
                            _req_fp_i = hashlib.sha256(
                                f"{peer_ip}|import".encode()
                            ).hexdigest()
                            if _req_fp_i != _stored_fp_i:
                                await _write_json(writer, 403, {"error": "recovery_fingerprint_mismatch"})
                                await writer.drain()
                                return
                        self._store.consume_recovery_operation(_recovery_op_id_i)

                    # R7: Import manifest verification
                    _manifest_hdr = headers_raw.get(b"x-proxion-import-manifest", b"").decode("utf-8", errors="replace")
                    _require_manifest = os.environ.get("PROXION_REQUIRE_IMPORT_MANIFEST") == "1"
                    _manifest_source = None
                    _manifest_sha256 = None
                    _manifest_exported_at = None
                    if _manifest_hdr:
                        try:
                            _manifest = json.loads(_manifest_hdr)
                            _manifest_source = _manifest.get("source")
                            _manifest_sha256 = _manifest.get("sha256")
                            _manifest_exported_at = _manifest.get("exported_at")
                            if _manifest_sha256:
                                import hashlib as _hl_imp
                                _actual_sha256 = _hl_imp.sha256(body).hexdigest()
                                if _actual_sha256 != _manifest_sha256:
                                    err = b'{"error":"import_manifest_hash_mismatch"}'
                                    writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: " +
                                                 str(len(err)).encode() + b"\r\n\r\n" + err)
                                    await writer.drain()
                                    return
                        except Exception:
                            err = b'{"error":"invalid_import_manifest"}'
                            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: " +
                                         str(len(err)).encode() + b"\r\n\r\n" + err)
                            await writer.drain()
                            return
                    elif _require_manifest:
                        err = b'{"error":"import_manifest_required"}'
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                        await writer.drain()
                        return
                    # Check for dry_run query parameter (Round 6)
                    _dry_run_i = "dry_run=1" in parts[1] if len(parts) > 1 else False
                    try:
                        import_data = json.loads(body)
                        if _dry_run_i:
                            # Dry-run: count valid messages without persisting
                            messages = import_data.get("messages", [])
                            relationships = import_data.get("relationships", [])
                            _msgs_valid = 0
                            for msg in messages:
                                if all(k in msg for k in ["message_id", "thread_id", "thread_type", "from_webid", "content", "timestamp"]):
                                    _msgs_valid += 1
                            _rels_valid = len(relationships)  # simplified validation
                            dry_resp = json.dumps({
                                "dry_run": True,
                                "messages_valid": _msgs_valid,
                                "relationships_valid": _rels_valid,
                                "rejected_rows": len(messages) - _msgs_valid + len([r for r in relationships if not isinstance(r, dict)])
                            }).encode()
                            writer.write(
                                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                b"Access-Control-Allow-Origin: *\r\n"
                                b"Content-Length: " + str(len(dry_resp)).encode() + b"\r\n\r\n" + dry_resp
                            )
                        else:
                            counts = self._store.import_data(import_data, owner_pub_hex=self.agent.identity_pub_bytes.hex())
                            await self.broadcast({"type": "import_complete", "counts": counts})
                            # R7: Save import provenance
                            if self._store:
                                import uuid as _uuid_imp, time as _t_imp
                                _summary = json.dumps({"messages": counts.get("messages", 0), "relationships": counts.get("relationships", 0)})
                                self._store.save_import_provenance(
                                    id=str(_uuid_imp.uuid4()),
                                    source=_manifest_source,
                                    body_sha256=_manifest_sha256,
                                    imported_by=peer_ip,
                                    imported_at=_t_imp.time(),
                                    dry_run=False,
                                    summary_json=_summary,
                                )
                            resp_bytes = json.dumps({"status": "ok", "counts": counts}).encode()
                            writer.write(
                                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                b"Access-Control-Allow-Origin: *\r\n"
                                b"Content-Length: " + str(len(resp_bytes)).encode() + b"\r\n\r\n" + resp_bytes
                            )
                    except Exception as exc:
                        err = json.dumps({"error": str(exc)}).encode()
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: " +
                                     str(len(err)).encode() + b"\r\n\r\n" + err)
                    await writer.drain()
                    return

                # ── GET /metrics — OpenMetrics text format (R13.4, R17) ──
                if method == "GET" and path == "/metrics":
                    _uptime = time.time() - self._start_time
                    from .security_policy import get_policy
                    _tier = get_policy().get_tier()
                    _relay_depth = 0
                    if self._store:
                        try:
                            _relay_depth = len(self._store.get_pending_relay_messages(max_attempts=99))
                        except Exception:
                            pass
                    lines = [
                        "# HELP proxion_uptime_seconds Seconds since gateway started",
                        "# TYPE proxion_uptime_seconds gauge",
                        f"proxion_uptime_seconds {_uptime:.3f}",
                        "# HELP proxion_security_tier Current adaptive security tier (0-3)",
                        "# TYPE proxion_security_tier gauge",
                        f"proxion_security_tier {_tier}",
                        "# HELP proxion_relay_queue_depth Pending relay messages awaiting delivery",
                        "# TYPE proxion_relay_queue_depth gauge",
                        f"proxion_relay_queue_depth {_relay_depth}",
                        "# HELP proxion_ws_connections_current Active WebSocket connections",
                        "# TYPE proxion_ws_connections_current gauge",
                        f"proxion_ws_connections_current {len(self._client_webids)}",
                    ]
                    for _k, _v in self._metrics.items():
                        _typ = "gauge" if _k.endswith("_current") else "counter"
                        _help = _k.replace("_", " ")
                        lines.append(f"# HELP proxion_{_k} {_help}")
                        lines.append(f"# TYPE proxion_{_k} {_typ}")
                        lines.append(f"proxion_{_k} {_v}")
                    _mtext = ("\n".join(lines) + "\n").encode("utf-8")
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4; charset=utf-8\r\n"
                        + _NO_STORE_HDR
                        + b"Content-Length: " + str(len(_mtext)).encode() + b"\r\n\r\n" + _mtext
                    )
                    await writer.drain()
                    return

                # ── GET /vapid-public-key — VAPID public key for WebPush registration ──
                if method == "GET" and path == "/vapid-public-key":
                    _vpub = getattr(self, "_vapid_public_b64", "")
                    if not _vpub:
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                    else:
                        import json as _jvap
                        _vbody = _jvap.dumps({"publicKey": _vpub}).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                            + b"Access-Control-Allow-Origin: *\r\n"
                            + b"Content-Length: " + str(len(_vbody)).encode() + b"\r\n\r\n"
                            + _vbody
                        )
                    await writer.drain()
                    return

                # ── POST /api/pod-disconnect — sign-out fallback usable without WebSocket ──
                if method == "POST" and path == "/api/pod-disconnect":
                    if not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    self._pod_url = None
                    self._pod_webid = None
                    self.dm_clients.clear()
                    if self.config.db_path:
                        from pathlib import Path as _Path_pd
                        _cp = _Path_pd(self.config.db_path).parent / "pod_creds.json"
                        _cp.unlink(missing_ok=True)
                    logger.info("pod disconnected via HTTP /api/pod-disconnect")
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\nContent-Length: 4\r\n\r\nnull"
                    )
                    await writer.drain()
                    return

                # ── GET /message-edits — edit history (R13.11) ──
                if method == "GET" and path == "/message-edits":
                    if not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    import urllib.parse as _up_me
                    _qs_me = parts[1].split("?", 1)[1] if (len(parts) > 1 and "?" in parts[1]) else ""
                    _mid = ""
                    for _kv in _qs_me.split("&"):
                        if _kv.startswith("message_id="):
                            _mid = _up_me.unquote_plus(_kv[len("message_id="):])
                    if self._store and _mid:
                        _edits = self._store.get_edits(_mid)
                    else:
                        _edits = []
                    _eb = json.dumps(_edits).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(_eb)).encode() + b"\r\n\r\n" + _eb
                    )
                    await writer.drain()
                    return

                # ── GET /contacts — contact list (R13.14) ──
                if method == "GET" and (path == "/contacts" or path.startswith("/contacts?")):
                    if not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    if self._store:
                        _contacts = self._store.get_all_contacts(100)
                    else:
                        _contacts = []
                    _cb = json.dumps(_contacts).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(_cb)).encode() + b"\r\n\r\n" + _cb
                    )
                    await writer.drain()
                    return

                # ── GET /contacts/search — contact typeahead (R13.14) ──
                if method == "GET" and path.startswith("/contacts/search"):
                    if not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    import urllib.parse as _up_cs
                    _qs_cs = parts[1].split("?", 1)[1] if (len(parts) > 1 and "?" in parts[1]) else ""
                    _q = ""
                    for _kv in _qs_cs.split("&"):
                        if _kv.startswith("q="):
                            _q = _up_cs.unquote_plus(_kv[2:])
                    if self._store and _q:
                        _results = self._store.search_contacts(_q)
                    else:
                        _results = []
                    _sb = json.dumps(_results).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(_sb)).encode() + b"\r\n\r\n" + _sb
                    )
                    await writer.drain()
                    return

                # ── POST /contacts — manual contact add (R13.14) ──
                if method == "POST" and path == "/contacts":
                    _api_token_env_c = os.environ.get("PROXION_API_TOKEN", "")
                    _auth_header_c = headers_raw.get(b"authorization", b"").decode("utf-8", errors="replace")
                    if _api_token_env_c:
                        if _auth_header_c != f"Bearer {_api_token_env_c}":
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                            await writer.drain()
                            return
                    elif not self._is_trusted_origin(origin_header, http_port, peer_ip):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    _body_c = b""
                    if content_length > 0:
                        _body_c = await _read_http_body(reader, min(content_length, 4096), 10.0)
                    try:
                        _cd = json.loads(_body_c)
                        if self._store and _cd.get("webid") and _cd.get("display_name"):
                            self._store.upsert_contact(
                                _cd["webid"], _cd["display_name"],
                                avatar_url=_cd.get("avatar_url"), source="manual"
                            )
                        _cr = b'{"status":"ok"}'
                        writer.write(b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: " + str(len(_cr)).encode() + b"\r\n\r\n" + _cr)
                    except Exception as _exc_c:
                        _err_c = json.dumps({"error": str(_exc_c)}).encode()
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: "
                                     + str(len(_err_c)).encode() + b"\r\n\r\n" + _err_c)
                    await writer.drain()
                    return

                # ── POST /webhook/{token} — incoming webhook delivery ──
                if path.startswith("/webhook/"):
                    token = path[len("/webhook/"):]
                    if method != "POST":
                        writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    wh = self._store.get_webhook_by_token_with_rotation(token) if self._store else None
                    if not wh or wh.get("direction") != "incoming" or not wh.get("active"):
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    # Secret-token check (if configured)
                    wh_secret = wh.get("secret_token") or ""
                    if wh_secret:
                        import hmac as _hmac
                        req_secret = headers_raw.get(b"x-proxion-secret", b"").decode("utf-8", errors="replace")
                        if not _hmac.compare_digest(req_secret.encode(), wh_secret.encode()):
                            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                            await writer.drain()
                            return
                    # IP allowlist check (if configured)
                    wh_allowed_ips = wh.get("allowed_ips") or ""
                    if wh_allowed_ips and peer_ip:
                        import ipaddress as _ipaddress
                        try:
                            peer_addr = _ipaddress.ip_address(peer_ip)
                            allowed = any(
                                peer_addr in _ipaddress.ip_network(cidr.strip(), strict=False)
                                for cidr in wh_allowed_ips.split(",")
                                if cidr.strip()
                            )
                        except ValueError:
                            allowed = False
                        if not allowed:
                            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                            await writer.drain()
                            return
                    body_bytes = b""
                    if content_length > 0:
                        body_bytes = await _read_http_body(reader, min(content_length, 65536), 10.0)
                    try:
                        body_json = json.loads(body_bytes)
                    except Exception:
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    wh_content = str(body_json.get("text", body_json.get("content", ""))).strip()[:4000]
                    if not wh_content:
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    import uuid as _uuid_wh_http
                    wh_msg_id = str(_uuid_wh_http.uuid4())
                    wh_event = {
                        "type": "message",
                        "source": "local_room",
                        "thread_id": wh["thread_id"],
                        "message_id": wh_msg_id,
                        "from_webid": f"webhook:{wh['id']}",
                        "from_display_name": wh["bot_name"],
                        "content": wh_content,
                        "is_bot": True,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "local": True,
                    }
                    room = self._local_rooms.get(wh["thread_id"], {})
                    for ws in list(room.get("members", set())):
                        try:
                            await ws.send(json.dumps(wh_event))
                        except Exception:
                            pass
                    if self._store:
                        self._store.save_message(
                            wh_msg_id, wh["thread_id"], "local_room",
                            wh_event["from_webid"], wh["bot_name"], wh_content,
                            wh_event["timestamp"],
                        )
                    ok_bytes = b'{"ok":true}'
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(ok_bytes)).encode() + b"\r\n\r\n" + ok_bytes
                    )
                    await writer.drain()
                    return

                # ── Static file serving ──
                _EXT_CT = {
                    '.html': b'text/html; charset=utf-8',
                    '.js':   b'application/javascript; charset=utf-8',
                    '.css':  b'text/css',
                    '.json': b'application/manifest+json',
                    '.svg':  b'image/svg+xml',
                    '.png':  b'image/png',
                    '.ico':  b'image/x-icon',
                }
                _CSP = (
                    b"default-src 'self'; "
                    b"script-src 'self' 'unsafe-inline'; "
                    b"worker-src 'self'; "
                    b"connect-src 'self' ws: wss: https:; "
                    b"style-src 'self' 'unsafe-inline'; "
                    b"img-src 'self' data: image/svg+xml; "
                    b"frame-src 'self'; "
                    b"frame-ancestors 'none'; "
                    b"base-uri 'self';"
                )
                fname = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
                if web_path is None:
                    body = b"Not found"
                    writer.write(b"HTTP/1.1 404 Not Found\r\n" + _SEC_HDR +
                                 b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                    await writer.drain()
                    return
                fpath = (web_path / fname).resolve()
                try:
                    fpath.relative_to(web_path.resolve())
                except ValueError:
                    body = b"Forbidden"
                    writer.write(b"HTTP/1.1 403 Forbidden\r\n" + _SEC_HDR +
                                 b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                    await writer.drain()
                    return
                ct = _EXT_CT.get(fpath.suffix.lower())
                if ct is None or not fpath.exists():
                    body = b"Not found"
                    writer.write(b"HTTP/1.1 404 Not Found\r\n" + _SEC_HDR +
                                 b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                    await writer.drain()
                    return
                body = fpath.read_bytes()
                is_index = (fname == "index.html")
                if is_index:
                    body = body.replace(b"</head>", inject + b"</head>", 1)
                cc = b"no-cache" if fname == "sw.js" else b"no-store"
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: " + ct + b"\r\n"
                    + _SEC_HDR
                    + b"Content-Length: " + str(len(body)).encode()
                    + b"\r\nCache-Control: " + cc + b"\r\n\r\n" + body
                )
                await writer.drain()
            except Exception as exc:
                logger.debug(f"HTTP handler error: {exc}")
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_server(
            handle, self.config.host, http_port, ssl=ssl_ctx_http
        )
        scheme = "https" if ssl_ctx_http else "http"
        host_display = self.config.host if self.config.host != "0.0.0.0" else "localhost"
        logger.info(f"Web UI available at {scheme}://{host_display}:{http_port}")
        async with server:
            await server.serve_forever()

    # _handle_relay_post: moved to _gateway_http.py (HttpEndpointsMixin).
