"""E2E: PLAN_ROUND_71 B2/B3 — a gateway with no state for a room reconstructs it
from the host-owned, SIGNED descriptor (client-driven rehydration). The signature
is mandatory and bound to the requester's identity: a fabricated, tampered, or
replayed descriptor is rejected.
"""
import base64

import pytest

from proxion_messenger_core.room_descriptor import canonical_bytes
from .helpers import connect_and_register


def _sign(desc, agent, signer_did):
    """Sign a descriptor with an agent's Ed25519 identity key (as the web client does)."""
    sig = agent.identity_key.sign(canonical_bytes(desc))
    return {**desc, "px:signer": signer_did, "px:sig": base64.b64encode(sig).decode()}


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
    await alice.send(cmd="rehost_room", descriptor=_sign(desc, alice_agent, alice.did))
    evt = await alice.recv_type("room_rehosted", timeout=5.0)
    assert evt["room_id"] == desc["room_id"]

    await alice.send(cmd="get_room_members", room_id=desc["room_id"])
    members = await alice.recv_type("room_members", timeout=5.0)
    webids = {m.get("webid") for m in members.get("members", [])}
    assert alice.webid in webids
    assert bob.webid in webids


@pytest.mark.asyncio
async def test_rehost_rejects_unsigned_descriptor(live_gateway, alice_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    await alice.send(cmd="rehost_room", descriptor=_descriptor(alice.webid, room_id="room-unsigned"))
    err = await alice.recv_type("error", timeout=5.0)
    assert err.get("code") == "E_REHOST"


@pytest.mark.asyncio
async def test_rehost_rejects_tampered_descriptor(live_gateway, alice_agent, bob_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    signed = _sign(_descriptor(alice.webid, room_id="room-tamper"), alice_agent, alice.did)
    # Inject an extra member AFTER signing — the signature no longer matches.
    signed["members"] = list(signed["members"]) + [{"webid": bob.webid, "role": "admin"}]
    await alice.send(cmd="rehost_room", descriptor=signed)
    err = await alice.recv_type("error", timeout=5.0)
    assert err.get("code") == "E_REHOST"


@pytest.mark.asyncio
async def test_rehost_rejects_replay_by_a_different_session(live_gateway, alice_agent, bob_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    # A perfectly valid descriptor Alice signed — but Bob tries to rehost it. The
    # signer (Alice) is not Bob's session identity, so it must be refused.
    valid = _sign(_descriptor(alice.webid, room_id="room-replay"), alice_agent, alice.did)
    await bob.send(cmd="rehost_room", descriptor=valid)
    err = await bob.recv_type("error", timeout=5.0)
    assert err.get("code") == "E_REHOST"


@pytest.mark.asyncio
async def test_rehost_works_for_a_pod_webid_session(live_gateway, alice_agent):
    # R73: a pod-connected client registers its did (on connect), then re-registers
    # with its webid (on pod login), so its display identity becomes the webid while
    # its descriptors are still signed by its did. Rehost must still succeed.
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    await alice.send(cmd="register", webid="https://alice.pod/profile/card#me", display_name="Alice")
    desc = _sign(_descriptor(alice.webid, room_id="room-podowner"), alice_agent, alice.did)
    await alice.send(cmd="rehost_room", descriptor=desc)
    evt = await alice.recv_type("room_rehosted", timeout=5.0)
    assert evt["room_id"] == "room-podowner"


@pytest.mark.asyncio
async def test_rehost_is_idempotent(live_gateway, alice_agent):
    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    desc = _sign(_descriptor(alice.webid, room_id="room-idem"), alice_agent, alice.did)

    await alice.send(cmd="rehost_room", descriptor=desc)
    first = await alice.recv_type("room_rehosted", timeout=5.0)
    assert not first.get("already")

    await alice.send(cmd="rehost_room", descriptor=desc)
    second = await alice.recv_type("room_rehosted", timeout=5.0)
    assert second.get("already") is True
