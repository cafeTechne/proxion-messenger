"""The owner leaving while every other member has the app closed transfers the
room to a stored member instead of deleting it."""
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
async def test_owner_leave_with_offline_members_transfers_room(tmp_path):
    gw, handle, url = _gateway(tmp_path)
    alice_agent, bob_agent, carol_agent = (AgentState.generate() for _ in range(3))
    alice = await connect_and_register(url, "Alice", alice_agent)
    bob = await connect_and_register(url, "Bob", bob_agent)
    carol = await connect_and_register(url, "Carol", carol_agent)

    await alice.send(cmd="chat_room_create", name="Book club")
    created = await alice.recv_type("room_created")
    room_id = created["room_id"]
    for s in (bob, carol):
        await s.send(cmd="join_room", code=created["code"])
        await s.recv_type("room_joined")
    # Carol is an admin, so she outranks Bob even though Bob joined first.
    _on_loop(handle, _set_role(gw, room_id, carol.did, "admin"))

    # Bob and Carol close the app.
    await bob.ws.close()
    await carol.ws.close()
    assert await _wait(lambda: len(gw._local_rooms[room_id]["members"]) == 1)

    await alice.send(cmd="leave_local_room", room_id=room_id)
    left = await alice.recv_type("left_room")
    assert not left.get("deleted"), left
    assert left.get("transferred_to") == "Carol"
    assert room_id in gw._local_rooms
    assert gw._local_rooms[room_id]["creator_webid"] == carol.did
    assert _stored_room(gw, room_id)["creator_webid"] == carol.did
    assert set(gw._store.get_room_members(room_id)) == {bob.did, carol.did}

    # Bob comes back and still has the room.
    bob = await connect_and_register(url, "Bob", bob_agent)
    await bob.send(cmd="get_local_history", thread_id=room_id)
    hist = await bob.recv_type("local_history")
    assert hist["thread_id"] == room_id
    assert room_id in gw._local_rooms
    await alice.ws.close()
    await bob.ws.close()


async def _set_role(gw, room_id, did, role):
    gw._store.set_room_role(room_id, did, role)


@pytest.mark.asyncio
async def test_owner_leave_prefers_longest_standing_member(tmp_path):
    gw, handle, url = _gateway(tmp_path)
    alice = await connect_and_register(url, "Alice", AgentState.generate())
    bob = await connect_and_register(url, "Bob", AgentState.generate())
    carol = await connect_and_register(url, "Carol", AgentState.generate())
    await alice.send(cmd="chat_room_create", name="R")
    created = await alice.recv_type("room_created")
    room_id = created["room_id"]
    for s in (bob, carol):
        await s.send(cmd="join_room", code=created["code"])
        await s.recv_type("room_joined")
    await bob.ws.close()  # Bob (oldest) offline, Carol online
    assert await _wait(lambda: len(gw._local_rooms[room_id]["members"]) == 2)
    await alice.send(cmd="leave_local_room", room_id=room_id)
    left = await alice.recv_type("left_room")
    assert left.get("transferred_to") == "Bob"
    assert gw._local_rooms[room_id]["creator_webid"] == bob.did
    evt = await carol.recv_type("ownership_transferred")
    assert evt["new_owner_did"] == bob.did
    await alice.ws.close()
    await carol.ws.close()


@pytest.mark.asyncio
async def test_owner_leave_as_last_member_deletes_room(tmp_path):
    gw, handle, url = _gateway(tmp_path)
    alice = await connect_and_register(url, "Alice", AgentState.generate())
    await alice.send(cmd="chat_room_create", name="Solo")
    room_id = (await alice.recv_type("room_created"))["room_id"]
    await alice.send(cmd="leave_local_room", room_id=room_id)
    left = await alice.recv_type("left_room")
    assert left.get("deleted") is True
    assert room_id not in gw._local_rooms
    assert _stored_room(gw, room_id) is None
    await alice.ws.close()
