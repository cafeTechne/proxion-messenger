"""Tests: read positions are flushed to pod and restored on cold start."""
from __future__ import annotations

import json
import time
import pytest
from unittest.mock import MagicMock

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
    mock_client.get = MagicMock(return_value=b'{}')
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_dirty_read_positions_accumulate(gateway):
    """_dirty_read_positions accumulates entries when update_last_read is called."""
    gateway._dirty_read_positions[("user1", "channel1")] = time.time()
    gateway._dirty_read_positions[("user1", "channel2")] = time.time()
    assert len(gateway._dirty_read_positions) == 2


@pytest.mark.asyncio
async def test_sync_read_positions_flushes_to_pod(gateway):
    """_sync_read_positions_to_pod puts merged positions to pod."""
    mock_client = _mock_pod_client(gateway)
    gateway._dirty_read_positions[("https://alice.pod/profile/card#me", "room-1")] = 1700000000.0
    await gateway._sync_read_positions_to_pod()
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    assert "read_positions.json" in uri


@pytest.mark.asyncio
async def test_sync_read_positions_clears_dirty(gateway):
    """After flush, _dirty_read_positions is empty."""
    mock_client = _mock_pod_client(gateway)
    gateway._dirty_read_positions[("user", "channel")] = time.time()
    await gateway._sync_read_positions_to_pod()
    assert len(gateway._dirty_read_positions) == 0


@pytest.mark.asyncio
async def test_restore_read_positions_loads_newer_values(gateway):
    """_restore_read_positions_from_pod sets timestamps newer than SQLite."""
    webid = "https://alice.pod/profile/card#me"
    channel = "channel-99"
    pod_ts = time.time() + 1000  # Pod has newer value
    data = {webid: {channel: pod_ts}}
    mock_client = _mock_pod_client(gateway)
    mock_client.get = MagicMock(return_value=json.dumps(data).encode())
    await gateway._restore_read_positions_from_pod()
    result = gateway._store.get_last_read(webid, channel)
    assert result == pytest.approx(pod_ts, abs=1.0)


@pytest.mark.asyncio
async def test_restore_read_positions_skips_older_values(gateway):
    """_restore_read_positions_from_pod does not overwrite newer SQLite values."""
    webid = "https://alice.pod/profile/card#me"
    channel = "channel-100"
    local_ts = time.time()
    gateway._store.set_last_read_ts(webid, channel, local_ts)
    pod_ts = local_ts - 500  # Pod has older value
    data = {webid: {channel: pod_ts}}
    mock_client = _mock_pod_client(gateway)
    mock_client.get = MagicMock(return_value=json.dumps(data).encode())
    await gateway._restore_read_positions_from_pod()
    result = gateway._store.get_last_read(webid, channel)
    assert result == pytest.approx(local_ts, abs=1.0)
