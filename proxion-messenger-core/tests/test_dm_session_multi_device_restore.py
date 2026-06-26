"""Tests: two sessions for same peer; pod restore picks higher-count session."""
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


def _make_pod_session(session_id, send_count, recv_count):
    return {
        "session_id": session_id,
        "owner_webid": "https://alice.pod/profile/card#me",
        "peer_webid": "https://bob.pod/profile/card#me",
        "root_key_b64": "YQ==",
        "send_chain_key_b64": "Yg==",
        "recv_chain_key_b64": "Yw==",
        "send_count": send_count,
        "recv_count": recv_count,
    }


@pytest.mark.asyncio
async def test_restore_picks_pod_session_when_ahead(gateway):
    """When pod has higher step count, pod session is loaded."""
    gateway._store.save_dm_session({
        "session_id": "sess-multi-1",
        "peer_webid": "https://bob.pod/profile/card#me",
        "owner_webid": "https://alice.pod/profile/card#me",
        "root_key": "YQ==",
        "send_chain_key": "Yg==",
        "recv_chain_key": "Yw==",
        "send_count": 2,
        "recv_count": 1,
    })
    pod_session = _make_pod_session("sess-multi-1", send_count=10, recv_count=5)

    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=["stash://pod/e2e_sessions/sess-multi-1.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(pod_session).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_e2e_sessions_from_pod()
    sess = gateway._store.get_dm_session_by_id("sess-multi-1")
    assert sess["send_count"] == 10


@pytest.mark.asyncio
async def test_restore_keeps_local_when_ahead_of_pod(gateway):
    """When local has higher step count, local session is kept."""
    gateway._store.save_dm_session({
        "session_id": "sess-multi-2",
        "peer_webid": "https://bob.pod/profile/card#me",
        "owner_webid": "https://alice.pod/profile/card#me",
        "root_key": "YQ==",
        "send_chain_key": "Yg==",
        "recv_chain_key": "Yw==",
        "send_count": 20,
        "recv_count": 15,
    })
    pod_session = _make_pod_session("sess-multi-2", send_count=5, recv_count=3)

    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=["stash://pod/e2e_sessions/sess-multi-2.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(pod_session).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_e2e_sessions_from_pod()
    sess = gateway._store.get_dm_session_by_id("sess-multi-2")
    assert sess["send_count"] == 20


@pytest.mark.asyncio
async def test_restore_multiple_sessions_independently(gateway):
    """Restore handles multiple sessions with different device scopes."""
    sessions = [
        _make_pod_session("sess-a", 10, 5),
        _make_pod_session("sess-b", 3, 2),
    ]
    gateway._store.save_dm_session({
        "session_id": "sess-a",
        "peer_webid": "https://bob.pod/profile/card#me",
        "owner_webid": "https://alice.pod/profile/card#me",
        "root_key": "YQ==",
        "send_chain_key": "Yg==",
        "recv_chain_key": "Yw==",
        "send_count": 2,
        "recv_count": 1,
    })

    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=[
        "stash://pod/e2e_sessions/sess-a.json",
        "stash://pod/e2e_sessions/sess-b.json",
    ])
    def get_side(uri):
        for s in sessions:
            if s["session_id"] in uri:
                return json.dumps(s).encode()
        return b"{}"
    mock_client.get = MagicMock(side_effect=get_side)
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_e2e_sessions_from_pod()
    sess_a = gateway._store.get_dm_session_by_id("sess-a")
    sess_b = gateway._store.get_dm_session_by_id("sess-b")
    assert sess_a["send_count"] == 10  # Updated from pod
    assert sess_b is not None  # Imported from pod
