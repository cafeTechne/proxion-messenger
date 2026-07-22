"""Tests for Round 11 hardening — disappearing messages, fingerprint, session UI,
operational endpoints, schema migration."""
from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("websockets")
import websockets

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import fingerprint_words, pub_key_to_did, did_to_pub_key
from gwharness import start_gateway as _serve_gw


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_gateway(tmp_path, host="127.0.0.1"):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host=host, port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={}, config=cfg,
        read_state=ReadState(),
    )
    return gw, ws_port, http_port


def _start_gateway(tmp_path, host="127.0.0.1"):
    gw, ws_port, http_port = _make_gateway(tmp_path, host)
    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.ws_port, handle.http_port, handle.ready


def _make_mock_ws(gw, webid):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._webid_sockets.setdefault(webid, set()).add(ws)
    return ws


# ── 11.6.1 — Expire loop triggers deletion + broadcast ──────────────────────

@pytest.mark.asyncio
async def test_expire_loop_deletes_and_broadcasts(tmp_path):
    """R11.6.1: _expire_messages_loop deletes expired messages and broadcasts message_deleted."""
    gw, _, _ = _make_gateway(tmp_path)

    room_id = "expire-test-room"
    member_ws = _make_mock_ws(gw, "did:key:member")
    gw._local_rooms[room_id] = {
        "name": "Test",
        "code": "EXPTST",
        "members": {member_ws},
        "creator_webid": "did:key:member",
        "messages": [
            {"message_id": "old-msg-1", "timestamp": "2020-01-01T00:00:00+00:00"},
            {"message_id": "old-msg-2", "timestamp": "2020-01-02T00:00:00+00:00"},
        ],
    }
    if gw._store:
        gw._store.save_room(room_id, "Test", "EXPTST", "", "open", "did:key:member")
        for mid in ("old-msg-1", "old-msg-2"):
            gw._store.save_message(
                message_id=mid, thread_id=room_id, thread_type="room",
                from_webid="did:key:member", from_display_name="M",
                content="x", timestamp="2020-01-01T00:00:00+00:00",
            )
    # 1 ms timer — everything older than "now - 1ms" (i.e. everything from 2020) should be deleted
    gw._room_disappear_timers[room_id] = 1

    sleep_call_count = 0

    async def _fake_sleep(n):
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count >= 2:
            raise asyncio.CancelledError

    with patch("proxion_messenger_core._gateway_rooms.asyncio.sleep", side_effect=_fake_sleep):
        try:
            await gw._expire_messages_loop()
        except asyncio.CancelledError:
            pass

    # Verify messages were removed from in-memory room
    remaining = gw._local_rooms[room_id].get("messages", [])
    assert len(remaining) == 0, f"Expected 0 remaining messages, got {len(remaining)}"

    # Verify message_deleted was broadcast
    sent = [json.loads(c.args[0]) for c in member_ws.send.call_args_list]
    deleted_ids = {m.get("message_id") for m in sent if m.get("type") == "message_deleted"}
    assert "old-msg-1" in deleted_ids, f"old-msg-1 not in broadcast: {sent}"
    assert "old-msg-2" in deleted_ids, f"old-msg-2 not in broadcast: {sent}"


# ── 11.6.2 — fingerprint_words determinism ───────────────────────────────────

def test_fingerprint_words_determinism():
    """R11.6.2: fingerprint_words is deterministic and returns 6 words from the word list."""
    from proxion_messenger_core.didkey import _SAFETY_WORDS

    pub_bytes = b"\x00" * 32
    words1 = fingerprint_words(pub_bytes)
    words2 = fingerprint_words(pub_bytes)

    assert len(words1) == 6, f"Expected 6 words, got {len(words1)}: {words1}"
    assert words1 == words2, "fingerprint_words is not deterministic"
    for w in words1:
        assert w in _SAFETY_WORDS, f"Word {w!r} not in safety word list"

    # Different input → different words (probabilistically certain)
    other_words = fingerprint_words(b"\xff" * 32)
    assert words1 != other_words, "Different keys produced same fingerprint words"


# ── 11.6.3 — GET /fingerprint/<did> returns correct JSON ─────────────────────

