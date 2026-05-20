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
from ._gateway_pod import PodSyncMixin, extract_mentions
from ._gateway_rooms import RoomHandlerMixin
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

class ProxionGateway(VoiceHandlerMixin, PodSyncMixin, RoomHandlerMixin, DmHandlerMixin, AuthHandlerMixin, MiscHandlerMixin):
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
        self._webhook_fire_ts: deque = deque()
        self._webhook_breakers: dict = {}  # webhook_id -> {"failures": int, "opened_at": float|None}
        self._checksum_mismatch: bool = False  # R9: set by checksum loop, cleared by ack_checksum_mismatch
        self._checksum_mismatch_tables: list = []  # R9: tables with mismatches
        self._client_webids = {}    # websocket -> identity str (did:key or pod webid)
        self._webid_sockets: dict = {}  # identity str -> set of websockets
        self._session_meta: dict = {}   # websocket -> {session_id, connected_at, ip_addr}
        self._did_pod_webids = {}   # did:key -> pod webid (set via link_pod)
        self._system_ws = set()      # stubs for internal operations (scheduler, etc)

        # Presence tracking
        self._user_presence = {}    # webid -> {"status": "online"|"offline"|"away"|"busy", "status_message": str, "updated_at": iso_timestamp, "last_active_at": iso_timestamp}

        # Cross-gateway relay: maps peer webid -> their gateway HTTP base URL
        self._peer_gateway_urls: dict = {}
        self._relay_queue: dict[str, list[dict]] = {}
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

        # Uptime tracking for /health endpoint
        self._start_time: float = time.time()

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
        }

        # Per-identity connection count for aggregated presence (R13.13)
        self._presence_by_identity: dict = {}  # webid -> set of ws

        # Semaphore bounding concurrent fire-and-forget pod sync tasks
        self._pod_sync_sem: asyncio.Semaphore = asyncio.Semaphore(8)

        # Read receipt toggle
        self._receipts_enabled: bool = True

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

    def _sockets_for(self, identity: str) -> list:
        """Return all connected sockets for identity (handles set and single-socket values)."""
        val = self._webid_sockets.get(identity)
        if val is None:
            return []
        candidates = val if isinstance(val, set) else {val}
        return [ws for ws in candidates if ws in self.clients]

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
        _AUTH_EXEMPT = {"ping", "pong", "auth_response", "register"}

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
                await self._handle_send_dm(websocket, data)
            elif cmd == "edit_message":
                await self._handle_edit_message(websocket, data)
            elif cmd == "send_room":
                await self._handle_send_room(websocket, data)
            elif cmd == "set_presence":
                await self._handle_set_presence(websocket, data)
            elif cmd == "get_rooms":
                await self._handle_get_rooms(websocket, data)
            elif cmd == "get_dms":
                await self._handle_get_dms(websocket, data)
            elif cmd == "send_file":
                await self._handle_send_file(websocket, data)
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
            elif cmd == "voice_invite":
                await self._handle_voice_invite(websocket, data)

            elif cmd == "voice_answer":
                await self._handle_voice_answer(websocket, data)

            elif cmd == "ice_candidate":
                await self._handle_ice_candidate(websocket, data)

            elif cmd == "voice_hangup":
                await self._handle_voice_hangup(websocket, data)

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
            elif cmd == "upload_sender_key":
                await self._handle_upload_sender_key(websocket, data)
            elif cmd == "get_sender_key":
                await self._handle_get_sender_key(websocket, data)
            elif cmd == "distribute_sender_key":
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
            elif cmd == "join_voice_channel":
                await self._handle_join_voice_channel(websocket, data)
            elif cmd == "get_identity":
                await self._handle_get_identity(websocket, data)
            elif cmd == "typing":
                await self._handle_typing(websocket, data)
            elif cmd == "add_reaction":
                await self._handle_add_reaction(websocket, data)
            elif cmd == "remove_reaction":
                await self._handle_remove_reaction(websocket, data)
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
                self._receipts_enabled = bool(data.get("enabled", True))
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

        # Persist to SQLite
        self._store.save_relationship(cert.to_dict(), peer_did=alice_did)

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
        await self.broadcast({
            "type": "contact_revoked",
            "cert_id": cert_id,
            "peer_did": peer_did,
        })

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
        if self.config.public_url:
            return self.config.public_url
        scheme = "wss" if (self.config.ssl_certfile and self.config.ssl_keyfile) else "ws"
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
                path = parts[1].split("?")[0] if len(parts) > 1 else "/"
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

                # Per-endpoint POST body size limits.
                _ENDPOINT_SIZE_LIMITS = {
                    "/relay":         128 * 1024,        # 128 KiB
                    "/invite":        128 * 1024,        # 128 KiB
                    "/invite/accept": 128 * 1024,        # 128 KiB
                    "/restore":       5 * 1024 * 1024,   # 5 MiB
                    "/import":        20 * 1024 * 1024,  # 20 MiB
                }
                _POST_MAX = 2 * 1024 * 1024  # 2 MB default for unlisted endpoints
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
                _JSON_POST_PATHS = {"/relay", "/invite", "/invite/accept", "/restore", "/import"}
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 65536)), timeout=10.0
                        )
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
                    gw_did = pub_key_to_did(self.agent.identity_pub_bytes)
                    http_url = self._gateway_http_url()
                    proxion_addr = self._proxion_address()
                    # R18.3.4: read version from version.txt next to the binary/package
                    import pathlib as _pl
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
                    discovery_data = {
                        "proxion_version": "0.1",
                        "gateway_version": _gw_version,
                        "did": gw_did,
                        "gateway_url": self._ws_public_url(),
                        "gateway_http_url": http_url,
                        "proxion_address": proxion_addr,
                        "require_auth": _require_auth_flag,
                    }
                    if self.dm_clients and self._pod_url:
                        discovery_data["pod_url"] = self._pod_url
                    if self._store:
                        own_x25519 = self._store.get_x25519_pub(gw_did)
                        if own_x25519:
                            discovery_data["x25519_pub"] = own_x25519
                        dn = self._store.get_display_name(gw_did)
                        if dn:
                            discovery_data["display_name"] = dn
                    # R11.2.1: include own fingerprint
                    from .pop import fingerprint as _fp
                    discovery_data["fingerprint"] = _fp(self.agent.identity_pub_bytes)
                    resp_bytes = json.dumps(discovery_data).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(resp_bytes)).encode() + b"\r\n\r\n" + resp_bytes
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
                    }).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + _SEC_HDR + _NO_STORE_HDR
                        + b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(health_body)).encode() + b"\r\n\r\n" + health_body
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 65536)), timeout=10.0
                        )
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
                        import urllib.parse as _up
                        proxion_addr = self._proxion_address()
                        redirect_url = "/invite?from=" + _up.quote(proxion_addr, safe="")
                        writer.write(
                            b"HTTP/1.1 302 Found\r\nLocation: " + redirect_url.encode()
                            + b"\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: 0\r\n\r\n"
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 4096)), timeout=5.0
                        )
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 65536)), timeout=10.0
                        )
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 8192)), timeout=10.0
                        )
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 4096)), timeout=5.0
                        )
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 4 * 1024 * 1024)), timeout=30.0
                        )
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
                                await asyncio.wait_for(
                                    reader.read(min(content_length, _IMPORT_MAX)), timeout=5.0
                                )
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
                        body = await asyncio.wait_for(
                            reader.read(min(content_length, 10 * 1024 * 1024)), timeout=30.0
                        )
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
                        _body_c = await asyncio.wait_for(
                            reader.read(min(content_length, 4096)), timeout=10.0
                        )
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
                        body_bytes = await asyncio.wait_for(
                            reader.read(min(content_length, 65536)), timeout=10.0
                        )
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

        # Reject payloads with a file attachment (not supported over relay)
        if "file" in data:
            return "400 Bad Request", '{"error":"unsupported_relay_attachment"}'

        # Reject unknown top-level keys
        _ALLOWED_RELAY_KEYS = frozenset({
            "from_webid", "from_display_name", "to_webid", "message_id", "content", "timestamp",
            "signature", "relay_nonce", "display_name", "origin_gateway_url",
            "sender_webid", "message_scope",
            # E2E keys
            "e2e", "nonce", "msg_num", "key_header", "ratchet_pub", "pn", "x25519_pub",
            # chain-integrity fields
            "seq_num", "prev_hash",
        })
        _unknown = set(data.keys()) - _ALLOWED_RELAY_KEYS
        if _unknown:
            return "400 Bad Request", '{"error":"unknown_relay_fields"}'

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

        # Record the sender's gateway URL for future relay (persist to SQLite)
        if origin_gateway and from_webid:
            self._record_peer_gateway(from_webid, origin_gateway)

        # Resolve thread_id: prefer cert_id so the browser routes to the right thread
        cert_id = None
        if self._store:
            cert_dict = self._store.get_relationship_by_did(from_webid)
            if cert_dict:
                cert_id = cert_dict.get("certificate_id")
            # Cache sender's X25519 pub key for future E2E bootstrap
            if "x25519_pub" in data:
                self._store.save_x25519_pub(from_webid, data["x25519_pub"])

        _E2E_KEYS = ("e2e", "nonce", "msg_num", "key_header", "ratchet_pub", "pn", "x25519_pub")
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
                if audit_purged or sec_purged:
                    logger.info("Retention purge: %d audit logs, %d security events removed",
                                audit_purged, sec_purged)
                if sessions_purged:
                    logger.info("Retention purge: %d expired DM sessions removed", sessions_purged)
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
        """Return messages in a thread that the client missed (seq > since_seq)."""
        if not self._store:
            await websocket.send(__import__("json").dumps({
                "type": "catch_up_batch", "messages": [], "thread_id": "",
            }))
            return
        thread_id = data.get("thread_id", "")
        since_seq = int(data.get("since_seq", 0))
        limit = min(int(data.get("limit", 100)), 200)
        if not thread_id:
            await websocket.send(__import__("json").dumps({"type": "error", "message": "thread_id required"}))
            return
        msgs = self._store.get_messages_since_seq(thread_id, since_seq, limit=limit)
        await websocket.send(__import__("json").dumps({
            "type": "catch_up_batch",
            "thread_id": thread_id,
            "since_seq": since_seq,
            "messages": msgs,
        }))

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
        _CRITICAL_TABLES = ["relationships", "peer_gateway_pins", "audit_logs", "security_events"]
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
            ]

            # R16: Continuous assurance loop
            if self._assurance_loop_instance is not None:
                main_tasks.append(asyncio.create_task(self._continuous_assurance_loop()))

            # 1b. Optional HTTP server for serving the web UI
            if self.config.http_port and self.config.web_dir:
                main_tasks.append(asyncio.create_task(
                    self._serve_http(self.config.web_dir, self.config.http_port)
                ))

            # 2. Add push notifications if enabled
            if self.config.push:
                logger.info("Push mode enabled. Subscribing to resources...")
                from .notifications import watch_stash_uri
                import os
                css_base = os.getenv("CSS_ALICE_URL", "") # simplified for discovery
                
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
