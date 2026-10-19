"""An ownership-transfer offer must die with the target's membership: a kicked or
banned member used to be able to accept a still-pending offer and take the room."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

OWNER = "did:key:z6MkOwner"
TARGET = "did:key:z6MkTarget"
RID = "room-o"


def _ws(gw, did):
    w = AsyncMock()
    w.__hash__ = lambda s: id(s)
    w.__eq__ = lambda s, o: s is o
    gw.clients.add(w)
    gw._client_webids[w] = did
    gw._webid_sockets.setdefault(did, set()).add(w)
    return w


def _setup(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "s.db")))
    owner, target = _ws(gw, OWNER), _ws(gw, TARGET)
    gw._local_rooms[RID] = {"name": "R", "code": "c", "members": {owner, target}, "invite_url": "",
                            "history_mode": "none", "messages": [], "creator_webid": OWNER}
    gw._store.add_room_member(RID, OWNER)
    gw._store.add_room_member(RID, TARGET)
    return gw, owner, target


async def _offer(gw, owner):
    await gw._handle_transfer_ownership(owner, {"room_id": RID, "to_did": TARGET})
    assert RID in gw._pending_ownership_transfers


@pytest.mark.asyncio
async def test_banned_target_cannot_accept(tmp_path):
    gw, owner, target = _setup(tmp_path)
    await _offer(gw, owner)
    await gw._handle_ban_member(owner, {"room_id": RID, "webid": TARGET})
    gw._local_rooms[RID]["members"].add(target)   # even if a socket lingers
    await gw._handle_accept_ownership(target, {"room_id": RID})
    assert gw._local_rooms[RID]["creator_webid"] == OWNER


@pytest.mark.asyncio
async def test_kicked_target_cannot_accept_after_rejoining(tmp_path):
    gw, owner, target = _setup(tmp_path)
    await _offer(gw, owner)
    await gw._handle_kick_member(owner, {"room_id": RID, "webid": TARGET})
    gw._local_rooms[RID]["members"].add(target)   # rejoins with the invite code
    await gw._handle_accept_ownership(target, {"room_id": RID})
    assert gw._local_rooms[RID]["creator_webid"] == OWNER


@pytest.mark.asyncio
async def test_departed_target_cannot_accept(tmp_path):
    gw, owner, target = _setup(tmp_path)
    await _offer(gw, owner)
    gw._local_rooms[RID]["members"].discard(target)
    await gw._handle_accept_ownership(target, {"room_id": RID})
    assert gw._local_rooms[RID]["creator_webid"] == OWNER


@pytest.mark.asyncio
async def test_member_can_still_accept(tmp_path):
    gw, owner, target = _setup(tmp_path)
    await _offer(gw, owner)
    await gw._handle_accept_ownership(target, {"room_id": RID})
    assert gw._local_rooms[RID]["creator_webid"] == TARGET