@pytest.mark.asyncio
async def test_fingerprint_endpoint(tmp_path):
    """R11.6.3: GET /fingerprint/<did> returns fingerprint + 6 safety words; unknown DID → 404."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    import hashlib
    import base64

    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    did = pub_key_to_did(pub_bytes)
    encoded_did = did.replace(":", "%3A")

    # Valid DID
    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(f"GET /fingerprint/{encoded_did} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()

    lines = response.split(b"\r\n")
    status = lines[0].decode()
    assert "200" in status, f"Expected 200, got: {status!r}"
    body_start = response.find(b"\r\n\r\n")
    body = json.loads(response[body_start + 4:])
    assert body["did"] == did
    assert len(body["safety_words"]) == 6, f"Expected 6 safety_words: {body}"
    # pop.fingerprint strips base64 padding — match that
    expected_fp = base64.urlsafe_b64encode(hashlib.sha256(pub_bytes).digest()).decode().rstrip("=")
    assert body["fingerprint"] == expected_fp, f"Fingerprint mismatch: {body}"

    # Invalid DID → 404
    reader2, writer2 = await asyncio.open_connection("127.0.0.1", http_port)
    writer2.write(b"GET /fingerprint/did%3Akey%3AINVALID HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    await writer2.drain()
    response2 = await asyncio.wait_for(reader2.read(1024), timeout=5.0)
    writer2.close()
    assert b"404" in response2.split(b"\r\n")[0], f"Expected 404 for invalid DID: {response2[:200]}"


# ── 11.6.4 — logout_all_devices closes other sessions ───────────────────────

@pytest.mark.asyncio
async def test_logout_all_devices(tmp_path):
    """R11.6.4: logout_all_devices closes all other sessions for the calling webid."""
    gw, _, _ = _make_gateway(tmp_path)

    webid = "did:key:multidevice-user"
    ws_caller = _make_mock_ws(gw, webid)
    ws_other1 = _make_mock_ws(gw, webid)
    ws_other2 = _make_mock_ws(gw, webid)

    await gw.process_command(ws_caller, {"cmd": "logout_all_devices"})

    # caller should receive logout_all_complete
    caller_msgs = [json.loads(c.args[0]) for c in ws_caller.send.call_args_list]
    complete_msgs = [m for m in caller_msgs if m.get("type") == "logout_all_complete"]
    assert complete_msgs, f"Expected logout_all_complete to caller, got: {caller_msgs}"
    assert complete_msgs[0]["revoked_count"] == 2, (
        f"Expected revoked_count=2, got: {complete_msgs[0]}"
    )

    # other sessions should receive session_revoked
    for ws in (ws_other1, ws_other2):
        msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        assert any(m.get("type") == "session_revoked" for m in msgs), (
            f"Expected session_revoked on other WS, got: {msgs}"
        )
        ws.close.assert_called_once()


# ── 11.6.5 — GET /health returns 200 with expected fields ───────────────────

@pytest.mark.asyncio
async def test_health_endpoint(tmp_path):
    """R11.6.5: GET /health returns 200 with status, connected_clients, and uptime_s."""
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()

    status_line = response.split(b"\r\n")[0].decode()
    assert "200" in status_line, f"Expected 200, got: {status_line!r}"
    body_start = response.find(b"\r\n\r\n")
    body = json.loads(response[body_start + 4:])
    assert body.get("status") == "ok", f"Expected status=ok: {body}"
    assert "connected_clients" in body, f"Missing connected_clients: {body}"
    assert isinstance(body["connected_clients"], int), f"connected_clients not int: {body}"
    assert "uptime_s" in body, f"Missing uptime_s: {body}"
    assert body["uptime_s"] >= 0, f"Negative uptime: {body}"


# ── 11.6.6 — Oversized WS message is rejected ────────────────────────────────

@pytest.mark.asyncio
async def test_oversized_ws_message_rejected(tmp_path):
    """R11.6.6: WS message > 4 MB is rejected (connection closed by library) when
    the server has max_size=4MB configured."""
    gw, _, _ = _make_gateway(tmp_path)
    ws_port = _free_port()
    # Start an inline server with explicit max_size matching the gateway configuration
    async with websockets.serve(
        gw.handle_client, "127.0.0.1", ws_port, max_size=4 * 1024 * 1024
    ):
        closed = False
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:{ws_port}",
                max_size=None,  # no client-side cap so we can send a large message
            ) as conn:
                # Drain initial messages (e.g. the "config" message sent on connect)
                while True:
                    try:
                        await asyncio.wait_for(conn.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        break

                # Send a 5 MB message — larger than the server's 4 MB cap
                await conn.send("X" * (5 * 1024 * 1024))

                # Drain until the connection closes or we timeout
                try:
                    while True:
                        await asyncio.wait_for(conn.recv(), timeout=3.0)
                except websockets.exceptions.ConnectionClosed:
                    closed = True
                except asyncio.TimeoutError:
                    pytest.fail("Server did not close connection after oversized message")
        except websockets.exceptions.ConnectionClosed:
            closed = True
        assert closed, "Expected connection to be closed after oversized message"


# ── 11.6.7 — schema_version table exists after DB init ──────────────────────

def test_schema_version_table_exists(tmp_path):
    """R11.6.7: LocalStore creates schema_version table with a non-negative integer row."""
    store = LocalStore(str(tmp_path / "test.db"))
    with store._conn() as conn:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    assert row is not None, "schema_version table is empty after init"
    assert row["version"] >= 0, f"schema_version.version is negative: {row['version']}"
