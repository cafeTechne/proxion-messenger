"""Tests: contact verification written to pod and restored on cold start."""
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
    mock_client.list = MagicMock(return_value=[])
    mock_client.get = MagicMock(return_value=b'{}')
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_verification_to_pod_puts_correct_uri(gateway):
    """_sync_verification_to_pod writes to the correct path."""
    import hashlib
    mock_client = _mock_pod_client(gateway)
    peer_webid = "https://bob.pod/profile/card#me"
    await gateway._sync_verification_to_pod(peer_webid, "1234 5678", "alice")
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    expected_hash = hashlib.sha256(peer_webid.encode()).hexdigest()[:24]
    assert f"stash://pod/verifications/{expected_hash}.json" == uri


@pytest.mark.asyncio
async def test_restore_verifications_from_pod_saves_to_sqlite(gateway):
    """_restore_verifications_from_pod imports verifications into SQLite."""
    rec = {
        "peer_webid": "https://charlie.pod/profile/card#me",
        "safety_numbers": "111 222 333",
        "verified_by": "alice",
        "verified_at": 1700000000.0,
    }
    import hashlib
    h = hashlib.sha256(rec["peer_webid"].encode()).hexdigest()[:24]
    uri = f"stash://pod/verifications/{h}.json"
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=[uri])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_verifications_from_pod()
    result = gateway._store.get_contact_verification(rec["peer_webid"])
    assert result is not None


@pytest.mark.asyncio
async def test_restore_verifications_skips_existing(gateway):
    """_restore_verifications_from_pod does not duplicate existing records."""
    peer_webid = "https://dave.pod/profile/card#me"
    gateway._store.save_contact_verification(peer_webid, "000 111", "alice")
    rec = {
        "peer_webid": peer_webid,
        "safety_numbers": "000 111",
        "verified_by": "alice",
    }
    import hashlib
    h = hashlib.sha256(peer_webid.encode()).hexdigest()[:24]
    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=[f"stash://pod/verifications/{h}.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_verifications_from_pod()
    all_v = gateway._store.list_contact_verifications("alice")
    assert len([v for v in all_v if v["peer_webid"] == peer_webid]) == 1
