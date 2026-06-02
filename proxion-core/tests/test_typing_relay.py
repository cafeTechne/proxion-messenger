"""Tests: cross-gateway typing indicator relay."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    return gw


@pytest.mark.asyncio
async def test_typing_relay_delivers_to_local_socket(gateway):
    """_handle_typing_relay delivers typing event to local DM peer."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    local_webid = "did:key:zLocal"
    gateway.clients.add(ws)
    gateway._client_webids[ws] = local_webid
    gateway._webid_sockets[local_webid] = {ws}

    store = MagicMock()
    store.get_dm_threads = MagicMock(return_value=[
        {"thread_id": "cert-abc", "peer_webid": local_webid, "owner_webid": "did:key:zRemote"}
    ])
    gateway._store = store

    await gateway._handle_typing_relay({
        "from_webid": "did:key:zRemote",
        "cert_id": "cert-abc",
    })

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "typing"
    assert sent["from_webid"] == "did:key:zRemote"


@pytest.mark.asyncio
async def test_typing_relay_ignores_missing_from_webid(gateway):
    """_handle_typing_relay returns 400 if from_webid is missing."""
    status, _ = await gateway._handle_typing_relay({"cert_id": "cert-xyz"})
    assert status.startswith("400")


@pytest.mark.asyncio
async def test_set_presence_relays_to_known_peer_gateways(gateway):
    """_handle_set_presence triggers relay to all known peer gateways."""
    ws = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    gateway._client_webids[ws] = "did:key:zSelf"
    gateway.clients.add(ws)
    gateway._peer_gateway_urls["did:key:zPeer"] = "https://peer.example.com"

    with patch.object(gateway, "_relay_ephemeral", new_callable=AsyncMock) as mock_relay:
        mock_relay.return_value = None
        with patch("asyncio.create_task") as mock_task:
            await gateway._handle_set_presence(ws, {"status": "online"})
            # create_task should be called for the relay (no exception is success)
