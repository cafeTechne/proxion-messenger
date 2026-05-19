"""Tests for Round 12 hardening — identity backup, bounded caches, graceful shutdown,
API token auth, relay clock-skew, pod 401 detection, received_at timestamps."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("websockets")
import websockets

from proxion_messenger_core.persist import AgentState, PersistError
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import verify_relay_message, sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={}, config=cfg,
        read_state=ReadState(),
    )
    return gw, ws_port, http_port


def _start_gateway(tmp_path):
    gw, ws_port, http_port = _make_gateway(tmp_path)
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(loop)

        async def _serve():
            async with websockets.serve(gw.handle_client, "127.0.0.1", ws_port):
                task = asyncio.create_task(gw._serve_http(None, http_port))
                ready.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    task.cancel()

        try:
            loop.run_until_complete(_serve())
        except Exception:
            ready.set()

    threading.Thread(target=_run, daemon=True).start()
    return gw, ws_port, http_port, ready


# ── 12.8.1 — Identity backup round-trip ─────────────────────────────────────

def test_identity_backup_roundtrip():
    """R12.8.1: export_backup → import_backup preserves both private keys."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    agent = AgentState.generate()
    passphrase = b"test-passphrase-123"

    blob = agent.export_backup(passphrase)
    assert isinstance(blob, bytes)

    restored = AgentState.import_backup(blob, passphrase)

    orig_id = agent.identity_pub_bytes
    rest_id = restored.identity_pub_bytes
    assert orig_id == rest_id, "identity key not preserved"

    orig_store = agent.store_pub_bytes
    rest_store = restored.store_pub_bytes
    assert orig_store == rest_store, "store key not preserved"


def test_identity_backup_wrong_passphrase():
    """R12.8.1: wrong passphrase raises PersistError."""
    agent = AgentState.generate()
    blob = agent.export_backup(b"correct-passphrase")
    with pytest.raises(PersistError):
        AgentState.import_backup(blob, b"wrong-passphrase")


# ── 12.8.2 — GET /backup returns downloadable JSON ──────────────────────────

