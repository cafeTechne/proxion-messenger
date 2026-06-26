"""Round 1: Owner-only command ACL tests for process_command."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9975), read_state=ReadState(),
    )
    return gw


def _owner_did(gw) -> str:
    from proxion_messenger_core.didkey import pub_key_to_did
    return pub_key_to_did(gw.agent.identity_pub_bytes)


def _registered_ws(gw, webid="did:key:test"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


@pytest.mark.asyncio
async def test_owner_only_command_denied_for_non_owner(gateway):
    """get_audit_logs from non-owner must return E_FORBIDDEN."""
    ws = _registered_ws(gateway, webid="did:key:zStranger")
    await gateway.process_command(ws, {"cmd": "get_audit_logs"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_FORBIDDEN"


@pytest.mark.asyncio
async def test_owner_only_command_allowed_for_owner(gateway):
    """get_audit_logs from the owner must not return E_FORBIDDEN."""
    owner = _owner_did(gateway)
    ws = _registered_ws(gateway, webid=owner)
    await gateway.process_command(ws, {"cmd": "get_audit_logs"})
    # May fail for other reasons (no store), but must not be E_FORBIDDEN
    if ws.send.call_args:
        sent = json.loads(ws.send.call_args[0][0])
        assert sent.get("code") != "E_FORBIDDEN"


@pytest.mark.asyncio
async def test_connect_css_forbidden_for_non_owner(gateway):
    """connect_css from non-owner must return E_FORBIDDEN."""
    ws = _registered_ws(gateway, webid="did:key:zStranger")
    await gateway.process_command(ws, {"cmd": "connect_css", "css_url": "https://pod.example.com", "email": "a@b.com"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_FORBIDDEN"


@pytest.mark.asyncio
async def test_disconnect_pod_forbidden_for_non_owner(gateway):
    """disconnect_pod from non-owner must return E_FORBIDDEN."""
    ws = _registered_ws(gateway, webid="did:key:zStranger")
    await gateway.process_command(ws, {"cmd": "disconnect_pod"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_FORBIDDEN"


@pytest.mark.asyncio
async def test_reconnect_pod_forbidden_for_non_owner(gateway):
    """reconnect_pod from non-owner must return E_FORBIDDEN."""
    ws = _registered_ws(gateway, webid="did:key:zStranger")
    await gateway.process_command(ws, {"cmd": "reconnect_pod"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_FORBIDDEN"


@pytest.mark.asyncio
async def test_unauthenticated_command_rejected_centrally(gateway):
    """Unregistered client gets 'Not registered', not E_FORBIDDEN."""
    ws = MagicMock()
    ws.send = AsyncMock()
    # Not in clients or _client_webids
    await gateway.process_command(ws, {"cmd": "get_audit_logs"})
    sent = json.loads(ws.send.call_args[0][0])
    assert "registered" in sent.get("message", "").lower() or sent.get("type") == "error"
    assert sent.get("code") != "E_FORBIDDEN"
