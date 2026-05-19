"""Round 8: Revocation checks — identity revoked at registration and mutating commands."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9973),
        read_state=ReadState(),
    )


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Registration: revoked DID rejected with close(1008)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_revoked_did_closes_connection(gateway):
    """Attempting to register a revoked DID closes the socket with code 1008."""
    revoked_did = "did:key:revoked-reg"
    gateway._revoked_dids.add(revoked_did)

    ws = _ws()
    gateway.clients.add(ws)
    # In test environment auth is not required (loopback), so register runs directly
    import os
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(os.environ, {"PROXION_REQUIRE_AUTH": "0"}):
        await gateway._handle_register(ws, {"did": revoked_did, "display_name": "evil"})

    ws.close.assert_called_once()
    call_args = ws.close.call_args
    assert call_args[0][0] == 1008


@pytest.mark.asyncio
async def test_register_non_revoked_did_succeeds(gateway):
    """Registering a non-revoked DID completes normally."""
    import os
    ws = _ws()
    gateway.clients.add(ws)
    with __import__("unittest.mock", fromlist=["patch"]).patch.dict(os.environ, {"PROXION_REQUIRE_AUTH": "0"}):
        await gateway._handle_register(ws, {"did": "did:key:good", "display_name": "alice"})

    ws.close.assert_not_called()
    assert "did:key:good" in gateway._client_webids.values()


# ---------------------------------------------------------------------------
# Mutating commands: revoked identity receives E_REVOKED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_room_from_revoked_identity_rejected(gateway):
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:revoker"
    gateway._revoked_dids.add("did:key:revoker")

    await gateway.process_command(ws, {
        "cmd": "send_room",
        "room_id": "some-room",
        "content": "hello",
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_REVOKED"


@pytest.mark.asyncio
async def test_send_dm_from_revoked_identity_rejected(gateway):
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:revoker2"
    gateway._revoked_dids.add("did:key:revoker2")

    await gateway.process_command(ws, {
        "cmd": "send_dm",
        "cert_id": "cid",
        "content": "msg",
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_REVOKED"


@pytest.mark.asyncio
async def test_non_mutating_command_from_revoked_not_blocked(gateway):
    """Non-mutating commands (e.g. get_rooms) are NOT blocked by the revoked-DID check."""
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:revoker3"
    gateway._revoked_dids.add("did:key:revoker3")

    await gateway.process_command(ws, {"cmd": "get_rooms"})
    if ws.send.called:
        sent = json.loads(ws.send.call_args[0][0])
        assert sent.get("code") != "E_REVOKED"


@pytest.mark.asyncio
async def test_ping_from_revoked_identity_passes(gateway):
    """ping/pong are rate-exempt heartbeats and must pass even for revoked identities."""
    ws = MagicMock()
    ws.send = AsyncMock()
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:revoker4"
    gateway._revoked_dids.add("did:key:revoker4")

    await gateway.process_command(ws, {"cmd": "ping"})
    # ping may or may not send a pong — what matters is no E_REVOKED
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        assert msg.get("code") != "E_REVOKED"
