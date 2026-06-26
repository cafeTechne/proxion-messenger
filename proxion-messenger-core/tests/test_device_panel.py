"""Tests: device list endpoint."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


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
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


@pytest.mark.asyncio
async def test_list_devices_excludes_attestation(gateway):
    """_handle_list_devices response never includes attestation_b64."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    owner_webid = "did:key:zOwner"
    gateway._client_webids[ws] = owner_webid
    gateway._store.register_device(
        "dev-001", owner_webid, "pubkeyb64==", "attestationb64=="
    )

    await gateway._handle_list_devices(ws, {})

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "devices"
    for d in sent["devices"]:
        assert "attestation_b64" not in d
