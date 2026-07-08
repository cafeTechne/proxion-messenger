import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import httpx
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, Callable, Awaitable

from .persist import AgentState
from .federation import RelationshipCertificate
from .solid_client import SolidClient
from .room import RoomMembership
from .readstate import ReadState
from .inbox import InboxEntry, poll_inbox
from .didkey import pub_key_to_did
from ._gateway_voice import VoiceHandlerMixin
from ._gateway_files import FileTransferMixin
from ._gateway_mailbox import MailboxMixin, relay_node_enabled, relay_fallback_url
from ._gateway_pod import PodSyncMixin, extract_mentions
from ._gateway_rooms import RoomHandlerMixin
from ._gateway_http import HttpEndpointsMixin
from ._gateway_dm import DmHandlerMixin
from ._gateway_auth import AuthHandlerMixin
from ._gateway_misc import MiscHandlerMixin
from .linkpreview import is_safe_url
from .relay import _validate_relay_target as _is_safe_gateway_url
from .command_validation import validate_command_payload, SchemaError, AUTH_RATE_COMMANDS, HEAVY_COMMANDS
from .security_policy import get_policy

logger = logging.getLogger(__name__)
# Suppress noisy "opening handshake failed" logs from zero-byte TCP probes
# (browser health checks, OS port scans) that are harmless but clutter the terminal.
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

