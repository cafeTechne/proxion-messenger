"""A room rename on the host gateway reaches a member gateway over the real
signed /relay path, and the member gateway refuses one from a non-admin."""
import asyncio
import json
import socket
import time

import pytest

pytest.importorskip("websockets")
import websockets  # noqa: F401  (gateway import path expects it)
from gwharness import start_gateway as _serve_gw


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(agent, ws_port: int, http_port: int, db_path: str):
    import asyncio as _asyncio
    import websockets as _ws
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path,
    )
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.loop, handle.ready


@pytest.fixture(autouse=True)
def allow_private_relay(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    # Loopback-http test gateways: opt out of the HTTPS enforcement on
    # federation mutations (same env the two-gateway browser smoke sets).
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


def _room(name, creator):
    return {"name": name, "code": "x" * 64, "members": set(), "invite_url": "",
            "history_mode": "none", "messages": [], "creator_webid": creator}


@pytest.mark.asyncio
async def test_room_rename_federates_over_real_relay(tmp_path):
    from unittest.mock import AsyncMock
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    ws_a, ws_b = _free_port(), _free_port()
    http_a, http_b = _free_port(), _free_port()
    agent_a, agent_b = AgentState.generate(), AgentState.generate()
    gw_a, loop_a, ready_a = _start_gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, loop_b, ready_b = _start_gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))
    assert ready_a.wait(timeout=5) and ready_b.wait(timeout=5)

    owner_did = pub_key_to_did(agent_a.identity_pub_bytes)
    member_did = pub_key_to_did(agent_b.identity_pub_bytes)
    room_id = "room-fed-rename"
    member_sock = AsyncMock()

    def _setup_a():
        gw_a._local_rooms[room_id] = _room("Old", owner_did)
        gw_a._store.add_federated_room_member(room_id, member_did, f"http://127.0.0.1:{http_b}")
    def _setup_b():
        gw_b._local_rooms[room_id] = _room("Old", owner_did)
        gw_b._local_rooms[room_id]["members"].add(member_sock)
    loop_a.call_soon_threadsafe(_setup_a)
    loop_b.call_soon_threadsafe(_setup_b)
    await asyncio.sleep(0.2)

    owner_sock = AsyncMock()
    async def _rename():
        gw_a._client_webids[owner_sock] = owner_did
        await gw_a._handle_update_room(owner_sock, {
            "cmd": "update_room", "room_id": room_id, "name": "New", "topic": "Plans"})
    asyncio.run_coroutine_threadsafe(_rename(), loop_a).result(timeout=5)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and gw_b._local_rooms[room_id]["name"] != "New":
        await asyncio.sleep(0.2)
    assert gw_b._local_rooms[room_id]["name"] == "New", "rename never reached the member gateway"
    assert gw_b._local_rooms[room_id]["topic"] == "Plans"
    sent = [json.loads(c.args[0]) for c in member_sock.send.call_args_list]
    assert {"type": "room_updated", "room_id": room_id, "name": "New",
            "topic": "Plans", "by": owner_did} in sent


@pytest.mark.asyncio
async def test_room_update_relay_from_a_plain_member_is_refused(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "m.db")), ReadState())
    gw._local_rooms["r1"] = _room("Keep", "did:key:zOwner")
    status, _ = await gw._handle_room_update_relay({
        "room_id": "r1", "from_webid": "did:key:zMember", "name": "Hijacked", "topic": ""})
    assert status.startswith("403")
    assert gw._local_rooms["r1"]["name"] == "Keep"

    gw._store.set_room_role("r1", "did:key:zAdmin", "admin")
    gw._store.add_room_member("r1", "did:key:zAdmin")
    status, _ = await gw._handle_room_update_relay({
        "room_id": "r1", "from_webid": "did:key:zAdmin", "name": "x" * 65, "topic": ""})
    assert status.startswith("400")
    status, _ = await gw._handle_room_update_relay({
        "room_id": "r1", "from_webid": "did:key:zAdmin", "name": "Renamed", "topic": "t"})
    assert status.startswith("200")
    assert gw._local_rooms["r1"]["name"] == "Renamed"
