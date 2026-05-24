"""Tests: local DM messages are written to the pod on send."""
from __future__ import annotations

import asyncio
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
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_local_dm_to_pod_fires_on_send(gateway):
    """_sync_local_dm_to_pod puts to pod with correct path structure."""
    mock_client = _mock_pod_client(gateway)
    thread_id = "thread-abc-123"
    message = {
        "message_id": "msg-001",
        "from_webid": "https://alice.pod/profile/card#me",
        "content": "Hello",
        "timestamp": "2024-01-01T00:00:00+00:00",
    }
    await gateway._sync_local_dm_to_pod(thread_id, message)
    assert mock_client.put.called
    call_args = mock_client.put.call_args
    uri = call_args[0][0]
    assert "stash://pod/local_dms/" in uri
    assert "msg-001.json" in uri


@pytest.mark.asyncio
async def test_sync_local_dm_skips_without_pod(gateway):
    """_sync_local_dm_to_pod is a no-op when no pod client."""
    # No pod client set up
    thread_id = "thread-abc"
    message = {"message_id": "msg-x", "content": "hi"}
    # Should not raise
    await gateway._sync_local_dm_to_pod(thread_id, message)


@pytest.mark.asyncio
async def test_sync_local_dm_skips_missing_message_id(gateway):
    """_sync_local_dm_to_pod skips when message_id is absent."""
    mock_client = _mock_pod_client(gateway)
    await gateway._sync_local_dm_to_pod("thread-1", {"content": "no id"})
    assert not mock_client.put.called


@pytest.mark.asyncio
async def test_sync_local_dm_thread_key_is_hashed(gateway):
    """Thread key in pod URI is sha256 hash, not raw thread_id."""
    import hashlib
    mock_client = _mock_pod_client(gateway)
    thread_id = "some:weird/path?id"
    message = {"message_id": "m1", "content": "test"}
    await gateway._sync_local_dm_to_pod(thread_id, message)
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    expected_key = hashlib.sha256(thread_id.encode()).hexdigest()[:16]
    assert expected_key in uri
