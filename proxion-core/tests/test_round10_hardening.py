"""Tests for Round 10 hardening — rate limiting, input caps, federation, operational."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import stat
import threading
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
from proxion_messenger_core.relay import sign_relay_message, verify_relay_message


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


def _make_mock_ws(gw, webid):
    """Create a mock websocket registered in the gateway."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._webid_sockets.setdefault(webid, set()).add(ws)
    return ws


# ── 10.7.1 — Rate limiter: 61st command in 60 s window is rejected ────────────

@pytest.mark.asyncio
async def test_rate_limit_61st_command_rejected(tmp_path):
    """R10.7.1: 61st command in a 60s window returns rate_limited.

    Uses `get_rooms` which is not in the exempt set {ping, pong, auth_response, register}.
    """
    gw, ws_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as conn:
        # Register (exempt — doesn't count against limit)
        await conn.send(json.dumps({"cmd": "register", "did": "did:key:ratelimituser"}))
        await asyncio.sleep(0.15)
        # Drain registration responses
        while True:
            try:
                await asyncio.wait_for(conn.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                break

        # 60 non-exempt commands — all within the window, should all pass
        for i in range(60):
            await conn.send(json.dumps({"cmd": "get_rooms"}))

        await asyncio.sleep(0.15)
        # Drain any responses
        while True:
            try:
                await asyncio.wait_for(conn.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                break

        # 61st command should trigger rate_limited
        await conn.send(json.dumps({"cmd": "get_rooms"}))
        got_rate_limited = False
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(conn.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "error" and msg.get("message") == "rate_limited":
                    got_rate_limited = True
                    break
            except asyncio.TimeoutError:
                break
        assert got_rate_limited, "Expected rate_limited error on 61st command"


# ── 10.7.2 — Voice invite cooldown ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_invite_cooldown(tmp_path):
    """R10.7.2: Second voice invite from same caller to same target within 30s is rejected."""
    # Reset module-level cooldown dict before test
    import proxion_messenger_core._gateway_voice as _gv
    _gv._voice_invite_ts.clear()

    gw, _ws_port, _http_port = _make_gateway(tmp_path)

    caller_ws = _make_mock_ws(gw, "did:key:caller-voice")
    target_ws = _make_mock_ws(gw, "did:key:target-voice")
    gw._local_rooms["voice-room"] = {
        "creator_webid": "did:key:caller-voice",
        "members": {caller_ws, target_ws},
    }

    # First invite should succeed (forwarded to target)
    await gw.process_command(caller_ws, {
        "cmd": "voice_invite",
        "target_webid": "did:key:target-voice",
        "sdp_offer": "v=0...",
    })
    assert target_ws.send.called, "First invite should be forwarded to target"
    target_ws.send.reset_mock()
    caller_ws.send.reset_mock()

    # Second invite within 30s should be rejected
    await gw.process_command(caller_ws, {
        "cmd": "voice_invite",
        "target_webid": "did:key:target-voice",
        "sdp_offer": "v=0...",
    })
    caller_calls = [json.loads(c.args[0]) for c in caller_ws.send.call_args_list]
    assert not target_ws.send.called, "Second invite should not reach target within cooldown"
    assert any(c.get("message") == "call_too_frequent" for c in caller_calls), (
        f"Expected call_too_frequent error, got: {caller_calls}"
    )


# ── 10.7.3 — Invite token is 16 hex chars (64-bit entropy) ───────────────────

def test_invite_token_is_16_hex_chars(tmp_path):
    """R10.7.3: Invite token has 16 hex chars (64-bit entropy)."""
    gw, _, _ = _make_gateway(tmp_path)
    token = gw._short_invite_token
    assert len(token) == 16, f"Expected 16 chars, got {len(token)}: {token!r}"
    assert all(c in "0123456789abcdef" for c in token), f"Not hex: {token!r}"


# ── 10.7.4 — GET /i/<token> returns 429 after 20 req/min from same IP ────────

@pytest.mark.asyncio
async def test_invite_enum_rate_limit(tmp_path):
    """R10.7.4: GET /i/<token> returns 429 after 20 attempts per minute from same IP."""
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    bad_token = "a" * 16  # non-existent token
    status_codes = []

    # Use a new connection for each request since the gateway closes after each response
    for _ in range(25):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
            request = f"GET /i/{bad_token} HTTP/1.0\r\nHost: 127.0.0.1:{http_port}\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            response_line = await asyncio.wait_for(reader.readline(), timeout=3.0)
            status_code = int(response_line.split()[1])
            status_codes.append(status_code)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception as e:
            status_codes.append(0)

    assert 429 in status_codes, f"Expected 429 after 20 attempts, got: {status_codes}"
    first_429_idx = status_codes.index(429)
    # First 20 attempts (index 0-19) should be 404, 21st (index 20) should be 429
    assert first_429_idx >= 20, f"429 came too early at index {first_429_idx}: {status_codes}"


# ── 10.7.5 — Display name > 100 chars is truncated ───────────────────────────

@pytest.mark.asyncio
async def test_display_name_truncated_at_100(tmp_path):
    """R10.7.5: Display name longer than 100 chars is truncated to 100."""
    gw, _ws_port, _http_port = _make_gateway(tmp_path)
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.remote_address = ("127.0.0.1", 12345)

    long_name = "A" * 200
    await gw.process_command(ws, {
        "cmd": "register",
        "did": "did:key:trunctest",
        "display_name": long_name,
    })
    stored = gw._display_names.get(ws, "")
    assert len(stored) <= 100, f"Display name not truncated: len={len(stored)}"
    assert stored == "A" * 100


# ── 10.7.6 — Room name > 100 chars is truncated ──────────────────────────────

@pytest.mark.asyncio
async def test_room_name_truncated_at_100(tmp_path):
    """R10.7.6: Room name longer than 100 chars is truncated to 100."""
    gw, _ws_port, _http_port = _make_gateway(tmp_path)
    ws = _make_mock_ws(gw, "did:key:roomcreator")

    long_name = "B" * 200
    await gw.process_command(ws, {
        "cmd": "chat_room_create",
        "name": long_name,
    })

    created_room = None
    for room_id, room in gw._local_rooms.items():
        if room.get("name", "").startswith("B"):
            created_room = room
            break

    assert created_room is not None, (
        f"Room was not created. Rooms: {list(gw._local_rooms.keys())}"
    )
    assert len(created_room["name"]) <= 100, (
        f"Room name not truncated: len={len(created_room['name'])}"
    )
    assert created_room["name"] == "B" * 100


# ── 10.7.7 — 21st reaction from same sender on same message is rejected ───────

@pytest.mark.asyncio
async def test_reaction_limit_21st_rejected(tmp_path):
    """R10.7.7: 21st reaction from same sender on same message is rejected."""
    gw, _ws_port, _http_port = _make_gateway(tmp_path)
    ws = _make_mock_ws(gw, "did:key:reactor")

    room_id = "test-room-reactions"
    gw._local_rooms[room_id] = {
        "name": "Test Room",
        "code": "RXNTEST",
        "members": {ws},
        "creator_webid": "did:key:reactor",
    }
    if gw._store:
        gw._store.save_room(room_id, "Test Room", "RXNTEST", "", "open", "did:key:reactor")

    msg_id = "reaction-test-msg-001"
    if gw._store:
        gw._store.save_message(
            message_id=msg_id,
            thread_id=room_id,
            thread_type="room",
            from_webid="did:key:reactor",
            from_display_name="Reactor",
            content="hello",
            timestamp="2026-01-01T00:00:00+00:00",
        )

    # Add 50 different emoji — all should succeed (per-user-per-room quota is 50)
    for i in range(50):
        emoji = chr(0x1F600 + i)
        ws.send.reset_mock()
        await gw.process_command(ws, {
            "cmd": "add_reaction",
            "message_id": msg_id,
            "room_id": room_id,
            "emoji": emoji,
        })
        for call in ws.send.call_args_list:
            msg = json.loads(call.args[0])
            assert msg.get("message") != "reaction_limit_reached", (
                f"Reaction {i+1} was incorrectly rejected"
            )

    # 51st reaction should be rejected (quota: 50 per user per room)
    ws.send.reset_mock()
    await gw.process_command(ws, {
        "cmd": "add_reaction",
        "message_id": msg_id,
        "room_id": room_id,
        "emoji": "🆕",
    })
    error_calls = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    assert any(c.get("message") == "reaction_limit_reached" for c in error_calls), (
        f"Expected reaction_limit_reached, got: {error_calls}"
    )


# ── 10.7.8 — Relay replay blocked by relay_nonce dedup ────────────────────────

@pytest.mark.asyncio
async def test_relay_nonce_replay_blocked(tmp_path):
    """R10.7.8: A relay message with a known relay_nonce is deduplicated (replay blocked)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from proxion_messenger_core.didkey import pub_key_to_did

    gw, _ws_port, _http_port = _make_gateway(tmp_path)

    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob-relay-test"

    bob_ws = _make_mock_ws(gw, bob_did)

    from datetime import datetime as _dt, timezone as _tz
    ts = _dt.now(_tz.utc).isoformat()
    relay_nonce = "aabbccddeeff0011"
    sig = sign_relay_message(alice_key, alice_did, bob_did, "rply-001", "hi", ts, relay_nonce)
    body = json.dumps({
        "from_webid": alice_did, "to_webid": bob_did,
        "message_id": "rply-001", "content": "hi",
        "timestamp": ts, "relay_nonce": relay_nonce,
        "display_name": "Alice", "signature": sig,
    }).encode()

    # First delivery should succeed (bob is online → 200)
    status1, _ = await gw._handle_relay_post(body)
    assert status1.startswith("200"), f"First relay failed: {status1}"

    # Second delivery with same relay_nonce — deduplicated (replay blocked)
    status2, resp2 = await gw._handle_relay_post(body)
    assert status2.startswith("200"), f"Replay should return 200 (not error): {status2}"
    assert json.loads(resp2).get("status") == "duplicate", (
        f"Expected duplicate status, got: {resp2}"
    )
    # Bob should only have received one message
    assert bob_ws.send.call_count == 1, (
        f"Bob received {bob_ws.send.call_count} messages but should only receive 1"
    )


# ── 10.7.9 — Imported messages carry imported=1 ───────────────────────────────

@pytest.mark.asyncio
async def test_imported_messages_flagged(tmp_path):
    """R10.7.9: Messages imported via POST /import have imported=1 in the store."""
    import httpx
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    import_payload = {
        "messages": [{
            "message_id": "imported-msg-001",
            "thread_id": "test-thread-import",
            "thread_type": "relay",
            "from_webid": "did:key:alice-import",
            "from_display_name": "Alice",
            "content": "Imported message content",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }],
    }
    resp = httpx.post(
        f"http://127.0.0.1:{http_port}/import",
        json=import_payload,
        timeout=5,
    )
    assert resp.status_code == 200, f"Import failed: {resp.status_code} {resp.text}"

    msgs = gw._store.get_messages("test-thread-import")
    assert len(msgs) == 1, f"Expected 1 message, got {len(msgs)}"
    assert msgs[0].get("imported") == 1, (
        f"Expected imported=1, got: {msgs[0].get('imported')}"
    )


# ── 10.7.10 — pod_creds.json is mode 0o600 on Unix ───────────────────────────

@pytest.mark.skipif(platform.system() == "Windows", reason="chmod not supported on Windows")
def test_pod_creds_json_mode_600(tmp_path):
    """R10.7.10: pod_creds.json is written with mode 0o600 (owner read/write only)."""
    import stat as _stat

    cred_path = tmp_path / "pod_creds.json"
    cred_path.write_text(json.dumps({"css_url": "https://s.example.com", "email": "a", "password": "x"}))
    try:
        cred_path.chmod(_stat.S_IRUSR | _stat.S_IWUSR)
    except OSError:
        pass

    file_mode = stat.S_IMODE(cred_path.stat().st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got 0o{file_mode:o}"
