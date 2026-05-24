"""Tests: push subscriptions written and deleted from pod; restored on cold start."""
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
    mock_client.delete = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_push_subscription_to_pod_correct_path(gateway):
    """_sync_push_subscription_to_pod writes to stash://pod/push/{id}.json."""
    mock_client = _mock_pod_client(gateway)
    await gateway._sync_push_subscription_to_pod(
        "sub-001", "https://alice.pod/profile/card#me",
        "https://push.example/endpoint", "p256dh==", "auth=="
    )
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    assert uri == "stash://pod/push/sub-001.json"


@pytest.mark.asyncio
async def test_delete_push_subscription_from_pod(gateway):
    """_delete_push_subscription_from_pod calls delete on correct path."""
    mock_client = _mock_pod_client(gateway)
    await gateway._delete_push_subscription_from_pod("sub-002")
    assert mock_client.delete.called
    uri = mock_client.delete.call_args[0][0]
    assert uri == "stash://pod/push/sub-002.json"


@pytest.mark.asyncio
async def test_restore_push_subscriptions_from_pod_populates_sqlite(gateway):
    """_restore_push_subscriptions_from_pod saves subscriptions into SQLite."""
    rec = {
        "subscription_id": "sub-restore-1",
        "owner_webid": "https://alice.pod/profile/card#me",
        "endpoint": "https://push.example/endpoint",
        "p256dh_b64": "p256dh==",
        "auth_b64": "auth==",
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/push/sub-restore-1.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_push_subscriptions_from_pod()
    subs = gateway._store.get_push_subscriptions("https://alice.pod/profile/card#me")
    assert any(s.get("subscription_id") == "sub-restore-1" for s in subs)


@pytest.mark.asyncio
async def test_restore_push_subscriptions_skips_existing(gateway):
    """_restore_push_subscriptions_from_pod does not duplicate existing subscriptions."""
    owner = "https://alice.pod/profile/card#me"
    gateway._store.save_push_subscription(
        "sub-exists", owner, "https://push.example/endpoint", "p256dh==", "auth=="
    )
    rec = {
        "subscription_id": "sub-exists",
        "owner_webid": owner,
        "endpoint": "https://push.example/new",
        "p256dh_b64": "newp256==",
        "auth_b64": "newauth==",
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/push/sub-exists.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_push_subscriptions_from_pod()
    subs = gateway._store.get_push_subscriptions(owner)
    existing = [s for s in subs if s["subscription_id"] == "sub-exists"]
    assert len(existing) == 1
    assert existing[0]["endpoint"] == "https://push.example/endpoint"  # Original
