"""Round 2: Per-room and global join rate limiting (room_join_attempts_v2)."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9962, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def _registered_ws(gw, ip="10.0.0.1"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = "did:key:testuser"
    gw._session_meta[ws] = {"ip_addr": ip}
    return ws


@pytest.mark.asyncio
async def test_room_scoped_join_limit_enforced(gw):
    """5 failed attempts per room per IP triggers join_rate_limited."""
    ws = _registered_ws(gw, ip="10.1.2.3")
    # Make 5 failed attempts (no room with this code exists)
    for _ in range(5):
        await gw.process_command(ws, {"cmd": "join_room", "code": "nosuchcode"})

    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "join_room", "code": "nosuchcode"})
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    rate_limited = [c for c in calls if c.get("message") == "join_rate_limited"]
    assert rate_limited, f"Expected join_rate_limited, got {calls}"
    assert rate_limited[0].get("retry_after") == 60


@pytest.mark.asyncio
async def test_global_join_limit_enforced(gw):
    """20 failed attempts across different rooms triggers global join_rate_limited."""
    ws = _registered_ws(gw, ip="10.5.6.7")
    # Make 20 attempts across 20 different nonexistent codes
    for i in range(20):
        await gw.process_command(ws, {"cmd": "join_room", "code": f"badcode{i:04d}"})

    ws.send.reset_mock()
    # 21st attempt on a new code
    await gw.process_command(ws, {"cmd": "join_room", "code": "yetanother"})
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    rate_limited = [c for c in calls if c.get("message") == "join_rate_limited"]
    assert rate_limited, f"Expected global join_rate_limited, got {calls}"


@pytest.mark.asyncio
async def test_successful_join_not_counted_as_failed_attempt(gw):
    """A successful join does not add to the failure count."""
    import secrets, base64
    ws = _registered_ws(gw, ip="10.9.0.1")
    # Create a real room
    raw_bytes = secrets.token_bytes(16)
    code = base64.b32encode(raw_bytes).rstrip(b"=").decode().lower()
    code_hash = gw._hmac_invite_code(code)
    room_id = "room-testsucc01"
    gw._local_rooms[room_id] = {
        "name": "Test", "code": code_hash, "members": set(),
        "invite_url": "", "history_mode": "none", "messages": [], "creator_webid": "",
    }
    gw._room_codes[code_hash] = room_id

    # Successful join
    await gw.process_command(ws, {"cmd": "join_room", "code": code})
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert any(c.get("type") == "room_joined" for c in calls), f"Should have joined: {calls}"
    # No rate_limited message
    assert not any(c.get("message") == "join_rate_limited" for c in calls)
