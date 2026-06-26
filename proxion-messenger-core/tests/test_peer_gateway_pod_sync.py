"""Tests: peer gateway URLs written to pod and restored without overriding pins."""
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


@pytest.mark.asyncio
async def test_sync_peer_gateway_to_pod_puts_correct_uri(gateway):
    """_sync_peer_gateway_to_pod writes to the correct path."""
    import hashlib
    mock_client = _mock_pod_client(gateway)
    did = "did:key:zAliceDID"
    url = "https://alice-gateway.example/"
    await gateway._sync_peer_gateway_to_pod(did, url)
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    expected_hash = hashlib.sha256(did.encode()).hexdigest()[:24]
    assert f"stash://pod/peer_gateways/{expected_hash}.json" == uri


@pytest.mark.asyncio
async def test_restore_peer_gateways_populates_memory_and_sqlite(gateway):
    """_restore_peer_gateways_from_pod loads into _peer_gateway_urls and SQLite."""
    did = "did:key:zBobDID"
    url = "https://bob-gateway.example/"
    rec = {"did": did, "gateway_url": url, "updated_at": 1700000000.0}
    import hashlib
    h = hashlib.sha256(did.encode()).hexdigest()[:24]
    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=[f"stash://pod/peer_gateways/{h}.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_peer_gateways_from_pod()
    assert gateway._peer_gateway_urls.get(did) == url
    assert gateway._store.get_peer_gateway(did) == url


@pytest.mark.asyncio
async def test_restore_peer_gateways_never_overrides_pin(gateway):
    """_restore_peer_gateways_from_pod skips DIDs that have a pin."""
    import time
    did = "did:key:zPinnedDID"
    pinned_url = "https://pinned-gateway.example/"
    pod_url = "https://evil-gateway.example/"
    gateway._store.save_peer_gateway(did, pinned_url)
    gateway._store.upsert_peer_gateway_pin(
        peer_did=did,
        pinned_gateway_url=pinned_url,
        pinned_at=time.time(),
        last_seen_gateway_url=pinned_url,
        last_seen_at=time.time(),
        pending_change=False,
    )
    rec = {"did": did, "gateway_url": pod_url}
    import hashlib
    h = hashlib.sha256(did.encode()).hexdigest()[:24]
    mock_client = MagicMock()
    mock_client.list = MagicMock(
        return_value=[f"stash://pod/peer_gateways/{h}.json"]
    )
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_peer_gateways_from_pod()
    # Pin should not be overridden
    assert gateway._store.get_peer_gateway(did) == pinned_url
