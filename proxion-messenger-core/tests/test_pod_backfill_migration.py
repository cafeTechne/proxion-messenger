"""Tests: _run_pod_backfill skips if version marker present; runs for missing data."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

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


@pytest.mark.asyncio
async def test_backfill_skips_if_version_marker_present(gateway):
    """_run_pod_backfill returns early if pod reports version >= 26."""
    marker = json.dumps({"version": 26, "completed_at": 1700000000.0}).encode()
    mock_client = MagicMock()
    mock_client.get = MagicMock(return_value=marker)
    mock_client.put = MagicMock(return_value=None)
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._run_pod_backfill()
    # put should NOT be called (skipped)
    put_uris = [call[0][0] for call in mock_client.put.call_args_list]
    assert not any("migration_version" in u for u in put_uris)


@pytest.mark.asyncio
async def test_backfill_writes_migration_marker_on_completion(gateway):
    """_run_pod_backfill writes version marker after completing backfill."""
    mock_client = MagicMock()
    mock_client.get = MagicMock(side_effect=Exception("not found"))
    mock_client.put = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    gateway._pod_url = "https://pod.example/"

    await gateway._run_pod_backfill()
    put_uris = [call[0][0] for call in mock_client.put.call_args_list]
    assert any("migration_version" in u for u in put_uris)


@pytest.mark.asyncio
async def test_backfill_no_op_without_pod(gateway):
    """_run_pod_backfill is no-op when no pod client."""
    await gateway._run_pod_backfill()  # Should not raise


@pytest.mark.asyncio
async def test_backfill_queues_cert_sync_tasks(gateway):
    """_run_pod_backfill fires cert sync for all existing relationships."""
    from proxion_messenger_core.federation import RelationshipCertificate
    gateway._store.save_relationship(
        {
            "certificate_id": "cert-backfill-1",
            "issuer": "a" * 64,
            "subject": "b" * 64,
            "capabilities": [],
            "expires_at": 9999999999,
        },
        peer_did=None,
    )
    synced_certs = []
    async def fake_sync_cert(cert_dict):
        synced_certs.append(cert_dict.get("certificate_id"))
    gateway._sync_cert_to_pod = fake_sync_cert

    mock_client = MagicMock()
    mock_client.get = MagicMock(side_effect=Exception("not found"))
    mock_client.put = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    gateway._pod_url = "https://pod.example/"

    import asyncio
    await gateway._run_pod_backfill()
    await asyncio.sleep(0)  # Let tasks run
    # Cert sync should have been called for the existing cert
    assert "cert-backfill-1" in synced_certs