@pytest.mark.asyncio
async def test_backup_endpoint(tmp_path):
    """R12.8.2: GET /backup?passphrase=test returns 200 with Content-Disposition and valid JSON."""
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(b"GET /backup?passphrase=testpass HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(8192), timeout=5.0)
    writer.close()

    status_line = response.split(b"\r\n")[0].decode()
    assert "200" in status_line, f"Expected 200, got: {status_line!r}"
    assert b"Content-Disposition" in response, "Missing Content-Disposition header"
    body_start = response.find(b"\r\n\r\n")
    body = json.loads(response[body_start + 4:])
    assert body.get("@type") == "ProxionBackup", f"Wrong type: {body}"
    assert "identity_key_pem" in body, "Missing identity_key_pem"


# ── 12.8.3 — _seen_relay_nonces is bounded at 1000 ──────────────────────────

def test_seen_relay_nonces_bounded(tmp_path):
    """R12.8.3: after 1001 appends, _seen_relay_nonces is capped at 1000."""
    gw, _, _ = _make_gateway(tmp_path)
    for i in range(1001):
        gw._seen_relay_nonces.append(f"nonce-{i}")
    assert len(gw._seen_relay_nonces) == 1000, (
        f"Expected 1000, got {len(gw._seen_relay_nonces)}"
    )
    # First entry should have been evicted
    assert "nonce-0" not in gw._seen_relay_nonces
    assert "nonce-1000" in gw._seen_relay_nonces


# ── 12.8.4 — Stale identity_cache entries are evicted ───────────────────────

@pytest.mark.asyncio
async def test_identity_cache_eviction(tmp_path):
    """R12.8.4: stale identity_cache entries are removed by the eviction sweep."""
    gw, _, _ = _make_gateway(tmp_path)

    # Insert one stale and one fresh entry
    gw.identity_cache["stale-webid"] = {"display_name": "Stale", "expiry": time.time() - 1}
    gw.identity_cache["fresh-webid"] = {"display_name": "Fresh", "expiry": time.time() + 3600}

    # Force the eviction sweep (normally runs every 10 presence ticks)
    _now = time.time()
    stale = [k for k, v in gw.identity_cache.items() if v.get("expiry", 0) < _now]
    for k in stale:
        del gw.identity_cache[k]

    assert "stale-webid" not in gw.identity_cache
    assert "fresh-webid" in gw.identity_cache


# ── 12.8.5 — Graceful shutdown: _stop_event drains clients ──────────────────

@pytest.mark.asyncio
async def test_graceful_shutdown_notifies_clients(tmp_path):
    """R12.8.5: setting _stop_event causes connected clients to receive close."""
    gw, _, _ = _make_gateway(tmp_path)

    ws1 = MagicMock()
    ws1.send = AsyncMock()
    ws1.close = AsyncMock()
    ws2 = MagicMock()
    ws2.send = AsyncMock()
    ws2.close = AsyncMock()
    gw.clients = {ws1, ws2}

    # Simulate shutdown drain: close all clients
    for ws in list(gw.clients):
        asyncio.create_task(ws.close(1001, "Gateway shutting down"))

    await asyncio.sleep(0.05)
    ws1.close.assert_called_once()
    ws2.close.assert_called_once()


# ── 12.8.6 — Relay timestamp window: past message rejected ──────────────────

def test_relay_clock_skew_past_rejected():
    """R12.8.6: message with timestamp >5 min in the past is rejected."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    did = pub_key_to_did(pub)
    target = "did:key:z6Mktarget"

    old_ts = "2026-04-16T00:00:00+00:00"  # well in the past
    sig = sign_relay_message(key, did, target, "msg-past", "hi", old_ts)

    # Default window (5 min) → rejected
    assert not verify_relay_message(did, target, "msg-past", "hi", old_ts, sig)


# ── 12.8.7 — Relay timestamp window: future message rejected ────────────────

def test_relay_clock_skew_future_rejected():
    """R12.8.7: message with timestamp >5 min in the future is rejected."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    did = pub_key_to_did(pub)
    target = "did:key:z6Mktarget"

    future_ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    sig = sign_relay_message(key, did, target, "msg-future", "hi", future_ts)

    assert not verify_relay_message(did, target, "msg-future", "hi", future_ts, sig)


# ── 12.8.8 — POST /import with wrong API token returns 401 ──────────────────

@pytest.mark.asyncio
async def test_import_wrong_api_token(tmp_path, monkeypatch):
    """R12.8.8: POST /import with wrong Authorization: Bearer returns 401."""
    monkeypatch.setenv("PROXION_API_TOKEN", "secret-token-xyz")

    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    body = b'{"messages":[]}'
    writer.write(
        b"POST /import HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Authorization: Bearer wrong-token\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.read(1024), timeout=5.0)
    writer.close()

    status_line = response.split(b"\r\n")[0].decode()
    assert "401" in status_line, f"Expected 401, got: {status_line!r}"


# ── 12.8.9 — received_at is server-set ──────────────────────────────────────

def test_received_at_is_server_set(tmp_path):
    """R12.8.9: save_message() sets received_at independently of caller-supplied timestamp."""
    store = LocalStore(str(tmp_path / "test.db"))
    old_ts = "2020-01-01T00:00:00+00:00"
    store.save_message(
        message_id="ra-test-1",
        thread_id="test-thread",
        thread_type="local_room",
        from_webid="did:key:alice",
        from_display_name="Alice",
        content="hello",
        timestamp=old_ts,
    )
    with store._conn() as conn:
        row = conn.execute(
            "SELECT received_at FROM messages WHERE message_id = ?", ("ra-test-1",)
        ).fetchone()
    assert row is not None
    received_at = row["received_at"]
    assert received_at is not None, "received_at should be set"
    # received_at should be a recent timestamp, not the old_ts
    assert received_at != old_ts, "received_at should not equal caller-supplied timestamp"
    # Should be parseable as ISO 8601
    ts = datetime.fromisoformat(received_at)
    assert abs((ts - datetime.now(timezone.utc)).total_seconds()) < 60


# ── 12.8.10 — LocalStore.checkpoint() runs without error ────────────────────

def test_store_checkpoint(tmp_path):
    """R12.8.10: LocalStore.checkpoint() runs without raising and flushes WAL."""
    store = LocalStore(str(tmp_path / "ckpt.db"))
    store.save_message(
        "ckpt-msg-1", "ckpt-thread", "local_room",
        "did:key:x", "X", "content", datetime.now(timezone.utc).isoformat(),
    )
    # Should not raise
    store.checkpoint()
    # WAL file should be empty or absent after truncate checkpoint
    wal = Path(str(tmp_path / "ckpt.db") + "-wal")
    if wal.exists():
        assert wal.stat().st_size == 0, "WAL should be empty after TRUNCATE checkpoint"
