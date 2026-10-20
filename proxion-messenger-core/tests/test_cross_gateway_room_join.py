"""A user on gateway B joins a room hosted on gateway A through B (signed
POST /room-join), the room lands on B, and messages relay both ways."""
import asyncio
import json
import socket
import time
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("websockets")
from gwharness import start_gateway as _serve_gw


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(agent, tmp_path, name):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState
    ws_port, http_port = _free_port(), _free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / f"{name}.db"))
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.loop, http_port


@pytest.fixture(autouse=True)
def _loopback_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


def _sent(sock):
    return [json.loads(c.args[0]) for c in sock.send.call_args_list]


async def _wait(pred, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.1)
    return pred()


def _host_room(gw, room_id, owner_did, code, http_port):
    gw._local_rooms[room_id] = {
        "name": "lounge", "code": gw._hmac_invite_code(code), "members": set(),
        "invite_url": f"http://127.0.0.1:{http_port}/?join={code}",
        "history_mode": "all", "messages": [], "creator_webid": owner_did, "topic": "",
    }
    gw._room_codes[gw._local_rooms[room_id]["code"]] = room_id
    gw._store.save_room(room_id, "lounge", gw._local_rooms[room_id]["code"],
                        gw._local_rooms[room_id]["invite_url"], "all", owner_did)
    gw._store.add_room_member(room_id, owner_did)


