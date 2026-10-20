"""decline_ownership: only the offered member can turn a transfer down, and a
declined offer can no longer be accepted."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

ROOM = "room-ownership-decline"


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _sent(ws) -> list[dict]:
    return [json.loads(c.args[0]) for c in ws.send.call_args_list]


@pytest.fixture
async def room(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw = ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "own.db")),
    )
    people = {}
    for name in ("owner", "target", "other"):
        did = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
        ws = _mock_ws()
        gw.clients.add(ws)
        await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": name})
        people[name] = (did, ws)
    gw._local_rooms[ROOM] = {
        "name": "Decline", "code": "decline", "invite_url": "",
        "members": {ws for _d, ws in people.values()},
        "creator_webid": people["owner"][0],
    }
    gw._room_codes["decline"] = ROOM
    for did, _ws in people.values():
        gw._store.add_room_member(ROOM, did)
    owner_ws = people["owner"][1]
    await gw.process_command(owner_ws, {"cmd": "transfer_ownership", "room_id": ROOM,
                                        "to_did": people["target"][0]})
    assert any(m.get("type") == "ownership_transfer_offer" for m in _sent(people["target"][1]))
    for _d, ws in people.values():
        ws.send.reset_mock()
    return gw, people


@pytest.mark.asyncio
async def test_target_declines_and_the_offer_is_gone(room):
    gw, people = room
    owner_did, owner_ws = people["owner"]
    _target_did, target_ws = people["target"]

    await gw.process_command(target_ws, {"cmd": "decline_ownership", "room_id": ROOM})
    assert ROOM not in gw._pending_ownership_transfers
    assert any("declined" in m.get("message", "") for m in _sent(owner_ws))

    target_ws.send.reset_mock()
    await gw.process_command(target_ws, {"cmd": "accept_ownership", "room_id": ROOM})
    assert gw._local_rooms[ROOM]["creator_webid"] == owner_did
    assert not any(m.get("type") == "ownership_transferred" for m in _sent(target_ws))


@pytest.mark.asyncio
async def test_someone_else_cannot_decline_on_the_targets_behalf(room):
    gw, people = room
    _target_did, target_ws = people["target"]

    await gw.process_command(people["other"][1], {"cmd": "decline_ownership", "room_id": ROOM})
    await gw.process_command(people["owner"][1], {"cmd": "decline_ownership", "room_id": ROOM})
    assert ROOM in gw._pending_ownership_transfers

    await gw.process_command(target_ws, {"cmd": "accept_ownership", "room_id": ROOM})
    assert gw._local_rooms[ROOM]["creator_webid"] == people["target"][0]
