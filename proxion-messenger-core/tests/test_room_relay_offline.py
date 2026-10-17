"""A relayed room message is stored even when no local member is connected,
so it shows up in history on reconnect."""
import asyncio
import time

import pytest

pytest.importorskip("websockets")
from gwharness import free_port, start_gateway
from e2e.helpers import connect_and_register

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _gateway(tmp_path):
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    handle = start_gateway(gw, ws_port, http_port)
    return gw, handle, f"ws://127.0.0.1:{ws_port}"


def _stored_room(gw, room_id):
    return next((r for r in gw._store.get_all_rooms() if r["room_id"] == room_id), None)


def _on_loop(handle, coro, timeout=5):
    return asyncio.run_coroutine_threadsafe(coro, handle.loop).result(timeout=timeout)


async def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


@pytest.mark.asyncio
async def test_room_relay_stored_while_members_offline(tmp_path):
    gw, handle, url = _gateway(tmp_path)
    alice_agent = AgentState.generate()
    alice = await connect_and_register(url, "Alice", alice_agent)
    await alice.send(cmd="chat_room_create", name="Fed")
    room_id = (await alice.recv_type("room_created"))["room_id"]
    remote = "did:key:z6MkRemoteMemberOnAnotherGateway"
    _on_loop(handle, _add_fed(gw, room_id, remote))

    await alice.ws.close()
    assert await _wait(lambda: not gw._local_rooms[room_id]["members"])

    status, _body = _on_loop(handle, gw._handle_room_relay({
        "room_id": room_id, "from_webid": remote, "message_id": "relay-offline-1",
        "content": "hello while you were away", "timestamp": "2026-10-16T10:00:00+00:00",
    }))
    assert status.startswith("200")

    alice = await connect_and_register(url, "Alice", alice_agent)
    await alice.send(cmd="get_local_history", thread_id=room_id)
    hist = await alice.recv_type("local_history")
    assert "relay-offline-1" in [m.get("message_id") for m in hist["messages"]]
    await alice.ws.close()


async def _add_fed(gw, room_id, did):
    gw._store.add_federated_room_member(room_id, did, "http://127.0.0.1:1")
