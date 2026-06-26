"""Round 2: Room invite defaults — expiry and max_uses."""
import json
import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9963, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    from proxion_messenger_core.didkey import pub_key_to_did
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    owner_did = pub_key_to_did(agent.identity_pub_bytes)
    gw._client_webids[ws] = owner_did
    gw._webid_sockets[owner_did] = ws
    return gw, ws


@pytest.mark.asyncio
async def test_new_room_invite_has_7day_expiry(gw):
    """Default invite created with chat_room_create expires in 7 days."""
    gateway, ws = gw
    before = time.time()
    await gateway.process_command(ws, {"cmd": "chat_room_create", "name": "TestRoom"})
    after = time.time()

    # Check the created invite in the store
    invites = gateway._store._conn().execute("SELECT * FROM room_invites").fetchall()
    assert invites, "Expected at least one invite to be created"
    invite = dict(invites[-1])
    expires_at = invite.get("expires_at")
    assert expires_at is not None, "expires_at should be set"
    expected_min = before + 7 * 24 * 3600 - 5
    expected_max = after + 7 * 24 * 3600 + 5
    assert expected_min <= expires_at <= expected_max, f"expires_at={expires_at} not in 7-day window"


@pytest.mark.asyncio
async def test_new_room_invite_has_default_max_uses(gw):
    """Default invite has uses_left = 100."""
    gateway, ws = gw
    await gateway.process_command(ws, {"cmd": "chat_room_create", "name": "TestRoom2"})
    invites = gateway._store._conn().execute("SELECT * FROM room_invites ORDER BY rowid DESC LIMIT 1").fetchall()
    assert invites
    invite = dict(invites[0])
    assert invite.get("uses_left") == 100, f"Expected uses_left=100, got {invite.get('uses_left')}"


@pytest.mark.asyncio
async def test_invite_creation_caps_expires_hours_and_max_uses(gw):
    """expires_hours is capped at 720; max_uses capped at 500."""
    gateway, ws = gw
    before = time.time()
    await gateway.process_command(ws, {
        "cmd": "chat_room_create",
        "name": "CappedRoom",
        "expires_hours": 9999,
        "max_uses": 9999,
    })
    after = time.time()
    invites = gateway._store._conn().execute("SELECT * FROM room_invites ORDER BY rowid DESC LIMIT 1").fetchall()
    assert invites
    invite = dict(invites[0])
    # Max hours = 720
    assert invite.get("expires_at") <= after + 720 * 3600 + 5
    # Max uses = 500
    assert invite.get("uses_left") == 500


@pytest.mark.asyncio
async def test_invite_creation_custom_within_bounds(gw):
    """Custom expires_hours=48, max_uses=10 are stored as-is."""
    gateway, ws = gw
    before = time.time()
    await gateway.process_command(ws, {
        "cmd": "chat_room_create",
        "name": "CustomRoom",
        "expires_hours": 48,
        "max_uses": 10,
    })
    after = time.time()
    invites = gateway._store._conn().execute("SELECT * FROM room_invites ORDER BY rowid DESC LIMIT 1").fetchall()
    assert invites
    invite = dict(invites[0])
    assert invite.get("uses_left") == 10
    expected_min = before + 48 * 3600 - 5
    expected_max = after + 48 * 3600 + 5
    assert expected_min <= invite.get("expires_at") <= expected_max
