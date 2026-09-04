"""Room-lifecycle authorization and eviction fixes.

Covers: owner-immunity to ban/mute/kick, role rows dropped on removal, admin
authz requiring live membership, all-sockets eviction on kick/ban/leave, ban
key-rotation parity with kick, invite expiry/max-uses enforcement on join, the
per-room member cap, and the forward source-read check.
"""
from __future__ import annotations

import json
import time

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core import _gateway_rooms


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    # A non-localhost address: registering from 127.0.0.1 triggers the gateway's
    # DID-rotation recovery, which auto-adopts every local room for the new
    # identity and would defeat the join/capacity assertions here.
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("203.0.113.10", 12345)
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
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "life.db")),
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


# ---------------------------------------------------------------------------
# A1 — owner immunity, role removal, admin-needs-membership
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_cannot_ban_mute_kick_owner(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    admin = Ed25519PrivateKey.generate()
    ws_owner, ws_admin = _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_admin, _did(admin))
    _room(gateway, "r1", _did(owner), [ws_owner, ws_admin])
    gateway._store.set_room_role("r1", _did(admin), "admin")

    for cmd in ("ban_member", "mute_member", "kick_member"):
        ws_admin.send.reset_mock()
        await gateway.process_command(
            ws_admin, {"cmd": cmd, "room_id": "r1", "webid": _did(owner)})
        assert _err(ws_admin) is not None, f"{cmd} should be refused against the owner"
    # Owner was neither banned nor removed.
    assert not gateway._store.is_room_banned("r1", _did(owner))
    assert _did(owner) in gateway._store.get_room_members("r1")


@pytest.mark.asyncio
async def test_ban_drops_role_and_membership_blocks_reconnect(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    admin = Ed25519PrivateKey.generate()
    ws_owner, ws_admin = _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_admin, _did(admin))
    _room(gateway, "r2", _did(owner), [ws_owner, ws_admin])
    gateway._store.set_room_role("r2", _did(admin), "admin")

    await gateway.process_command(
        ws_owner, {"cmd": "ban_member", "room_id": "r2", "webid": _did(admin)})
    assert gateway._store.is_room_banned("r2", _did(admin))
    # Role row gone and no longer a member.
    assert gateway._store.get_room_role("r2", _did(admin)) == "member"
    assert _did(admin) not in gateway._store.get_room_members("r2")

    # A reconnect of the ex-admin cannot run admin ops (role alone is not enough).
    ws_admin2 = _mock_ws()
    await _register(gateway, ws_admin2, _did(admin))
    assert gateway._check_room_permission(ws_admin2, "r2", "admin") is False


@pytest.mark.asyncio
async def test_admin_needs_current_membership(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    admin = Ed25519PrivateKey.generate()
    ws_owner, ws_admin = _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_admin, _did(admin))
    _room(gateway, "r3", _did(owner), [ws_owner, ws_admin])
    gateway._store.set_room_role("r3", _did(admin), "admin")
    assert gateway._check_room_permission(ws_admin, "r3", "admin") is True
    # Drop membership but keep the role row: authz must now fail.
    gateway._store.remove_room_member("r3", _did(admin))
    assert gateway._check_room_permission(ws_admin, "r3", "admin") is False


# ---------------------------------------------------------------------------
# A2 — all-sockets eviction + ban key rotation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kick_evicts_all_target_sockets(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    victim = Ed25519PrivateKey.generate()
    ws_owner, ws_v1, ws_v2 = _mock_ws(), _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_v1, _did(victim))
    await _register(gateway, ws_v2, _did(victim))
    _room(gateway, "r4", _did(owner), [ws_owner, ws_v1, ws_v2])

    await gateway.process_command(
        ws_owner, {"cmd": "kick_member", "room_id": "r4", "webid": _did(victim)})
    members = gateway._local_rooms["r4"]["members"]
    assert ws_v1 not in members and ws_v2 not in members
    assert _got(ws_v1, "kicked_from_room") and _got(ws_v2, "kicked_from_room")


@pytest.mark.asyncio
async def test_ban_evicts_all_sockets_and_rotates_key(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    victim = Ed25519PrivateKey.generate()
    ws_owner, ws_v1, ws_v2 = _mock_ws(), _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_v1, _did(victim))
    await _register(gateway, ws_v2, _did(victim))
    _room(gateway, "r5", _did(owner), [ws_owner, ws_v1, ws_v2])

    ws_owner.send.reset_mock()
    await gateway.process_command(
        ws_owner, {"cmd": "ban_member", "room_id": "r5", "webid": _did(victim)})
    members = gateway._local_rooms["r5"]["members"]
    assert ws_v1 not in members and ws_v2 not in members
    assert _got(ws_v1, "kicked_from_room") and _got(ws_v2, "kicked_from_room")
    # Remaining owner receives a room_key_update (rotation parity with kick).
    assert _got(ws_owner, "room_key_update")


