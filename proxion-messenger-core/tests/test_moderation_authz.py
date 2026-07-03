"""Room moderation authorization.

Regression: ban/unban/mute used _check_room_permission(role="admin"), which for
local rooms fell through to 'any member', so any member could ban/mute anyone.
Now it requires the owner or an explicitly-granted admin.
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
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _got(ws, type_):
    return any(json.loads(c[0][0]).get("type") == type_ for c in ws.send.call_args_list)


def _err(ws):
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == "error":
            return m
    return None


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "mod.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _room(gw, room_id, owner_did, member_wss):
    gw._local_rooms[room_id] = {
        "creator_webid": owner_did, "members": set(member_wss),
        "messages": [], "history_mode": "none", "name": "R", "code": "c",
    }
    for ws in member_wss:
        gw._store.add_room_member(room_id, gw._client_webids[ws])


@pytest.mark.asyncio
async def test_ordinary_member_cannot_ban(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    m1 = Ed25519PrivateKey.generate()
    victim = Ed25519PrivateKey.generate()
    ws_owner, ws_m1, ws_v = _mock_ws(), _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_m1, _did(m1))
    await _register(gateway, ws_v, _did(victim))
    _room(gateway, "r1", _did(owner), [ws_owner, ws_m1, ws_v])

    # A plain member tries to ban another member.
    ws_m1.send.reset_mock()
    await gateway.process_command(ws_m1, {"cmd": "ban_member", "room_id": "r1", "webid": _did(victim)})
    assert _err(ws_m1) is not None
    assert not gateway._store.is_room_banned("r1", _did(victim))


@pytest.mark.asyncio
async def test_owner_can_ban(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    victim = Ed25519PrivateKey.generate()
    ws_owner, ws_v = _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_v, _did(victim))
    _room(gateway, "r2", _did(owner), [ws_owner, ws_v])

    await gateway.process_command(ws_owner, {"cmd": "ban_member", "room_id": "r2", "webid": _did(victim)})
    assert gateway._store.is_room_banned("r2", _did(victim))


@pytest.mark.asyncio
async def test_federated_room_admin_permission(gateway, noauth_env):
    """Federated (pod) rooms had the same hole: any member passed role='admin'.
    Now only the owner or a granted admin does."""
    import types
    owner = _did(Ed25519PrivateKey.generate())
    member = _did(Ed25519PrivateKey.generate())
    admin = _did(Ed25519PrivateKey.generate())
    ws_owner, ws_member, ws_admin = _mock_ws(), _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, owner)
    await _register(gateway, ws_member, member)
    await _register(gateway, ws_admin, admin)

    room_id = "fed-room-1"
    membership = types.SimpleNamespace(room=types.SimpleNamespace(owner_webid=owner))
    gateway.room_memberships[room_id] = (membership, object())
    gateway._store.set_room_role(room_id, admin, "admin")

    assert gateway._check_room_permission(ws_owner, room_id, "admin") is True
    assert gateway._check_room_permission(ws_admin, room_id, "admin") is True
    assert gateway._check_room_permission(ws_member, room_id, "admin") is False
    # Member-level actions are still open to any member.
    assert gateway._check_room_permission(ws_member, room_id, "member") is True


@pytest.mark.asyncio
async def test_granted_admin_can_ban(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    admin = Ed25519PrivateKey.generate()
    victim = Ed25519PrivateKey.generate()
    ws_owner, ws_admin, ws_v = _mock_ws(), _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_admin, _did(admin))
    await _register(gateway, ws_v, _did(victim))
    _room(gateway, "r3", _did(owner), [ws_owner, ws_admin, ws_v])
    gateway._store.set_room_role("r3", _did(admin), "admin")

    await gateway.process_command(ws_admin, {"cmd": "ban_member", "room_id": "r3", "webid": _did(victim)})
    assert gateway._store.is_room_banned("r3", _did(victim))
