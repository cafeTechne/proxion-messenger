"""Tests: E2E session checkpoint fires every 5 steps with ETag guard."""
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


def _mock_pod_client(gw):
    mock_client = MagicMock()
    mock_client.put = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    mock_client.get = MagicMock(return_value=b'{}')
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


def _make_session(store, session_id, send_count, recv_count):
    store.save_dm_session({
        "session_id": session_id,
        "peer_webid": "https://bob.pod/profile/card#me",
        "owner_webid": "https://alice.pod/profile/card#me",
        "root_key": "YQ==" * 8,
        "send_chain_key": "Yg==" * 8,
        "recv_chain_key": "Yw==" * 8,
        "send_count": send_count,
        "recv_count": recv_count,
    })


@pytest.mark.asyncio
async def test_checkpoint_fires_at_step_5(gateway):
    """_checkpoint_e2e_session writes to pod when (send+recv) % 5 == 0."""
    mock_client = _mock_pod_client(gateway)
    _make_session(gateway._store, "sess-001", send_count=3, recv_count=2)
    await gateway._checkpoint_e2e_session("sess-001")
    assert mock_client.put.called


@pytest.mark.asyncio
async def test_checkpoint_skips_at_non_multiple_of_5(gateway):
    """_checkpoint_e2e_session skips when step count is not a multiple of 5."""
    mock_client = _mock_pod_client(gateway)
    _make_session(gateway._store, "sess-002", send_count=2, recv_count=2)  # total=4
    await gateway._checkpoint_e2e_session("sess-002")
    assert not mock_client.put.called


@pytest.mark.asyncio
async def test_checkpoint_412_does_not_update_etag(gateway):
    """On 412 Precondition Failed, ETag is not updated."""
    mock_client = _mock_pod_client(gateway)
    mock_client.put = MagicMock(side_effect=Exception("412 Precondition Failed"))
    _make_session(gateway._store, "sess-003", send_count=5, recv_count=0)
    gateway._store.set_dm_session_checkpoint_etag("sess-003", "old-etag")
    await gateway._checkpoint_e2e_session("sess-003")
    etag = gateway._store.get_dm_session_checkpoint_etag("sess-003")
    assert etag == "old-etag"


@pytest.mark.asyncio
async def test_restore_e2e_sessions_loads_newer_from_pod(gateway):
    """_restore_e2e_sessions_from_pod imports sessions with higher step counts."""
    pod_session = {
        "session_id": "sess-restore-1",
        "owner_webid": "https://alice.pod/profile/card#me",
        "peer_webid": "https://bob.pod/profile/card#me",
        "root_key_b64": "YQ==",
        "send_chain_key_b64": "Yg==",
        "recv_chain_key_b64": "Yw==",
        "send_count": 10,
        "recv_count": 5,
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=["stash://pod/e2e_sessions/sess-restore-1.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(pod_session).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    # Local has older state (total=5)
    _make_session(gateway._store, "sess-restore-1", send_count=3, recv_count=2)
    await gateway._restore_e2e_sessions_from_pod()

    sess = gateway._store.get_dm_session_by_id("sess-restore-1")
    assert sess["send_count"] == 10


@pytest.mark.asyncio
async def test_restore_e2e_sessions_keeps_local_if_newer(gateway):
    """_restore_e2e_sessions_from_pod skips when local is ahead of pod."""
    pod_session = {
        "session_id": "sess-restore-2",
        "owner_webid": "https://alice.pod/profile/card#me",
        "peer_webid": "https://bob.pod/profile/card#me",
        "root_key_b64": "YQ==",
        "send_chain_key_b64": "Yg==",
        "recv_chain_key_b64": "Yw==",
        "send_count": 2,
        "recv_count": 1,
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=["stash://pod/e2e_sessions/sess-restore-2.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(pod_session).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    # Local has newer state (total=15 > pod's 3)
    _make_session(gateway._store, "sess-restore-2", send_count=10, recv_count=5)
    await gateway._restore_e2e_sessions_from_pod()

    sess = gateway._store.get_dm_session_by_id("sess-restore-2")
    assert sess["send_count"] == 10  # Local should be kept