# ---------------------------------------------------------------------------
# A3 — invite expiry / max-uses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_max_uses_one(gateway, noauth_env):
    creator = _mock_ws()
    await _register(gateway, creator, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_chat_room_create(
        creator, {"name": "Cap", "history_mode": "none", "max_uses": 1})
    code = json.loads(creator.send.call_args[0][0])["code"]

    j1 = _mock_ws()
    await _register(gateway, j1, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_join_room(j1, {"code": code})
    assert _got(j1, "room_joined")

    j2 = _mock_ws()
    await _register(gateway, j2, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_join_room(j2, {"code": code})
    assert not _got(j2, "room_joined")
    assert _err(j2) is not None


@pytest.mark.asyncio
async def test_invite_expired_rejected(gateway, noauth_env):
    creator = _mock_ws()
    await _register(gateway, creator, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_chat_room_create(creator, {"name": "Exp", "history_mode": "none"})
    resp = json.loads(creator.send.call_args[0][0])
    code, room_id = resp["code"], resp["room_id"]
    # Force the stored invite to be already expired.
    code_hash = gateway._hmac_invite_code(code)
    with gateway._store._conn() as conn:
        conn.execute("UPDATE room_invites SET expires_at = ? WHERE code_hash = ?",
                     (time.time() - 1, code_hash))

    joiner = _mock_ws()
    await _register(gateway, joiner, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_join_room(joiner, {"code": code})
    assert not _got(joiner, "room_joined")
    assert _err(joiner) is not None


@pytest.mark.asyncio
async def test_invite_multi_use_still_works(gateway, noauth_env):
    creator = _mock_ws()
    await _register(gateway, creator, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_chat_room_create(
        creator, {"name": "Multi", "history_mode": "none", "max_uses": 3})
    code = json.loads(creator.send.call_args[0][0])["code"]
    for _ in range(3):
        j = _mock_ws()
        await _register(gateway, j, _did(Ed25519PrivateKey.generate()))
        await gateway._handle_join_room(j, {"code": code})
        assert _got(j, "room_joined")


@pytest.mark.asyncio
async def test_legacy_plaintext_join_unaffected(gateway, noauth_env):
    """A room whose code is registered directly (no invite row) still joins —
    the consume gate applies only when an invite row exists for the code."""
    owner_ws = _mock_ws()
    await _register(gateway, owner_ws, _did(Ed25519PrivateKey.generate()))
    plain = "legacycode"
    gateway._local_rooms["legacy-room"] = {
        "creator_webid": gateway._client_webids[owner_ws], "members": {owner_ws},
        "messages": [], "history_mode": "none", "name": "Legacy", "code": plain,
        "invite_url": "",
    }
    gateway._room_codes[plain] = "legacy-room"
    gateway._store.save_room("legacy-room", "Legacy", plain, "", "none",
                             gateway._client_webids[owner_ws])

    joiner = _mock_ws()
    await _register(gateway, joiner, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_join_room(joiner, {"code": plain})
    assert _got(joiner, "room_joined")


# ---------------------------------------------------------------------------
# A4 — capacity cap + leave all-sockets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_over_cap_join_rejected(gateway, noauth_env, monkeypatch):
    monkeypatch.setattr(_gateway_rooms, "_MAX_ROOM_MEMBERS", 2)
    creator = _mock_ws()
    await _register(gateway, creator, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_chat_room_create(
        creator, {"name": "Small", "history_mode": "none", "max_uses": 50})
    code = json.loads(creator.send.call_args[0][0])["code"]
    # Creator is member #1; one join fills the cap of 2.
    j1 = _mock_ws()
    await _register(gateway, j1, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_join_room(j1, {"code": code})
    assert _got(j1, "room_joined")
    # Next join is over the cap.
    j2 = _mock_ws()
    await _register(gateway, j2, _did(Ed25519PrivateKey.generate()))
    await gateway._handle_join_room(j2, {"code": code})
    assert not _got(j2, "room_joined")
    assert (_err(j2) or {}).get("message") == "room_full"


@pytest.mark.asyncio
async def test_leave_evicts_all_leaver_sockets(gateway, noauth_env):
    owner = Ed25519PrivateKey.generate()
    leaver = Ed25519PrivateKey.generate()
    ws_owner, ws_l1, ws_l2 = _mock_ws(), _mock_ws(), _mock_ws()
    await _register(gateway, ws_owner, _did(owner))
    await _register(gateway, ws_l1, _did(leaver))
    await _register(gateway, ws_l2, _did(leaver))
    _room(gateway, "r6", _did(owner), [ws_owner, ws_l1, ws_l2])

    await gateway.process_command(ws_l1, {"cmd": "leave_local_room", "room_id": "r6"})
    members = gateway._local_rooms["r6"]["members"]
    assert ws_l1 not in members and ws_l2 not in members


# ---------------------------------------------------------------------------
# A5 — forward source-read check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forward_rejects_unreadable_source(gateway, noauth_env):
    actor = Ed25519PrivateKey.generate()
    ws_actor = _mock_ws()
    await _register(gateway, ws_actor, _did(actor))
    # The source-read check is gated on _auth_enforced() (like the other read-authz
    # handlers): only meaningful when strangers can register. Force it on here.
    gateway._force_auth = True
    # actor is in the TARGET room but not the SOURCE room.
    _room(gateway, "target", _did(actor), [ws_actor])
    _room(gateway, "secret", _did(Ed25519PrivateKey.generate()), [])
    gateway._store.save_message(
        "msg-secret", "secret", "room",
        _did(Ed25519PrivateKey.generate()), "S", "top secret",
        "2026-01-01T00:00:00+00:00",
    )
    ws_actor.send.reset_mock()
    await gateway._handle_forward_message(
        ws_actor, {"message_id": "msg-secret", "target_thread_id": "target"})
    assert _err(ws_actor) is not None
    assert _err(ws_actor)["message"] == "Cannot read source message"
