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

    async def _fetch_remote_device_keys(self, peer_did: str) -> list:
        """Fetch a REMOTE peer's device E2E roster from their gateway's signed,
        relationship-gated POST /devices (cross-gateway multi-device fanout).

        Returns [{device_id, pub_b64u}] or [] on any failure. Results are cached
        60s in memory so a DM open doesn't hammer the peer gateway.
        """
        peer_gw = self._resolve_peer_gateway(peer_did)
        if not peer_gw:
            return []
        cache = getattr(self, "_remote_device_cache", None)
        if cache is None:
            cache = self._remote_device_cache = {}
        hit = cache.get(peer_did)
        now = time.monotonic()
        if hit and now - hit[1] < 60:
            return hit[0]
        try:
            import base64 as _b64
            import secrets as _sec
            from datetime import datetime as _dt, timezone as _tz
            from .didkey import pub_key_to_did as _p2d
            from .network import async_safe_post_content as _post
            own_did = _p2d(self.agent.identity_pub_bytes)
            ts = _dt.now(_tz.utc).isoformat()
            nonce = _sec.token_hex(8)
            sig = _b64.urlsafe_b64encode(
                self.agent.identity_key.sign(f"{own_did}|{peer_did}|{ts}|{nonce}".encode())
            ).rstrip(b"=").decode()
            url = (peer_gw.replace("wss://", "https://").replace("ws://", "http://")
                   .rstrip("/") + "/devices")
            raw = await _post(url, {
                "requester_did": own_did, "target_did": peer_did,
                "ts": ts, "nonce": nonce, "signature": sig,
            }, timeout=8.0)
            devices = json.loads(raw).get("devices") or []
            devices = [
                {"device_id": d.get("device_id", ""), "pub_b64u": d.get("pub_b64u", "")}
                for d in devices[:16]
                if isinstance(d, dict) and d.get("device_id") and d.get("pub_b64u")
            ]
            cache[peer_did] = (devices, now)
            return devices
        except Exception as exc:
            logger.debug("remote device-key fetch failed for %s: %s", peer_did[:24], exc)
            cache[peer_did] = ([], now)  # negative-cache failures too
            return []

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
                asyncio.create_task(self._sync_peer_gateway_to_pod(did, gateway_url))
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
                asyncio.create_task(self._sync_peer_gateway_to_pod(did, gateway_url))
        else:
            self._peer_gateway_urls[did] = gateway_url

    def _resolve_peer_x25519_pub(self, did: str) -> Optional[str]:
        """Return the known X25519 public key (base64url) for a peer DID, or None."""
        if self._store:
            return self._store.get_x25519_pub(did)
        return None

    async def _discover_peer_gateway(self, address: str) -> Optional[dict]:
        """Fetch /.well-known/proxion from a peer gateway and cache the result.

        Accepts 'did:key:z6Mk...@https://gateway.example.com' or a bare gateway URL.
        Returns the parsed .well-known JSON dict, or None on any error.
        """
        import urllib.request as _urlreq
        from .relay import parse_proxion_address

        peer_did: Optional[str] = None
        gateway_url: Optional[str] = None
        try:
            peer_did, gateway_url = parse_proxion_address(address)
        except Exception:
            if address.startswith(("http://", "https://")):
                gateway_url = address
            else:
                return None

        if not gateway_url:
            return None

        gateway_http = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
        well_known_url = gateway_http.rstrip("/") + "/.well-known/proxion"

        try:
            loop = asyncio.get_event_loop()

            def _fetch() -> Optional[dict]:
                req = _urlreq.Request(
                    well_known_url, headers={"Accept": "application/json", "User-Agent": "Proxion/0.1"}
                )
                with _urlreq.urlopen(req, timeout=5) as resp:
                    if resp.getcode() != 200:
                        return None
                    raw = resp.read(65536)
                    return json.loads(raw)

            result = await loop.run_in_executor(None, _fetch)
        except Exception as exc:
            logger.debug("_discover_peer_gateway %s: %s", well_known_url, exc)
            return None

        if not isinstance(result, dict):
            return None

        discovered_did = result.get("did", "")
        discovered_http = result.get("gateway_http_url", "")

        if not discovered_did or not discovered_http:
            return None

        # Fingerprint check: DID in address must match DID in response
        if peer_did and peer_did != discovered_did:
            logger.warning("_discover_peer_gateway: DID mismatch expected=%s got=%s", peer_did, discovered_did)
            return None

        # Cache the gateway URL
        self._record_peer_gateway(discovered_did, discovered_http)

        # Cache x25519 pub for sealed relay
        if "x25519_pub" in result and self._store:
            self._store.save_x25519_pub(discovered_did, result["x25519_pub"])

        return result

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
                await self._restore_relationships_from_pod()
                await self._restore_dms_from_pod()
                await self._restore_local_dms_from_pod()
                await self._restore_read_positions_from_pod()
                await self._restore_verifications_from_pod()
                await self._restore_peer_gateways_from_pod()
                await self._restore_rooms_from_pod()
                await self._ensure_pod_room_containers()
                await self._restore_e2e_sessions_from_pod()
                await self._restore_sender_keys_from_pod()
                await self._restore_devices_from_pod()
                await self._restore_push_subscriptions_from_pod()
                asyncio.create_task(self._run_pod_backfill())
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
            await self._restore_relationships_from_pod()
            await self._restore_dms_from_pod()
            await self._restore_local_dms_from_pod()
            await self._restore_read_positions_from_pod()
            await self._restore_verifications_from_pod()
            await self._restore_peer_gateways_from_pod()
            await self._restore_rooms_from_pod()
            await self._ensure_pod_room_containers()
            await self._restore_e2e_sessions_from_pod()
            await self._restore_sender_keys_from_pod()
            await self._restore_devices_from_pod()
            await self._restore_push_subscriptions_from_pod()
            asyncio.create_task(self._run_pod_backfill())
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

                # R38: if direct delivery failed, try the sealed relay-node mailbox
                # (works when the recipient gateway is unreachable / behind CGNAT).
                if not delivered and _peer_did_chain:
                    try:
                        from ._gateway_mailbox import relay_fallback_url as _rfu
                        if _rfu() and hasattr(self, "_send_via_mailbox"):
                            if await self._send_via_mailbox(_peer_did_chain, payload):
                                delivered = True
                                logger.info("Pending relay %s delivered via mailbox fallback", relay["id"])
                    except Exception as _mb_exc:
                        logger.debug("mailbox fallback failed: %s", _mb_exc)

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

    def _pod_entry_recipients(self, entry) -> list:
        """The local identities a polled pod entry belongs to.

        dm  -> the account owning the relationship cert (R53's owner column).
        room-> the room's member identities (offline ones are a no-op send;
               remote members are served by their own gateways' polls).
        []  -> caller falls back to broadcast — single-user-safe (older
               relationship rows lack an owner), and the one case where
               broadcast == correct delivery.
        """
        try:
            if not self._store:
                return []
            source = getattr(entry, "source", "")
            if source == "dm":
                cert_id = getattr(entry.cert, "certificate_id", "") or ""
                owner = self._store.get_relationship_owner_by_cert_id(cert_id) if cert_id else ""
                return [owner] if owner else []
            if source == "room":
                room_id = getattr(entry, "thread_id", "") or ""
                return self._store.get_room_members(room_id) if room_id else []
        except Exception:
            pass
        return []

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

                # Scope polled entries to their participants (a DM's owner, a
                # room's members) instead of every session on the gateway; fall
                # back to broadcast only when the entry can't be attributed
                # (older ownerless relationship rows — single-user-safe).
                _event = self._entry_to_event(entry, entry.source, known_names)
                _recips = self._pod_entry_recipients(entry)
                if _recips:
                    _payload = json.dumps(_event)
                    for _r in set(_recips):
                        await self._send_to_identity(_r, _payload)
                else:
                    await self.broadcast(_event)

                from .linkpreview import extract_urls
                if self._link_previews_enabled and extract_urls(entry.message.content):
                    asyncio.create_task(self.process_link_previews(
                        entry.message.content, entry.source,
                        entry.message.message_id, recipients=_recips))

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
                    asyncio.create_task(self._sync_cert_to_pod(cert.to_dict()))
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
                    asyncio.create_task(self._sync_cert_to_pod(cert.to_dict()))
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

    async def _restore_relationships_from_pod(self) -> None:
        """Pull relationship certs from pod into SQLite and dm_clients.

        Pod path: stash://pod/relationships/{cert_id}.json
        Ensures the contact graph survives a SQLite cold start or DID rotation.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        container = "stash://pod/relationships/"
        try:
            member_uris = await loop.run_in_executor(None, client.list, container)
        except Exception as exc:
            logger.debug("_restore_relationships_from_pod: list failed: %s", exc)
            return

        known_ids = {r.get("certificate_id") for r in self._store.list_relationships()}
        restored = 0
        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                cert_dict = json.loads(raw.decode("utf-8"))
                cert_id = cert_dict.get("certificate_id")
                if not cert_id or cert_id in known_ids:
                    continue
                from .federation import RelationshipCertificate as _RC
                cert = _RC.from_dict(cert_dict)
                peer_pub = cert_dict.get("subject") or cert_dict.get("issuer", "")
                peer_did = None
                if peer_pub:
                    peer_did = pub_key_to_did(bytes.fromhex(peer_pub))
                self._store.save_relationship(cert_dict, peer_did=peer_did)
                if cert_id not in self.dm_clients:
                    self.dm_clients[cert_id] = (cert, client)
                known_ids.add(cert_id)
                restored += 1
            except Exception as exc:
                logger.debug("_restore_relationships_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_relationships_from_pod: restored %d cert(s) from pod", restored)

    async def _restore_dms_from_pod(self) -> None:
        """Pull DM message history from the pod into SQLite for all known relationships.

        Calls messaging.receive() for each RelationshipCertificate in dm_clients and
        saves any messages not yet in SQLite. This is the pod→SQLite mirror step for DMs.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        from .messaging import receive as _msg_receive
        from .federation import RelationshipCertificate as _RC2
        loop = asyncio.get_event_loop()

        certs = [
            cert for cert, _ in self.dm_clients.values()
            if isinstance(cert, _RC2)
        ]
        if not certs:
            return

        restored = 0
        for cert in certs:
            try:
                messages = await loop.run_in_executor(None, _msg_receive, cert, client)
                for msg in messages:
                    if self._store.get_message(msg.message_id):
                        continue
                    ts_iso = datetime.fromtimestamp(msg.timestamp, tz=timezone.utc).isoformat()
                    try:
                        self._store.save_message(
                            message_id=msg.message_id,
                            thread_id=cert.certificate_id,
                            thread_type="dm",
                            from_webid=msg.from_pub_hex,
                            from_display_name=None,
                            content=msg.content,
                            timestamp=ts_iso,
                            seq_num=int(msg.seq_num or 0),
                            prev_hash=str(msg.prev_hash or ""),
                        )
                        restored += 1
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("_restore_dms_from_pod: failed for cert %s: %s",
                             cert.certificate_id[:8], exc)

        if restored:
            logger.info("_restore_dms_from_pod: restored %d DM message(s) from pod", restored)

    async def _sync_cert_to_pod(self, cert_dict: dict) -> None:
        """Write a relationship cert to the pod for durability.

        Pod path: stash://pod/relationships/{cert_id}.json
        Call fire-and-forget via asyncio.create_task() alongside save_relationship().
        """
        client = self._pod_client()
        if not client:
            return
        cert_id = cert_dict.get("certificate_id")
        if not cert_id:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                uri = f"stash://pod/relationships/{cert_id}.json"
                data = json.dumps(cert_dict).encode("utf-8")
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_cert_to_pod failed for %s: %s", cert_id[:8], exc)

    async def _sync_profile_to_pod(
        self, identity: str, display_name: str = "", x25519_pub: str = ""
    ) -> None:
        """Write the operator's own profile to the pod.

        Pod path: stash://pod/profile/me.json
        Provides a pod-durable record of identity and display name that survives
        SQLite cold starts. Called fire-and-forget after register.
        """
        client = self._pod_client()
        if not client:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                profile: dict = {"identity": identity,
                                 "updated_at": datetime.now(timezone.utc).isoformat()}
                if display_name:
                    profile["display_name"] = display_name
                if x25519_pub:
                    profile["x25519_pub"] = x25519_pub
                data = json.dumps(profile).encode("utf-8")
                await loop.run_in_executor(
                    None, lambda: client.put(
                        "stash://pod/profile/me.json", data, content_type="application/json"
                    )
                )
            except Exception as exc:
                logger.debug("_sync_profile_to_pod failed: %s", exc)

    async def _sync_local_dm_to_pod(self, thread_id: str, message: dict) -> None:
        """Write a gateway-relayed (non-federated) DM message to the pod.

        Pod path: stash://pod/local_dms/{thread_key}/{message_id}.json
        thread_key is sha256(thread_id)[:16] to ensure path-safety.
        """
        client = self._pod_client()
        if not client:
            return
        message_id = message.get("message_id", "")
        if not message_id:
            return
        import hashlib as _hl
        thread_key = _hl.sha256(thread_id.encode()).hexdigest()[:16]
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                payload = json.dumps({**message, "thread_id": thread_id}).encode("utf-8")
                uri = f"stash://pod/local_dms/{thread_key}/{message_id}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, payload, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_local_dm_to_pod failed [%s]: %s", thread_id[:20], exc)

    async def _restore_local_dms_from_pod(self) -> None:
        """Pull gateway-relayed DM messages from pod into SQLite on cold start.

        Pod path: stash://pod/local_dms/{thread_key}/
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            thread_uris = await loop.run_in_executor(None, client.list, "stash://pod/local_dms/")
        except Exception as exc:
            logger.debug("_restore_local_dms_from_pod: list failed: %s", exc)
            return

        restored = 0
        for thread_uri in thread_uris:
            try:
                msg_uris = await loop.run_in_executor(None, client.list, thread_uri)
            except Exception:
                continue
            for uri in msg_uris:
                if not uri.endswith(".json"):
                    continue
                try:
                    raw = await loop.run_in_executor(None, client.get, uri)
                    msg = json.loads(raw.decode("utf-8"))
                    message_id = msg.get("message_id", "")
                    thread_id = msg.get("thread_id", "")
                    if not message_id or not thread_id:
                        continue
                    if self._store.get_message(message_id):
                        continue
                    self._store.save_message(
                        message_id=message_id,
                        thread_id=thread_id,
                        thread_type="dm",
                        from_webid=msg.get("from_webid", ""),
                        from_display_name=msg.get("from_display_name"),
                        content=msg.get("content", ""),
                        timestamp=msg.get("timestamp", ""),
                        reply_to_id=msg.get("reply_to_id"),
                        seq_num=int(msg.get("seq_num") or 0),
                        prev_hash=str(msg.get("prev_hash") or ""),
                    )
                    restored += 1
                except Exception as exc:
                    logger.debug("_restore_local_dms_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_local_dms_from_pod: restored %d local DM message(s) from pod", restored)

    async def _read_position_flush_loop(self) -> None:
        """Background task: flush dirty read positions to pod every 30 s."""
        while True:
            await asyncio.sleep(30)
            if not getattr(self, "_dirty_read_positions", None):
                continue
            try:
                await self._sync_read_positions_to_pod()
            except Exception as exc:
                logger.debug("_read_position_flush_loop error: %s", exc)

    async def _sync_read_positions_to_pod(self) -> None:
        """Flush all dirty read positions to pod as a single JSON document.

        Pod path: stash://pod/read_positions.json
        Format: { webid: { channel_id: ts, ... }, ... }
        """
        dirty = getattr(self, "_dirty_read_positions", {})
        if not dirty:
            return
        client = self._pod_client()
        if not client:
            return
        # Snapshot + clear before the await so new dirty entries aren't lost
        snapshot = dict(dirty)
        dirty.clear()
        merged: dict = {}
        for (webid, channel_id), ts in snapshot.items():
            merged.setdefault(webid, {})[channel_id] = ts
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                data = json.dumps(merged).encode("utf-8")
                await loop.run_in_executor(
                    None, lambda: client.put(
                        "stash://pod/read_positions.json", data, content_type="application/json"
                    )
                )
            except Exception as exc:
                # Restore dirty entries on failure so they're retried next cycle
                dirty.update(snapshot)
                logger.debug("_sync_read_positions_to_pod failed: %s", exc)

    async def _restore_read_positions_from_pod(self) -> None:
        """Pull last-read timestamps from pod into SQLite on cold start.

        Pod path: stash://pod/read_positions.json
        Only imports entries newer than what SQLite already has.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None, client.get, "stash://pod/read_positions.json"
            )
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.debug("_restore_read_positions_from_pod: %s", exc)
            return

        restored = 0
        for webid, channels in data.items():
            if not isinstance(channels, dict):
                continue
            for channel_id, ts in channels.items():
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    continue
                current = self._store.get_last_read(webid, channel_id)
                if ts > current:
                    self._store.set_last_read_ts(webid, channel_id, ts)
                    restored += 1

        if restored:
            logger.info("_restore_read_positions_from_pod: restored %d read position(s)", restored)

    async def _sync_pin_to_pod(self, room_id: str, message_id: str, pinned_by: str,
                                content: str = "", pinned_at: float = 0.0) -> None:
        """Write a pin record to the pod.

        Pod path: stash://pod/rooms/{room_id}/pins/{message_id}.json
        """
        client = self._pod_client()
        if not client or not room_id or not message_id:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                record = {
                    "room_id": room_id,
                    "message_id": message_id,
                    "pinned_by": pinned_by,
                    "content": content,
                    "pinned_at": pinned_at or time.time(),
                }
                data = json.dumps(record).encode("utf-8")
                uri = f"stash://pod/rooms/{room_id}/pins/{message_id}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_pin_to_pod failed [%s/%s]: %s", room_id, message_id, exc)

    async def _delete_pin_from_pod(self, room_id: str, message_id: str) -> None:
        """Remove a pin record from the pod."""
        client = self._pod_client()
        if not client or not room_id or not message_id:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                uri = f"stash://pod/rooms/{room_id}/pins/{message_id}.json"
                from .solid_client import SolidError
                def _del():
                    try:
                        client.delete(uri)
                    except SolidError as e:
                        if e.status_code != 404:
                            raise
                await loop.run_in_executor(None, _del)
            except Exception as exc:
                logger.debug("_delete_pin_from_pod failed [%s/%s]: %s", room_id, message_id, exc)

    async def _restore_room_pins_from_pod(self, room_id: str) -> None:
        """Pull pin records for a room from pod into SQLite.

        Pod path: stash://pod/rooms/{room_id}/pins/
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        container = f"stash://pod/rooms/{room_id}/pins/"
        try:
            member_uris = await loop.run_in_executor(None, client.list, container)
        except Exception:
            return  # container doesn't exist yet — not an error

        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                pin = json.loads(raw.decode("utf-8"))
                message_id = pin.get("message_id", "")
                pinned_by = pin.get("pinned_by", "")
                content = pin.get("content", "")
                if not message_id:
                    continue
                # INSERT OR IGNORE — save_pin uses INSERT OR IGNORE so it's idempotent
                self._store.save_pin(room_id, message_id, pinned_by, content)
            except Exception as exc:
                logger.debug("_restore_room_pins_from_pod: failed for %s: %s", uri, exc)

    async def _sync_verification_to_pod(
        self, peer_webid: str, safety_numbers: str, verified_by: str
    ) -> None:
        """Write a contact verification record to the pod.

        Pod path: stash://pod/verifications/{peer_hash}.json
        peer_hash = sha256(peer_webid)[:24] to ensure path-safety.
        """
        client = self._pod_client()
        if not client or not peer_webid:
            return
        import hashlib as _hl
        peer_hash = _hl.sha256(peer_webid.encode()).hexdigest()[:24]
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                record = {
                    "peer_webid": peer_webid,
                    "safety_numbers": safety_numbers,
                    "verified_by": verified_by,
                    "verified_at": time.time(),
                }
                data = json.dumps(record).encode("utf-8")
                uri = f"stash://pod/verifications/{peer_hash}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_verification_to_pod failed [%s]: %s", peer_webid[:20], exc)

    async def _restore_verifications_from_pod(self) -> None:
        """Pull contact verification records from pod into SQLite on cold start.

        Pod path: stash://pod/verifications/
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            member_uris = await loop.run_in_executor(None, client.list, "stash://pod/verifications/")
        except Exception as exc:
            logger.debug("_restore_verifications_from_pod: list failed: %s", exc)
            return

        restored = 0
        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                rec = json.loads(raw.decode("utf-8"))
                peer_webid = rec.get("peer_webid", "")
                safety_numbers = rec.get("safety_numbers", "")
                verified_by = rec.get("verified_by", "")
                if not peer_webid or not safety_numbers:
                    continue
                existing = self._store.get_contact_verification(peer_webid)
                if existing:
                    continue
                self._store.save_contact_verification(peer_webid, safety_numbers, verified_by)
                restored += 1
            except Exception as exc:
                logger.debug("_restore_verifications_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_verifications_from_pod: restored %d verification(s) from pod", restored)

    async def _sync_peer_gateway_to_pod(self, did: str, gateway_url: str) -> None:
        """Write a peer gateway URL to the pod for durability.

        Pod path: stash://pod/peer_gateways/{did_hash}.json
        did_hash = sha256(did)[:24]
        """
        client = self._pod_client()
        if not client or not did:
            return
        import hashlib as _hl
        did_hash = _hl.sha256(did.encode()).hexdigest()[:24]
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                record = {"did": did, "gateway_url": gateway_url, "updated_at": time.time()}
                data = json.dumps(record).encode("utf-8")
                uri = f"stash://pod/peer_gateways/{did_hash}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_peer_gateway_to_pod failed [%s]: %s", did[:20], exc)

    async def _restore_peer_gateways_from_pod(self) -> None:
        """Pull peer gateway URLs from pod into SQLite on cold start.

        Pod path: stash://pod/peer_gateways/
        Pod values are lower-trust than existing SQLite pins — never override a pin.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            member_uris = await loop.run_in_executor(
                None, client.list, "stash://pod/peer_gateways/"
            )
        except Exception as exc:
            logger.debug("_restore_peer_gateways_from_pod: list failed: %s", exc)
            return

        restored = 0
        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                rec = json.loads(raw.decode("utf-8"))
                did = rec.get("did", "")
                gateway_url = rec.get("gateway_url", "")
                if not did or not gateway_url:
                    continue
                # Only restore if no existing pin — pod value is lower-trust
                pin = self._store.get_peer_gateway_pin(did)
                if pin is not None:
                    continue
                existing = self._store.get_peer_gateway(did)
                if existing:
                    continue
                self._store.save_peer_gateway(did, gateway_url)
                self._peer_gateway_urls[did] = gateway_url
                restored += 1
            except Exception as exc:
                logger.debug("_restore_peer_gateways_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_peer_gateways_from_pod: restored %d peer gateway URL(s) from pod", restored)

    async def _restore_rooms_from_pod(self) -> None:
        """Pull room metadata from the pod into _local_rooms and SQLite.

        The pod is the durable source of truth. This runs at startup so that
        rooms are available even when the local SQLite mirror is cold or the
        operator's DID has rotated.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        from .pod_room_store import PodRoomStore
        store = PodRoomStore(client)
        loop = asyncio.get_event_loop()
        try:
            room_ids = await loop.run_in_executor(None, store.list_room_ids)
        except Exception as exc:
            logger.warning("_restore_rooms_from_pod: failed to list rooms: %s", exc)
            return

        restored = 0
        for room_id in room_ids:
            if room_id in self._local_rooms:
                continue
            try:
                meta = await loop.run_in_executor(None, store.read_room_meta, room_id)
            except Exception as exc:
                logger.debug("_restore_rooms_from_pod: failed to read meta for %s: %s", room_id, exc)
                continue
            if not meta:
                continue
            name = meta.get("name", room_id)
            code = meta.get("code", "")
            creator_webid = meta.get("creator_webid", "")
            history_mode = meta.get("history_mode", "none")
            invite_url = meta.get("invite_url", "")
            self._store.save_room(room_id, name, code, invite_url, history_mode, creator_webid)
            self._local_rooms[room_id] = {
                "name": name,
                "code": code,
                "invite_url": invite_url,
                "creator_webid": creator_webid,
                "history_mode": history_mode,
                "members": set(),
            }
            if code:
                self._room_codes[code] = room_id
            asyncio.create_task(self._restore_room_pins_from_pod(room_id))
            restored += 1

        if restored:
            logger.info("_restore_rooms_from_pod: restored %d room(s) from pod", restored)

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

    async def _checkpoint_e2e_session(self, session_id: str) -> None:
        """Checkpoint a DM ratchet session to the pod after every 5 messages.

        Pod path: stash://pod/e2e_sessions/{session_id}.json
        Uses If-Match ETag to prevent concurrent device overwrite.
        """
        if not self._store:
            return
        sess = self._store.get_dm_session_by_id(session_id)
        if not sess:
            return
        step_count = sess.get("send_count", 0) + sess.get("recv_count", 0)
        if step_count % 5 != 0:
            return
        client = self._pod_client()
        if not client:
            return
        existing_etag = self._store.get_dm_session_checkpoint_etag(session_id)
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                payload = {
                    "session_id": sess["session_id"],
                    "owner_webid": sess["owner_webid"],
                    "peer_webid": sess["peer_webid"],
                    "root_key_b64": sess["root_key"],
                    "send_chain_key_b64": sess["send_chain_key"],
                    "recv_chain_key_b64": sess["recv_chain_key"],
                    "send_count": sess["send_count"],
                    "recv_count": sess["recv_count"],
                    "checkpoint_ts": time.time(),
                }
                data = json.dumps(payload).encode("utf-8")
                uri = f"stash://pod/e2e_sessions/{session_id}.json"
                kwargs = {"content_type": "application/json"}
                if existing_etag:
                    kwargs["etag"] = existing_etag
                response = await loop.run_in_executor(
                    None, lambda: client.put(uri, data, **kwargs)
                )
                new_etag = getattr(response, "etag", None) or ""
                if new_etag:
                    self._store.set_dm_session_checkpoint_etag(session_id, new_etag)
            except Exception as exc:
                _exc_str = str(exc)
                if "412" in _exc_str or "Precondition Failed" in _exc_str:
                    logger.debug(
                        "_checkpoint_e2e_session: 412 for %s — another device is ahead, skipping",
                        session_id[:16],
                    )
                else:
                    logger.debug("_checkpoint_e2e_session failed [%s]: %s", session_id[:16], exc)

    async def _restore_e2e_sessions_from_pod(self) -> None:
        """Pull E2E session checkpoints from pod into SQLite on cold start.

        Pod path: stash://pod/e2e_sessions/
        Pod session wins when its (send_count + recv_count) > SQLite value.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            member_uris = await loop.run_in_executor(
                None, client.list, "stash://pod/e2e_sessions/"
            )
        except Exception as exc:
            logger.debug("_restore_e2e_sessions_from_pod: list failed: %s", exc)
            return

        restored = 0
        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                pod_sess = json.loads(raw.decode("utf-8"))
                session_id = pod_sess.get("session_id", "")
                if not session_id:
                    continue
                pod_step = int(pod_sess.get("send_count", 0)) + int(pod_sess.get("recv_count", 0))
                local_sess = self._store.get_dm_session_by_id(session_id)
                local_step = 0
                if local_sess:
                    local_step = int(local_sess.get("send_count", 0)) + int(local_sess.get("recv_count", 0))
                if pod_step > local_step:
                    self._store.save_dm_session({
                        "session_id": session_id,
                        "peer_webid": pod_sess.get("peer_webid", ""),
                        "owner_webid": pod_sess.get("owner_webid", ""),
                        "root_key": pod_sess.get("root_key_b64", ""),
                        "send_chain_key": pod_sess.get("send_chain_key_b64", ""),
                        "recv_chain_key": pod_sess.get("recv_chain_key_b64", ""),
                        "send_count": pod_sess.get("send_count", 0),
                        "recv_count": pod_sess.get("recv_count", 0),
                    })
                    restored += 1
            except Exception as exc:
                logger.debug("_restore_e2e_sessions_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_e2e_sessions_from_pod: restored %d session(s) from pod", restored)

    async def _sync_sender_key_to_pod(
        self, room_id: str, sender_webid: str, chain_key_b64: str, iteration: int
    ) -> None:
        """Write a group sender key to the pod.

        Pod path: stash://pod/sender_keys/{room_key}/{sender_hash}.json
        """
        client = self._pod_client()
        if not client or not room_id or not sender_webid:
            return
        import hashlib as _hl
        room_key = _hl.sha256(room_id.encode()).hexdigest()[:16]
        sender_hash = _hl.sha256(sender_webid.encode()).hexdigest()[:16]
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                record = {
                    "room_id": room_id,
                    "sender_webid": sender_webid,
                    "chain_key_b64": chain_key_b64,
                    "iteration": iteration,
                    "updated_at": time.time(),
                }
                data = json.dumps(record).encode("utf-8")
                uri = f"stash://pod/sender_keys/{room_key}/{sender_hash}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_sender_key_to_pod failed [%s/%s]: %s", room_id[:16], sender_webid[:16], exc)

    async def _delete_sender_keys_for_room_from_pod(self, room_id: str) -> None:
        """Delete all sender key records for a room from the pod (called on rekey)."""
        client = self._pod_client()
        if not client or not room_id:
            return
        import hashlib as _hl
        room_key = _hl.sha256(room_id.encode()).hexdigest()[:16]
        container = f"stash://pod/sender_keys/{room_key}/"
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                uris = await loop.run_in_executor(None, client.list, container)
                from .solid_client import SolidError
                for uri in uris:
                    def _del(u=uri):
                        try:
                            client.delete(u)
                        except SolidError as e:
                            if e.status_code != 404:
                                raise
                    await loop.run_in_executor(None, _del)
            except Exception as exc:
                logger.debug("_delete_sender_keys_for_room_from_pod failed [%s]: %s", room_id[:16], exc)

    async def _restore_sender_keys_from_pod(self) -> None:
        """Pull group sender keys from pod into SQLite on cold start.

        Pod path: stash://pod/sender_keys/{room_key}/{sender_hash}.json
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            room_uris = await loop.run_in_executor(None, client.list, "stash://pod/sender_keys/")
        except Exception as exc:
            logger.debug("_restore_sender_keys_from_pod: list failed: %s", exc)
            return

        restored = 0
        for room_uri in room_uris:
            try:
                sender_uris = await loop.run_in_executor(None, client.list, room_uri)
            except Exception:
                continue
            for uri in sender_uris:
                if not uri.endswith(".json"):
                    continue
                try:
                    raw = await loop.run_in_executor(None, client.get, uri)
                    rec = json.loads(raw.decode("utf-8"))
                    room_id = rec.get("room_id", "")
                    sender_webid = rec.get("sender_webid", "")
                    chain_key_b64 = rec.get("chain_key_b64", "")
                    iteration = int(rec.get("iteration", 0))
                    if not room_id or not sender_webid or not chain_key_b64:
                        continue
                    existing = self._store.get_sender_key(room_id, sender_webid)
                    if existing:
                        continue
                    self._store.save_sender_key(room_id, sender_webid, chain_key_b64, iteration)
                    restored += 1
                except Exception as exc:
                    logger.debug("_restore_sender_keys_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_sender_keys_from_pod: restored %d sender key(s) from pod", restored)

    async def _sync_device_to_pod(
        self, device_id: str, owner_webid: str, device_pub_b64: str,
        attestation_b64: str, is_primary: bool = False
    ) -> None:
        """Write a device registration to the pod.

        Pod path: stash://pod/devices/{device_id}.json
        """
        client = self._pod_client()
        if not client or not device_id or not owner_webid:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                record = {
                    "device_id": device_id,
                    "owner_webid": owner_webid,
                    "device_pub_b64": device_pub_b64,
                    "attestation_b64": attestation_b64,
                    "is_primary": is_primary,
                    "registered_at": time.time(),
                }
                data = json.dumps(record).encode("utf-8")
                uri = f"stash://pod/devices/{device_id}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_device_to_pod failed [%s]: %s", device_id[:16], exc)

    async def _delete_device_from_pod(self, device_id: str) -> None:
        """Remove a device registration from the pod."""
        client = self._pod_client()
        if not client or not device_id:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                uri = f"stash://pod/devices/{device_id}.json"
                from .solid_client import SolidError
                def _del():
                    try:
                        client.delete(uri)
                    except SolidError as e:
                        if e.status_code != 404:
                            raise
                await loop.run_in_executor(None, _del)
            except Exception as exc:
                logger.debug("_delete_device_from_pod failed [%s]: %s", device_id[:16], exc)

    async def _restore_devices_from_pod(self) -> None:
        """Pull device registrations from pod into SQLite on cold start.

        Pod path: stash://pod/devices/
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            member_uris = await loop.run_in_executor(None, client.list, "stash://pod/devices/")
        except Exception as exc:
            logger.debug("_restore_devices_from_pod: list failed: %s", exc)
            return

        restored = 0
        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                rec = json.loads(raw.decode("utf-8"))
                device_id = rec.get("device_id", "")
                owner_webid = rec.get("owner_webid", "")
                device_pub_b64 = rec.get("device_pub_b64", "")
                attestation_b64 = rec.get("attestation_b64", "")
                if not device_id or not owner_webid:
                    continue
                existing = self._store.get_device(device_id)
                if existing:
                    continue
                self._store.register_device(device_id, owner_webid, device_pub_b64, attestation_b64)
                restored += 1
            except Exception as exc:
                logger.debug("_restore_devices_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_devices_from_pod: restored %d device(s) from pod", restored)

    async def _sync_push_subscription_to_pod(
        self, subscription_id: str, owner_webid: str,
        endpoint: str, p256dh_b64: str, auth_b64: str
    ) -> None:
        """Write a push subscription to the pod.

        Pod path: stash://pod/push/{subscription_id}.json
        """
        client = self._pod_client()
        if not client or not subscription_id or not owner_webid:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                record = {
                    "subscription_id": subscription_id,
                    "owner_webid": owner_webid,
                    "endpoint": endpoint,
                    "p256dh_b64": p256dh_b64,
                    "auth_b64": auth_b64,
                    "created_at": time.time(),
                }
                data = json.dumps(record).encode("utf-8")
                uri = f"stash://pod/push/{subscription_id}.json"
                await loop.run_in_executor(
                    None, lambda: client.put(uri, data, content_type="application/json")
                )
            except Exception as exc:
                logger.debug("_sync_push_subscription_to_pod failed [%s]: %s", subscription_id[:16], exc)

    async def _delete_push_subscription_from_pod(self, subscription_id: str) -> None:
        """Remove a push subscription from the pod."""
        client = self._pod_client()
        if not client or not subscription_id:
            return
        async with self._pod_sync_sem:
            try:
                loop = asyncio.get_event_loop()
                uri = f"stash://pod/push/{subscription_id}.json"
                from .solid_client import SolidError
                def _del():
                    try:
                        client.delete(uri)
                    except SolidError as e:
                        if e.status_code != 404:
                            raise
                await loop.run_in_executor(None, _del)
            except Exception as exc:
                logger.debug("_delete_push_subscription_from_pod failed [%s]: %s", subscription_id[:16], exc)

    async def _restore_push_subscriptions_from_pod(self) -> None:
        """Pull push subscriptions from pod into SQLite on cold start.

        Pod path: stash://pod/push/
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()
        try:
            member_uris = await loop.run_in_executor(None, client.list, "stash://pod/push/")
        except Exception as exc:
            logger.debug("_restore_push_subscriptions_from_pod: list failed: %s", exc)
            return

        restored = 0
        for uri in member_uris:
            if not uri.endswith(".json"):
                continue
            try:
                raw = await loop.run_in_executor(None, client.get, uri)
                rec = json.loads(raw.decode("utf-8"))
                subscription_id = rec.get("subscription_id", "")
                owner_webid = rec.get("owner_webid", "")
                endpoint = rec.get("endpoint", "")
                p256dh_b64 = rec.get("p256dh_b64", "")
                auth_b64 = rec.get("auth_b64", "")
                if not subscription_id or not owner_webid or not endpoint:
                    continue
                existing = self._store.get_push_subscriptions(owner_webid)
                if any(s.get("subscription_id") == subscription_id for s in existing):
                    continue
                self._store.save_push_subscription(subscription_id, owner_webid, endpoint, p256dh_b64, auth_b64)
                restored += 1
            except Exception as exc:
                logger.debug("_restore_push_subscriptions_from_pod: failed for %s: %s", uri, exc)

        if restored:
            logger.info("_restore_push_subscriptions_from_pod: restored %d push subscription(s) from pod", restored)

    async def _run_pod_backfill(self) -> None:
        """One-time migration: push pre-R25 SQLite data to pod.

        Gated by stash://pod/meta/migration_version.json — skips if version >= 26.
        All pod writes are fire-and-forget tasks so startup isn't blocked.
        """
        client = self._pod_client()
        if not client or not self._store:
            return
        loop = asyncio.get_event_loop()

        # Check migration marker
        try:
            raw = await loop.run_in_executor(None, client.get, "stash://pod/meta/migration_version.json")
            marker = json.loads(raw.decode("utf-8"))
            if int(marker.get("version", 0)) >= 26:
                return
        except Exception:
            pass  # Marker missing — proceed with backfill

        logger.info("_run_pod_backfill: starting pre-R25 data backfill")

        # Backfill relationship certs
        try:
            for cert_dict in self._store.list_relationships():
                asyncio.create_task(self._sync_cert_to_pod(cert_dict))
        except Exception as exc:
            logger.debug("_run_pod_backfill: cert backfill error: %s", exc)

        # Backfill contact verifications
        if self._pod_webid:
            try:
                for v in self._store.list_contact_verifications(self._pod_webid):
                    peer_webid = v.get("peer_webid", "")
                    safety_numbers = v.get("safety_numbers", "")
                    verified_by = v.get("verified_by", "")
                    if peer_webid and safety_numbers:
                        asyncio.create_task(
                            self._sync_verification_to_pod(peer_webid, safety_numbers, verified_by)
                        )
            except Exception as exc:
                logger.debug("_run_pod_backfill: verification backfill error: %s", exc)

        # Backfill rooms (ensure pod containers exist for all local rooms)
        asyncio.create_task(self._ensure_pod_room_containers())

        # Write migration marker
        try:
            marker_data = json.dumps({
                "version": 26,
                "completed_at": time.time(),
            }).encode("utf-8")
            await loop.run_in_executor(
                None,
                lambda: client.put(
                    "stash://pod/meta/migration_version.json",
                    marker_data,
                    content_type="application/json",
                )
            )
            logger.info("_run_pod_backfill: migration marker written (version=26)")
        except Exception as exc:
            logger.debug("_run_pod_backfill: failed to write migration marker: %s", exc)

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

    async def _relay_room_message(self, gateway_url: str, room_id: str, event: dict) -> None:
        """Relay a room message to a federated peer gateway."""
        from .relay import sign_relay_message, post_relay
        from .didkey import pub_key_to_did
        import secrets as _sec
        gw_did = pub_key_to_did(self.agent.identity_pub_bytes)
        relay_nonce = _sec.token_hex(8)
        ts = event.get("timestamp", datetime.now(timezone.utc).isoformat())
        sig = sign_relay_message(
            self.agent.identity_key, gw_did, room_id,
            event.get("message_id", ""), event.get("content", ""), ts, relay_nonce,
        )
        payload = {
            **{k: v for k, v in event.items() if k not in ("own",)},
            "content_type": "room_message",
            "room_id": room_id,
            "relay_nonce": relay_nonce,
            "signature": sig,
            "origin_gateway_url": self._gateway_http_url(),
        }
        http_base = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
        try:
            await post_relay(http_base.rstrip("/") + "/relay", payload)
        except Exception as exc:
            logger.debug("room relay to %s failed: %s", gateway_url, exc)

    async def _relay_ephemeral(self, gateway_url: str, payload: dict) -> None:
        """POST a lightweight ephemeral event (presence/typing) to a peer gateway.
        No signature required — advisory only, fire-and-forget."""
        from .relay import post_relay
        http_base = gateway_url.replace("wss://", "https://").replace("ws://", "http://")
        try:
            await post_relay(http_base.rstrip("/") + "/relay", payload)
        except Exception:
            pass
