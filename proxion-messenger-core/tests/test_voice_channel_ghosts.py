"""A member who leaves a room voice channel, by closing the tab or from another
gateway, must not stay listed as a ghost participant.

* handle_client popped the socket's webid before the voice cleanup ran, so the
  cleanup found no identity and skipped the channel.
* A cross-gateway joiner who left while alone in the call relayed the leave to
  nobody (no remote member to relay to), so the host kept it forever.
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("websockets")
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gwharness import start_gateway
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


class _FakeWS:
    """Async-iterable socket: yields queued commands, then closes on None."""

    def __init__(self, msgs=()):
        self.q = asyncio.Queue()
        for m in msgs:
            self.q.put_nowait(m)
        self.sent = []
        self.remote_address = ("127.0.0.1", 1234)
        self.request = None

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def close(self, *a):
        self.q.put_nowait(None)

    def push(self, m):
        self.q.put_nowait(m)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return json.dumps(m)


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, o: self is o
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _last(ws, t):
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == t:
            return m


def _gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rooms.db")))


async def _room_with_voice_member(gw):
    """B owns a room; A joins it through handle_client and joins its voice channel."""
    b, b_did = _mock_ws(), _did()
    gw.clients.add(b)
    await gw.process_command(b, {"cmd": "register", "did": b_did, "display_name": "B"})
    await gw.process_command(b, {"cmd": "chat_room_create", "name": "R"})
    created = _last(b, "room_created")
    rid = created["room_id"]
    a_did = _did()
    a = _FakeWS([{"cmd": "register", "did": a_did, "display_name": "A"},
                 {"cmd": "join_room", "code": created["code"]},
                 {"cmd": "join_voice_channel", "room_id": rid}])
    task = asyncio.create_task(gw.handle_client(a))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if a_did in gw._voice_channels.get(rid, {}).get("members", {}):
            break
    assert _last(b, "voice_channel_state")["participants"] == [a_did]
    return b, rid, a, a_did, task


@pytest.mark.asyncio
async def test_tab_close_removes_voice_channel_member(tmp_path):
    gw = _gateway(tmp_path)
    b, rid, a, a_did, task = await _room_with_voice_member(gw)
    a.push(None)  # the tab closes
    await asyncio.wait_for(task, 5)
    await asyncio.sleep(0.05)
    assert a_did not in gw._voice_channels.get(rid, {}).get("members", {}), \
        "closed tab left a ghost voice member"
    assert _last(b, "voice_channel_state")["participants"] == []


@pytest.mark.asyncio
async def test_closing_an_idle_tab_keeps_the_tab_in_the_call(tmp_path):
    gw = _gateway(tmp_path)
    b, rid, a, a_did, task = await _room_with_voice_member(gw)
    # A second tab of the same account, not in the call, closes.
    idle = _FakeWS([{"cmd": "register", "did": a_did, "display_name": "A"}])
    idle_task = asyncio.create_task(gw.handle_client(idle))
    await asyncio.sleep(0.05)
    idle.push(None)
    await asyncio.wait_for(idle_task, 5)
    await asyncio.sleep(0.05)
    assert a_did in gw._voice_channels.get(rid, {}).get("members", {}), \
        "closing an idle tab dropped the account's other tab from the call"
    a.push(None)
    await asyncio.wait_for(task, 5)


# ---------------------------------------------------------------- cross-gateway

from gwharness import free_port as _free_port


def _start(tmp_path, name):
    from proxion_messenger_core.readstate import ReadState
    ws_port, http_port = _free_port(), _free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / f"{name}.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    handle = start_gateway(gw, ws_port, http_port)
    return gw, handle.loop, ws_port


@pytest.fixture
def _loopback_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


async def _wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.1)
    return pred()


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["leave", "close"])
async def test_cross_gateway_joiner_leaving_alone_does_not_ghost(tmp_path, _loopback_federation, how):
    gw_a, loop_a, _ = _start(tmp_path, "a")      # hosts the room
    gw_b, loop_b, ws_b = _start(tmp_path, "b")   # Bob's gateway
    a_url, b_url = gw_a._gateway_http_url(), gw_b._gateway_http_url()
    ga_did = pub_key_to_did(gw_a.agent.identity_pub_bytes)
    alice, bob = _did(), _did()
    room_id = "room-xgw-voice"
    alice_sock = _mock_ws()

    def _setup_a():
        gw_a._client_webids[alice_sock] = alice
        gw_a._local_rooms[room_id] = {"name": "R", "members": {alice_sock}}
        gw_a._store.add_federated_room_member(room_id, bob, b_url)

    def _setup_b():
        gw_b._peer_gateway_urls[ga_did] = a_url
    loop_a.call_soon_threadsafe(_setup_a)
    loop_b.call_soon_threadsafe(_setup_b)
    await asyncio.sleep(0.2)

    def _host_members():
        return set(gw_a._voice_channels.get(room_id, {}).get("members", {}))

    async with websockets.connect(f"ws://127.0.0.1:{ws_b}") as ws:
        await ws.send(json.dumps({"cmd": "register", "did": bob, "display_name": "Bob"}))
        assert await _wait(lambda: bob in gw_b._client_webids.values())
        # Bob joins alone: Alice is in the room but not in the call.
        await ws.send(json.dumps({"cmd": "join_voice_channel", "channel_id": room_id}))
        assert await _wait(lambda: bob in _host_members()), "join never reached the host"
        assert _last(alice_sock, "voice_channel_state")["participants"] == [bob]
        if how == "leave":
            await ws.send(json.dumps({"cmd": "leave_voice_channel", "channel_id": room_id}))
            assert await _wait(lambda: bob not in _host_members()), \
                "host kept the cross-gateway joiner as a ghost after leave"
    if how == "close":
        assert await _wait(lambda: bob not in _host_members()), \
            "host kept the cross-gateway joiner as a ghost after the tab closed"
    assert await _wait(lambda: _last(alice_sock, "voice_channel_state")["participants"] == [])
    # Bob's gateway dropped its mirror of the channel once no local user was in it.
    assert await _wait(lambda: room_id not in gw_b._voice_channels)
