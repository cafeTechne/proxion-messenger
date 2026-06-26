"""MailboxMixin — sealed managed-relay fallback (R38).

For gateways that aren't directly reachable (UPnP off, CGNAT), a relay node
acts as a sealed post office. A sender that can't reach a peer directly seals
the relay payload to the peer GATEWAY's X25519 key and drops it in the peer's
mailbox on a relay node. The peer drains its mailbox and unseals locally.

Privacy: the relay node stores only {recipient_did, opaque_sealed_blob}. It
holds no decryption key and never sees the sender or plaintext. This honors
the directive: no advertising IPs/ports; sealed sender is the model.

Two roles, both the same binary:
- Relay node (PROXION_RELAY_NODE=1): serves POST/GET /mailbox/{did}.
- Ordinary gateway (PROXION_RELAY_FALLBACK_URL=...): sends to / drains from a
  relay node when direct delivery is unavailable.

Requires on self: agent, _own_x25519_priv, _store, _resolve_peer_x25519_pub(),
_handle_relay_post().
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time

logger = logging.getLogger("proxion_messenger_core.gateway")

_MAILBOX_TTL_S = 7 * 24 * 3600   # blobs live up to 7 days
_MAX_SEALED_LEN = 256 * 1024     # 256 KB per sealed blob


def relay_node_enabled() -> bool:
    return os.environ.get("PROXION_RELAY_NODE") == "1"


def relay_fallback_url() -> str:
    return (os.environ.get("PROXION_RELAY_FALLBACK_URL") or "").rstrip("/")


class MailboxMixin:

    # ---- Relay-node side: serve the mailbox ----

    async def _handle_mailbox_store(self, recipient_did: str, body: bytes) -> tuple[str, str]:
        """POST /mailbox/{did} — store a sealed blob. Relay-node only."""
        if not relay_node_enabled() or not self._store:
            return "404 Not Found", '{"error":"mailbox_disabled"}'
        if not recipient_did:
            return "400 Bad Request", '{"error":"missing_recipient"}'
        try:
            data = json.loads(body)
            sealed = data.get("sealed_blob", "")
        except Exception:
            return "400 Bad Request", '{"error":"invalid_json"}'
        if not sealed or not isinstance(sealed, str) or len(sealed) > _MAX_SEALED_LEN:
            return "400 Bad Request", '{"error":"invalid_sealed_blob"}'
        blob_id = secrets.token_hex(16)
        ok = self._store.enqueue_mailbox(blob_id, recipient_did, sealed, time.time() + _MAILBOX_TTL_S)
        if not ok:
            return "507 Insufficient Storage", '{"error":"mailbox_full"}'
        return "200 OK", json.dumps({"status": "stored", "blob_id": blob_id})

    async def _handle_mailbox_drain(self, recipient_did: str, sig: str, ts: str,
                                    nonce: str) -> tuple[str, str]:
        """GET /mailbox/{did} — return + delete blobs. Requires a signature
        proving control of recipient_did. Relay-node only."""
        if not relay_node_enabled() or not self._store:
            return "404 Not Found", '{"error":"mailbox_disabled"}'
        if not recipient_did or not sig or not ts or not nonce:
            return "401 Unauthorized", '{"error":"missing_auth"}'
        # Reject stale timestamps (replay window 5 min). ISO-8601 expected.
        from datetime import datetime, timezone
        try:
            _ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if _ts_dt.tzinfo is None:
                _ts_dt = _ts_dt.replace(tzinfo=timezone.utc)
            if abs((datetime.now(timezone.utc) - _ts_dt).total_seconds()) > 300:
                return "401 Unauthorized", '{"error":"stale_timestamp"}'
        except (ValueError, TypeError):
            return "401 Unauthorized", '{"error":"bad_timestamp"}'
        # Verify the drainer controls recipient_did by checking a signature over
        # (recipient_did, recipient_did, "mailbox-drain", "", ts, nonce).
        from .relay import verify_relay_message
        if not verify_relay_message(recipient_did, recipient_did, "mailbox-drain",
                                    "", ts, sig, nonce):
            return "401 Unauthorized", '{"error":"bad_signature"}'
        blobs = self._store.drain_mailbox(recipient_did)
        return "200 OK", json.dumps({"blobs": blobs})

    # ---- Sender side: deliver via mailbox when direct fails ----

    async def _send_via_mailbox(self, recipient_did: str, payload: dict) -> bool:
        """Seal *payload* to the recipient gateway's X25519 key and POST it to the
        configured relay node's mailbox. Returns True on success."""
        fallback = relay_fallback_url()
        if not fallback or not recipient_did:
            return False
        peer_x25519 = self._resolve_peer_x25519_pub(recipient_did)
        if not peer_x25519:
            return False
        try:
            from .sealed_relay import seal_relay_payload
            from .relay import post_relay
            sealed = seal_relay_payload(payload, peer_x25519)
            url = f"{fallback}/mailbox/{recipient_did}"
            return await post_relay(url, {"sealed_blob": sealed})
        except Exception as exc:
            logger.debug("mailbox send failed: %s", exc)
            return False

    # ---- Recipient side: drain own mailbox + dispatch ----

    async def _drain_own_mailbox(self) -> int:
        """Drain this gateway's mailbox from the relay node, unseal each blob, and
        feed it through the normal inbound relay dispatch. Returns count handled."""
        fallback = relay_fallback_url()
        if not fallback or not self._own_x25519_priv:
            return 0
        from .didkey import pub_key_to_did
        from .relay import sign_relay_message
        from .network import async_safe_get
        import urllib.parse as _up
        from datetime import datetime, timezone
        own_did = pub_key_to_did(self.agent.identity_pub_bytes)
        ts = datetime.now(timezone.utc).isoformat()
        nonce = secrets.token_hex(8)
        sig = sign_relay_message(self.agent.identity_key, own_did, own_did,
                                 "mailbox-drain", "", ts, nonce)
        qs = _up.urlencode({"sig": sig, "ts": ts, "nonce": nonce})
        url = f"{fallback}/mailbox/{_up.quote(own_did, safe='')}?{qs}"
        try:
            raw = await async_safe_get(url, timeout=8.0)
            resp = json.loads(raw)
        except Exception as exc:
            logger.debug("mailbox drain request failed: %s", exc)
            return 0
        blobs = (resp or {}).get("blobs", [])
        handled = 0
        from .sealed_relay import unseal_relay_payload
        for entry in blobs:
            try:
                inner = unseal_relay_payload(entry["sealed_blob"], self._own_x25519_priv)
                await self._handle_relay_post(json.dumps(inner).encode(), client_ip="mailbox")
                handled += 1
            except Exception as exc:
                logger.debug("mailbox blob dispatch failed: %s", exc)
        return handled

    # ---- Background loops ----

    async def _mailbox_drain_loop(self) -> None:
        """Periodically drain our mailbox from the relay node."""
        import asyncio
        while True:
            try:
                await self._drain_own_mailbox()
            except Exception as exc:
                logger.debug("mailbox drain loop error: %s", exc)
            await asyncio.sleep(60)

    async def _mailbox_purge_loop(self) -> None:
        """Relay node: purge expired mailbox blobs hourly."""
        import asyncio
        while True:
            await asyncio.sleep(3600)
            try:
                if self._store:
                    n = self._store.purge_expired_mailbox()
                    if n:
                        logger.info("Purged %d expired mailbox blobs", n)
            except Exception as exc:
                logger.debug("mailbox purge error: %s", exc)
