"""Federated room edit/delete relay authz (R54).

The room_edit / room_delete relay handlers acted on ANY message by id with no
author check (room_delete didn't even read from_webid), so a federated member's
gateway could rewrite or delete OTHER members' messages in a room you host. The
LOCAL edit/delete paths already check the author; the relay paths didn't.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock(); ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self); ws.__eq__ = lambda self, o: self is o
    ws.remote_address = ("127.0.0.1", 1)
    return ws


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "rra.db")),
    )


def _room_with_message(gw, room_id, author, mid, content="orig"):
    ws = _mock_ws()
    gw._local_rooms[room_id] = {"members": {ws}, "messages": [], "creator_webid": "did:key:zOwner"}
    gw._store.save_message(mid, room_id, "local_room", author, "A", content, "2026-01-01T00:00:00Z")
    return ws


@pytest.mark.asyncio
async def test_room_edit_relay_rejects_non_author(gateway):
    _room_with_message(gateway, "r1", "did:key:zAuthor", "m1", "original")
    status, _ = await gateway._handle_room_edit_relay({
        "room_id": "r1", "message_id": "m1", "new_content": "HACKED",
        "edited_at": "2026-01-02T00:00:00Z", "from_webid": "did:key:zAttacker",
    })
    assert status.startswith("403")
    assert gateway._store.get_message("m1")["content"] == "original", "message must be unchanged"


@pytest.mark.asyncio
async def test_room_edit_relay_allows_author(gateway):
    _room_with_message(gateway, "r2", "did:key:zAuthor", "m2", "original")
    status, _ = await gateway._handle_room_edit_relay({
        "room_id": "r2", "message_id": "m2", "new_content": "my own edit",
        "edited_at": "2026-01-02T00:00:00Z", "from_webid": "did:key:zAuthor",
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m2")["content"] == "my own edit"


@pytest.mark.asyncio
async def test_room_delete_relay_rejects_non_author(gateway):
    _room_with_message(gateway, "r3", "did:key:zAuthor", "m3")
    status, _ = await gateway._handle_room_delete_relay({
        "room_id": "r3", "message_id": "m3", "from_webid": "did:key:zAttacker",
    })
    assert status.startswith("403")
    assert gateway._store.get_message("m3") is not None, "message must NOT be deleted"


@pytest.mark.asyncio
async def test_room_delete_relay_owner_can_delete_any(gateway):
    _room_with_message(gateway, "r4", "did:key:zAuthor", "m4")
    status, _ = await gateway._handle_room_delete_relay({
        "room_id": "r4", "message_id": "m4", "from_webid": "did:key:zOwner",  # room creator
        # Owner authority is only honored off an ALREADY-established signer binding
        # (set by the relay dispatch before it can seed one); simulate that here.
        "_relay_sender_established": True,
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m4") is None


@pytest.mark.asyncio
async def test_room_delete_relay_owner_claim_rejected_without_established(gateway):
    """A first-use TOFU seed claiming to be the owner must NOT get delete-any
    authority: without an established signer binding, the owner-authority branch
    is not honored (F3)."""
    _room_with_message(gateway, "r5", "did:key:zAuthor", "m5")
    status, _ = await gateway._handle_room_delete_relay({
        "room_id": "r5", "message_id": "m5", "from_webid": "did:key:zOwner",  # room creator
        # No _relay_sender_established → fresh TOFU seed, not honored.
    })
    assert status.startswith("403")
    assert gateway._store.get_message("m5") is not None, "message must NOT be deleted"


@pytest.mark.asyncio
async def test_room_edit_relay_owner_can_edit_any_when_established(gateway):
    _room_with_message(gateway, "r6", "did:key:zAuthor", "m6", "original")
    status, _ = await gateway._handle_room_edit_relay({
        "room_id": "r6", "message_id": "m6", "new_content": "owner edit",
        "edited_at": "2026-01-02T00:00:00Z", "from_webid": "did:key:zOwner",
        "_relay_sender_established": True,
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m6")["content"] == "owner edit"


@pytest.mark.asyncio
async def test_room_edit_relay_owner_claim_rejected_without_established(gateway):
    """A first-use TOFU seed claiming to be the owner must NOT rewrite another
    member's message (F3)."""
    _room_with_message(gateway, "r7", "did:key:zAuthor", "m7", "original")
    status, _ = await gateway._handle_room_edit_relay({
        "room_id": "r7", "message_id": "m7", "new_content": "HACKED",
        "edited_at": "2026-01-02T00:00:00Z", "from_webid": "did:key:zOwner",
    })
    assert status.startswith("403")
    assert gateway._store.get_message("m7")["content"] == "original", "message must be unchanged"


