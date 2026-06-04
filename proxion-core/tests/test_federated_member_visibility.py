"""Tests: federated member visibility in room member list."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


@pytest.mark.asyncio
async def test_announce_room_join_broadcasts_member_joined(gateway):
    """announce_room_join broadcasts room_member_joined to local members."""
    local_ws = _ws()
    caller_ws = _ws()
    room_id = "room-vis-1"
    code = "vis123"
    caller_webid = "did:key:zBob"
    gateway._client_webids[caller_ws] = caller_webid
    gateway._local_rooms[room_id] = {
        "name": "Test", "code": code,
        "members": {local_ws, caller_ws},
    }
    gateway.clients.add(local_ws)
    gateway.clients.add(caller_ws)

    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(caller_ws, {
            "room_id": room_id,
            "code": code,
            "home_gateway": "https://bob.example.com",
        })

    # local_ws should have received room_member_joined
    calls = [json.loads(c[0][0]) for c in local_ws.send.call_args_list]
    joined = [c for c in calls if c.get("type") == "room_member_joined"]
    assert len(joined) == 1
    assert joined[0]["federated"] is True
    assert joined[0]["webid"] == caller_webid


@pytest.mark.asyncio
async def test_room_member_joined_has_gateway_field(gateway):
    """room_member_joined event includes the gateway URL."""
    local_ws = _ws()
    caller_ws = _ws()
    room_id = "room-vis-2"
    code = "vis456"
    caller_webid = "did:key:zCarol"
    gateway._client_webids[caller_ws] = caller_webid
    gateway._local_rooms[room_id] = {
        "name": "Test", "code": code,
        "members": {local_ws},
    }
    gateway.clients.add(local_ws)
    gateway.clients.add(caller_ws)

    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway._handle_announce_room_join(caller_ws, {
            "room_id": room_id, "code": code,
            "home_gateway": "https://carol.example.com",
        })

    calls = [json.loads(c[0][0]) for c in local_ws.send.call_args_list]
    joined = [c for c in calls if c.get("type") == "room_member_joined"]
    assert joined[0]["gateway"] == "https://carol.example.com"


@pytest.mark.asyncio
async def test_get_room_members_includes_federated(gateway):
    """get_room_members response includes federated members from store."""
    ws = _ws()
    room_id = "room-vis-3"
    local_webid = "did:key:zAlice"
    gateway._client_webids[ws] = local_webid
    gateway._local_rooms[room_id] = {"name": "Test", "code": "x", "members": {ws}}
    gateway.clients.add(ws)
    gateway._store.add_room_member(room_id, local_webid)
    gateway._store.add_federated_room_member(room_id, "did:key:zBob", "https://bob.example.com")

    await gateway._handle_get_room_members(ws, {"room_id": room_id})

    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    resp = next(c for c in calls if c.get("type") == "room_members")
    webids = [m["webid"] for m in resp["members"]]
    assert "did:key:zBob" in webids
    fed_member = next(m for m in resp["members"] if m["webid"] == "did:key:zBob")
    assert fed_member.get("federated") is True


@pytest.mark.asyncio
async def test_announce_room_join_rejects_private_ip_gateway(gateway):
    """announce_room_join returns error when home_gateway is a private IP (S3 SSRF guard)."""
    ws = _ws()
    room_id = "room-ssrf-1"
    code = "ssrfcode"
    caller_webid = "did:key:zEve"
    gateway._client_webids[ws] = caller_webid
    gateway._local_rooms[room_id] = {"name": "Test", "code": code, "members": {ws}}
    gateway.clients.add(ws)

    import json
    await gateway._handle_announce_room_join(ws, {
        "room_id": room_id,
        "code": code,
        "home_gateway": "http://10.0.0.1/admin",
    })

    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert any(c.get("type") == "error" for c in calls), \
        f"Expected error for private IP gateway, got: {calls}"
    assert gateway._store.get_federated_room_members(room_id) == []
