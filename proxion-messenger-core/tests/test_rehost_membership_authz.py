"""F2: rehosting an ALREADY-hosted room must not add a non-member or banned
caller to the live member set.

A valid SELF-signed descriptor proves only "I am who I say"; it does NOT prove
room membership. Rooms are hydrated into _local_rooms at startup, so without a
membership gate any authenticated caller who learned a room id could self-sign a
descriptor and be added to the live members (the oracle for send/history/
members/reactions/pins), defeating the invite-code gate and ban-survives-
reconnect.
"""
from __future__ import annotations

import base64
import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.room_descriptor import canonical_bytes


def _mock_ws():
    ws = AsyncMock(); ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self); ws.__eq__ = lambda self, o: self is o
    ws.remote_address = ("127.0.0.1", 1)
    return ws


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rehost.db")),
    )


def _identity():
    priv = Ed25519PrivateKey.generate()
    did = pub_key_to_did(priv.public_key().public_bytes_raw())
    return priv, did


def _signed_descriptor(priv, did, room_id):
    desc = {
        "px:type": "RoomDescriptor", "px:version": 1,
        "room_id": room_id, "title": "Room", "owner": did, "members": [],
    }
    sig = priv.sign(canonical_bytes(desc))
    return {**desc, "px:signer": did, "px:sig": base64.b64encode(sig).decode()}


def _session(gw, ws, webid, did):
    gw._client_webids[ws] = webid
    gw._session_signing_did[ws] = did


def _host_room(gw, room_id, owner_did):
    gw._local_rooms[room_id] = {
        "name": "Hosted", "code": "", "members": set(),
        "creator_webid": owner_did, "history_mode": "none", "messages": [],
    }
    gw._store.save_room(room_id, "Hosted", "", "", "none", owner_did)
    gw._store.add_room_member(room_id, owner_did)


@pytest.mark.asyncio
async def test_rehost_already_hosted_rejects_non_member(gateway):
    gateway._force_auth = True
    room_id = "room-hosted-1"
    _, owner_did = _identity()
    _host_room(gateway, room_id, owner_did)

    priv, did = _identity()  # an unrelated authenticated caller
    ws = _mock_ws()
    _session(gateway, ws, "https://evil.example/card#me", did)
    await gateway._handle_rehost_room(ws, {"descriptor": _signed_descriptor(priv, did, room_id)})

    assert ws not in gateway._local_rooms[room_id]["members"], "non-member must not join live set"
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error" and sent["code"] == "E_REHOST"


@pytest.mark.asyncio
async def test_rehost_already_hosted_rejects_banned_member(gateway):
    gateway._force_auth = True
    room_id = "room-hosted-2"
    _, owner_did = _identity()
    _host_room(gateway, room_id, owner_did)

    priv, did = _identity()
    gateway._store.add_room_member(room_id, did)                    # once a member
    gateway._store.ban_room_member(room_id, did, owner_did, "spam")  # then banned
    ws = _mock_ws()
    _session(gateway, ws, "https://x.example/card#me", did)
    await gateway._handle_rehost_room(ws, {"descriptor": _signed_descriptor(priv, did, room_id)})

    assert ws not in gateway._local_rooms[room_id]["members"], "banned member must not rejoin live set"
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error" and sent["code"] == "E_REHOST"


@pytest.mark.asyncio
async def test_rehost_already_hosted_allows_stored_member(gateway):
    gateway._force_auth = True
    room_id = "room-hosted-3"
    _, owner_did = _identity()
    _host_room(gateway, room_id, owner_did)

    priv, did = _identity()
    gateway._store.add_room_member(room_id, did)   # a legit current member
    ws = _mock_ws()
    _session(gateway, ws, "https://ok.example/card#me", did)
    await gateway._handle_rehost_room(ws, {"descriptor": _signed_descriptor(priv, did, room_id)})

    assert ws in gateway._local_rooms[room_id]["members"], "legit member rehost must still work"
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "room_rehosted" and sent.get("already") is True


@pytest.mark.asyncio
async def test_rehost_already_hosted_allows_creator(gateway):
    gateway._force_auth = True
    room_id = "room-hosted-4"
    priv, owner_did = _identity()
    _host_room(gateway, room_id, owner_did)
    # Drop the creator's stored member row so the creator branch is exercised on
    # its own (the creator is authorized even without a member row).
    gateway._store.remove_room_member(room_id, owner_did)

    ws = _mock_ws()
    _session(gateway, ws, "https://owner.example/card#me", owner_did)
    await gateway._handle_rehost_room(ws, {"descriptor": _signed_descriptor(priv, owner_did, room_id)})

    assert ws in gateway._local_rooms[room_id]["members"], "creator rehost must work"
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "room_rehosted" and sent.get("already") is True