@pytest.mark.asyncio
async def test_room_message_relay_rejects_non_member_sender(gateway):
    """A relayed room message from a webid that isn't a known member (local or
    federated) is rejected — a peer gateway can't inject messages 'from' an
    arbitrary webid into a room you host."""
    room_id = "r-spoof"
    gateway._local_rooms[room_id] = {"members": set(), "creator_webid": "did:key:zOwner"}
    gateway._store.add_room_member(room_id, "did:key:zRealMember")
    # zEvil is not a member.
    status, _ = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zEvil",
        "message_id": "m-spoof", "content": "injected", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert status.startswith("403")


@pytest.mark.asyncio
async def test_room_message_relay_allows_federated_member(gateway):
    room_id = "r-ok"
    gateway._local_rooms[room_id] = {"members": set(), "creator_webid": "did:key:zOwner"}
    gateway._store.add_federated_room_member(room_id, "did:key:zFedMember", "https://gw.test")
    status, _ = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zFedMember",
        "message_id": "m-ok", "content": "hi", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert status.startswith("200")


@pytest.mark.asyncio
async def test_room_relay_rejects_message_id_with_markup(gateway):
    """F2: a relayed message_id carrying markup/control characters is rejected on
    the ingestion path before it is broadcast/persisted (belt-and-suspenders on
    top of client-side escaping)."""
    room_id = "r-mid"
    gateway._local_rooms[room_id] = {"members": set(), "creator_webid": "did:key:zOwner"}
    gateway._store.add_federated_room_member(room_id, "did:key:zFedMember", "https://gw.test")
    status, resp = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zFedMember",
        "message_id": "<img src=x onerror=alert(1)>",
        "content": "hi", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert status.startswith("400")
    assert "invalid_message_id" in resp


@pytest.mark.asyncio
async def test_room_message_relay_refuses_memberless_room(gateway):
    """F3b: a known room with NO resolvable members refuses a relayed message
    instead of failing open. This protects a pod-restored room whose member set
    is empty from an injected relay; the sender cannot be verified, so the message
    is neither persisted nor broadcast."""
    room_id = "r-empty"
    gateway._local_rooms[room_id] = {"members": set(), "creator_webid": "did:key:zOwner"}
    # No stored or federated members recorded for this room.
    status, resp = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zSomeone",
        "message_id": "m-empty", "content": "injected", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert status.startswith("403")
    assert "sender_not_member" in resp
    assert gateway._store.get_message("m-empty") is None, "must not be persisted"


@pytest.mark.asyncio
async def test_room_relay_accepts_uuid_message_id(gateway):
    """A normal UUID-style message_id (letters, digits, hyphens) still passes."""
    room_id = "r-mid-ok"
    gateway._local_rooms[room_id] = {"members": set(), "creator_webid": "did:key:zOwner"}
    gateway._store.add_federated_room_member(room_id, "did:key:zFedMember", "https://gw.test")
    status, _ = await gateway._handle_room_relay({
        "room_id": room_id, "from_webid": "did:key:zFedMember",
        "message_id": "550e8400-e29b-41d4-a716-446655440000",
        "content": "hi", "timestamp": "2026-01-01T00:00:00Z",
    })
    assert status.startswith("200")
