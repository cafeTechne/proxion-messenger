"""E2E: PLAN_ROUND_71 B2 — a gateway with no state for a room reconstructs it from
the host-owned descriptor (client-driven rehydration). Owner-only and idempotent.
"""
import pytest

from .helpers import connect_and_register


def _descriptor(owner_webid, room_id="room-rehosttest", members=None):
    return {
        "px:type": "RoomDescriptor",
        "px:version": 1,
        "room_id": room_id,
        "title": "Rehosted Room",
        "owner": owner_webid,
        "members": members if members is not None else [{"webid": owner_webid, "role": "owner"}],
        "code": "rehostcode123",
        "long_chat": "https://owner.pod/proxion/rooms/room-rehosttest/",
    }


@pytest.mark.asyncio
async def test_rehost_reconstructs_room_and_membership(live_gateway, alice_agent, bob_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    desc = _descriptor(alice.webid, members=[
        {"webid": alice.webid, "role": "owner"},
        {"webid": bob.webid, "role": "member"},
    ])
    await alice.send(cmd="rehost_room", descriptor=desc)
    evt = await alice.recv_type("room_rehosted", timeout=5.0)
    assert evt["room_id"] == desc["room_id"]

    # The room and its membership are now known to the gateway.
    await alice.send(cmd="get_room_members", room_id=desc["room_id"])
    members = await alice.recv_type("room_members", timeout=5.0)
    webids = {m.get("webid") for m in members.get("members", [])}
    assert alice.webid in webids
    assert bob.webid in webids


@pytest.mark.asyncio
async def test_rehost_rejects_non_owner(live_gateway, alice_agent, bob_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    # Bob tries to rehost a room owned by Alice (his session webid != owner).
    await bob.send(cmd="rehost_room", descriptor=_descriptor(alice.webid, room_id="room-notbobs"))
    err = await bob.recv_type("error", timeout=5.0)
    assert err.get("code") == "E_REHOST"


@pytest.mark.asyncio
async def test_rehost_is_idempotent(live_gateway, alice_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    desc = _descriptor(alice.webid, room_id="room-idem")

    await alice.send(cmd="rehost_room", descriptor=desc)
    first = await alice.recv_type("room_rehosted", timeout=5.0)
    assert not first.get("already")

    await alice.send(cmd="rehost_room", descriptor=desc)
    second = await alice.recv_type("room_rehosted", timeout=5.0)
    assert second.get("already") is True     # already hosted, not clobbered
