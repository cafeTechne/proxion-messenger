"""PodSyncMixin — Solid Pod connection, polling, and federation for ProxionGateway.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: agent, config, dm_clients, _store, _pod_url, _pod_webid,
                  _pod_available, _peer_gateway_urls, read_state, message_cache,
                  identity_cache, blocklist, room_memberships, outbox, _stop_event,
                  _local_rooms, _client_webids, broadcast(), process_command(),
                  process_link_previews(), _ws_public_url().
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from .inbox import InboxEntry, poll_inbox
from .didkey import pub_key_to_did
from .msgcrypto import decrypt_message, derive_message_key, is_encrypted

logger = logging.getLogger("proxion_messenger_core.gateway")


def _derive_cred_key(identity_key) -> bytes:
    """Derive a 32-byte Fernet key from the agent's Ed25519 private key via HKDF."""
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.backends import default_backend
    raw_priv = identity_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"proxion-pod-creds-v2",
        info=b"",
        backend=default_backend(),
    ).derive(raw_priv)


def _encrypt_creds(identity_key, plaintext: bytes) -> str:
    """Encrypt *plaintext* with a Fernet key derived from the identity key."""
    import base64
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(_derive_cred_key(identity_key))
    return Fernet(key).encrypt(plaintext).decode("ascii")


def _decrypt_creds(identity_key, token: str) -> bytes:
    """Decrypt a token previously produced by _encrypt_creds."""
    import base64
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(_derive_cred_key(identity_key))
    return Fernet(key).decrypt(token.encode("ascii"))


def extract_mentions(content: str, known_display_names: dict) -> list:
    """Identify mentioned WebIDs in content based on @display_name."""
    import re
    mentions = []
    matches = re.findall(r"@(\w+)", content)
    for name in matches:
        if name in known_display_names:
            mentions.append(known_display_names[name])
    return list(set(mentions))


