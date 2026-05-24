"""Tests: local room pins written to pod and restored per room."""
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
    mock_client.get = MagicMock(return_value=b'{}')
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_pin_to_pod_puts_correct_uri(gateway):
    """_sync_pin_to_pod writes to the correct pod path."""
    mock_client = _mock_pod_client(gateway)
    await gateway._sync_pin_to_pod("room-1", "msg-1", "alice", "Hello world")
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    assert "stash://pod/rooms/room-1/pins/msg-1.json" == uri


@pytest.mark.asyncio
async def test_delete_pin_from_pod(gateway):
    """_delete_pin_from_pod calls client.delete on the correct URI."""
    mock_client = _mock_pod_client(gateway)
    await gateway._delete_pin_from_pod("room-1", "msg-1")
    assert mock_client.delete.called
    uri = mock_client.delete.call_args[0][0]
    assert "stash://pod/rooms/room-1/pins/msg-1.json" == uri


@pytest.mark.asyncio
async def test_restore_room_pins_from_pod_saves_to_sqlite(gateway):
    """_restore_room_pins_from_pod populates SQLite with pin records."""
    pin_record = {
        "room_id": "room-restore",
        "message_id": "pinned-msg-1",
        "pinned_by": "alice",
        "content": "Important message",
        "pinned_at": 1700000000.0,
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=["stash://pod/rooms/room-restore/pins/pinned-msg-1.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(pin_record).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_room_pins_from_pod("room-restore")
    pins = gateway._store.get_pins("room-restore")
    assert any(p.get("message_id") == "pinned-msg-1" for p in pins)


@pytest.mark.asyncio
async def test_sync_pin_skips_without_pod(gateway):
    """_sync_pin_to_pod is no-op when no pod."""
    await gateway._sync_pin_to_pod("room-1", "msg-1", "alice", "content")


@pytest.mark.asyncio
async def test_delete_pin_tolerates_404(gateway):
    """_delete_pin_from_pod swallows 404 errors."""
    from proxion_messenger_core.solid_client import SolidError
    mock_client = MagicMock()
    err = SolidError("not found", status_code=404)
    mock_client.delete = MagicMock(side_effect=err)
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    # Should not raise
    await gateway._delete_pin_from_pod("room-1", "msg-1")
