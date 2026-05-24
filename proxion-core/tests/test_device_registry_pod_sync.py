"""Tests: device registrations written and deleted from pod; restored on cold start."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=GatewayConfig(port=9990, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _mock_pod_client(gw):
    mock_client = MagicMock()
    mock_client.put = MagicMock(return_value=None)
    mock_client.delete = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_device_to_pod_puts_correct_path(gateway):
    """_sync_device_to_pod writes to stash://pod/devices/{device_id}.json."""
    mock_client = _mock_pod_client(gateway)
    await gateway._sync_device_to_pod(
        "dev-001", "https://alice.pod/profile/card#me", "pubkey==", "attest==", False
    )
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    assert uri == "stash://pod/devices/dev-001.json"


@pytest.mark.asyncio
async def test_delete_device_from_pod_calls_delete(gateway):
    """_delete_device_from_pod calls client.delete."""
    mock_client = _mock_pod_client(gateway)
    await gateway._delete_device_from_pod("dev-002")
    assert mock_client.delete.called
    uri = mock_client.delete.call_args[0][0]
    assert uri == "stash://pod/devices/dev-002.json"


@pytest.mark.asyncio
async def test_restore_devices_from_pod_populates_sqlite(gateway):
    """_restore_devices_from_pod saves devices into SQLite."""
    rec = {
        "device_id": "dev-restore-1",
        "owner_webid": "https://alice.pod/profile/card#me",
        "device_pub_b64": "pubkey==",
        "attestation_b64": "attest==",
        "is_primary": False,
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/devices/dev-restore-1.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    device = gateway._store.get_device("dev-restore-1")
    assert device is not None
    assert device["owner_webid"] == "https://alice.pod/profile/card#me"


@pytest.mark.asyncio
async def test_restore_devices_skips_existing(gateway):
    """_restore_devices_from_pod does not duplicate existing device records."""
    gateway._store.register_device(
        "dev-exists", "https://alice.pod/profile/card#me", "pub==", "att=="
    )
    rec = {
        "device_id": "dev-exists",
        "owner_webid": "https://alice.pod/profile/card#me",
        "device_pub_b64": "newpub==",
        "attestation_b64": "newatt==",
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/devices/dev-exists.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    device = gateway._store.get_device("dev-exists")
    assert device["device_pub_b64"] == "pub=="  # Original preserved


@pytest.mark.asyncio
async def test_delete_device_tolerates_404(gateway):
    """_delete_device_from_pod swallows 404."""
    from proxion_messenger_core.solid_client import SolidError
    mock_client = MagicMock()
    mock_client.delete = MagicMock(side_effect=SolidError("not found", status_code=404))
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    await gateway._delete_device_from_pod("dev-404")  # Should not raise
