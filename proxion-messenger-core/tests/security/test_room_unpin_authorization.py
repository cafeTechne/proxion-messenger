"""Round 1: Room unpin authorization tests (mirrors pin policy)."""
import json
import secrets
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9974), read_state=ReadState(),
    )


def _setup_room(gw, owner_webid="did:key:zOwner", room_id=None):
    room_id = room_id or "room-" + secrets.token_hex(4)
    ws_owner = MagicMock()
    ws_owner.send = AsyncMock()
    gw.clients.add(ws_owner)
    gw._client_webids[ws_owner] = owner_webid
    gw._local_rooms[room_id] = {
        "name": "Test Room",
        "code": "testcode",
        "members": {ws_owner},
        "creator_webid": owner_webid,
        "invite_url": "",
    }
    gw._room_codes["testcode"] = room_id
    # Set room role to owner
    gw._room_roles = getattr(gw, "_room_roles", {})
    gw._room_roles[(room_id, owner_webid)] = "owner"
    return ws_owner, room_id


@pytest.mark.asyncio
async def test_owner_can_unpin(gateway):
    ws_owner, room_id = _setup_room(gateway, "did:key:zOwner")
    await gateway._handle_unpin_message(ws_owner, {
        "message_id": "msg-123",
        "thread_id": f"room:{room_id}",
    })
    sent_msgs = [json.loads(c[0][0]) for c in ws_owner.send.call_args_list]
    # Should broadcast "unpinned", not an error
    assert any(m.get("type") == "unpinned" for m in sent_msgs), \
        f"Expected unpinned event, got: {sent_msgs}"


@pytest.mark.asyncio
async def test_non_owner_cannot_unpin(gateway):
    ws_owner, room_id = _setup_room(gateway, "did:key:zOwner")
    # Create a non-owner member
    ws_member = MagicMock()
    ws_member.send = AsyncMock()
    gateway.clients.add(ws_member)
    gateway._client_webids[ws_member] = "did:key:zMember"
    gateway._local_rooms[room_id]["members"].add(ws_member)

    await gateway._handle_unpin_message(ws_member, {
        "message_id": "msg-123",
        "thread_id": f"room:{room_id}",
    })
    sent = json.loads(ws_member.send.call_args[0][0])
    assert sent.get("type") == "error"
    assert "owner" in sent.get("message", "").lower()


@pytest.mark.asyncio
async def test_admin_policy_matches_expected_role_rules(gateway):
    """A member with 'member' role cannot unpin (must be owner)."""
    ws_owner, room_id = _setup_room(gateway, "did:key:zOwner")
    ws_admin = MagicMock()
    ws_admin.send = AsyncMock()
    gateway.clients.add(ws_admin)
    gateway._client_webids[ws_admin] = "did:key:zAdmin"
    gateway._local_rooms[room_id]["members"].add(ws_admin)
    # Explicitly set role to 'admin' (not owner)
    gateway._room_roles[(room_id, "did:key:zAdmin")] = "admin"

    await gateway._handle_unpin_message(ws_admin, {
        "message_id": "msg-123",
        "thread_id": f"room:{room_id}",
    })
    sent = json.loads(ws_admin.send.call_args[0][0])
    # admin should not be able to unpin (only owner)
    assert sent.get("type") == "error"
