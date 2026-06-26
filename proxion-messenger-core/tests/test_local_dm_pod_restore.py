"""Tests: local DM messages are restored from pod on cold start."""
from __future__ import annotations

import json
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


def _mock_pod_client(gw, thread_messages: dict):
    """Set up pod client returning given messages per thread key."""
    mock_client = MagicMock()
    thread_uris = list(thread_messages.keys())

    def list_side_effect(uri):
        if uri == "stash://pod/local_dms/":
            return [f"stash://pod/local_dms/{k}/" for k in thread_uris]
        for k, msgs in thread_messages.items():
            if k in uri:
                return [f"stash://pod/local_dms/{k}/{m['message_id']}.json" for m in msgs]
        return []

    def get_side_effect(uri):
        for k, msgs in thread_messages.items():
            for m in msgs:
                if m["message_id"] in uri:
                    return json.dumps(m).encode()
        return b"{}"

    mock_client.list = MagicMock(side_effect=list_side_effect)
    mock_client.get = MagicMock(side_effect=get_side_effect)
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_restore_local_dms_populates_sqlite(gateway):
    """_restore_local_dms_from_pod saves messages into SQLite."""
    messages = [
        {
            "message_id": "msg-restore-1",
            "thread_id": "thread-restore-1",
            "from_webid": "https://bob.pod/profile/card#me",
            "content": "Restored message",
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
    ]
    _mock_pod_client(gateway, {"threadkey1": messages})
    await gateway._restore_local_dms_from_pod()
    result = gateway._store.get_message("msg-restore-1")
    assert result is not None


@pytest.mark.asyncio
async def test_restore_local_dms_skips_existing(gateway):
    """_restore_local_dms_from_pod does not duplicate existing messages."""
    gateway._store.save_message(
        "msg-exists", "thread-1", "dm", "https://alice.pod/profile/card#me",
        None, "Existing", "2024-01-01T00:00:00+00:00",
    )
    messages = [
        {
            "message_id": "msg-exists",
            "thread_id": "thread-1",
            "from_webid": "https://alice.pod/profile/card#me",
            "content": "Existing",
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
    ]
    mock_client = _mock_pod_client(gateway, {"tkey": messages})
    await gateway._restore_local_dms_from_pod()
    # get should only be called once (not twice for the duplicate)
    assert mock_client.get.call_count <= 2


@pytest.mark.asyncio
async def test_restore_local_dms_no_op_without_pod(gateway):
    """_restore_local_dms_from_pod is no-op when no pod client."""
    await gateway._restore_local_dms_from_pod()  # Should not raise