class PodSyncMixin:

    # ── Identity / Relationship helpers ─────────────────────────────────────

    def _rehydrate_relationships(self, pod_client) -> int:
        """Load stored relationship certs into dm_clients so pod DMs work after restart."""
        if not self._store:
            return 0
        count = 0
        for cert_dict in self._store.list_relationships():
            try:
                from .federation import RelationshipCertificate
                cert = RelationshipCertificate.from_dict(cert_dict)
                cert_id = cert.certificate_id
                if cert_id and cert_id not in self.dm_clients:
                    self.dm_clients[cert_id] = (cert, pod_client)
                    count += 1
            except Exception as exc:
                logger.warning(f"Could not rehydrate relationship {cert_dict.get('certificate_id')}: {exc}")
        logger.info(f"Rehydrated {count} relationship certs into dm_clients")
        return count

    def _pod_client(self):
        """Return the live DPoP SolidClient if a pod is connected, else None."""
        if not self._pod_webid:
            return None
        entry = self.dm_clients.get(self._pod_webid)
        if entry is None:
            return None
        _, client = entry
        return client

    def _resolve_peer_gateway(self, did: str) -> Optional[str]:
        """Return the known HTTP base URL for a peer gateway, or None."""
        url = self._peer_gateway_urls.get(did)
        if url:
            return url
        if self._store:
            url = self._store.get_peer_gateway(did)
            if url:
                self._peer_gateway_urls[did] = url
                return url
        return None

    def _record_peer_gateway(self, did: str, gateway_url: str) -> None:
        """Store a peer gateway URL in memory and (if available) SQLite.

        R8: First-seen URL becomes the trust pin. Subsequent changes create a
        pending change request and queue outbound relay until approved.
        """
        now = time.time()
        if self._store:
            pin = self._store.get_peer_gateway_pin(did)
            if pin is None:
                # First seen — pin it
                self._store.upsert_peer_gateway_pin(
                    peer_did=did,
                    pinned_gateway_url=gateway_url,
                    pinned_at=now,
                    last_seen_gateway_url=gateway_url,
                    last_seen_at=now,
                    pending_change=False,
                )
                self._peer_gateway_urls[did] = gateway_url
                self._store.save_peer_gateway(did, gateway_url)
            elif pin["pinned_gateway_url"] != gateway_url:
                # URL changed from pinned value — create a change request
                if not pin.get("pending_change"):
                    import uuid as _uuid_pgp
                    self._store.record_peer_gateway_change_request(
                        id=str(_uuid_pgp.uuid4()),
                        peer_did=did,
                        old_gateway_url=pin["pinned_gateway_url"],
                        new_gateway_url=gateway_url,
                        observed_at=now,
                    )
                    if self._store:
                        try:
                            self._store.save_security_event(
                                "peer_gateway_change_pending",
                                "warning",
                                details=f"peer_did={did} old={pin['pinned_gateway_url']} new={gateway_url}",
                            )
                        except Exception:
                            pass
                # Update last-seen but keep pinned URL; mark pending
                self._store.upsert_peer_gateway_pin(
                    peer_did=did,
                    pinned_gateway_url=pin["pinned_gateway_url"],
                    pinned_at=pin["pinned_at"],
                    last_seen_gateway_url=gateway_url,
                    last_seen_at=now,
                    pending_change=True,
                )
                # Keep using the pinned URL for outbound relay (do NOT update in-memory or store URL)
                return
            else:
                # Same URL — update last_seen only
                self._store.upsert_peer_gateway_pin(
                    peer_did=did,
                    pinned_gateway_url=pin["pinned_gateway_url"],
                    pinned_at=pin["pinned_at"],
                    last_seen_gateway_url=gateway_url,
                    last_seen_at=now,
                    pending_change=bool(pin.get("pending_change")),
                )
                self._peer_gateway_urls[did] = gateway_url
                self._store.save_peer_gateway(did, gateway_url)
        else:
            self._peer_gateway_urls[did] = gateway_url

    # ── Pod connection ───────────────────────────────────────────────────────

    def _proxion_address(self) -> str:
        """Return this gateway's full Proxion address: did:key:...@https://gateway_url."""
        from .relay import format_proxion_address
        gateway_did = pub_key_to_did(self.agent.identity_pub_bytes)
        http_url = self._gateway_http_url()
        if not http_url:
            ws_url = self._ws_public_url()
            http_url = ws_url.replace("wss://", "https://").replace("ws://", "http://")
        return format_proxion_address(gateway_did, http_url)

    def _connect_css_sync(self, css_url: str, email: str, password: str):
        """Synchronous pod connect — runs in executor. Returns (creds, pod_url, webid)."""
        from .css_setup import CssAccountManager, build_dpop_client
        from pathlib import Path
        mgr = CssAccountManager(css_url)
        creds, pod_url, webid = mgr.connect_agent(self.agent.identity_key, email, password)
        client = build_dpop_client(creds, pod_url)
        self.dm_clients[webid] = (creds, client)
        self._pod_url = pod_url
        self._pod_webid = webid
        logger.info(f"Connected Solid Pod: {pod_url} (webid={webid})")

        if self.config.db_path:
            cred_path = Path(self.config.db_path).parent / "pod_creds.json"
            plaintext = json.dumps({
                "css_url": css_url,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "pod_url": pod_url,
                "webid": webid,
            }).encode()
            enc_token = _encrypt_creds(self.agent.identity_key, plaintext)
            cred_path.write_text(json.dumps({"v": 2, "enc": enc_token}))
            import stat as _stat
            try:
                cred_path.chmod(_stat.S_IRUSR | _stat.S_IWUSR)
            except OSError:
                pass  # Windows — chmod not supported
            self._rehydrate_relationships(client)

        return creds, pod_url, webid

    def _reconnect_stored_pod_sync(self) -> Optional[tuple]:
        """Load saved pod credentials from disk and reconnect without password."""
        if not self.config.db_path:
            return None
        from pathlib import Path
        cred_path = Path(self.config.db_path).parent / "pod_creds.json"
        if not cred_path.exists():
            return None
        raw = json.loads(cred_path.read_text())
        if raw.get("v") == 2:
            data = json.loads(_decrypt_creds(self.agent.identity_key, raw["enc"]))
        else:
            data = raw  # v1 plaintext — migrate immediately
            _v1_keys = ("css_url", "client_id", "client_secret", "pod_url", "webid")
            plaintext = json.dumps({k: data[k] for k in _v1_keys if k in data}).encode()
            enc_token = _encrypt_creds(self.agent.identity_key, plaintext)
            cred_path.write_text(json.dumps({"v": 2, "enc": enc_token}))
            import stat as _stat
            try:
                cred_path.chmod(_stat.S_IRUSR | _stat.S_IWUSR)
            except OSError:
                pass
            logger.info("Migrated pod_creds.json v1→v2 (encrypted)")
        from .css_setup import build_dpop_client
        from .css_auth import CssClientCredentials
        creds = CssClientCredentials(
            css_base_url=data["css_url"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            identity_key=self.agent.identity_key,
        )
        pod_url = data["pod_url"]
        webid = data["webid"]
        client = build_dpop_client(creds, pod_url)
        self.dm_clients[webid] = (creds, client)
        self._pod_url = pod_url
        self._pod_webid = webid
        logger.info(f"Auto-reconnected pod from stored credentials: {pod_url}")
        self._rehydrate_relationships(client)
        return creds, pod_url, webid

    async def _setup_pod_connection(self) -> None:
        """If CSS credentials are configured, connect to the Solid Pod at startup."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._reconnect_stored_pod_sync
            )
            if result:
                await self._ensure_pod_room_containers()
                return
        except Exception as exc:
            logger.warning(f"Stored pod reconnect failed: {exc}")
            if self.config.db_path:
                from pathlib import Path
                cred_path = Path(self.config.db_path).parent / "pod_creds.json"
                cred_path.unlink(missing_ok=True)

        if not (self.config.css_url and self.config.css_email and self.config.css_password):
            return
        logger.info(f"Connecting to Solid Pod at {self.config.css_url} ...")
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._connect_css_sync,
                self.config.css_url,
                self.config.css_email,
                self.config.css_password,
            )
            logger.info("Solid Pod connection established.")
            await self._ensure_pod_room_containers()
        except Exception as exc:
            logger.warning(f"Could not connect to Solid Pod at startup: {exc}")

        self._pod_available = bool(self._pod_url)
        try:
            from .solid_migration import migration_store, current_auth_mode
            migration_store.set_auth_mode(current_auth_mode())
        except Exception:
            pass

    async def _pod_health_check(self):
        """Lightweight liveness check: HEAD on pod root.

        Returns
        -------
        True
            Pod is reachable and responding normally.
        "auth_expired"
            Pod returned 401 or 403 — credentials need refresh.
        False
            Pod is unreachable or returned a 5xx error.
        """
        if not self._pod_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.head(self._pod_url)
                if r.status_code in (401, 403):
                    return "auth_expired"
                return r.status_code < 500
        except Exception:
            return False

    async def _pod_watchdog(self) -> None:
        """Background task: monitors pod reachability and reconnects on failure."""
        if not self._pod_url and not (
            self.config.css_url and self.config.css_email and self.config.css_password
        ):
            return

        backoff = 60.0
        MAX_BACKOFF = 300.0

        while True:
            await asyncio.sleep(backoff)

            health = await self._pod_health_check()
            if health is True:
                if not self._pod_available:
                    self._pod_available = True
                    logger.info("Pod reconnected — broadcasting pod_status available=true")
                    await self.broadcast({"type": "pod_status", "available": True})
                backoff = 60.0
                continue

            # Auth expired: reconnect immediately without incrementing backoff
            if health == "auth_expired":
                logger.info("Pod credentials expired — attempting immediate re-auth …")
                try:
                    await self._setup_pod_connection()
                    if self._pod_available:
                        # R13.2: broadcast restored so client can dismiss warning
                        await self.broadcast({"type": "pod_auth_restored"})
                        await self.broadcast({"type": "pod_status", "available": True})
                    else:
                        await self.broadcast({
                            "type": "pod_auth_error",
                            "message": "Pod credentials expired — re-enter in Settings",
                        })
                except Exception as exc:
                    logger.warning(f"Pod re-auth failed: {exc}")
                    await self.broadcast({
                        "type": "pod_auth_error",
                        "message": "Pod credentials expired — re-enter in Settings",
                    })
                continue

            if self._pod_available:
                self._pod_available = False
                logger.warning("Pod unreachable — broadcasting pod_status available=false")
                await self.broadcast({"type": "pod_status", "available": False})

            logger.info(f"Attempting pod reconnect (backoff={backoff:.0f}s) …")
            try:
                await self._setup_pod_connection()
                if self._pod_available:
                    await self.broadcast({"type": "pod_status", "available": True})
                    backoff = 60.0
                    continue
            except Exception as exc:
                logger.warning(f"Pod reconnect attempt failed: {exc}")

            backoff = min(backoff * 2, MAX_BACKOFF)

    def _gateway_http_url(self) -> str:
        """Return the HTTP base URL for this gateway's /relay endpoint."""
        if self.config.http_public_url:
            return self.config.http_public_url.rstrip("/")
        if self.config.http_port:
            scheme = "https" if (self.config.ssl_certfile and self.config.ssl_keyfile) else "http"
            host = self.config.host if self.config.host != "0.0.0.0" else "127.0.0.1"
            return f"{scheme}://{host}:{self.config.http_port}"
        return ""

    async def _relay_retry_loop(self) -> None:
        """Background task: retry pending outbound relays with exponential backoff.

        R9.3.1: Cap backoff at 3600s (1 hour, was 600s/10min).
        R9.3.2: Expire messages older than 7 days.
        R9.3.3: Emit relay_expired to sender sockets on discard.
        R9.4.1: Emit relay_delivered to sender sockets on success.
        R3: Check expires_at column and drop after 10 attempts with jitter.
        """
        if not self._store:
            return
        import random as _random
        SEVEN_DAYS = 7 * 24 * 3600
        while True:
            await asyncio.sleep(30)
            pending = self._store.get_pending_relays(limit=50)
            now = time.time()
            for relay in pending:
                attempt = relay["attempt_count"]
                last = relay["last_attempt_at"] or 0.0
                created = relay.get("created_at", now)
                expires_at = relay.get("expires_at", 0)

                # R3: Check expires_at (if set and non-zero)
                if expires_at > 0 and now > expires_at:
                    self._store.mark_relay_permanently_failed(relay["id"])
                    logger.warning("Relay %s expired (expires_at check)", relay["id"])
                    try:
                        payload_dict = json.loads(relay["payload_json"])
                        peer_did_exp = payload_dict.get("to_webid", "") or ""
                        self._store.append_relay_delivery_event(relay["id"], peer_did_exp, "expired")
                        sender_webid = payload_dict.get("from_webid", "")
                        if sender_webid:
                            await self._send_to_identity(sender_webid, json.dumps({
                                "type": "relay_expired",
                                "message_id": relay["id"],
                            }))
                    except Exception:
                        pass
                    continue

                # R9.3.2: 7-day expiry
                if now - created > SEVEN_DAYS:
                    self._store.mark_relay_permanently_failed(relay["id"])
                    logger.warning("Relay %s expired after 7 days", relay["id"])
                    # R9.3.3: notify sender
                    try:
                        payload_dict = json.loads(relay["payload_json"])
                        peer_did_7d = payload_dict.get("to_webid", "") or ""
                        self._store.append_relay_delivery_event(relay["id"], peer_did_7d, "expired")
                        sender_webid = payload_dict.get("from_webid", "")
                        if sender_webid:
                            await self._send_to_identity(sender_webid, json.dumps({
                                "type": "relay_expired",
                                "message_id": relay["id"],
                            }))
                    except Exception:
                        pass
                    continue

                # R9.3.1: exponential backoff, cap at 3600s (1 hour), add jitter
                next_delay = min(30 * (2 ** attempt), 3600)
                next_delay *= _random.uniform(0.75, 1.25)
                if (now - last) < next_delay:
                    continue

                try:
                    payload = json.loads(relay["payload_json"])
                    relay_url = relay["to_gateway_url"].rstrip("/") + "/relay"
                    from .relay import post_relay as _post_relay
                    delivered = await _post_relay(relay_url, payload)
                except Exception:
                    delivered = False
                    payload = {}

                _peer_did_chain = payload.get("to_webid", "") or ""
                if delivered:
                    self._store.mark_relay_delivered(relay["id"])
                    try:
                        self._store.append_relay_delivery_event(relay["id"], _peer_did_chain, "delivered")
                    except Exception:
                        pass
                    logger.info("Pending relay %s delivered on retry", relay["id"])
                    # R9.4.1: notify sender of delivery
                    try:
                        sender_webid = payload.get("from_webid", "")
                        if sender_webid:
                            await self._send_to_identity(sender_webid, json.dumps({
                                "type": "relay_delivered",
                                "message_id": relay["id"],
                            }))
                    except Exception:
                        pass
                elif attempt >= 9:
                    self._store.mark_relay_permanently_failed(relay["id"])
                    try:
                        self._store.append_relay_delivery_event(relay["id"], _peer_did_chain, "failed")
                    except Exception:
                        pass
                    logger.warning("Pending relay %s permanently failed after 10 attempts", relay["id"])
                else:
                    try:
                        self._store.append_relay_delivery_event(relay["id"], _peer_did_chain, "failed")
                    except Exception:
                        pass
                    self._store.increment_relay_attempt(relay["id"])

    # ── Polling ──────────────────────────────────────────────────────────────

    async def trigger_poll(self):
        """Trigger an immediate poll (used by push notifications)."""
        await self.do_poll()

    async def do_poll(self):
        """Internal polling logic."""
        try:
            from .federation import RelationshipCertificate
            rel_pairs = [
                (cert, client)
                for cert, client in self.dm_clients.values()
                if isinstance(cert, RelationshipCertificate)
            ]
            new_entries = poll_inbox(
                self.agent,
                rel_pairs,
                list(self.room_memberships.values()),
                since=self.read_state.get_last_poll_time() if self.read_state else None
            )

            known_names = {}
            for webid, info in self.identity_cache.items():
                if "display_name" in info:
                    known_names[info["display_name"]] = webid

            for entry in new_entries:
                msg_id = entry.message.message_id
                if self.blocklist.is_blocked(entry.message.from_pub_hex):
                    continue

                if self.read_state and self.read_state.is_seen(msg_id):
                    continue
                if self.read_state:
                    self.read_state.mark_seen(msg_id)

                self.message_cache.append(entry)  # deque(maxlen=2000) auto-evicts oldest

                if self._store and getattr(entry, "source", None) == "dm":
                    try:
                        ts_iso = datetime.fromtimestamp(entry.message.timestamp, tz=timezone.utc).isoformat()
                        thread_id = getattr(entry.cert, "certificate_id", None) or msg_id
                        self._store.save_message(
                            message_id=msg_id,
                            thread_id=thread_id,
                            thread_type="dm",
                            from_webid=entry.message.from_pub_hex,
                            from_display_name=None,
                            content=entry.message.content,
                            timestamp=ts_iso,
                            seq_num=int(getattr(entry.message, "seq_num", None) or 0),
                            prev_hash=str(getattr(entry.message, "prev_hash", None) or ""),
                        )
                    except Exception as exc:
                        logger.debug(f"Could not persist pod DM message: {exc}")

                await self.broadcast(self._entry_to_event(entry, entry.source, known_names))

                from .linkpreview import extract_urls
                if self._link_previews_enabled and extract_urls(entry.message.content):
                    asyncio.create_task(self.process_link_previews(entry.message.content, entry.source, entry.message.message_id))

            if new_entries and self.read_state:
                latest_ts = max(e.message.timestamp for e in new_entries)
                self.read_state.set_last_poll_time(datetime.fromtimestamp(latest_ts, tz=timezone.utc))

            await self._poll_handshake_completions()
        except Exception as exc:
            logger.error(f"Error in do_poll: {exc}")

    def _get_store(self):
        """Return SolidStore if a pod client is available, else a no-op MemoryStore."""
        if self.dm_clients:
            _, pod_client = next(iter(self.dm_clients.values()))
            from .solid_store import SolidStore
            return SolidStore(pod_client)
        from .store import MemoryStore
        return MemoryStore()

    async def _poll_handshake_completions(self):
        """Check for inbound acceptances and certificates; finalize if found."""
        if not self._store:
            return
        store = self._get_store()

        try:
            from . import handshake
            from .federation import FederationInvite

            acceptances = handshake.receive_acceptances(self.agent.store_key, store)
            for acceptance, valid in acceptances:
                if not valid:
                    continue
                invite_dict = self._store.get_pending_invite(acceptance.invitation_id)
                if not invite_dict:
                    continue
                try:
                    original_invite = FederationInvite.from_dict(invite_dict)
                except Exception:
                    continue
                try:
                    cert = handshake.finalize_handshake(
                        acceptance, original_invite, self.agent.identity_key
                    )
                except Exception:
                    continue
                try:
                    peer_store_pub = bytes.fromhex(acceptance.responder["store_key"])
                    handshake.send_certificate(cert, peer_store_pub, store)
                except Exception:
                    pass
                try:
                    peer_did = pub_key_to_did(bytes.fromhex(cert.subject))
                    self._store.save_relationship(cert.to_dict(), peer_did=peer_did)
                    self._store.mark_invite_status(acceptance.invitation_id, "finalized")
                except Exception:
                    pass
                try:
                    pod_client = None
                    if self._pod_webid and self._pod_webid in self.dm_clients:
                        _, pod_client = self.dm_clients[self._pod_webid]
                    if pod_client and cert.certificate_id:
                        self.dm_clients[cert.certificate_id] = (cert, pod_client)
                except Exception as exc:
                    logger.warning(f"Could not wire outbound cert into dm_clients: {exc}")
                await self.broadcast({
                    "type": "relationship_established",
                    "certificate_id": cert.certificate_id,
                    "peer_did": peer_did if 'peer_did' in locals() else None,
                })

            certs = handshake.receive_certificates(self.agent.store_key, store)
            for cert, valid in certs:
                if not valid:
                    continue
                try:
                    peer_did = pub_key_to_did(bytes.fromhex(cert.issuer))
                    self._store.save_relationship(cert.to_dict(), peer_did=peer_did)
                    try:
                        pod_client = None
                        if self._pod_webid and self._pod_webid in self.dm_clients:
                            _, pod_client = self.dm_clients[self._pod_webid]
                        if pod_client and cert.certificate_id:
                            self.dm_clients[cert.certificate_id] = (cert, pod_client)
                    except Exception as exc:
                        logger.warning(f"Could not wire inbound cert into dm_clients: {exc}")
                    await self.broadcast({
                        "type": "relationship_established",
                        "certificate_id": cert.certificate_id,
                        "peer_did": peer_did,
                    })
                except Exception as exc:
                    logger.warning(f"Failed to process inbound certificate: {exc}")

            if self._store:
                warn_threshold = int(time.time()) + 7 * 86400
                for cert_dict in self._store.list_relationships():
                    expires_at = cert_dict.get("expires_at", 0)
                    if 0 < expires_at < warn_threshold:
                        await self.broadcast({
                            "type": "cert_expiring_soon",
                            "certificate_id": cert_dict.get("certificate_id") or cert_dict.get("id"),
                            "peer_did": cert_dict.get("peer_did"),
                            "expires_at": expires_at,
                        })
        except Exception as exc:
            logger.debug(f"Error in _poll_handshake_completions: {exc}")

    async def poll_loop(self):
        """Standard polling loop with exponential backoff on persistent errors.

        Respects ``PROXION_SOLID_NOTIFS_MODE``:
        - ``sdk``: SDK-based push notifications (not yet fully implemented;
          falls back to polling and records the fallback reason).
        - ``auto`` (default): try SDK, fall back to polling on error.
        - ``legacy``: polling only.

        Skips the full poll when no WebSocket clients are connected, sleeping
        for 1800 s instead of the normal interval to avoid unnecessary network
        traffic for offline/stale users.
        """
        import os as _os_nm
        notifs_mode = _os_nm.environ.get("PROXION_SOLID_NOTIFS_MODE", "auto")
        if notifs_mode == "sdk":
            try:
                from .solid_migration import migration_store
                migration_store.record_notifs_fallback("sdk_not_implemented")
            except Exception:
                pass
            logger.warning(
                "PROXION_SOLID_NOTIFS_MODE=sdk requested but SDK notifications not fully "
                "implemented; falling back to polling (SOLID_NOT_SUPPORTED)"
            )

        backoff = self.config.poll_interval
        max_backoff = 120.0
        _OFFLINE_INTERVAL = 1800.0
        while not self._stop_event.is_set():
            if not self.clients:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=_OFFLINE_INTERVAL)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self.do_poll()
                backoff = self.config.poll_interval
            except Exception as exc:
                logger.warning(f"poll_loop unhandled error: {exc}")
                backoff = min(backoff * 2, max_backoff)
            await asyncio.sleep(backoff)

    async def flush_loop(self):
        """Periodically try to send queued messages."""
        while not self._stop_event.is_set():
            items = self.outbox.get_items()
            if items:
                logger.info(f"Flush loop: {len(items)} items pending")
                for item in items:
                    client = None
                    if item.target_cert_id and item.target_cert_id in self.dm_clients:
                        _, client = self.dm_clients[item.target_cert_id]
                    elif item.room_id and item.room_id in self.room_memberships:
                        _, client = self.room_memberships[item.room_id]

                    if client:
                        try:
                            from .messaging import send as msend
                            msend(item.message, client)
                            self.outbox.remove(item.item_id)
                            logger.info(f"Flushed message {item.item_id}")
                        except Exception as e:
                            logger.debug(f"Flush failed for {item.item_id}: {e}")

            await asyncio.sleep(60.0)

    # ── Pod room helpers ─────────────────────────────────────────────────────

    async def _backfill_rooms_from_pod(self, room_ids: list) -> None:
        """Pull pod room history into SQLite cache for a list of room_ids."""
        client = self._pod_client()
        if not client or not self._store:
            return
        from .pod_room_store import PodRoomStore
        store = PodRoomStore(client)
        loop = asyncio.get_event_loop()
        for room_id in room_ids:
            # R13: retry logic with 3 attempts and 2s backoff
            for _attempt in range(3):
                try:
                    msgs = await loop.run_in_executor(None, store.read_messages, room_id)
                    for m in msgs:
                        message_id = m["message_id"]
                        # R13: deduplication — skip if already exists
                        if self._store.get_message(message_id):
                            continue
                        try:
                            self._store.save_message(
                                message_id, room_id, "room",
                                m.get("from_webid", ""), m.get("from_display_name"),
                                m.get("content", ""), m.get("timestamp", ""),
                                reply_to_id=m.get("reply_to_id"),
                                seq_num=int(m.get("seq_num") or 0),
                                prev_hash=str(m.get("prev_hash") or ""),
                            )
                        except Exception:
                            pass
                    break
                except Exception as exc:
                    if _attempt == 2:
                        logger.warning("_backfill_rooms_from_pod failed after 3 attempts: %s", exc)
                    else:
                        await asyncio.sleep(2)

    async def _ensure_pod_room_containers(self) -> None:
        """Create pod room.json for any local rooms not yet on the pod."""
        client = self._pod_client()
        if not client or not self._local_rooms:
            return
        from .pod_room_store import PodRoomStore
        store = PodRoomStore(client)
        loop = asyncio.get_event_loop()
        try:
            existing = await loop.run_in_executor(None, store.list_room_ids)
        except Exception:
            existing = []
        for room_id, room in list(self._local_rooms.items()):
            if room_id not in existing:
                asyncio.create_task(self._init_room_on_pod(
                    room_id,
                    room["name"],
                    room.get("creator_webid", ""),
                    code=room.get("code", ""),
                    history_mode=room.get("history_mode", "none"),
                ))

    async def _sync_message_to_pod(
        self,
        pod_client,
        cert_dict: dict,
        content: str,
        message_id: str,
        from_webid: str,
    ) -> None:
        """Write a relay message to the pod for durability. Errors are swallowed."""
        async with self._pod_sync_sem:
            try:
                from .federation import RelationshipCertificate
                from .messaging import send_message
                cert = RelationshipCertificate.from_dict(cert_dict)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    send_message,
                    self.agent.identity_key,
                    cert,
                    content,
                    pod_client,
                )
            except Exception as exc:
                logger.warning("pod write-through failed: %s", exc)

    # ── Inbox event conversion ───────────────────────────────────────────────

    def _entry_to_event(self, entry: InboxEntry, source: str, known_names: Optional[dict] = None) -> dict:
        """Convert InboxEntry to a broadcastable JSON event."""
        content = entry.message.content

        if is_encrypted(content):
            try:
                key = derive_message_key(entry.cert)
                content = decrypt_message(content, key)
            except Exception as exc:
                logger.warning(f"Failed to decrypt message {entry.message.message_id}: {exc}")
                content = "[Decryption Failed]"

        try:
            payload = json.loads(content)
            if isinstance(payload, dict) and "type" in payload:
                if payload["type"] in ["voice_invite", "voice_answer", "ice_candidate", "voice_hangup"]:
                    payload["source"] = source
                    payload["from_webid"] = entry.message.from_pub_hex
                    return payload

            if entry.message.message_type == "reaction":
                emoji = payload.get("emoji")
                target = payload.get("target")
                if emoji and target:
                    return {
                        "type": "reaction_added",
                        "thread_id": entry.thread_id if hasattr(entry, "thread_id") else "unknown",
                        "message_id": target,
                        "emoji": emoji,
                        "from_webid": entry.message.from_pub_hex,
                        "reaction_message_id": entry.message.message_id
                    }
        except Exception:
            pass

        info = self.identity_cache.get(entry.message.from_pub_hex, {
            "display_name": entry.message.from_pub_hex[:12],
            "avatar_b64": None
        })

        mentions = []
        if known_names:
            mentions = extract_mentions(content, known_names)

        return {
            "type": "message",
            "source": source,
            "thread_id": entry.thread_id if hasattr(entry, "thread_id") else "unknown",
            "from_webid": entry.message.from_pub_hex,
            "from_display_name": info.get("display_name"),
            "from_avatar_b64": info.get("avatar_b64"),
            "content": content,
            "timestamp": datetime.fromtimestamp(entry.message.timestamp, tz=timezone.utc).isoformat(),
            "message_id": entry.message.message_id,
            "reply_to_id": entry.message.reply_to_id,
            "mentions": mentions
        }