@dataclass
class GatewayConfig:
    host: str = field(default_factory=lambda: os.environ.get("PROXION_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("PROXION_WS_PORT") or os.environ.get("PROXION_PORT", "7474")))
    poll_interval: float = 3.0
    push: bool = False
    turn_url: Optional[str] = field(default_factory=lambda: os.environ.get("TURN_URL"))
    turn_secret: Optional[str] = field(default_factory=lambda: os.environ.get("TURN_SECRET"))
    http_port: Optional[int] = None   # if set, serve web UI on this port
    web_dir: Optional[str] = None     # path to the web/ directory
    db_path: Optional[str] = None     # if set, persist rooms/messages/names here
    ssl_certfile: Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_SSL_CERT"))
    ssl_keyfile:  Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_SSL_KEY"))
    public_url:   Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_PUBLIC_URL"))
    # Solid Pod credentials (optional — enables pod-backed federation)
    css_url:         Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_CSS_URL"))
    css_email:       Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_CSS_EMAIL"))
    css_password:    Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_CSS_PASSWORD"))
    # Default CSS server shown in the onboarding "create your pod" step
    css_default_url: Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_CSS_DEFAULT_URL"))
    # Override the HTTP base URL advertised to federation peers (required behind a reverse proxy)
    http_public_url: Optional[str] = field(default_factory=lambda: os.environ.get("PROXION_HTTP_PUBLIC_URL"))
    # Set automatically by run_gateway.py when UPnP succeeds
    upnp_mapped: bool = field(default_factory=lambda: os.environ.get("PROXION_UPNP_MAPPED") == "1")

class ProxionGateway(VoiceHandlerMixin, FileTransferMixin, MailboxMixin, PodSyncMixin, RoomHandlerMixin, DmHandlerMixin, AuthHandlerMixin, MiscHandlerMixin, HttpEndpointsMixin):
    def __init__(
        self,
        agent: AgentState,
        dm_clients: Any,
        room_memberships: Any,
        config: GatewayConfig = GatewayConfig(),
        read_state: Optional[ReadState] = None,
    ):
        self.agent = agent
        # Support both old list-of-tuples and new dict-by-id signatures
        if isinstance(dm_clients, list):
            self.dm_clients = {c.certificate_id: (c, cl) for c, cl in dm_clients}
        else:
            self.dm_clients = dm_clients

        if isinstance(room_memberships, list):
            # Try to use room_id or id if it's a dict-like or membership
            self.room_memberships = {}
            for m, cl in room_memberships:
                rid = getattr(m, "room_id", None) or m.get("id")
                self.room_memberships[rid] = (m, cl)
        else:
            self.room_memberships = room_memberships
        self.config = config
        self.read_state = read_state or ReadState()
        self.clients = set()
        self.message_cache: deque = deque(maxlen=2000)  # bounded in-memory search cache
        self.identity_cache = {} # webid -> {"card": IdentityCard, "avatar_b64": str, "expiry": timestamp}
        self._stop_event = asyncio.Event()
        
        from .blocklist import Blocklist
        from .outbox import Outbox
        import os
        # Default to a subfolder of the agent's identity directory if possible
        outbox_dir = os.path.expanduser("~/.proxion/outbox")
        self.outbox = Outbox(outbox_dir)
        
        blocklist_path = os.path.expanduser("~/.proxion/blocklist.json")
        self.blocklist = Blocklist(blocklist_path)
        self._rate_counters = {}       # websocket -> [count, window_start]  (global 30/10s)
        self._rate_auth_counters = {}  # websocket -> [count, window_start]  (auth 5/min)
        self._rate_heavy_counters = {} # websocket -> [count, window_start]  (heavy 10/min)
        self._rate_lock = asyncio.Lock()
        self._ip_connection_counts: dict = {}  # remote_ip -> active connection count
        self._revoked_sessions: set = set()  # websockets awaiting close after revocation
        # Stable per-instance HMAC key for invite code hashing (derived from identity key)
        import hashlib as _hl
        try:
            _pub = self.agent.identity_pub_bytes
            if isinstance(_pub, bytes):
                self._invite_hmac_key: bytes = _hl.blake2b(_pub, digest_size=32).digest()
            else:
                self._invite_hmac_key = secrets.token_bytes(32)
        except Exception:
            self._invite_hmac_key = secrets.token_bytes(32)

        # Lightweight stash for invites and notifications
        from pathlib import Path
        stash_dir = Path(os.path.expanduser("~/.proxion/stash"))
        stash_dir.mkdir(parents=True, exist_ok=True)
        from .cli import SimpleStash
        self.stash = SimpleStash(stash_dir)

        # Voice session tracking for direct WS relay
        self._voice_sessions = {}   # session_id -> {"caller_ws": ws, "callee_ws": ws|None}
        self._voice_channels: dict = {}  # channel_id -> {"members": {webid: websocket}}
        self._webhook_fire_ts: deque = deque()
        self._webhook_breakers: dict = {}  # webhook_id -> {"failures": int, "opened_at": float|None}
        self._checksum_mismatch: bool = False  # R9: set by checksum loop, cleared by ack_checksum_mismatch
        self._checksum_mismatch_tables: list = []  # R9: tables with mismatches
        self._client_webids = {}    # websocket -> identity str (did:key or pod webid)
        # Multi-device: websocket -> the device_did this session physically is,
        # when it authenticated as a delegated device (account_did lives in
        # _client_webids). Absent for primary/single-device sessions.
        self._session_device_did: dict = {}
        # Multi-device pairing relay: pairing_code -> pairing session dict. A
        # primary starts a session; a new device submits its device_did against
        # the code; the primary signs+relays a delegation cert back. Short TTL,
        # single-use. See _gateway_misc pairing handlers.
        self._pairing_sessions: dict = {}
        self._webid_sockets: dict = {}  # identity str -> set of websockets
        self._session_meta: dict = {}   # websocket -> {session_id, connected_at, ip_addr}
        self._did_pod_webids = {}   # did:key -> pod webid (set via link_pod)
        self._system_ws = set()      # stubs for internal operations (scheduler, etc)

        # Presence tracking
        self._user_presence = {}    # webid -> {"status": "online"|"offline"|"away"|"busy", "status_message": str, "updated_at": iso_timestamp, "last_active_at": iso_timestamp}

        # Cross-gateway relay: maps peer webid -> their gateway HTTP base URL
        self._peer_gateway_urls: dict = {}
        self._relay_queue: dict[str, list[dict]] = {}
        # Per-device fanout envelopes held for OFFLINE devices until they
        # re-register (mirrors _relay_queue). Keyed (to_webid, to_device_id).
        # Without this, a fanout DM to an offline device was silently lost —
        # fanout has no server-side history to recover from.
        self._fanout_queue: dict[tuple, list[dict]] = {}
        self._relay_rate_limiter: dict[str, deque] = {}
        self._seen_relay_ids: deque = deque(maxlen=1000)  # LRU dedup for relay posts (R9)
        self._seen_relay_nonces: deque = deque(maxlen=1000)  # auto-evicting nonce dedup
        self._revoked_dids: set = set()                   # cert-revoked peer DIDs (R12)

        # R17.2: short invite token (/i/<8-hex> → /invite?from=<address>)
        import secrets as _sec
        self._short_invite_token: str = _sec.token_hex(8)  # 16 hex chars
        
        # Connected pod URL and webid (for gateway discovery endpoint)
        self._pod_url: Optional[str] = None
        self._pod_webid: Optional[str] = None
        self._pod_available: bool = False

        # Local (pod-free) rooms
        self._local_rooms = {}      # room_id -> {"name": str, "code": str, "members": set, "invite_url": str, ...}
        self._room_codes = {}       # code -> room_id
        self._display_names = {}    # websocket -> display_name (session cache)
        self._pending_ownership_transfers = {}  # room_id -> {"from_ws": ws, "to_ws": ws, "to_did": str}
        self._pending_auth: dict = {}      # websocket -> {did, webid, display_name, nonce, expires_at}
        self._auth_verified: set = set()   # websockets that have passed DID challenge
        self._auth_fail_counts: dict = {}  # (id(ws), ip) -> {count, first_at}

        # Disappearing messages: room_id -> ms (0 = disabled) — must init before _hydrate_from_store
        self._room_disappear_timers: dict = {}
        self._dm_disappear_timers: dict = {}  # cert_id -> ms

        # Dirty read positions for pod flush (flushed every 30s by _read_position_flush_loop)
        self._dirty_read_positions: dict = {}  # (webid, channel_id) -> ts

        # Uptime tracking for /health endpoint
        self._start_time: float = time.time()

        # Local LAN IP — detected once at startup for connectivity guidance
        try:
            import socket as _sock
            _s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            _s.connect(("8.8.8.8", 80))
            self._local_ip: str = _s.getsockname()[0]
            _s.close()
        except Exception:
            self._local_ip = "127.0.0.1"

        # Prometheus-style counters (R13.4)
        self._metrics: dict = {
            "messages_total": 0,
            "relay_posts_total": 0,
            "relay_posts_rejected_total": 0,
            "pod_writes_total": 0,
            "pod_write_errors_total": 0,
            "ws_connections_current": 0,
            "ws_connections_total": 0,
            "identity_cache_evictions_total": 0,
            # MVP runtime counters (Round 21)
            "dm_decrypt_errors_total": 0,
            "session_recovery_attempts_total": 0,
            "catchup_batches_total": 0,
            "delivery_state_regressions_total": 0,
            # WG overlay counters (Round 22)
            "wg_peers_total": 0,
            "wg_peers_direct": 0,
            "wg_peers_relay": 0,
            "relay_fallback_total": 0,
            "relay_to_direct_recovery_total": 0,
            # Hole punch counters (Round 23)
            "hole_punch_attempts_total": 0,
            "hole_punch_succeeded_total": 0,
            "hole_punch_failed_total": 0,
        }

        # Per-identity connection count for aggregated presence (R13.13)
        self._presence_by_identity: dict = {}  # webid -> set of ws

        # Semaphore bounding concurrent fire-and-forget pod sync tasks
        self._pod_sync_sem: asyncio.Semaphore = asyncio.Semaphore(8)

        # Per-user read receipt preferences
        self._client_receipts_prefs: dict = {}  # webid → bool

        # Link previews toggle (default off)
        self._link_previews_enabled: bool = os.environ.get("PROXION_LINK_PREVIEWS", "0") == "1"

        # Persistent store (SQLite) — optional
        self._store = None
        if config.db_path:
            from .local_store import LocalStore
            self._store = LocalStore(config.db_path)
            self._hydrate_from_store()
            self._peer_gateway_urls.update(self._store.get_all_peer_gateways())

        # VAPID keypair for WebPush (R18)
        import os as _os_vapid
        self._vapid_private_pem: str = _os_vapid.environ.get("PROXION_VAPID_PRIVATE_KEY", "")
        self._vapid_public_b64: str = _os_vapid.environ.get("PROXION_VAPID_PUBLIC_KEY", "")
        self._vapid_subject: str = _os_vapid.environ.get("PROXION_VAPID_SUBJECT", "")
        if not self._vapid_private_pem:
            try:
                from .webpush import generate_vapid_keypair
                self._vapid_private_pem, self._vapid_public_b64 = generate_vapid_keypair()
                logger.debug("Generated ephemeral VAPID keypair (set PROXION_VAPID_* env vars to persist)")
            except Exception as _ve:
                logger.debug("VAPID keypair generation skipped: %s", _ve)

        # Derive a stable X25519 keypair for sealed relay (derived from Ed25519 identity key)
        self._own_x25519_priv: Optional[bytes] = None
        self._own_x25519_pub_b64: str = ""
        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey as _X25519Priv
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF as _HKDF_gw
            from cryptography.hazmat.primitives.hashes import SHA256 as _SHA256_gw
            from cryptography.hazmat.primitives.serialization import Encoding as _Enc, PrivateFormat as _PF, NoEncryption as _NE
            import base64 as _b64_gw
            _raw_ed_priv = agent.identity_key.private_bytes(_Enc.Raw, _PF.Raw, _NE())
            _x25519_priv_bytes = _HKDF_gw(_SHA256_gw(), 32, salt=b"proxion-gw-x25519-v1", info=b"").derive(_raw_ed_priv)
            self._own_x25519_priv = _x25519_priv_bytes
            _x_priv = _X25519Priv.from_private_bytes(_x25519_priv_bytes)
            _x_pub_bytes = _x_priv.public_key().public_bytes_raw()
            self._own_x25519_pub_b64 = _b64_gw.urlsafe_b64encode(_x_pub_bytes).rstrip(b"=").decode()
            if self._store:
                _gw_did_x = pub_key_to_did(agent.identity_pub_bytes)
                self._store.save_x25519_pub(_gw_did_x, self._own_x25519_pub_b64)
        except Exception as _xe:
            logger.debug("X25519 keypair derivation failed: %s", _xe)

    def _make_turn_creds(self, identity: str) -> Optional[dict]:
        """Return coturn-compatible time-limited TURN credentials, or None if not configured."""
        secret = self.config.turn_secret
        url = self.config.turn_url
        if not secret or not url:
            return None
        ttl = 3600  # 1 hour
        expiry = int(time.time()) + ttl
        username = f"{expiry}:{identity}"
        import hmac
        mac = hmac.new(secret.encode(), username.encode(), hashlib.sha1)
        password = base64.b64encode(mac.digest()).decode()
        return {"urls": [url], "username": username, "credential": password}

    def _hydrate_from_store(self) -> None:
        """Re-populate _local_rooms and _room_codes from the persistent store on startup."""
        for room in self._store.get_all_rooms():
            self._local_rooms[room["room_id"]] = {
                "name": room["name"],
                "code": room["code"],
                "invite_url": room.get("invite_url", ""),
                "history_mode": room["history_mode"],
                "creator_webid": room.get("creator_webid", ""),
                "members": set(),   # repopulated as clients reconnect
                "messages": [],     # served from DB on demand
            }
            self._room_codes[room["code"]] = room["room_id"]
            # Back-fill room_members for rooms that predate the membership table
            creator = room.get("creator_webid", "")
            if creator and creator not in self._store.get_room_members(room["room_id"]):
                self._store.add_room_member(room["room_id"], creator)
        logger.info(f"Hydrated {len(self._local_rooms)} local rooms from store")
        # R11.2.3: restore per-room disappear timers from SQLite
        for room in self._store.get_all_rooms():
            ms = room.get("disappear_after_ms", 0)
            if ms:
                self._room_disappear_timers[room["room_id"]] = ms
        # Restore per-DM disappear timers (dedicated dm_disappear_timers table)
        # so DM disappearing messages survive a gateway restart. The prior
        # UPDATE-rooms persistence was a no-op for DM ids, so this never worked.
        try:
            for _tid, _dms in self._store.get_all_dm_disappear_timers().items():
                if _dms:
                    self._dm_disappear_timers[_tid] = _dms
        except Exception:
            logger.debug("DM disappear-timer restore skipped", exc_info=True)
        # R12: load revoked DIDs into fast in-memory set
        if self._store:
            self._revoked_dids = self._store.get_revoked_dids()

    def _strip_thread_prefix(self, thread_id: str) -> str:
        """Strip 'room:' or 'dm:' prefix added by the client for pin/unpin lookups."""
        if thread_id.startswith("room:"):
            return thread_id[5:]
        if thread_id.startswith("dm:"):
            return thread_id[3:]
        return thread_id

    def _name_for(self, websocket, webid: str) -> str:
        """Resolve a display name for a websocket/webid, with a unique fallback."""
        name = self._display_names.get(websocket)
        if not name and webid and self._store:
            name = self._store.get_display_name(webid)
        if not name and webid:
            # Use last 6 chars — unique for did:key, readable for URLs
            name = "…" + webid[-6:]
        return name or "?"

    def _any_socket(self, identity: str):
        """Return any connected websocket for identity, or None.

        Handles both the live set[WebSocket] stored by register() and single-socket
        values that unit-test fixtures inject directly.
        """
        val = self._webid_sockets.get(identity)
        if val is None:
            return None
        candidates = val if isinstance(val, set) else {val}
        for ws in candidates:
            if ws in self.clients:
                return ws
        return None

    def _own_gateway_did(self) -> str:
        """This gateway's own identity DID (the DID half of its Proxion address). Cached."""
        d = getattr(self, "_own_gateway_did_cache", None)
        if d is None:
            from .didkey import pub_key_to_did
            d = pub_key_to_did(self.agent.identity_pub_bytes)
            self._own_gateway_did_cache = d
        return d

    def _auth_enforced(self) -> bool:
        """Whether registration actually required proof of identity, mirroring the
        register handler (_gateway_auth.py). When True, ``_client_webids[ws]`` is a
        cryptographically proven identity (auth challenge and/or delegation cert),
        so a participant/owner check on it is meaningful and secure. When False
        (loopback single-user dev, PROXION_REQUIRE_AUTH=0), the registered DID is an
        unauthenticated self-claim: the check is unenforceable (an attacker could
        just claim the owner DID) AND wrongly rejects the real local user, whose
        browser registers under its own session DID (≠ the gateway/account DID).
        So auth-scoped local participant checks must be gated on this."""
        _env = os.environ.get("PROXION_REQUIRE_AUTH", "")
        if _env == "1":
            return True
        if _env == "0":
            return False
        _host = (getattr(self, "config", None) and self.config.host) or ""
        # Only genuine loopback skips auth; wildcard (0.0.0.0/::) is routable → auth.
        return _host not in ("127.0.0.1", "localhost", "::1")

    def _sockets_for(self, identity: str) -> list:
        """Return all connected sockets for identity (handles set and single-socket values).

        One-gateway-per-user: a delivery addressed to THIS gateway's own identity
        (the DID peers send to — the Proxion address) is for our local user, whose
        browser registers under its OWN client DID, not the gateway DID. So when the
        identity is our own and the normal lookup yields nothing, return the connected
        client(s). Centralizes the fix that cross-gateway DMs/voice/receipts all need;
        without it those silently never reach the local browser.
        """
        val = self._webid_sockets.get(identity)
        result = []
        if val is not None:
            candidates = val if isinstance(val, set) else {val}
            result = [ws for ws in candidates if ws in self.clients]
        if not result and identity and identity == self._own_gateway_did():
            return [ws for ws in self.clients]
        return result

    async def _send_to_identity(self, identity: str, payload: str):
        """Send a message to all connected sockets for an identity, skipping broken ones."""
        for ws in self._sockets_for(identity):
            try:
                await ws.send(payload)
            except Exception:
                pass

    async def _fire_outgoing_webhook(self, wh: dict, event: dict):
        from .network import _resolve_safe_ip
        from urllib.parse import urlparse
        import time as _time
        wh_id = wh.get("id", "")
        wh_url = wh.get("url", "")
        if not wh_url.startswith(("http://", "https://")):
            logger.warning("Skipping webhook fire — non-HTTP URL: %s", wh_url)
            return

        # Per-webhook circuit breaker check
        _BREAKER_THRESHOLD = 10
        _BREAKER_COOLDOWN = 600  # 10 minutes
        _breaker = self._webhook_breakers.get(wh_id, {"failures": 0, "opened_at": None})
        if _breaker["opened_at"] is not None:
            _elapsed = _time.time() - _breaker["opened_at"]
            if _elapsed < _BREAKER_COOLDOWN:
                logger.debug("Webhook %s circuit open, suppressing delivery (%.0fs remaining)", wh_id, _BREAKER_COOLDOWN - _elapsed)
                return
            else:
                # Half-open: allow one attempt through
                logger.info("Webhook %s circuit half-open, allowing probe attempt", wh_id)

        resolved_ip = _resolve_safe_ip(wh_url)
        if resolved_ip is None:
            logger.warning("Skipping webhook fire — URL resolves to private/unsafe IP: %s", wh_url)
            return
        _now = _time.monotonic()
        while self._webhook_fire_ts and _now - self._webhook_fire_ts[0] >= 60.0:
            self._webhook_fire_ts.popleft()
        if len(self._webhook_fire_ts) >= 30:
            logger.warning("Outgoing webhook rate limit (30/min) exceeded, dropping: %s", wh_id)
            return
        self._webhook_fire_ts.append(_now)
        import hmac as _hmac
        payload = json.dumps(event).encode()
        sig = _hmac.new(wh["token"].encode(), payload, hashlib.sha256).hexdigest()

        # Webhook payload signing v2 (Round 6)
        _wh_ts = str(int(_time.time()))
        _body_sha256 = hashlib.sha256(payload).hexdigest()
        _sig_v2_input = f"{_wh_ts}.{_body_sha256}".encode()
        _sig_v2 = _hmac.new(wh["token"].encode(), _sig_v2_input, hashlib.sha256).hexdigest()

        parsed = urlparse(wh_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path_qs = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        headers = {
            "Content-Type": "application/json",
            "X-Proxion-Signature": f"sha256={sig}",
            "X-Proxion-Hook-Id": wh_id,
            "X-Proxion-Timestamp": _wh_ts,
            "X-Proxion-Body-SHA256": _body_sha256,
            "X-Proxion-Signature-V2": f"sha256={_sig_v2}",
        }
        # Pin HTTP connections to the resolved IP to prevent DNS-rebinding after resolution.
        if parsed.scheme == "http":
            pinned_url = f"http://{resolved_ip}:{port}{path_qs}"
            headers["Host"] = parsed.hostname
        else:
            pinned_url = wh_url  # HTTPS: TLS cert validation enforces the binding

        # Webhook delivery logging (Round 6)
        _t0 = _time.time()
        _status_code = None
        _success = False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                _resp = await client.post(pinned_url, content=payload, headers=headers)
                _status_code = _resp.status_code
                _success = 200 <= _status_code < 300
        except Exception as exc:
            logger.warning("Outgoing webhook %s failed: %s", wh_id, exc)
        finally:
            _latency_ms = int((_time.time() - _t0) * 1000)
            # Update circuit breaker state
            _breaker = self._webhook_breakers.get(wh_id, {"failures": 0, "opened_at": None})
            if _success:
                # Close breaker on success
                _breaker = {"failures": 0, "opened_at": None}
            else:
                _breaker["failures"] = _breaker.get("failures", 0) + 1
                if _breaker["failures"] >= _BREAKER_THRESHOLD and _breaker["opened_at"] is None:
                    _breaker["opened_at"] = _time.time()
                    logger.warning("Webhook %s circuit breaker opened after %d consecutive failures", wh_id, _breaker["failures"])
                    if self._store:
                        self._store.save_security_event(
                            "webhook_circuit_opened", "warning",
                            webid=None, ip=None,
                            details=f"webhook_id={wh_id} failures={_breaker['failures']}",
                        )
            self._webhook_breakers[wh_id] = _breaker
            if self._store:
                self._store.save_webhook_delivery_log(
                    webhook_id=wh_id,
                    thread_id=wh.get("thread_id", ""),
                    status_code=_status_code,
                    success=_success,
                    latency_ms=_latency_ms,
                )

    async def broadcast(self, event: dict):
        """Send JSON event to all connected WebSocket clients."""
        if not self.clients:
            return
        message = json.dumps(event)
        for client in list(self.clients):
            try:
                await client.send(message)
            except Exception as exc:
                logger.warning(f"Failed to send to client: {exc}")

    # Alias used by some handlers
    _broadcast = broadcast

    async def _broadcast_to_owner(self, event: dict) -> None:
        """Send event only to WebSocket sessions authenticated as the gateway owner."""
        owner_did = pub_key_to_did(self.agent.identity_pub_bytes)
        message = json.dumps(event)
        for ws, webid in list(self._client_webids.items()):
            if webid == owner_did:
                try:
                    await ws.send(message)
                except Exception as exc:
                    logger.warning(f"_broadcast_to_owner: failed: {exc}")

    async def broadcast_to_room(self, room_id: str, event: dict):
        """Send JSON event only to members of a specific room."""
        room = self._local_rooms.get(room_id)
        if not room:
            return
        message = json.dumps(event)
        for ws in list(room["members"]):
            try:
                await ws.send(message)
            except Exception as exc:
                logger.warning(f"broadcast_to_room {room_id}: failed to send: {exc}")

    async def handle_client(self, websocket):
        """Handle individual WebSocket connection."""
        _max = int(os.environ.get("PROXION_MAX_CLIENTS", "200"))
        current = len(self.clients)
        if current >= _max * 2:
            try:
                await websocket.close(1013, "Server at capacity")
            except Exception:
                pass
            return
        if current >= _max:
            logger.warning("Client count at soft limit (%d/%d)", current, _max)

        # Per-IP connection limit
        _peer = getattr(websocket, "remote_address", None)
        _remote_ip = _peer[0] if isinstance(_peer, (tuple, list)) and _peer else ""
        _max_per_ip = int(os.environ.get("PROXION_MAX_CONNECTIONS_PER_IP", "8"))
        if _remote_ip:
            _cur_ip = self._ip_connection_counts.get(_remote_ip, 0) + 1
            if _cur_ip > _max_per_ip:
                try:
                    await websocket.close(1008, "ip_connection_limit")
                except Exception:
                    pass
                return
            self._ip_connection_counts[_remote_ip] = _cur_ip

        self.clients.add(websocket)
        self._metrics["ws_connections_current"] += 1
        self._metrics["ws_connections_total"] += 1
        # Hard timeout for unauthenticated sockets
        _auth_timeout_s = int(os.environ.get("PROXION_AUTH_TIMEOUT_SECONDS", "30"))
        async def _auth_timeout_task():
            await asyncio.sleep(_auth_timeout_s)
            if websocket in self.clients and websocket not in self._client_webids:
                logger.info("Closing unauthenticated socket after %ds timeout", _auth_timeout_s)
                try:
                    await websocket.close(1008, "auth_timeout")
                except Exception:
                    pass
        _timeout_task = asyncio.create_task(_auth_timeout_task())

        # Idle session timeout
        _idle_timeout_s = int(os.environ.get("PROXION_SESSION_IDLE_TIMEOUT_S", "86400"))
        _last_activity = [time.time()]

        async def _idle_timeout_task():
            while websocket in self.clients:
                await asyncio.sleep(60)
                if websocket in self.clients and time.time() - _last_activity[0] > _idle_timeout_s:
                    logger.info("Closing idle socket after %ds", _idle_timeout_s)
                    try:
                        await websocket.close(1001, "idle_timeout")
                    except Exception:
                        pass
                    break
        _idle_task = asyncio.create_task(_idle_timeout_task())

        try:
            # Always send config on connect; client uses this to sync pod state and decide onboarding.
            try:
                await websocket.send(json.dumps({
                    "type": "config",
                    "first_run": True,
                    "pod_connected": bool(self._pod_url),
                    "pod_url": self._pod_url or "",
                    "pod_webid": self._pod_webid or "",
                }))
            except Exception:
                pass

            try:
                async for message in websocket:
                    try:
                        _last_activity[0] = time.time()
                        data = json.loads(message)
                        await self.process_command(websocket, data)
                    except json.JSONDecodeError:
                        logger.warning("Received invalid JSON from client")
                    except Exception as exc:
                        logger.error(f"Error processing command: {exc}")
            except Exception:
                pass  # connection closed or dropped — not an error
        finally:
            _timeout_task.cancel()
            try:
                await _timeout_task
            except (asyncio.CancelledError, Exception):
                pass
            _idle_task.cancel()
            try:
                await _idle_task
            except (asyncio.CancelledError, Exception):
                pass
            self.clients.discard(websocket)
            self._metrics["ws_connections_current"] = max(0, self._metrics["ws_connections_current"] - 1)
            if _remote_ip:
                self._ip_connection_counts[_remote_ip] = max(
                    0, self._ip_connection_counts.get(_remote_ip, 1) - 1
                )
            self._pending_auth.pop(websocket, None)
            self._auth_verified.discard(websocket)
            webid = self._client_webids.pop(websocket, None)
            self._session_device_did.pop(websocket, None)
            # Drop any pairing session this socket was part of (primary or new device).
            for _pc in [
                _c for _c, _s in self._pairing_sessions.items()
                if _s.get("primary_ws") is websocket or _s.get("device_ws") is websocket
            ]:
                self._pairing_sessions.pop(_pc, None)
            self._session_meta.pop(websocket, None)
            self._rate_counters.pop(websocket, None)
            self._rate_auth_counters.pop(websocket, None)
            self._rate_heavy_counters.pop(websocket, None)
            self._display_names.pop(websocket, None)
            self._revoked_sessions.discard(websocket)
            if webid:
                sockets = self._webid_sockets.get(webid, set())
                sockets.discard(websocket)
                if not sockets:
                    self._webid_sockets.pop(webid, None)

                # R13.13: multi-device presence aggregation
                _pid_set = self._presence_by_identity.get(webid, set())
                _pid_set.discard(websocket)
                if not _pid_set:
                    self._presence_by_identity.pop(webid, None)
                    # Last connection gone → broadcast offline
                    now = datetime.now(timezone.utc).isoformat()
                    old_presence = self._user_presence.get(webid, {})
                    self._user_presence[webid] = {
                        "status": "offline",
                        "status_message": old_presence.get("status_message", ""),
                        "updated_at": now,
                        "last_active_at": now,
                    }
                    asyncio.create_task(self.broadcast({
                        "type": "presence_update",
                        "webid": webid,
                        "status": "offline",
                        "status_message": old_presence.get("status_message", ""),
                        "updated_at": now,
                        "last_active_at": now,
                    }))
            
            await self._cleanup_voice_sessions(websocket)
            for room in self._local_rooms.values():
                room["members"].discard(websocket)

    def _check_ws_rate_limit(self, websocket) -> bool:
        """Return True if command is allowed; False if global rate limit exceeded.

        Global limit: 60 commands per 10-second sliding window.
        Uses in-memory counters (per-socket) — SQLite is too slow for per-command writes.
        """
        import time as _time
        now = _time.monotonic()
        entry = self._rate_counters.get(websocket)
        if entry is None:
            self._rate_counters[websocket] = [1, now]
            return True
        count, window_start = entry
        if now - window_start >= 10.0:
            self._rate_counters[websocket] = [1, now]
            return True
        if count >= 60:
            return False
        entry[0] += 1
        return True

    def _check_auth_rate_limit(self, websocket) -> bool:
        """Return True if auth command is allowed (5 per minute per socket)."""
        webid = self._client_webids.get(websocket, "")
        if self._store and webid:
            bucket_key = f"auth:{webid}"
            return self._store.rate_limit_check_and_increment(bucket_key, limit=5, window_seconds=60.0)
        import time as _time
        now = _time.monotonic()
        entry = self._rate_auth_counters.get(websocket)
        if entry is None:
            self._rate_auth_counters[websocket] = [1, now]
            return True
        count, window_start = entry
        if now - window_start >= 60.0:
            self._rate_auth_counters[websocket] = [1, now]
            return True
        if count >= 5:
            return False
        entry[0] += 1
        return True

    def _check_heavy_rate_limit(self, websocket) -> bool:
        """Return True if heavy command is allowed (10 per minute per socket)."""
        webid = self._client_webids.get(websocket, "")
        if self._store and webid:
            bucket_key = f"heavy:{webid}"
            return self._store.rate_limit_check_and_increment(bucket_key, limit=10, window_seconds=60.0)
        import time as _time
        now = _time.monotonic()
        entry = self._rate_heavy_counters.get(websocket)
        if entry is None:
            self._rate_heavy_counters[websocket] = [1, now]
            return True
        count, window_start = entry
        if now - window_start >= 60.0:
            self._rate_heavy_counters[websocket] = [1, now]
            return True
        if count >= 10:
            return False
        entry[0] += 1
        return True

    async def process_command(self, websocket, data: dict):
        """Process inbound commands from clients."""
        cmd = data.get("cmd", "")
        _RATE_EXEMPT = {"ping", "pong"}
        # pair_submit / pair_cancel are sent by a NOT-yet-registered new device
        # (it has no account identity until it receives the delegation cert), so
        # they must be reachable before register — like auth_response.
        _AUTH_EXEMPT = {"ping", "pong", "auth_response", "register", "disconnect_pod",
                        "pair_submit", "pair_cancel"}

        if not cmd:
            return

        # Global rate limit: 30 cmd/10s burst 60
        if cmd not in _RATE_EXEMPT and not self._check_ws_rate_limit(websocket):
            await websocket.send(json.dumps({"type": "error", "code": "E_RATE", "message": "rate_limited"}))
            return
        # Auth-specific limit: 5/min
        if cmd in AUTH_RATE_COMMANDS and not self._check_auth_rate_limit(websocket):
            await websocket.send(json.dumps({"type": "error", "code": "E_RATE", "message": "rate_limited"}))
            return
        # Heavy command limit: 10/min
        if cmd in HEAVY_COMMANDS and not self._check_heavy_rate_limit(websocket):
            await websocket.send(json.dumps({"type": "error", "code": "E_RATE", "message": "rate_limited"}))
            return

        # Drop all commands from explicitly-revoked sessions immediately.
        if websocket in self._revoked_sessions:
            return

        # R1: Reject commands from unregistered clients; internal stubs bypass.
        # Note: 'link_pod' and 'handshake' commands are often sent before a pod is linked,
        # but the client MUST have completed 'register' (DID proof) first.
        is_internal = websocket in self._system_ws or isinstance(websocket, getattr(self, "_NullWs", type(None)))
        if cmd not in _AUTH_EXEMPT and websocket not in self._client_webids and not is_internal:
            logger.warning("Rejecting unauthenticated command '%s' from %s", cmd, websocket)
            await websocket.send(json.dumps({"type": "error", "message": "Not registered"}))
            return

        # Schema validation (after auth — unregistered clients get "Not registered" not schema errors)
        try:
            validate_command_payload(cmd, data)
        except SchemaError as _se:
            logger.warning("Schema error cmd=%s: %s", cmd, _se)
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "invalid payload"}))
            if self._store:
                _ip = (self._session_meta.get(websocket) or {}).get("ip_addr", "")
                _webid = self._client_webids.get(websocket, "")
                self._store.save_security_event(
                    "schema_reject", "info",
                    webid=_webid or None,
                    ip=_ip or None,
                    details=f"cmd={cmd} reason={str(_se)[:200]}",
                )
            return

        # R12: Reject mutating commands from identities in the revoked-DID set.
        from .command_validation import MUTATING_COMMANDS
        if cmd in MUTATING_COMMANDS and websocket in self._client_webids:
            _sender_wid = self._client_webids[websocket]
            if _sender_wid in getattr(self, "_revoked_dids", set()):
                await websocket.send(json.dumps({
                    "type": "error", "code": "E_REVOKED",
                    "message": "identity_revoked",
                }))
                return

        # R9: Route all commands through the centralized security policy engine
        _caller_webid_pol = self._client_webids.get(websocket, "")
        try:
            _owner_did_pol = pub_key_to_did(self.agent.identity_pub_bytes)
        except Exception:
            _owner_did_pol = ""
        _pol_decision = get_policy().evaluate_ws_command(cmd, _caller_webid_pol, _owner_did_pol)
        if not _pol_decision.allow:
            await websocket.send(json.dumps({
                "type": "error",
                "code": _pol_decision.deny_code or "E_FORBIDDEN",
                "message": _pol_decision.deny_reason or "policy_deny",
            }))
            if self._store and _pol_decision.audit_event_type:
                try:
                    self._store.save_security_event(
                        _pol_decision.audit_event_type,
                        _pol_decision.severity or "warning",
                        webid=_caller_webid_pol,
                        details=f"cmd={cmd} denied by policy",
                    )
                except Exception:
                    pass
            return

        # Fine-grained ACL groups (defensive layer — handlers also validate internally)
        _caller_webid_acl = self._client_webids.get(websocket, "")
        _ROOM_MEMBER_CMDS = frozenset({"send_room", "get_history", "get_reactions"})
        _ROOM_OWNER_CMDS = frozenset({"delete_room", "transfer_ownership"})
        _DM_PARTICIPANT_CMDS = frozenset({"get_message"})

        if cmd in _ROOM_MEMBER_CMDS or cmd in _ROOM_OWNER_CMDS:
            _room_id_acl = data.get("room_id", "")
            if _room_id_acl and _room_id_acl in self._local_rooms:
                from .authz import is_room_member, is_room_owner
                if cmd in _ROOM_MEMBER_CMDS:
                    if not is_room_member(self._store, self._local_rooms, _room_id_acl, _caller_webid_acl, websocket):
                        await websocket.send(json.dumps({
                            "type": "error", "code": "E_FORBIDDEN", "message": "not_room_member",
                        }))
                        return
                if cmd in _ROOM_OWNER_CMDS:
                    if not is_room_owner(self._store, self._local_rooms, _room_id_acl, _caller_webid_acl):
                        await websocket.send(json.dumps({
                            "type": "error", "code": "E_FORBIDDEN", "message": "not_room_owner",
                        }))
                        return

        if cmd in _DM_PARTICIPANT_CMDS and self._store:
            _thread_id_acl = data.get("thread_id", "")
            if _thread_id_acl and not _thread_id_acl.startswith("room-"):
                from .authz import is_dm_participant
                if not is_dm_participant(self._store, _thread_id_acl, _caller_webid_acl):
                    await websocket.send(json.dumps({
                        "type": "error", "code": "E_FORBIDDEN", "message": "not_dm_participant",
                    }))
                    return

        # Safe mode: block mutating commands
        import os as _os_sm
        if _os_sm.environ.get("PROXION_SAFE_MODE") == "1":
            from .command_validation import MUTATING_COMMANDS
            if cmd in MUTATING_COMMANDS:
                await websocket.send(json.dumps({"type": "error", "code": "E_SAFE_MODE", "message": "safe_mode_enabled"}))
                return

        # R8: DB integrity guard — block mutating WebSocket commands when DB is corrupt
        if self._store and not getattr(self._store, "_integrity_ok", True):
            from .command_validation import MUTATING_COMMANDS as _MUT_CMDS_IC
            if cmd in _MUT_CMDS_IC:
                await websocket.send(json.dumps({"type": "error", "code": "E_DB_INTEGRITY", "message": "db_integrity_failed"}))
                return

        try:
            if cmd == "send_dm":
                _op_id = data.get("op_id")
                _actor = self._client_webids.get(websocket, "")
                if _op_id and self._store:
                    _prior = self._store.get_operation_result(_op_id)
                    if _prior is not None:
                        await websocket.send(json.dumps({"type": "send_dm_ack", "op_id": _op_id, "result_code": _prior.get("result_code", "ok"), "replayed": True}))
                    else:
                        await self._handle_send_dm(websocket, data)
                        self._store.record_operation_result(_op_id, "send_dm", _actor, data.get("device_id"), "ok")
                else:
                    await self._handle_send_dm(websocket, data)
            elif cmd == "edit_message":
                await self._handle_edit_message(websocket, data)
            elif cmd == "send_room":
                _op_id = data.get("op_id")
                _actor = self._client_webids.get(websocket, "")
                if _op_id and self._store:
                    _prior = self._store.get_operation_result(_op_id)
                    if _prior is not None:
                        await websocket.send(json.dumps({"type": "send_room_ack", "op_id": _op_id, "result_code": _prior.get("result_code", "ok"), "replayed": True}))
                    else:
                        await self._handle_send_room(websocket, data)
                        self._store.record_operation_result(_op_id, "send_room", _actor, data.get("device_id"), "ok")
                else:
                    await self._handle_send_room(websocket, data)
            elif cmd == "set_presence":
                await self._handle_set_presence(websocket, data)
            elif cmd == "get_rooms":
                await self._handle_get_rooms(websocket, data)
            elif cmd == "get_dms":
                await self._handle_get_dms(websocket, data)
            elif cmd == "send_file":
                await self._handle_send_file(websocket, data)
            elif cmd == "file_offer":
                await self._handle_file_offer(websocket, data)
            elif cmd == "file_accept":
                await self._handle_file_accept(websocket, data)
            elif cmd == "file_reject":
                await self._handle_file_reject(websocket, data)
            elif cmd == "file_chunk":
                await self._handle_file_chunk(websocket, data)
            elif cmd == "file_complete":
                await self._handle_file_complete(websocket, data)
            elif cmd == "auth_response":
                await self._handle_auth_response(websocket, data)
            elif cmd == "register":
                await self._handle_register(websocket, data)
            elif cmd == "link_pod":
                await self._handle_link_pod(websocket, data)
            elif cmd == "local_dm":
                await self._handle_local_dm(websocket, data)
            elif cmd == "get_local_history":
                await self._handle_get_local_history(websocket, data)
            elif cmd == "delete_local_message":
                await self._handle_delete_local_message(websocket, data)
            elif cmd == "edit_local_message":
                await self._handle_edit_local_message(websocket, data)
            elif cmd == "forward_message":
                await self._handle_forward_message(websocket, data)
            elif cmd == "get_room_members":
                await self._handle_get_room_members(websocket, data)
            elif cmd == "announce_room_join":
                await self._handle_announce_room_join(websocket, data)
            elif cmd == "leave_local_room":
                await self._handle_leave_local_room(websocket, data)
            elif cmd == "delete_room":
                await self._handle_delete_room(websocket, data)
            elif cmd == "transfer_ownership":
                await self._handle_transfer_ownership(websocket, data)
            elif cmd == "accept_ownership":
                await self._handle_accept_ownership(websocket, data)
            elif cmd == "decline_ownership":
                await self._handle_decline_ownership(websocket, data)
            elif cmd == "resolve_did":
                await self._handle_resolve_did(websocket, data)
            elif cmd == "discover_peer":
                await self._handle_discover_peer(websocket, data)
            elif cmd == "voice_invite":
                await self._handle_voice_invite(websocket, data)

            elif cmd == "voice_answer":
                await self._handle_voice_answer(websocket, data)

            elif cmd == "ice_candidate":
                await self._handle_ice_candidate(websocket, data)

            elif cmd == "voice_hangup":
                await self._handle_voice_hangup(websocket, data)

            elif cmd == "join_voice_channel":
                await self._handle_join_voice_channel(websocket, data)

            elif cmd == "leave_voice_channel":
                await self._handle_leave_voice_channel(websocket, data)

            elif cmd == "get_presence":
                await self._handle_get_presence(websocket, data)
            elif cmd == "get_all_presence":
                await self._handle_get_all_presence(websocket, data)
            elif cmd == "search":
                await self._handle_search(websocket, data)
            elif cmd == "ack_delivered":
                await self._handle_ack_delivered(websocket, data)
            elif cmd == "ack_read":
                await self._handle_ack_read(websocket, data)
            elif cmd == "upload_prekeys":
                await self._handle_upload_prekeys(websocket, data)
            elif cmd == "get_prekey_bundle":
                await self._handle_get_prekey_bundle(websocket, data)
            elif cmd == "sealed_dm":
                await self._handle_sealed_dm(websocket, data)
            elif cmd == "verify_contact":
                await self._handle_verify_contact(websocket, data)
            elif cmd == "get_contact_verification":
                await self._handle_get_contact_verification(websocket, data)
            elif cmd == "list_verified_contacts":
                await self._handle_list_verified_contacts(websocket, data)
            elif cmd == "subscribe_push":
                await self._handle_subscribe_push(websocket, data)
            elif cmd == "unsubscribe_push":
                await self._handle_unsubscribe_push(websocket, data)
            elif cmd == "list_dm_sessions":
                await self._handle_list_dm_sessions(websocket, data)
            elif cmd == "expire_dm_session":
                await self._handle_expire_dm_session(websocket, data)
            elif cmd == "save_session_state":
                await self._handle_save_session_state(websocket, data)
            elif cmd == "upload_sender_key":
                await self._handle_upload_sender_key(websocket, data)
            elif cmd == "get_sender_key":
                await self._handle_get_sender_key(websocket, data)
            elif cmd == "distribute_sender_key":
                _op_id = data.get("op_id")
                _actor = self._client_webids.get(websocket, "")
                if _op_id and self._store:
                    _prior = self._store.get_operation_result(_op_id)
                    if _prior is not None:
                        await websocket.send(json.dumps({"type": "distribute_sender_key_ack", "op_id": _op_id, "result_code": _prior.get("result_code", "ok"), "replayed": True}))
                    else:
                        await self._handle_distribute_sender_key(websocket, data)
                        self._store.record_operation_result(_op_id, "distribute_sender_key", _actor, data.get("device_id"), "ok")
                else:
                    await self._handle_distribute_sender_key(websocket, data)
            elif cmd == "ack_sender_key_rotation":
                await self._handle_ack_sender_key_rotation(websocket, data)
            elif cmd == "register_device":
                await self._handle_register_device(websocket, data)
            elif cmd == "list_devices":
                await self._handle_list_devices(websocket, data)
            elif cmd == "unregister_device":
                await self._handle_unregister_device(websocket, data)
            elif cmd == "rotate_spk":
                await self._handle_rotate_spk(websocket, data)
            elif cmd == "session_unknown":
                await self._handle_session_unknown(websocket, data)
            elif cmd == "session_ready":
                await self._handle_session_ready(websocket, data)
            elif cmd == "catch_up":
                await self._handle_catch_up(websocket, data)
            elif cmd == "catch_up_ack":
                await self._handle_catch_up_ack(websocket, data)
            elif cmd == "get_peer_devices":
                await self._handle_get_peer_devices(websocket, data)
            elif cmd == "get_peer_device_keys":
                await self._handle_get_peer_device_keys(websocket, data)
            elif cmd == "send_dm_fanout":
                await self._handle_send_dm_fanout(websocket, data)
            elif cmd == "sync_contact_verifications":
                await self._handle_sync_contact_verifications(websocket, data)
            elif cmd == "apply_contact_verification_sync":
                await self._handle_apply_contact_verification_sync(websocket, data)
            elif cmd == "dm_decrypt_failed":
                await self._handle_dm_decrypt_failed(websocket, data)
            elif cmd == "set_primary_device":
                await self._handle_set_primary_device(websocket, data)
            elif cmd == "revoke_device_and_rekey":
                await self._handle_revoke_device_and_rekey(websocket, data)
            elif cmd == "pair_start":
                await self._handle_pair_start(websocket, data)
            elif cmd == "pair_submit":
                await self._handle_pair_submit(websocket, data)
            elif cmd == "pair_approve":
                await self._handle_pair_approve(websocket, data)
            elif cmd == "pair_cancel":
                await self._handle_pair_cancel(websocket, data)
            elif cmd == "device_recovery_code_generate":
                await self._handle_device_recovery_code_generate(websocket, data)
            elif cmd == "device_recovery_code_use":
                await self._handle_device_recovery_code_use(websocket, data)
            elif cmd == "join_voice_channel":
                await self._handle_join_voice_channel(websocket, data)
            elif cmd == "get_identity":
                await self._handle_get_identity(websocket, data)
            elif cmd == "get_connect_id":
                await self._handle_get_connect_id(websocket, data)
            elif cmd == "resolve_connect_id":
                await self._handle_resolve_connect_id(websocket, data)
            elif cmd == "request_hole_punch":
                await self._handle_request_hole_punch(websocket, data)
            elif cmd == "hole_punch_complete_notify":
                await self._handle_hole_punch_complete_notify(websocket, data)
            elif cmd == "typing":
                await self._handle_typing(websocket, data)
            elif cmd == "add_reaction":
                await self._handle_add_reaction(websocket, data)
            elif cmd == "remove_reaction":
                await self._handle_remove_reaction(websocket, data)
            elif cmd == "set_thread_mute":
                await self._handle_set_thread_mute(websocket, data)
            elif cmd == "block":
                await self._handle_block(websocket, data)
            elif cmd == "unblock":
                await self._handle_unblock(websocket, data)
            elif cmd == "mark_read":
                await self._handle_mark_read(websocket, data)
            elif cmd == "update_last_read":
                await self._handle_update_last_read(websocket, data)
            elif cmd == "get_message":
                await self._handle_get_message(websocket, data)
            elif cmd == "get_receipts":
                await self._handle_get_receipts(websocket, data)
            elif cmd == "create_invite":
                await self._handle_create_invite(websocket, data)
            elif cmd == "join_by_invite":
                await self._handle_join_by_invite(websocket, data)
            elif cmd == "get_notifications":
                await self._handle_get_notifications(websocket, data)
            elif cmd == "mark_notification_read":
                await self._handle_mark_notification_read(websocket, data)
            elif cmd == "set_identity":
                await self._handle_set_identity(websocket, data)
            elif cmd == "set_receipts_enabled":
                _pref_webid = self._client_webids.get(websocket, "")
                if _pref_webid:
                    self._client_receipts_prefs[_pref_webid] = bool(data.get("enabled", True))
            elif cmd == "set_link_previews_enabled":
                self._link_previews_enabled = bool(data.get("enabled", False))
            elif cmd == "read_dm":
                await self._handle_read_dm(websocket, data)
            elif cmd == "read_room":
                await self._handle_read_room(websocket, data)
            elif cmd == "chat_room_create":
                await self._handle_chat_room_create(websocket, data)
            elif cmd == "join_room":
                await self._handle_join_room(websocket, data)
            elif cmd == "kick_member":
                await self._handle_kick_member(websocket, data)
            elif cmd == "ban_member":
                await self._handle_ban_member(websocket, data)
            elif cmd == "unban_member":
                await self._handle_unban_member(websocket, data)
            elif cmd == "mute_member":
                await self._handle_mute_member(websocket, data)
            elif cmd == "unmute_member":
                await self._handle_unmute_member(websocket, data)
            elif cmd == "get_room_bans":
                await self._handle_get_room_bans(websocket, data)
            elif cmd == "get_message_readers":
                await self._handle_get_message_readers(websocket, data)
            elif cmd == "pin_message":
                await self._handle_pin_message(websocket, data)
            elif cmd == "get_pins":
                await self._handle_get_pins(websocket, data)
            elif cmd == "unpin_message":
                await self._handle_unpin_message(websocket, data)
            elif cmd == "connect_css":
                await self._handle_connect_css(websocket, data)
            elif cmd == "disconnect_pod":
                await self._handle_disconnect_pod(websocket, data)
            elif cmd == "reconnect_pod":
                await self._handle_reconnect_pod(websocket, data)
            elif cmd == "get_my_address":
                await self._handle_get_my_address(websocket, data)
            elif cmd == "get_relationships":
                await self._handle_get_relationships(websocket, data)
            elif cmd == "pod_status":
                await self._handle_pod_status(websocket, data)
            elif cmd == "send_friend_request":
                await self._handle_send_friend_request(websocket, data)

            elif cmd == "accept_friend_request":
                await self._handle_accept_friend_request(websocket, data)

            elif cmd == "list_friend_requests":
                await self._handle_list_friend_requests(websocket, data)

            elif cmd == "restore_contacts":
                await self._handle_restore_contacts(websocket, data)

            elif cmd == "schedule_message":
                await self._handle_schedule_message(websocket, data)
            elif cmd == "list_scheduled":
                await self._handle_list_scheduled(websocket, data)
            elif cmd == "cancel_scheduled":
                await self._handle_cancel_scheduled(websocket, data)
            elif cmd == "set_disappear_timer":
                await self._handle_set_disappear_timer(websocket, data)
            elif cmd == "get_disappear_timer":
                await self._handle_get_disappear_timer(websocket, data)
            elif cmd == "send_voice_message":
                await self._handle_send_voice_message(websocket, data)
            elif cmd == "screenshare_started":
                await self._handle_screenshare_started(websocket, data)
            elif cmd == "screenshare_stopped":
                await self._handle_screenshare_stopped(websocket, data)
            elif cmd == "list_sessions":
                await self._handle_list_sessions(websocket, data)
            elif cmd == "revoke_session":
                await self._handle_revoke_session(websocket, data)
            elif cmd == "logout_all_devices":
                await self._handle_logout_all_devices(websocket, data)
            elif cmd == "set_member_role":
                await self._handle_set_member_role(websocket, data)
            elif cmd == "get_room_roles":
                await self._handle_get_room_roles(websocket, data)
            elif cmd == "create_webhook":
                await self._handle_create_webhook(websocket, data)
            elif cmd == "list_webhooks":
                await self._handle_list_webhooks(websocket, data)
            elif cmd == "delete_webhook":
                await self._handle_delete_webhook(websocket, data)
            elif cmd == "rotate_webhook":
                await self._handle_rotate_webhook(websocket, data)
            elif cmd == "revoke_contact":
                await self._handle_revoke_contact(websocket, data)
            elif cmd == "get_audit_logs":
                await self._handle_get_audit_logs(websocket, data)
            elif cmd == "get_security_events":
                await self._handle_get_security_events(websocket, data)
            elif cmd == "get_runtime_security_state":
                await self._handle_get_runtime_security_state(websocket, data)
            elif cmd == "get_degraded_mode_state":
                await self._handle_get_degraded_mode_state(websocket, data)
            elif cmd == "get_realtime_abuse_signals":
                await self._handle_get_realtime_abuse_signals(websocket, data)
            elif cmd == "approve_peer_gateway_change":
                await self._handle_approve_peer_gateway_change(websocket, data)
            elif cmd == "prepare_recovery_operation":
                await self._handle_prepare_recovery_operation(websocket, data)
            elif cmd == "confirm_recovery_operation":
                await self._handle_confirm_recovery_operation(websocket, data)
            elif cmd == "export_security_snapshot":
                await self._handle_export_security_snapshot(websocket, data)
            elif cmd == "resolve_peer_trust_dispute":
                await self._handle_resolve_peer_trust_dispute(websocket, data)
            elif cmd == "list_quarantine_items":
                await self._handle_list_quarantine_items(websocket, data)
            elif cmd == "release_quarantine_item":
                await self._handle_release_quarantine_item(websocket, data)
            elif cmd == "drop_quarantine_item":
                await self._handle_drop_quarantine_item(websocket, data)
            elif cmd == "ack_checksum_mismatch":
                await self._handle_ack_checksum_mismatch(websocket, data)
            elif cmd == "set_security_tier":
                await self._handle_set_security_tier(websocket, data)
            elif cmd == "get_security_tier_state":
                await self._handle_get_security_tier_state(websocket, data)
            elif cmd == "set_retention_lock":
                await self._handle_set_retention_lock(websocket, data)
            elif cmd == "list_retention_locks":
                await self._handle_list_retention_locks(websocket, data)
            elif cmd == "clear_retention_lock":
                await self._handle_clear_retention_lock(websocket, data)
            elif cmd == "run_security_self_test":
                await self._handle_run_security_self_test(websocket, data)
            # R11 commands
            elif cmd == "request_admin_action":
                await self._handle_request_admin_action(websocket, data)
            elif cmd == "confirm_admin_action":
                await self._handle_confirm_admin_action(websocket, data)
            elif cmd == "simulate_incident_policy":
                await self._handle_simulate_incident_policy(websocket, data)
            elif cmd == "create_trust_revocation":
                await self._handle_create_trust_revocation(websocket, data)
            elif cmd == "list_trust_revocations":
                await self._handle_list_trust_revocations(websocket, data)
            elif cmd == "start_compromise_recovery":
                await self._handle_start_compromise_recovery(websocket, data)
            elif cmd == "get_compromise_recovery_status":
                await self._handle_get_compromise_recovery_status(websocket, data)
            elif cmd == "resume_compromise_recovery":
                await self._handle_resume_compromise_recovery(websocket, data)
            elif cmd == "abort_compromise_recovery":
                await self._handle_abort_compromise_recovery(websocket, data)
            elif cmd == "get_security_event_stream":
                await self._handle_get_security_event_stream(websocket, data)
            elif cmd == "get_solid_migration_errors":
                await self._handle_get_solid_migration_errors(websocket, data)
            elif cmd == "get_access_grants_policy_state":
                await self._handle_get_access_grants_policy_state(websocket, data)
            elif cmd == "get_security_exit_gate_status":
                await self._handle_get_security_exit_gate_status(websocket, data)
            elif cmd == "list_recovery_drill_templates":
                await self._handle_list_recovery_drill_templates(websocket, data)
            elif cmd == "run_recovery_drill":
                await self._handle_run_recovery_drill(websocket, data)
            elif cmd == "get_recovery_drill_report":
                await self._handle_get_recovery_drill_report(websocket, data)
            else:
                logger.warning(f"Unknown command: {cmd}")
                await websocket.send(json.dumps({"type": "error", "message": f"Unknown command: {cmd}"}))

        except Exception as exc:
            logger.error("Error executing command %s: %s", cmd, exc, exc_info=True)
            await websocket.send(json.dumps({"type": "error", "code": "E_INTERNAL", "message": "internal error"}))

    async def _handle_send_friend_request(self, websocket, data: dict):
        """Handle send_friend_request command."""
        target_address = data.get("target_address", "")
        
        # Split on last @
        if "@" not in target_address:
            await websocket.send(json.dumps({
                "type": "error", "message": "invalid_address",
                "detail": "Expected format: did:key:…@wss://gateway or did:key:…@https://gateway",
            }))
            return

        last_at = target_address.rfind("@")
        target_did = target_address[:last_at]
        target_gateway_url = target_address[last_at + 1:]

        if not target_did or not target_gateway_url:
            await websocket.send(json.dumps({
                "type": "error", "message": "invalid_address",
                "detail": "Expected format: did:key:…@wss://gateway or did:key:…@https://gateway",
            }))
            return

        # Extract target_pub_hex from DID
        try:
            from .didkey import did_to_pub_key
            target_pub_bytes = did_to_pub_key(target_did)
            target_pub_hex = target_pub_bytes.hex()
        except Exception as exc:
            logger.warning(f"Failed to parse DID {target_did}: {exc}")
            await websocket.send(json.dumps({
                "type": "error", "message": "invalid_address",
                "detail": f"Could not parse DID '{target_did}' — check the address and try again.",
            }))
            return
        
        # Build capabilities
        from .federation import Capability
        caps = [Capability(with_="stash://dm/", can="crud/write")]
        
        # Create invite — include our HTTP base URL so the acceptor can POST back
        from . import handshake
        my_http_url = self._gateway_http_url()
        if not my_http_url:
            ws = self._ws_public_url()
            my_http_url = ws.replace("wss://", "https://").replace("ws://", "http://")
        sender_webid = self._client_webids.get(websocket, "")
        sender_dn = (self._display_names.get(websocket)
                     or (self._store.get_display_name(sender_webid) if self._store else None))
        invite = handshake.create_invite(
            self.agent.identity_key,
            self.agent.store_pub_bytes,
            caps,
            endpoint_hints=[my_http_url],
            display_name=sender_dn or None,
            e2e_pub=self._store.get_x25519_pub(self._client_webids.get(websocket, "")) if self._store else None,
        )

        # POST invite to target gateway — accept both http:// and ws:// target URLs
        http_target = target_gateway_url.replace("wss://", "https://").replace("ws://", "http://")
        if not _is_safe_gateway_url(http_target):
            await websocket.send(json.dumps({
                "type": "error",
                "message": "invalid_address",
                "detail": "Target gateway URL resolves to a private or disallowed address.",
            }))
            return
        # R7: HTTPS enforcement for federation endpoints
        if not os.environ.get("PROXION_ALLOW_INSECURE_FEDERATION") == "1":
            if http_target.startswith("http://"):
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "insecure_federation_endpoint",
                    "detail": "Federation endpoints must use HTTPS. Set PROXION_ALLOW_INSECURE_FEDERATION=1 to override.",
                }))
                return
        from .network import async_safe_post
        sent = await async_safe_post(f"{http_target}/invite", invite.to_dict())
        if not sent:
            await websocket.send(json.dumps({
                "type": "error",
                "message": "delivery_failed",
                "detail": f"Could not reach {http_target} — check the address and try again.",
            }))
            return

        # Save pending invite if store is available
        if self._store:
            self._store.save_pending_invite(invite.to_dict(), target_did)

        await websocket.send(json.dumps({
            "type": "friend_request_sent",
            "invitation_id": invite.invitation_id,
            "target_did": target_did,
            "target_address": target_address,
        }))

    async def _handle_accept_friend_request(self, websocket, data: dict):
        """Accept an inbound FederationInvite: issue a RelationshipCertificate,
        persist it, notify the requester's gateway, and emit the cert to the browser."""
        invitation_id = data.get("invitation_id")

        if not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "no_store"}))
            return

        invite_dict = self._store.get_pending_invite(invitation_id)
        if not invite_dict:
            await websocket.send(json.dumps({"type": "error", "message": "invite_not_found"}))
            return

        from .federation import FederationInvite, RelationshipCertificate, Capability
        from .handshake import _ed25519_verify
        from .didkey import pub_key_to_did
        import time

        try:
            invite = FederationInvite.from_dict(invite_dict)
        except Exception as exc:
            logger.warning(f"Failed to deserialize invite: {exc}")
            await websocket.send(json.dumps({"type": "error", "message": "invalid_invite"}))
            return

        if not invite.verify(_ed25519_verify):
            await websocket.send(json.dumps({"type": "error", "message": "invalid_signature"}))
            return

        if invite.expires_at < time.time():
            await websocket.send(json.dumps({"type": "error", "message": "expired"}))
            return

        # Extract requester identity
        alice_pub_hex = invite.issuer.get("public_key", "")
        try:
            alice_pub_bytes = bytes.fromhex(alice_pub_hex)
            alice_did = pub_key_to_did(alice_pub_bytes)
        except Exception:
            await websocket.send(json.dumps({"type": "error", "message": "invalid_issuer_key"}))
            return

        bob_pub_hex = self.agent.identity_pub_bytes.hex()
        bob_did = pub_key_to_did(self.agent.identity_pub_bytes)

        # Build RelationshipCertificate (Bob is issuer, Alice is subject)
        cert = RelationshipCertificate(
            issuer=bob_pub_hex,
            subject=alice_pub_hex,
            capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        )
        cert.sign(self.agent.identity_key)

        # Persist to SQLite and pod
        self._store.save_relationship(cert.to_dict(), peer_did=alice_did)
        # Store the requester's browser E2E key (from the invite) so we can encrypt
        # the first DM to the right key even if we message before receiving one.
        _alice_e2e = invite.issuer.get("e2e_key")
        if _alice_e2e and alice_did:
            self._store.save_e2e_key(alice_did, _alice_e2e)
        asyncio.create_task(self._sync_cert_to_pod(cert.to_dict()))

        # Register Alice's endpoint so relay routing works immediately
        requester_gw_ws = (invite.endpoint_hints or [None])[0]
        if requester_gw_ws and alice_did:
            self._record_peer_gateway(alice_did, requester_gw_ws)

        # Populate dm_clients so pod-backed DMs can use the relationship
        pod_client = self._pod_client()
        if pod_client:
            self.dm_clients[cert.certificate_id] = (cert, pod_client)

        # Mark invite as accepted
        self._store.mark_invite_status(invitation_id, "accepted")

        # POST acceptance back to requester's gateway so Alice creates her side too
        if requester_gw_ws:
            http_base = requester_gw_ws.replace("wss://", "https://").replace("ws://", "http://")
            if _is_safe_gateway_url(http_base):
                accept_url = http_base.rstrip("/") + "/invite/accept"
                accept_payload = {
                    "@type": "InviteAcceptance",
                    "invitation_id": invitation_id,
                    "certificate": cert.to_dict(),
                    "from_did": bob_did,
                    "from_pub_hex": bob_pub_hex,
                    "from_gateway_http_url": self._gateway_http_url(),
                    # Browser-level E2E key (NOT the gateway store key used for sealing):
                    # carry the acceptor's client x25519 so the requester can encrypt the
                    # first DM to the right key. See federation-status memory.
                    "from_e2e_key": self._store.get_x25519_pub(self._client_webids.get(websocket, "")) or "",
                }
                asyncio.create_task(self._post_invite_accept(accept_url, accept_payload))
            else:
                logger.warning("Skipping invite accept POST — peer endpoint_hint is a private URL: %s", http_base)

        # Emit cert to browser so it writes the contact to the pod
        await websocket.send(json.dumps({
            "type": "friend_request_accepted",
            "invitation_id": invitation_id,
            "certificate": cert.to_dict(),
            "peer_did": alice_did,
        }))

    async def _post_invite_accept(self, url: str, payload: dict) -> None:
        """POST the acceptance notification to the requester's gateway via SSRF-safe transport."""
        from .network import async_safe_post
        ok = await async_safe_post(url, payload)
        if not ok:
            logger.warning(f"invite/accept POST to {url!r} failed or was SSRF-blocked")

    async def _handle_restore_contacts(self, websocket, data: dict) -> None:
        """Rehydrate dm_clients and SQLite from pod-persisted RelationshipCertificates."""
        certs = data.get("certs", [])
        if not isinstance(certs, list):
            return
        from .didkey import pub_key_to_did
        owner_pub_hex = self.agent.identity_pub_bytes.hex()
        restored = 0
        for cert_dict in certs:
            if not isinstance(cert_dict, dict):
                continue
            cert_id = cert_dict.get("certificate_id")
            if not cert_id:
                continue
            try:
                # Reject certs that don't involve this gateway owner as issuer or subject.
                # Prevents a compromised client from injecting third-party relationships.
                issuer = cert_dict.get("issuer", "")
                subject = cert_dict.get("subject", "")
                if issuer != owner_pub_hex and subject != owner_pub_hex:
                    logger.warning(
                        f"restore_contacts: rejected cert {cert_id} — "
                        "neither issuer nor subject matches gateway owner"
                    )
                    continue
                peer_pub_hex = subject if issuer == owner_pub_hex else issuer
                peer_did = None
                if peer_pub_hex:
                    peer_did = pub_key_to_did(bytes.fromhex(peer_pub_hex))
                if self._store:
                    self._store.save_relationship(cert_dict, peer_did=peer_did)
                    asyncio.create_task(self._sync_cert_to_pod(cert_dict))
                pod_client = self._pod_client()
                if pod_client and cert_id not in self.dm_clients:
                    cert_obj = RelationshipCertificate.from_dict(cert_dict)
                    self.dm_clients[cert_id] = (cert_obj, pod_client)
                if peer_did:
                    endpoint = cert_dict.get("endpoint_hints", [None])[0] if cert_dict.get("endpoint_hints") else None
                    if endpoint:
                        self._record_peer_gateway(peer_did, endpoint)
                restored += 1
            except Exception as exc:
                logger.debug(f"restore_contacts: skipped cert {cert_id}: {exc}")
        logger.info(f"restore_contacts: rehydrated {restored} certs")

    async def _handle_list_friend_requests(self, websocket, data: dict):
        """Handle list_friend_requests command."""
        pending = []
        relationships = []
        caller_did = self._client_webids.get(websocket, "")
        from .didkey import pub_key_to_did as _p2d
        owner_did = _p2d(self.agent.identity_pub_bytes)
        owner_pub_hex = self.agent.identity_pub_bytes.hex()

        if self._store:
            # Only show pending invites to the gateway owner (both sent and received)
            if caller_did == owner_did:
                pending_invites = self._store.list_pending_invites("pending")
                for inv_dict in pending_invites:
                    pending.append({
                        "invitation_id": inv_dict.get("invitation_id"),
                        "target_did": inv_dict.get("issuer", {}).get("did"),
                        "status": "pending"
                    })

            # Scope relationships to the calling identity
            rels = self._store.list_relationships(owner_webid=caller_did)
            for rel_dict in rels:
                relationships.append({
                    "certificate_id": rel_dict.get("id"),
                    "peer_did": rel_dict.get("subject"),
                    "expires_at": rel_dict.get("expires_at")
                })
        
        await websocket.send(json.dumps({
            "type": "friend_requests",
            "pending": pending,
            "relationships": relationships
        }))

    async def _handle_revoke_contact(self, websocket, data: dict) -> None:
        """R12.3.1 — Mark a contact's cert as revoked, update in-memory set, broadcast event."""
        cert_id = data.get("cert_id", "")
        caller_webid = self._client_webids.get(websocket, "")
        if not cert_id or not self._store:
            await websocket.send(json.dumps({"type": "error", "message": "cert_id required"}))
            return
        cert_dict = self._store.get_relationship_by_cert_id(cert_id)
        if not cert_dict:
            await websocket.send(json.dumps({"type": "error", "message": "cert_not_found"}))
            return
        peer_did = cert_dict.get("peer_did", "")
        self._store.mark_revoked(cert_id, peer_did)
        self._revoked_dids.add(peer_did)
        # Scope to the revoker's OWN identity (all their devices, so each purges
        # the contact + cached DM plaintext) rather than telling every session on
        # the gateway that this user revoked this peer (a relationship-metadata
        # leak on a shared gateway). Fall back to broadcast if the caller isn't
        # identified — single-user-safe (their own sessions only).
        _revoke_event = {
            "type": "contact_revoked",
            "cert_id": cert_id,
            "peer_did": peer_did,
        }
        if caller_webid:
            await self._send_to_identity(caller_webid, json.dumps(_revoke_event))
        else:
            await self.broadcast(_revoke_event)

    def _build_discovery_data(self) -> dict:
        """Build the /.well-known/proxion discovery payload."""
        import pathlib as _pl
        from .didkey import pub_key_to_did as _p2d
        from .pop import fingerprint as _fp
        gw_did = _p2d(self.agent.identity_pub_bytes)
        _vpath = _pl.Path(__file__).parent.parent.parent / "version.txt"
        if not _vpath.exists():
            _vpath = _pl.Path(__file__).parent / "version.txt"
        _gw_version = _vpath.read_text().strip() if _vpath.exists() else "0.1.0"
        _env_auth = os.environ.get("PROXION_REQUIRE_AUTH", "")
        _loopback_or_wildcard = self.config.host in (
            "127.0.0.1", "localhost", "::1", "", "0.0.0.0", "::")
        _require_auth_flag = (
            _env_auth == "1" or
            (_env_auth != "0" and not _loopback_or_wildcard)
        )
        data = {
            "proxion_version": "0.1",
            "gateway_version": _gw_version,
            "did": gw_did,
            "gateway_url": self._ws_public_url(),
            "gateway_http_url": self._gateway_http_url(),
            "proxion_address": self._proxion_address(),
            "require_auth": _require_auth_flag,
            "fingerprint": _fp(self.agent.identity_pub_bytes),
        }
        if not self.config.public_url:
            data["nat_warning"] = True
        data["upnp_mapped"] = self.config.upnp_mapped
        data["local_ip"]    = getattr(self, "_local_ip", "127.0.0.1")
        data["local_port"]  = self.config.http_port or 8080
        if self.dm_clients and self._pod_url:
            data["pod_url"] = self._pod_url
        if self._store:
            own_x25519 = self._store.get_x25519_pub(gw_did)
            if own_x25519:
                data["x25519_pub"] = own_x25519
            dn = self._store.get_display_name(gw_did)
            if dn:
                data["display_name"] = dn
        return data

    def _make_ssl_context(self):
        """Build and return an SSLContext from config, or None if no certs configured."""
        if self.config.ssl_certfile and self.config.ssl_keyfile:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.config.ssl_certfile, self.config.ssl_keyfile)
            return ctx
        return None

    def _ws_public_url(self) -> str:
        """Return the public WebSocket URL for this gateway."""
        tls_on = bool(self.config.ssl_certfile and self.config.ssl_keyfile)
        if self.config.public_url:
            url = self.config.public_url
            # When this gateway terminates TLS, the web UI is served over https and
            # a secure page cannot open an insecure ws:// socket (browsers block it
            # as mixed content). An explicit ws:// public_url in that case can never
            # connect, so upgrade it to wss://. (Reverse-proxy setups terminate TLS
            # upstream and leave ssl_certfile unset, so they're untouched.)
            if tls_on and url.startswith("ws://"):
                url = "wss://" + url[len("ws://"):]
            return url
        scheme = "wss" if tls_on else "ws"
        # Use 127.0.0.1 (not "localhost") when binding to all interfaces — on Windows,
        # "localhost" resolves to ::1 (IPv6) first but the server only binds IPv4 (0.0.0.0),
        # causing the browser WebSocket to get connection-refused before falling back.
        host = self.config.host if self.config.host != "0.0.0.0" else "127.0.0.1"
        return f"{scheme}://{host}:{self.config.port}"

    @staticmethod
    def _is_trusted_origin(origin: bytes, http_port: int, peer_ip: str = "") -> bool:
        """Return True if the HTTP Origin header is local/Tauri (trusted for sensitive endpoints).

        b"null" is NOT trusted — browsers send it from sandboxed iframes and it is
        trivially forgeable.  An absent Origin header is only trusted when the TCP
        connection comes from a loopback address (127.0.0.1 or ::1); a remote peer
        with no Origin header is not trusted (closes the curl-from-LAN bypass).
        peer_ip="" means the caller did not provide an IP (test / internal context);
        treat as loopback for backward compatibility.
        """
        if not origin:
            return peer_ip in ("", "127.0.0.1", "::1")
        o = origin.decode("utf-8", errors="replace").lower().rstrip("/")
        trusted = {
            f"http://127.0.0.1:{http_port}",
            f"http://localhost:{http_port}",
            "tauri://localhost",
            "https://tauri.localhost",
        }
        return o in trusted

    @staticmethod
    def _redact_dict(data: dict, keys: set = None) -> dict:
        """Return a copy of *data* with sensitive keys replaced by '<redacted>'."""
        if keys is None:
            keys = {"passphrase", "token", "secret", "secret_token", "bearer", "authorization",
                    "password", "key", "private_key", "webhook_token"}
        return {
            k: "<redacted>" if k.lower() in keys else v
            for k, v in data.items()
        }

    # _serve_http: moved to _gateway_http.py (HttpEndpointsMixin).

    async def _handle_voice_signal_relay(self, data: dict) -> tuple[str, str]:
        """Handle an inbound voice signal relayed from a peer gateway.

        Delivers immediately to the target's WebSocket; never queued.
        """
        to_webid = data.get("to_webid", "")
        from_webid = data.get("from_webid", "")
        signal_type = data.get("signal_type", "")
        signal_data = data.get("signal_data") or {}
        session_id = data.get("session_id", "")

        if not to_webid or not signal_type:
            return "400 Bad Request", '{"error":"missing voice signal fields"}'

        # Anti-spoof: only accept a voice signal from a peer the recipient has a
        # relationship with, or a co-member of a voice channel. Otherwise any
        # gateway could spam voice invites at any webid and spoof the caller.
        if self._store and from_webid:
            _related = bool(self._store.get_relationship_by_did(from_webid))
            _co_channel = any(
                from_webid in ch.get("members", {}) and to_webid in ch.get("members", {})
                for ch in self._voice_channels.values()
            )
            if not (_related or _co_channel):
                return "202 Accepted", '{"status":"ignored"}'
            if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
                return "202 Accepted", '{"status":"ignored"}'

        target_sockets = self._sockets_for(to_webid)  # _sockets_for handles the own-identity fallback
        if not target_sockets:
            return "202 Accepted", '{"status":"offline"}'

        event = json.dumps({
            "type": "voice_signal",
            "signal_type": signal_type,
            "session_id": session_id,
            "from_webid": from_webid,
            "signal_data": signal_data,
        })
        delivered = False
        for ws in target_sockets:
            try:
                await ws.send(event)
                delivered = True
            except Exception:
                pass
        return ("200 OK", '{"status":"delivered"}') if delivered else ("202 Accepted", '{"status":"offline"}')

    async def _handle_room_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed room message — deliver to local members of that room."""
        room_id = data.get("room_id", "")
        from_webid = data.get("from_webid", "")
        message_id = data.get("message_id", "")
        content = data.get("content", "")
        timestamp = data.get("timestamp", "")
        display_name = data.get("from_display_name") or data.get("display_name") or from_webid[:12]

        if not room_id or not from_webid or not message_id:
            return "400 Bad Request", '{"error":"missing_room_relay_fields"}'

        room = self._local_rooms.get(room_id)
        if not room:
            return "404 Not Found", '{"error":"room_not_found"}'

        # Moderation enforcement (R-C1): drop relayed messages from banned/muted
        # senders. This is what makes ban/mute effective across gateways — a
        # banned user on another gateway cannot get messages into the room.
        if self._store:
            if self._store.is_room_banned(room_id, from_webid):
                return "403 Forbidden", '{"error":"sender_banned"}'
            if self._store.is_room_muted(room_id, from_webid):
                return "403 Forbidden", '{"error":"sender_muted"}'
            # Sender verification: from_webid must be a known member (local or
            # federated) of the room. Without this a peer gateway could inject a
            # message appearing "from" an arbitrary non-banned webid into a room
            # you host. Fail-open only when we have NO membership records at all
            # (avoid breaking a legit member whose federation join wasn't tracked).
            _known = set(self._store.get_room_members(room_id))
            _known |= {m.get("member_did") for m in self._store.get_federated_room_members(room_id)}
            _known.discard("")
            if _known and from_webid not in _known:
                return "403 Forbidden", '{"error":"sender_not_member"}'

        event = {
            "type": "message",
            "source": "relay",
            "room_id": room_id,
            "thread_id": room_id,
            "from_webid": from_webid,
            "from_display_name": display_name,
            "content": content,
            "timestamp": timestamp,
            "message_id": message_id,
            "own": False,
        }
        delivered = False
        for ws in list(room.get("members", set())):
            try:
                await ws.send(json.dumps(event))
                delivered = True
            except Exception:
                pass
        if delivered and self._store:
            self._store.save_message(
                message_id, room_id, "relay",
                from_webid, display_name, content, timestamp,
            )

        # WebPush for members with no active socket
        _vpk  = getattr(self, "_vapid_private_pem", None)
        _vsub = getattr(self, "_vapid_subject", None)
        if self._store and _vpk and _vsub:
            from .webpush import send_web_push
            _all_member_dids = self._store.get_room_members(room_id) or []
            for _mid in _all_member_dids:
                if _mid == from_webid:
                    continue
                if self._any_socket(_mid):
                    continue  # already delivered via WebSocket
                if self._store.is_thread_muted(_mid, room_id):
                    continue  # member muted this room — no push
                _subs = self._store.get_push_subscriptions(_mid)
                for _sub in (_subs or []):
                    try:
                        send_web_push(
                            subscription={
                                "endpoint": _sub["endpoint"],
                                "keys": {
                                    "p256dh": _sub["p256dh_b64"],
                                    "auth":   _sub["auth_b64"],
                                },
                            },
                            payload={
                                "type": "message",
                                "thread_id": room_id,
                                "display_name": display_name,
                                "room_name": room.get("name", ""),
                            },
                            vapid_private_pem=_vpk,
                            vapid_subject=_vsub,
                        )
                    except Exception:
                        pass

        return "200 OK", '{"status":"delivered"}' if delivered else '{"status":"offline"}'

    async def _handle_dm_disappear_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed DM disappear-timer from a peer gateway. Set the timer
        on OUR cert_id so our expiry loop deletes the shared messages too."""
        from_webid = data.get("from_webid", "")
        to_webid   = data.get("to_webid", "")
        try:
            ms = max(0, int(data.get("ms", 0)))
        except (TypeError, ValueError):
            return "400 Bad Request", '{"error":"invalid_ms"}'
        if not from_webid or not to_webid:
            return "400 Bad Request", '{"error":"missing_fields"}'
        cert_dict = self._store.get_relationship_by_did(from_webid) if self._store else None
        if not cert_dict:
            return "200 OK", '{"status":"received"}'
        if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
            return "200 OK", '{"status":"received"}'
        our_cert_id = cert_dict.get("certificate_id") or cert_dict.get("id") or ""
        if our_cert_id:
            self._dm_disappear_timers[our_cert_id] = ms
            if self._store:
                try:
                    self._store.set_dm_disappear_timer(our_cert_id, ms)
                except Exception:
                    pass
            await self._send_to_identity(to_webid, json.dumps({
                "type": "disappear_timer_updated", "room_id": our_cert_id, "ms": ms,
            }))
        return "200 OK", '{"status":"received"}'

    async def _handle_dm_pin_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed DM pin/unpin from a peer gateway. Persist keyed by OUR
        cert_id and deliver message_pinned/unpinned to the local recipient."""
        from_webid = data.get("from_webid", "")
        to_webid   = data.get("to_webid", "")
        message_id = data.get("message_id", "")
        action     = data.get("action", "pin")
        if not all([from_webid, to_webid, message_id]):
            return "400 Bad Request", '{"error":"missing_pin_fields"}'
        cert_dict = self._store.get_relationship_by_did(from_webid) if self._store else None
        if not cert_dict:
            return "200 OK", '{"status":"received"}'
        if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
            return "200 OK", '{"status":"received"}'
        our_cert_id = cert_dict.get("certificate_id") or cert_dict.get("id") or ""
        if self._store and our_cert_id:
            try:
                if action == "unpin":
                    self._store.remove_pin(our_cert_id, message_id)
                else:
                    _mr = self._store.get_messages_by_ids([message_id])
                    _c = (_mr[0].get("content", "") if _mr else "")
                    self._store.save_pin(our_cert_id, message_id, from_webid, _c)
            except Exception:
                pass
        await self._send_to_identity(to_webid, json.dumps({
            "type": "unpinned" if action == "unpin" else "message_pinned",
            "thread_id": our_cert_id,
            "message_id": message_id,
        }))
        return "200 OK", '{"status":"received"}'

    async def _handle_dm_delete_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed DM delete-for-everyone from a peer gateway. Remove our
        stored copy (only if the peer authored it) and deliver message_deleted to
        the local recipient, keyed by OUR cert_id for the relationship."""
        from_webid = data.get("from_webid", "")
        to_webid   = data.get("to_webid", "")
        message_id = data.get("message_id", "")
        if not all([from_webid, to_webid, message_id]):
            return "400 Bad Request", '{"error":"missing_delete_fields"}'
        cert_dict = self._store.get_relationship_by_did(from_webid) if self._store else None
        if not cert_dict:
            return "200 OK", '{"status":"received"}'
        if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
            return "200 OK", '{"status":"received"}'
        our_cert_id = cert_dict.get("certificate_id") or cert_dict.get("id") or ""
        # Only let a peer delete a message THEY authored (if we have it stored).
        if self._store:
            try:
                _sender = self._store.get_message_sender(message_id)
                if _sender and _sender != from_webid:
                    return "200 OK", '{"status":"received"}'
                self._store.delete_message(message_id)
            except Exception:
                pass
        await self._send_to_identity(to_webid, json.dumps({
            "type": "message_deleted",
            "thread_id": our_cert_id,
            "message_id": message_id,
        }))
        return "200 OK", '{"status":"received"}'

    async def _handle_dm_edit_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed DM edit from a peer gateway. Update our stored copy
        and deliver message_edited to the local recipient, keyed by OUR cert_id
        for the relationship (cert_id asymmetry)."""
        from_webid  = data.get("from_webid", "")
        to_webid    = data.get("to_webid", "")
        message_id  = data.get("message_id", "")
        new_content = data.get("new_content", "")
        if not all([from_webid, to_webid, message_id]) or not isinstance(new_content, str):
            return "400 Bad Request", '{"error":"missing_edit_fields"}'
        if len(new_content.encode("utf-8")) > 16_384:
            return "400 Bad Request", '{"error":"content_too_large"}'
        cert_dict = self._store.get_relationship_by_did(from_webid) if self._store else None
        if not cert_dict:
            return "200 OK", '{"status":"received"}'   # no relationship, no reveal
        if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
            return "200 OK", '{"status":"received"}'
        our_cert_id = cert_dict.get("certificate_id") or cert_dict.get("id") or ""
        if self._store:
            try:
                self._store.update_message(message_id, new_content,
                                           edited_at=datetime.now(timezone.utc).isoformat())
            except Exception:
                pass
        await self._send_to_identity(to_webid, json.dumps({
            "type": "message_edited",
            "thread_id": our_cert_id,
            "message_id": message_id,
            "new_content": new_content,
        }))
        return "200 OK", '{"status":"received"}'

    async def _handle_dm_reaction_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed DM reaction add/remove from a peer gateway. Deliver a
        reaction_added/removed to the local recipient, keyed by OUR cert_id for
        the relationship (the sender's thread_id is their cert_id — the cert_id
        asymmetry — so we map from_webid -> our cert via get_relationship_by_did)."""
        from_webid = data.get("from_webid", "")
        to_webid   = data.get("to_webid", "")
        message_id = data.get("message_id", "")
        emoji      = data.get("emoji", "")
        action     = data.get("action", "add")
        if not all([from_webid, to_webid, message_id, emoji]):
            return "400 Bad Request", '{"error":"missing_reaction_fields"}'
        # Anti-spoof: only accept reactions from a peer we hold a relationship
        # with. Unknown senders are silently accepted (200, no block-reveal) but
        # not delivered — matching the relay block-enforcement model.
        cert_dict = self._store.get_relationship_by_did(from_webid) if self._store else None
        if not cert_dict:
            return "200 OK", '{"status":"received"}'
        if from_webid in getattr(self, "_revoked_dids", set()):
            return "200 OK", '{"status":"received"}'
        if self.blocklist.is_blocked(from_webid):
            return "200 OK", '{"status":"received"}'
        our_cert_id = cert_dict.get("certificate_id") or cert_dict.get("id") or ""
        if self._store and our_cert_id:
            try:
                if action == "remove":
                    self._store.remove_reaction(our_cert_id, message_id, emoji, from_webid)
                else:
                    self._store.save_reaction(our_cert_id, message_id, emoji, from_webid)
            except Exception:
                pass
        event = json.dumps({
            "type": "reaction_removed" if action == "remove" else "reaction_added",
            "thread_id": our_cert_id,
            "message_id": message_id,
            "emoji": emoji,
            "from_webid": from_webid,
        })
        await self._send_to_identity(to_webid, event)
        return "200 OK", '{"status":"received"}'

    async def _handle_room_reaction_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed reaction add/remove — deliver to local room members."""
        room_id    = data.get("room_id", "")
        message_id = data.get("message_id", "")
        emoji      = data.get("emoji", "")
        from_webid = data.get("from_webid", "")
        action     = data.get("action", "add")
        if not all([room_id, message_id, emoji, from_webid]):
            return "400 Bad Request", '{"error":"missing_reaction_fields"}'
        room = self._local_rooms.get(room_id)
        if not room:
            return "404 Not Found", '{"error":"room_not_found"}'
        # Moderation enforcement (R-C1): banned/muted senders cannot react.
        if self._store and (self._store.is_room_banned(room_id, from_webid)
                            or self._store.is_room_muted(room_id, from_webid)):
            return "403 Forbidden", '{"error":"sender_moderated"}'
        if self._store:
            if action == "add":
                self._store.save_reaction(room_id, message_id, emoji, from_webid)
            else:
                self._store.remove_reaction(room_id, message_id, emoji, from_webid)
        event_type = "reaction_added" if action == "add" else "reaction_removed"
        event = json.dumps({
            "type": event_type,
            "thread_id": room_id,
            "message_id": message_id,
            "emoji": emoji,
            "from_webid": from_webid,
        })
        for ws in list(room.get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_room_moderation_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed moderation action (R-C1) — apply locally and notify members.

        Lets a ban/mute issued on the host gateway propagate to federated member
        gateways so they enforce and display it consistently.
        """
        room_id = data.get("room_id", "")
        action  = data.get("action", "")
        target  = data.get("webid", "")
        caller  = data.get("from_webid", "")
        if not room_id or not action or not target or not self._store:
            return "400 Bad Request", '{"error":"missing_moderation_fields"}'
        # Authz: the moderation action must come from the room's owner or an
        # admin. Without this ANY gateway reaching /relay could ban legit members
        # or UNBAN a banned user (defeating moderation) in a room you host. The
        # local mod path checks admin before relaying; the receiver must re-verify.
        _room = self._local_rooms.get(room_id)
        _owner = _room.get("creator_webid") if _room else None
        _is_admin = bool(caller) and self._store.get_room_role(room_id, caller) == "admin"
        if not caller or (caller != _owner and not _is_admin):
            return "403 Forbidden", '{"error":"not_authorized_to_moderate"}'
        if action == "ban":
            self._store.ban_room_member(room_id, target, caller, str(data.get("reason", "")))
            evt = {"type": "member_banned", "room_id": room_id, "webid": target,
                   "reason": data.get("reason", "")}
        elif action == "unban":
            self._store.unban_room_member(room_id, target)
            evt = {"type": "member_unbanned", "room_id": room_id, "webid": target}
        elif action == "mute":
            self._store.mute_room_member(room_id, target, caller, data.get("expires_at"))
            evt = {"type": "member_muted", "room_id": room_id, "webid": target,
                   "expires_at": data.get("expires_at")}
        elif action == "unmute":
            self._store.unmute_room_member(room_id, target)
            evt = {"type": "member_unmuted", "room_id": room_id, "webid": target}
        else:
            return "400 Bad Request", '{"error":"unknown_moderation_action"}'
        room = self._local_rooms.get(room_id)
        if room:
            payload = json.dumps(evt)
            for ws in list(room.get("members", set())):
                try:
                    await ws.send(payload)
                except Exception:
                    pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_room_edit_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed message edit — apply to local store and deliver to room members."""
        room_id     = data.get("room_id", "")
        message_id  = data.get("message_id", "")
        new_content = data.get("new_content", "")
        edited_at   = data.get("edited_at", "")
        from_webid  = data.get("from_webid", "")
        if not all([room_id, message_id, new_content]):
            return "400 Bad Request", '{"error":"missing_edit_fields"}'
        room = self._local_rooms.get(room_id)
        if not room:
            return "404 Not Found", '{"error":"room_not_found"}'
        # Authz: only the message's author (or the room owner) may edit it — the
        # relay path used to edit ANY message by id, letting a federated member's
        # gateway rewrite other members' messages. Mirrors the local edit check.
        if self._store:
            _sender = self._store.get_message_sender(message_id)
            if _sender and _sender != from_webid and room.get("creator_webid") != from_webid:
                return "403 Forbidden", '{"error":"not_message_author"}'
            self._store.update_message(message_id, new_content, edited_at,
                                       editor_webid=from_webid)
        event = json.dumps({
            "type": "message_edited",
            "message_id": message_id,
            "thread_id": room_id,
            "new_content": new_content,
            "edited_at": edited_at,
            "has_history": True,
        })
        for ws in list(room.get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_room_delete_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed message delete — remove from local store and deliver to room members."""
        room_id    = data.get("room_id", "")
        message_id = data.get("message_id", "")
        from_webid = data.get("from_webid", "")
        if not room_id or not message_id:
            return "400 Bad Request", '{"error":"missing_delete_fields"}'
        room = self._local_rooms.get(room_id)
        if not room:
            return "404 Not Found", '{"error":"room_not_found"}'
        # Authz: only the message's author (or the room owner) may delete it —
        # the relay path used to delete ANY message by id (no from_webid at all),
        # letting a federated member's gateway delete others' messages.
        if self._store and from_webid:
            _sender = self._store.get_message_sender(message_id)
            if _sender and _sender != from_webid and room.get("creator_webid") != from_webid:
                return "403 Forbidden", '{"error":"not_message_author"}'
        if self._store:
            self._store.delete_message(message_id)
        event = json.dumps({
            "type": "message_deleted",
            "message_id": message_id,
            "thread_id": room_id,
        })
        for ws in list(room.get("members", set())):
            try:
                await ws.send(event)
            except Exception:
                pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_presence_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed presence update — update local cache and broadcast to connected clients."""
        from_webid = data.get("from_webid", "")
        status = data.get("status", "")
        status_message = data.get("status_message", "")
        updated_at = data.get("updated_at", "")

        if not from_webid or status not in ("online", "away", "busy", "offline"):
            return "400 Bad Request", '{"error":"invalid_presence"}'

        # Anti-spoof: only accept presence for a peer we hold a relationship
        # with. Without this, any friended gateway could inject presence for ANY
        # webid (fake a stranger online, or a contact offline). Unknown senders
        # get 200 (no reveal) but are ignored. Also skip revoked/blocked peers.
        rel = self._store.get_relationship_by_did(from_webid) if self._store else None
        if not rel:
            return "200 OK", '{"status":"received"}'
        if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
            return "200 OK", '{"status":"received"}'

        self._user_presence[from_webid] = {
            "status": status,
            "status_message": status_message,
            "updated_at": updated_at,
            "last_active_at": updated_at,
        }
        presence_event = json.dumps({
            "type": "presence",
            "webid": from_webid,
            "status": status,
            "status_message": status_message,
            "updated_at": updated_at,
        })
        # Deliver to the local user(s) who actually have this peer as a contact,
        # not every session on the gateway (a multi-user leak). Fall back to
        # broadcast only when the owner is unknown — single-user-safe.
        _owner = self._store.get_relationship_owner(from_webid) if self._store else ""
        if _owner:
            await self._send_to_identity(_owner, presence_event)
        else:
            for ws in list(self.clients):
                try:
                    await ws.send(presence_event)
                except Exception:
                    pass
        return "200 OK", '{"status":"ok"}'

    async def _handle_typing_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed typing indicator — deliver to the local DM peer."""
        from_webid = data.get("from_webid", "")
        cert_id = data.get("cert_id", "")
        if not from_webid:
            return "400 Bad Request", '{"error":"missing_from_webid"}'
        # Anti-spoof: only accept typing from a peer we hold a relationship with,
        # so a gateway can't inject "X is typing" for arbitrary webids. Unknown
        # senders are ignored (200, no reveal). (Delivery logic below unchanged.)
        if self._store:
            if not self._store.get_relationship_by_did(from_webid):
                return "200 OK", '{"status":"received"}'
            if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
                return "200 OK", '{"status":"received"}'
        # Find the local user who is in this DM thread. (Was get_dm_threads() with
        # no owner → always empty, so cross-gateway typing never delivered.)
        if self._store and cert_id:
            threads = [t for t in self._store.get_all_dm_threads() if t["thread_id"] == cert_id]
            if threads:
                local_webid = threads[0].get("peer_webid") or threads[0].get("owner_webid")
                if local_webid:
                    sockets = self._sockets_for(local_webid)
                    event = json.dumps({"type": "typing", "from_webid": from_webid, "cert_id": cert_id})
                    for ws in sockets:
                        try:
                            await ws.send(event)
                        except Exception:
                            pass
        return "200 OK", '{"status":"ok"}'

    # _handle_invite_post / _handle_invite_accept_post: moved to
    # _gateway_http.py (HttpEndpointsMixin).

    async def _retention_purge_loop(self) -> None:
        """Purge old audit logs, security events, and expired DM sessions every 24 hours."""
        import os as _os_rp
        while True:
            await asyncio.sleep(86400)
            if not self._store:
                continue
            try:
                audit_days = int(_os_rp.environ.get("PROXION_AUDIT_RETENTION_DAYS", "90"))
                sec_days = int(_os_rp.environ.get("PROXION_SECURITY_EVENT_RETENTION_DAYS", "90"))
                dm_session_days = int(_os_rp.environ.get("PROXION_DM_SESSION_RETENTION_DAYS", "90"))
                audit_cutoff = time.time() - audit_days * 86400
                sec_cutoff = time.time() - sec_days * 86400
                audit_purged = self._store.purge_old_audit_logs(audit_cutoff)
                sec_purged = self._store.purge_old_security_events(sec_cutoff)
                sessions_purged = self._store.prune_expired_dm_sessions(
                    max_age_seconds=dm_session_days * 86400
                )
                idempotency_hours = int(_os_rp.environ.get("PROXION_IDEMPOTENCY_RETENTION_HOURS", "72"))
                idempotency_pruned = self._store.prune_expired_idempotency_ops(retention_hours=idempotency_hours)
                if audit_purged or sec_purged:
                    logger.info("Retention purge: %d audit logs, %d security events removed",
                                audit_purged, sec_purged)
                if sessions_purged:
                    logger.info("Retention purge: %d expired DM sessions removed", sessions_purged)
                if idempotency_pruned:
                    logger.info("Retention purge: %d expired idempotency records removed", idempotency_pruned)
                self._metrics["audit_logs_purged"] = self._metrics.get("audit_logs_purged", 0) + audit_purged
                self._metrics["security_events_purged"] = self._metrics.get("security_events_purged", 0) + sec_purged
            except Exception as exc:
                logger.warning("Retention purge failed: %s", exc)

    async def _prekey_replenishment_loop(self) -> None:
        """Check prekey pool depth and SPK age for connected users every 15 minutes."""
        _THRESHOLD = int(__import__("os").environ.get("PROXION_PREKEY_LOW_THRESHOLD", "5"))
        _SPK_MAX_AGE = float(__import__("os").environ.get("PROXION_SPK_MAX_AGE_SECONDS", "604800"))
        while True:
            await asyncio.sleep(900)  # 15 minutes
            if not self._store:
                continue
            try:
                for webid in list(self._webid_sockets.keys()):
                    if not webid:
                        continue
                    # One-time prekey low-count check
                    count = self._store.count_unused_one_time_prekeys(webid)
                    if count < _THRESHOLD:
                        for ws in self._sockets_for(webid):
                            try:
                                await ws.send(__import__("json").dumps({
                                    "type": "prekey_replenishment_needed",
                                    "current_count": count,
                                    "threshold": _THRESHOLD,
                                }))
                            except Exception:
                                pass
                    # Signed prekey rotation check (7-day default)
                    stale_spks = self._store.get_expired_signed_prekeys(webid, _SPK_MAX_AGE)
                    for spk in stale_spks:
                        for ws in self._sockets_for(webid):
                            try:
                                await ws.send(__import__("json").dumps({
                                    "type": "spk_rotation_needed",
                                    "prekey_id": spk["prekey_id"],
                                }))
                            except Exception:
                                pass
            except Exception as exc:
                logger.warning("Prekey replenishment check failed: %s", exc)

    async def _handle_catch_up(self, websocket, data: dict) -> None:
        """Return messages in a thread that the client missed (seq > since_seq).

        Response includes batch_hash = sha256(sorted "msg_id:seq" pairs) for
        integrity verification, and first_seq/last_seq bounds.
        """
        import hashlib as _hl
        import json as _j
        if not self._store:
            await websocket.send(_j.dumps({
                "type": "catch_up_batch", "messages": [], "thread_id": "",
                "first_seq": None, "last_seq": None, "batch_hash": "",
            }))
            return
        thread_id = data.get("thread_id", "")
        since_seq = int(data.get("since_seq", 0))
        limit = min(int(data.get("limit", 100)), 200)
        if not thread_id:
            await websocket.send(_j.dumps({"type": "error", "message": "thread_id required"}))
            return
        msgs = self._store.get_messages_since_seq(thread_id, since_seq, limit=limit)
        first_seq = msgs[0]["seq"] if msgs else None
        last_seq = msgs[-1]["seq"] if msgs else None
        hash_input = "|".join(
            sorted(f"{m.get('message_id', '')}:{m.get('seq', '')}" for m in msgs)
        ).encode()
        batch_hash = _hl.sha256(hash_input).hexdigest()
        self._metrics["catchup_batches_total"] += 1
        await websocket.send(_j.dumps({
            "type": "catch_up_batch",
            "thread_id": thread_id,
            "since_seq": since_seq,
            "first_seq": first_seq,
            "last_seq": last_seq,
            "batch_hash": batch_hash,
            "messages": msgs,
        }))

    async def _handle_catch_up_ack(self, websocket, data: dict) -> None:
        """Client ACKs a catch-up batch; updates the per-device watermark."""
        import json as _j
        thread_id = data.get("thread_id", "")
        last_seq = data.get("last_seq")
        owner_device_id = data.get("owner_device_id", "")
        owner_webid = self._client_webids.get(websocket, "")
        if not self._store or not thread_id or last_seq is None:
            await websocket.send(_j.dumps({"type": "catch_up_ack_ok", "ok": False}))
            return
        self._store.set_catchup_watermark(owner_webid, owner_device_id, thread_id, int(last_seq))
        await websocket.send(_j.dumps({"type": "catch_up_ack_ok", "ok": True, "last_seq": last_seq}))

    async def _presence_loop(self):
        """Periodically broadcast presence heartbeats and mark idle users as away."""
        _sweep_tick = 0
        while True:
            await asyncio.sleep(30)
            _sweep_tick += 1
            try:
                now = datetime.now(timezone.utc).timestamp()
                for webid, pres in list(self._user_presence.items()):
                    if pres.get("status") == "online":
                        last_active = pres.get("last_active_at", pres.get("updated_at", ""))
                        try:
                            last_ts = datetime.fromisoformat(last_active).timestamp()
                        except Exception:
                            continue
                        if now - last_ts > 300:
                            self._user_presence[webid]["status"] = "away"
                            await self.broadcast({
                                "type": "presence_update",
                                "webid": webid,
                                "status": "away",
                                "status_message": pres.get("status_message", ""),
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                                "last_active_at": last_active,
                            })
            except Exception as exc:
                logger.debug("Presence loop error: %s", exc)

            # Every 10 ticks (~5 minutes): evict stale caches
            if _sweep_tick % 10 == 0:
                try:
                    _now = time.time()
                    # Evict expired identity_cache entries
                    stale = [k for k, v in self.identity_cache.items() if v.get("expiry", 0) < _now]
                    for k in stale:
                        del self.identity_cache[k]
                    self._metrics["identity_cache_evictions_total"] += len(stale)
                    # Prune relay_rate_limiter entries with no recent activity
                    _cutoff = time.monotonic() - 300
                    stale_ips = [ip for ip, dq in self._relay_rate_limiter.items()
                                 if not dq or dq[-1] < _cutoff]
                    for ip in stale_ips:
                        del self._relay_rate_limiter[ip]
                except Exception as exc:
                    logger.debug("Cache eviction error: %s", exc)

    async def _continuous_assurance_loop(self) -> None:
        """R16: Run scheduled assurance evaluations."""
        from .continuous_assurance import get_assurance_interval
        import asyncio as _asyncio
        interval = get_assurance_interval()
        await _asyncio.sleep(interval)
        while True:
            try:
                if self._assurance_loop_instance:
                    result = self._assurance_loop_instance.run_once()
                    state = result.get("assurance_state", "unknown")
                    if state == "red":
                        logger.warning("Continuous assurance state: RED")
                        if self._store:
                            self._store.save_security_event(
                                "assurance_state_red", "critical",
                                details=f"gates={result.get('gates', {}).get('all_pass')}",
                            )
                        try:
                            from .security_policy import get_policy as _get_pol_ca, TIER_RESTRICTIVE
                            _pol_ca = _get_pol_ca()
                            if _pol_ca.get_tier() < TIER_RESTRICTIVE:
                                _pol_ca.set_tier(TIER_RESTRICTIVE, reason="assurance_loop_critical")
                        except Exception:
                            pass
                    elif state == "amber":
                        logger.info("Continuous assurance state: AMBER")
                    else:
                        logger.debug("Continuous assurance state: green")
            except Exception as exc:
                logger.error("Continuous assurance loop error: %s", exc)
            await _asyncio.sleep(interval)

    async def _checksum_maintenance_loop(self) -> None:
        """R9: Periodic checksum verification for critical tables."""
        if not self._store:
            return
        _CRITICAL_TABLES = ["relationships", "peer_gateway_pins", "audit_logs"]
        _INTERVAL = int(os.environ.get("PROXION_CHECKSUM_INTERVAL", "300"))  # 5 min default
        # Wait one interval before first check to allow initial snapshot
        await asyncio.sleep(_INTERVAL)
        while True:
            try:
                mismatches = self._store.verify_security_checksums(_CRITICAL_TABLES)
                if mismatches:
                    self._checksum_mismatch = True
                    self._checksum_mismatch_tables = [m["table"] for m in mismatches]
                    for m in mismatches:
                        try:
                            self._store.save_security_event(
                                "checksum_mismatch_detected", "critical",
                                details=f"table={m['table']} expected={m['expected_checksum'][:16]} actual={m['actual_checksum'][:16]}",
                            )
                        except Exception:
                            pass
                    logger.error("Checksum mismatch detected in tables: %s", self._checksum_mismatch_tables)
                else:
                    # Refresh baseline snapshot after clean check
                    self._store.snapshot_security_checksums(_CRITICAL_TABLES)

                # R10: auto-escalate policy tier from rolling abuse signals
                try:
                    _signals = self._store.get_abuse_signal_rollups(hours=1)
                    from .security_policy import get_policy as _get_pol
                    _get_pol().escalate_tier_from_signals(_signals)
                except Exception:
                    pass

                # R12: replay cache cardinality pruning
                _REPLAY_TABLES = [
                    ("relay_seen_nonces", "seen_at"),
                    ("relay_seen_ids", "seen_at"),
                    ("dpop_seen_jti", "seen_at"),
                    ("invite_seen_nonces", "seen_at"),
                ]
                for _rt, _tc in _REPLAY_TABLES:
                    try:
                        _removed = self._store.prune_replay_table_by_cardinality(_rt, _tc)
                        if _removed > 0:
                            self._store.save_security_event(
                                "replay_cache_pruned", "info",
                                details=f"table={_rt} rows_removed={_removed}",
                            )
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("Checksum loop error: %s", exc)
            await asyncio.sleep(_INTERVAL)

    async def _build_security_snapshot(self) -> dict:
        """R9/R15: Build a signed security snapshot for export or HTTP endpoint."""
        snap: dict = {
            "generated_at": time.time(),
            "gateway_did": pub_key_to_did(self.agent.identity_pub_bytes),
            "checksum_mismatch": self._checksum_mismatch,
            "checksum_mismatch_tables": list(getattr(self, "_checksum_mismatch_tables", [])),
        }
        if self._store:
            snap["security_events"] = self._store.get_security_events(limit=50)
            snap["trust_disputes"] = self._store.list_peer_trust_disputes(status="open", limit=50)
            snap["abuse_signals_1h"] = self._store.get_abuse_signal_rollups(hours=1)
            snap["abuse_signals_24h"] = self._store.get_abuse_signal_rollups(hours=24)

            # R15: peer attestation summary
            try:
                snap["peer_attestation_summary"] = {
                    "active_count": len(self._store.get_open_security_events_by_severity([], limit=0)),
                }
            except Exception:
                snap["peer_attestation_summary"] = {}

            # R15: provenance verification status
            try:
                from .provenance_verify import verify_provenance
                _prov = verify_provenance()
                snap["provenance_verification"] = {
                    "ok": _prov["ok"],
                    "error_code": _prov.get("error_code", ""),
                }
            except Exception:
                snap["provenance_verification"] = {"ok": None}

            # R15: policy transition history slice
            try:
                snap["policy_transition_slice"] = self._store.get_recent_policy_tier_transitions(limit=5)
            except Exception:
                snap["policy_transition_slice"] = []

            # R15: scoped recovery budget usage (global scope, today)
            try:
                from datetime import datetime as _dt, timezone as _tz
                _day = _dt.now(_tz.utc).strftime("%Y-%m-%d")
                snap["scoped_recovery_budget"] = {
                    "day": _day,
                    "backup_global": self._store.check_scoped_budget("backup", "global", _day, 999),
                    "restore_global": self._store.check_scoped_budget("restore", "global", _day, 999),
                }
            except Exception:
                snap["scoped_recovery_budget"] = {}

            # R15: event stream continuity status
            try:
                _cursor = self._store.get_stream_cursor("siem_primary")
                snap["stream_continuity"] = {
                    "consumer_id": "siem_primary",
                    "last_sequence": _cursor["last_sequence"] if _cursor else None,
                }
            except Exception:
                snap["stream_continuity"] = {}

        snap_bytes = json.dumps(snap, default=str, sort_keys=True).encode()
        try:
            sig = self.agent.identity_key.sign(snap_bytes)
            snap["signature"] = sig.hex()
            snap["pub_key_hex"] = self.agent.identity_pub_bytes.hex()
        except Exception:
            pass
        return snap

    async def _build_security_self_test_report(self) -> dict:
        """R10: Build a signed security self-test report with non-destructive checks."""
        report: dict = {
            "generated_at": time.time(),
            "gateway_did": "",
            "checks": {},
        }
        try:
            report["gateway_did"] = pub_key_to_did(self.agent.identity_pub_bytes)
        except Exception:
            pass

        checks = report["checks"]

        # DB integrity
        checks["db_integrity"] = bool(self._store and getattr(self._store, "_integrity_ok", False))

        # Checksum mismatch flag
        checks["checksum_ok"] = not self._checksum_mismatch

        # Policy engine sanity
        try:
            from .security_policy import get_policy as _get_pol_sst
            _pol = _get_pol_sst()
            _test_decision = _pol.evaluate_ws_command("get_audit_logs", "nonexistent", "owner")
            checks["policy_engine"] = not _test_decision.allow
        except Exception:
            checks["policy_engine"] = False

        # Replay cache write/read check (in-memory)
        try:
            _test_nonce = secrets.token_hex(16)
            _replay_ok = False
            if self._store:
                self._store.record_relay_nonce(_test_nonce, time.time())
                _replay_ok = self._store.has_relay_nonce(_test_nonce)
            checks["replay_cache"] = _replay_ok
        except Exception:
            checks["replay_cache"] = False

        # Signed config status
        checks["signed_config_required"] = os.environ.get("PROXION_REQUIRE_SIGNED_CONFIG") == "1"

        # Runtime integrity
        checks["runtime_integrity_required"] = os.environ.get("PROXION_REQUIRE_RUNTIME_INTEGRITY") == "1"
        try:
            from .supply_chain import verify_runtime_integrity as _vri
            _ri = _vri(strict=False)
            checks["runtime_integrity_passed"] = _ri.get("passed", False)
        except Exception:
            checks["runtime_integrity_passed"] = False

        # Security tier
        try:
            from .security_policy import get_policy as _gp_sst
            checks["security_tier"] = _gp_sst().get_tier()
        except Exception:
            checks["security_tier"] = 0

        report["passed"] = all([
            checks.get("db_integrity", False),
            checks.get("checksum_ok", False),
            checks.get("policy_engine", False),
        ])

        # Sign report
        report_bytes = json.dumps({k: v for k, v in report.items() if k != "signature"},
                                  default=str, sort_keys=True).encode()
        try:
            sig = self.agent.identity_key.sign(report_bytes)
            report["signature"] = sig.hex()
            report["pub_key_hex"] = self.agent.identity_pub_bytes.hex()
        except Exception:
            pass

        # R11: append to security snapshot chain
        try:
            import hashlib as _hl_ssc, uuid as _uuid_ssc
            _snap_id = str(_uuid_ssc.uuid4())
            _snap_hash = _hl_ssc.sha256(report_bytes).hexdigest()
            _prev = self._store.get_latest_security_snapshot_chain_entry() if self._store else None
            _prev_hash = (_prev or {}).get("snapshot_hash", "")
            _signer_key_id = report.get("pub_key_hex", "")[:16]
            _sig_str = report.get("signature", "")
            if self._store:
                self._store.append_security_snapshot_chain_entry(
                    snapshot_id=_snap_id,
                    prev_hash=_prev_hash,
                    snapshot_hash=_snap_hash,
                    signature=_sig_str,
                    signer_key_id=_signer_key_id,
                )
            report["snapshot_id"] = _snap_id
            report["prev_hash"] = _prev_hash
            report["chain_ok"] = True
        except Exception:
            report["chain_ok"] = False

        return report

    async def run(self):
        """Start the gateway server and poll loop."""
        try:
            import websockets
        except ImportError:
            raise ImportError("The 'websockets' package is required for the gateway. Install with: pip install websockets")

        # R9: Signed config startup check
        try:
            from .config_verify import check_signed_config_startup
            check_signed_config_startup(store=self._store)
        except Exception as _cfg_exc:
            logger.error("Signed config startup check failed: %s", _cfg_exc)
            raise

        # R10: Runtime integrity startup check
        try:
            from .supply_chain import check_runtime_integrity_startup
            check_runtime_integrity_startup(store=self._store)
        except Exception as _ri_exc:
            logger.error("Runtime integrity startup check failed: %s", _ri_exc)
            raise

        # R14: Runtime SDK support guard
        try:
            from .sdk_support_guard import enforce_sdk_support_guard
            enforce_sdk_support_guard(store=self._store)
        except RuntimeError as _sdk_exc:
            logger.error("SDK support guard failed: %s", _sdk_exc)
            raise
        except Exception as _sdk_exc:
            logger.warning("SDK support guard error (non-fatal): %s", _sdk_exc)

        # R15: Build provenance guard (after config/integrity checks, before network bind)
        try:
            from .provenance_verify import enforce_provenance_guard
            enforce_provenance_guard()
        except RuntimeError as _prov_exc:
            logger.error("Build provenance guard failed: %s", _prov_exc)
            raise
        except Exception as _prov_exc:
            logger.warning("Build provenance guard error (non-fatal): %s", _prov_exc)

        # R16: Continuous assurance loop (optional)
        self._assurance_loop_instance = None
        try:
            from .continuous_assurance import ContinuousAssuranceLoop, is_continuous_assurance_enabled
            if is_continuous_assurance_enabled():
                self._assurance_loop_instance = ContinuousAssuranceLoop(store=self._store)
                logger.info("Continuous assurance loop enabled")
        except Exception as _ca_exc:
            logger.warning("Continuous assurance loop init error (non-fatal): %s", _ca_exc)

        # Connect to Solid Pod if credentials are configured
        await self._setup_pod_connection()

        ssl_ctx = self._make_ssl_context()
        scheme = "wss" if ssl_ctx else "ws"
        _max_ws_clients = int(os.environ.get("PROXION_MAX_CLIENTS", "200"))
        _allowed_origins_env = os.environ.get("PROXION_ALLOWED_ORIGINS", "")
        _allowed_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()] or None
        async with websockets.serve(
            self.handle_client, self.config.host, self.config.port,
            ssl=ssl_ctx, ping_interval=60, ping_timeout=20,
            max_size=4 * 1024 * 1024,
            origins=_allowed_origins,
        ):
            logger.info(f"Proxion gateway running on {scheme}://{self.config.host}:{self.config.port}")

            # 1. Start core loops (polling + flushing)
            main_tasks = [
                asyncio.create_task(self.poll_loop()),
                asyncio.create_task(self.flush_loop()),
                asyncio.create_task(self._presence_loop()),
                asyncio.create_task(self._scheduler_loop()),
                asyncio.create_task(self._pod_watchdog()),
                asyncio.create_task(self._relay_retry_loop()),
                asyncio.create_task(self._expire_messages_loop()),
                asyncio.create_task(self._retention_purge_loop()),
                asyncio.create_task(self._checksum_maintenance_loop()),
                asyncio.create_task(self._prekey_replenishment_loop()),
                asyncio.create_task(self._read_position_flush_loop()),
            ]

            # R16: Continuous assurance loop
            if self._assurance_loop_instance is not None:
                main_tasks.append(asyncio.create_task(self._continuous_assurance_loop()))

            # R38: drain our sealed mailbox from the relay node (if configured)
            if relay_fallback_url():
                main_tasks.append(asyncio.create_task(self._mailbox_drain_loop()))
            # R38: relay node — periodically purge expired mailbox blobs
            if relay_node_enabled():
                main_tasks.append(asyncio.create_task(self._mailbox_purge_loop()))

            # 1b. Optional HTTP server for serving the web UI
            if self.config.http_port and self.config.web_dir:
                main_tasks.append(asyncio.create_task(
                    self._serve_http(self.config.web_dir, self.config.http_port)
                ))

            # 2. Add push notifications if enabled
            if self.config.push:
                logger.info("Push mode enabled. Subscribing to resources...")
                from .notifications import watch_stash_uri
                import os as _os_push
                css_base = _os_push.getenv("CSS_ALICE_URL", "") # simplified for discovery
                
                for cert_id, (cert, client) in self.dm_clients.items():
                    try:
                        from .messaging import thread_path
                        topic = thread_path(cert.certificate_id)
                        main_tasks.append(asyncio.create_task(
                            watch_stash_uri(client, topic, self.trigger_poll, css_base)
                        ))
                        logger.info(f"Subscribed to updates for DM thread: {cert_id}")
                    except Exception as e:
                        logger.warning(f"Failed to subscribe to DM thread {cert_id}: {e}")
                
                for room_id, (membership, client) in self.room_memberships.items():
                    try:
                        topic = f"stash://rooms/{membership.room_id}/messages/"
                        main_tasks.append(asyncio.create_task(
                            watch_stash_uri(client, topic, self.trigger_poll, css_base)
                        ))
                        logger.info(f"Subscribed to updates for room: {room_id}")
                    except Exception as e:
                        logger.warning(f"Failed to subscribe to room {room_id}: {e}")

            # Register SIGTERM/SIGINT to set _stop_event (Windows-safe)
            import signal as _signal
            _loop = asyncio.get_event_loop()
            for _sig in (getattr(_signal, "SIGTERM", None), getattr(_signal, "SIGINT", None)):
                if _sig is not None:
                    try:
                        _loop.add_signal_handler(_sig, self._stop_event.set)
                    except (NotImplementedError, RuntimeError):
                        pass  # Windows: signal handlers not supported in asyncio loops

            # 3. Wait until a main task fails or shutdown is requested
            _wait_task = asyncio.create_task(
                asyncio.wait(main_tasks, return_when=asyncio.FIRST_EXCEPTION)
            )
            _stop_task = asyncio.create_task(self._stop_event.wait())
            try:
                await asyncio.wait(
                    [_wait_task, _stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                _stop_task.cancel()
                _wait_task.cancel()
                # Drain: notify clients and give them 2 s
                for ws in list(self.clients):
                    asyncio.create_task(ws.close(1001, "Gateway shutting down"))
                await asyncio.sleep(2)
                for t in main_tasks:
                    if not t.done():
                        t.cancel()
                # WAL checkpoint
                if self._store:
                    try:
                        self._store.checkpoint()
                    except Exception:
                        pass


async def run_gateway(*args, **kwargs):
    gateway = ProxionGateway(*args, **kwargs)
    await gateway.run()
