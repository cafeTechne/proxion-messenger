"""Tests for room role management (set_member_role, get_room_roles)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def agent():
    a = AgentState.generate()
    a.webid = "https://alice.pod/profile/card#me"
    return a


@pytest.fixture
def gateway(agent):
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


async def _register(gw, ws, webid="https://alice.pod/profile/card#me", name="Alice"):
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._display_names[ws] = name


@pytest.mark.asyncio
async def test_set_member_role_by_creator(gateway):
    """Room creator can set any member's role."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice)
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me", "Bob")
    room_id = "room-roles"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob}, "messages": [], "history_mode": "none",
        "creator_webid": "https://alice.pod/profile/card#me",
    }
    await gateway.process_command(ws_alice, {
        "cmd": "set_member_role",
        "room_id": room_id,
        "webid": "https://bob.pod/profile/card#me",
        "role": "mod",
    })
    alice_calls = [json.loads(c[0][0]) for c in ws_alice.send.call_args_list]
    assert any(e.get("type") == "member_role_updated" for e in alice_calls)


@pytest.mark.asyncio
async def test_set_member_role_invalid_role_rejected(gateway):
    """Invalid role string returns error."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-inv"
    gateway._local_rooms[room_id] = {
        "members": {ws}, "messages": [], "history_mode": "none",
        "creator_webid": "https://alice.pod/profile/card#me",
    }
    await gateway.process_command(ws, {
        "cmd": "set_member_role",
        "room_id": room_id,
        "webid": "https://bob.pod/profile/card#me",
        "role": "superuser",
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    # Schema validator or handler both reject unknown roles
    assert "invalid" in resp["message"].lower() or resp.get("code") == "E_SCHEMA"


@pytest.mark.asyncio
async def test_set_member_role_non_admin_rejected(gateway):
    """Regular member cannot set roles."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice)
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me", "Bob")
    room_id = "room-perm"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob}, "messages": [], "history_mode": "none",
        "creator_webid": "https://charlie.pod/profile/card#me",
    }
    await gateway.process_command(ws_alice, {
        "cmd": "set_member_role",
        "room_id": room_id,
        "webid": "https://bob.pod/profile/card#me",
        "role": "mod",
    })
    resp = json.loads(ws_alice.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "admin" in resp["message"].lower()


@pytest.mark.asyncio
async def test_get_room_roles_returns_dict(gateway):
    """get_room_roles returns a roles dict."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-getr"
    gateway._local_rooms[room_id] = {
        "members": {ws}, "messages": [], "history_mode": "none",
        "creator_webid": "https://alice.pod/profile/card#me",
    }
    await gateway.process_command(ws, {"cmd": "get_room_roles", "room_id": room_id})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "room_roles"
    assert "roles" in resp


@pytest.mark.asyncio
async def test_get_room_roles_denied_to_non_member(gateway):
    """A non-member cannot enumerate a room's roles (info-leak fix): the roles
    map (member webids + who is admin/mod) must not leak to outsiders, mirroring
    the get_room_members membership gate."""
    ws_member = _mock_ws()
    ws_outsider = _mock_ws()
    await _register(gateway, ws_member)
    await _register(gateway, ws_outsider, "https://mallory.pod/profile/card#me", "Mallory")
    room_id = "room-private"
    gateway._local_rooms[room_id] = {
        "members": {ws_member}, "messages": [], "history_mode": "none",
        "creator_webid": "https://alice.pod/profile/card#me",
    }
    mock_store = MagicMock()
    mock_store.get_room_members.return_value = ["https://alice.pod/profile/card#me"]
    mock_store.get_all_room_roles.return_value = {"https://alice.pod/profile/card#me": "admin"}
    gateway._store = mock_store
    await gateway.process_command(ws_outsider, {"cmd": "get_room_roles", "room_id": room_id})
    resp = json.loads(ws_outsider.send.call_args[0][0])
    assert resp["type"] == "room_roles"
    assert resp["roles"] == {}                       # leak blocked
    mock_store.get_all_room_roles.assert_not_called()  # short-circuited before the read


@pytest.mark.asyncio
async def test_set_member_role_broadcasts_to_all(gateway):
    """set_member_role event is broadcast to all room members."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    await _register(gateway, ws_alice)
    await _register(gateway, ws_bob, "https://bob.pod/profile/card#me", "Bob")
    room_id = "room-bcast"
    gateway._local_rooms[room_id] = {
        "members": {ws_alice, ws_bob}, "messages": [], "history_mode": "none",
        "creator_webid": "https://alice.pod/profile/card#me",
    }
    await gateway.process_command(ws_alice, {
        "cmd": "set_member_role",
        "room_id": room_id,
        "webid": "https://bob.pod/profile/card#me",
        "role": "admin",
    })
    bob_calls = [json.loads(c[0][0]) for c in ws_bob.send.call_args_list]
    assert any(e.get("type") == "member_role_updated" and e.get("role") == "admin" for e in bob_calls)


@pytest.mark.asyncio
async def test_set_member_role_persists_to_store(gateway):
    """set_member_role calls store.set_room_role when store is present."""
    ws = _mock_ws()
    await _register(gateway, ws)
    room_id = "room-persist"
    gateway._local_rooms[room_id] = {
        "members": {ws}, "messages": [], "history_mode": "none",
        "creator_webid": "https://alice.pod/profile/card#me",
    }
    mock_store = MagicMock()
    mock_store.get_room_role.return_value = "admin"
    gateway._store = mock_store
    await gateway.process_command(ws, {
        "cmd": "set_member_role",
        "room_id": room_id,
        "webid": "https://bob.pod/profile/card#me",
        "role": "mod",
    })
    mock_store.set_room_role.assert_called_once_with(room_id, "https://bob.pod/profile/card#me", "mod")