@pytest.mark.asyncio
async def test_invite_link_from_another_gateway_joins_through_home_gateway(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    gw_a, loop_a, http_a = _start(AgentState.generate(), tmp_path, "a")
    gw_b, loop_b, http_b = _start(AgentState.generate(), tmp_path, "b")
    alice = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    bob = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    room_id, code = "room-xgw-join", "c0de" * 8
    alice_sock, bob_sock = AsyncMock(), AsyncMock()

    def _setup_a():
        _host_room(gw_a, room_id, alice, code, http_a)
        gw_a._client_webids[alice_sock] = alice
        gw_a._local_rooms[room_id]["members"].add(alice_sock)
        gw_a._store.save_message("m-before", room_id, "local", alice, "Alice",
                                 "said before bob came", "2026-10-17T00:00:00+00:00")
        # A host must not be able to put words in the mouth of B's own user.
        gw_a._store.save_message("m-forged", room_id, "local", bob, "Bob",
                                 "forged by the host", "2026-10-17T00:00:01+00:00")
    def _setup_b():
        gw_b._client_webids[bob_sock] = bob
        gw_b._display_names[bob_sock] = "Bob"
    loop_a.call_soon_threadsafe(_setup_a)
    loop_b.call_soon_threadsafe(_setup_b)
    await asyncio.sleep(0.2)

    link = f"http://127.0.0.1:{http_a}/?join={code}"
    asyncio.run_coroutine_threadsafe(
        gw_b._handle_join_remote_room(bob_sock, {"url": link}), loop_b).result(timeout=15)

    joined = [e for e in _sent(bob_sock) if e.get("type") == "room_joined"]
    assert joined, f"no room_joined on B: {_sent(bob_sock)}"
    assert joined[0]["room_id"] == room_id and joined[0]["name"] == "lounge"
    # The room lives on Bob's own gateway, with Bob as a stored member.
    assert room_id in gw_b._local_rooms
    assert bob in gw_b._store.get_room_members(room_id)
    assert any(m["message_id"] == "m-before" for m in gw_b._store.get_messages(room_id))
    assert not any(m["message_id"] == "m-forged" for m in gw_b._store.get_messages(room_id))
    # The host recorded Bob as a federated member living on B.
    fed = gw_a._store.get_federated_room_members(room_id)
    assert [(m["member_did"], m["gateway_url"]) for m in fed] == [(bob, f"http://127.0.0.1:{http_b}")]
    assert any(e.get("type") == "room_member_joined" and e.get("webid") == bob
               for e in _sent(alice_sock))

    # A -> B
    asyncio.run_coroutine_threadsafe(gw_a._handle_send_room(
        alice_sock, {"room_id": room_id, "content": "hi from alice"}), loop_a).result(timeout=5)
    assert await _wait(lambda: any(e.get("content") == "hi from alice" for e in _sent(bob_sock))), \
        "host message never reached the member gateway"
    # B -> A
    asyncio.run_coroutine_threadsafe(gw_b._handle_send_room(
        bob_sock, {"room_id": room_id, "content": "hi from bob"}), loop_b).result(timeout=5)
    assert await _wait(lambda: any(e.get("content") == "hi from bob" for e in _sent(alice_sock))), \
        "member message never reached the host gateway"


@pytest.mark.asyncio
async def test_room_join_post_refusals(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.relay import sign_relay_envelope
    from datetime import datetime, timezone
    import secrets

    gw_a, loop_a, http_a = _start(AgentState.generate(), tmp_path, "a")
    agent_b = AgentState.generate()
    gw_b, loop_b, http_b = _start(agent_b, tmp_path, "b")
    alice = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    room_id, code = "room-xgw-refuse", "beef" * 8
    loop_a.call_soon_threadsafe(_host_room, gw_a, room_id, alice, code, http_a)
    await asyncio.sleep(0.2)

    def _req(member, the_code=code, tamper=False):
        p = {"code": the_code, "member_did": member, "display_name": "x",
             "origin_gateway_url": f"http://127.0.0.1:{http_b}",
             "relay_sig_did": pub_key_to_did(agent_b.identity_pub_bytes),
             "relay_ts": datetime.now(timezone.utc).isoformat(),
             "relay_nonce": secrets.token_hex(8)}
        p["signature"] = sign_relay_envelope(agent_b.identity_key, p)
        if tamper:
            p["member_did"] = alice
        return asyncio.run_coroutine_threadsafe(
            gw_a._handle_room_join_post(json.dumps(p).encode(), "1.2.3.4"), loop_a).result(timeout=10)

    stranger = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    status, _ = _req(stranger, tamper=True)
    assert status.startswith("401")
    status, body = _req(stranger, the_code="wrong")
    assert json.loads(body) == {"ok": False, "error": "invalid_code"}
    # B may not vouch for the room owner, who lives on A.
    status, body = _req(alice)
    assert json.loads(body)["error"] == "invalid_member"
    loop_a.call_soon_threadsafe(gw_a._store.ban_room_member, room_id, stranger, alice, "")
    await asyncio.sleep(0.2)
    status, body = _req(stranger)
    assert json.loads(body)["error"] == "banned_from_room"
    assert gw_a._store.get_federated_room_members(room_id) == []

    # An accepted join binds the member to the vouching gateway at once, so a
    # different gateway can no longer claim them (before any relay arrives).
    carol = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    status, body = _req(carol)
    assert json.loads(body)["ok"] is True
    b_hex = agent_b.identity_pub_bytes.hex()
    assert any(h.get("pubkey_hex") == b_hex
               for h in gw_a._store.get_identity_key_history("relaygw:" + carol))
    agent_b = AgentState.generate()
    status, body = _req(carol)
    assert json.loads(body)["error"] == "invalid_member"


def test_codeless_mirror_room_is_not_joinable_by_empty_code(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    db = str(tmp_path / "m.db")
    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=db), ReadState())
    gw._store.save_room("mirror-1", "m", "", "http://host/?join=x", "none", "did:key:zOwner")
    gw2 = ProxionGateway(AgentState.generate(), {}, {},
                         GatewayConfig(host="127.0.0.1", db_path=db), ReadState())
    assert "mirror-1" in gw2._local_rooms
    assert "" not in gw2._room_codes


@pytest.mark.asyncio
async def test_guest_voice_join_reaches_the_host_channel(tmp_path):
    """Two users on B joined A's room through B. Their voice joins must go to
    A's channel (B keeps the room as a mirror in _local_rooms), and the two
    guests must still meet each other on B."""
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    gw_a, loop_a, http_a = _start(AgentState.generate(), tmp_path, "a")
    gw_b, loop_b, http_b = _start(AgentState.generate(), tmp_path, "b")
    alice, bob, carol = (pub_key_to_did(AgentState.generate().identity_pub_bytes)
                         for _ in range(3))
    room_id, code = "room-xgw-voice", "f00d" * 8
    alice_sock, bob_sock, carol_sock = AsyncMock(), AsyncMock(), AsyncMock()

    def _setup_a():
        _host_room(gw_a, room_id, alice, code, http_a)
        gw_a._client_webids[alice_sock] = alice
        gw_a._local_rooms[room_id]["members"].add(alice_sock)

    def _setup_b():
        gw_b._client_webids[bob_sock] = bob
        gw_b._client_webids[carol_sock] = carol
    loop_a.call_soon_threadsafe(_setup_a)
    loop_b.call_soon_threadsafe(_setup_b)
    await asyncio.sleep(0.2)

    link = f"http://127.0.0.1:{http_a}/?join={code}"
    for sock in (bob_sock, carol_sock):
        asyncio.run_coroutine_threadsafe(
            gw_b._handle_join_remote_room(sock, {"url": link}), loop_b).result(timeout=15)
    assert room_id in gw_b._local_rooms

    def _voice(gw, loop, sock):
        asyncio.run_coroutine_threadsafe(gw._handle_join_voice_channel(
            sock, {"channel_id": room_id}), loop).result(timeout=5)

    def _has(sock, typ, peer):
        return any(e.get("type") == typ and e.get("peer_webid") == peer for e in _sent(sock))

    _voice(gw_a, loop_a, alice_sock)
    _voice(gw_b, loop_b, bob_sock)
    assert await _wait(lambda: _has(alice_sock, "voice_peer_joined", bob)), \
        "the host never heard about the guest's voice join"
    assert await _wait(lambda: _has(bob_sock, "voice_peer_present", alice)), \
        "the guest never learned the host member is in the call"

    _voice(gw_b, loop_b, carol_sock)
    assert await _wait(lambda: _has(alice_sock, "voice_peer_joined", carol))
    assert await _wait(lambda: _has(carol_sock, "voice_peer_present", alice))
    # The two guests on B meet each other: bob (already in) calls carol.
    assert await _wait(lambda: _has(bob_sock, "voice_peer_joined", carol))
    assert await _wait(lambda: _has(carol_sock, "voice_peer_present", bob))
    await asyncio.sleep(0.5)
    assert not _has(bob_sock, "voice_peer_present", bob)
    assert not _has(carol_sock, "voice_peer_present", carol)
    assert set(gw_a._voice_channels[room_id]["members"]) == {alice, bob, carol}

    # A guest leaving reaches the host.
    asyncio.run_coroutine_threadsafe(gw_b._handle_leave_voice_channel(
        bob_sock, {"channel_id": room_id}), loop_b).result(timeout=5)
    assert await _wait(lambda: _has(alice_sock, "voice_peer_left", bob))
    assert await _wait(lambda: bob not in gw_a._voice_channels[room_id]["members"])
