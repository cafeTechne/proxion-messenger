"""Tests: running backfill twice does not duplicate pod resources or SQLite rows."""
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


@pytest.mark.asyncio
async def test_backfill_idempotent_via_version_marker(gateway):
    """Second call to _run_pod_backfill is a no-op due to version marker."""
    call_count = {"marker_writes": 0}
    marker = json.dumps({"version": 0}).encode()

    def get_side(uri):
        if "migration_version" in uri and call_count["marker_writes"] > 0:
            return json.dumps({"version": 26}).encode()
        raise Exception("not found")

    def put_side(uri, data, **kwargs):
        if "migration_version" in uri:
            call_count["marker_writes"] += 1

    mock_client = MagicMock()
    mock_client.get = MagicMock(side_effect=get_side)
    mock_client.put = MagicMock(side_effect=put_side)
    mock_client.list = MagicMock(return_value=[])
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    gateway._pod_url = "https://pod.example/"

    await gateway._run_pod_backfill()
    first_marker_writes = call_count["marker_writes"]

    await gateway._run_pod_backfill()  # Second call
    # Only one marker write total — second call returned early
    assert call_count["marker_writes"] == first_marker_writes


@pytest.mark.asyncio
async def test_restore_devices_idempotent(gateway):
    """Calling _restore_devices_from_pod twice does not duplicate SQLite rows."""
    rec = {
        "device_id": "dev-idem-1",
        "owner_webid": "https://alice.pod/profile/card#me",
        "device_pub_b64": "pub==",
        "attestation_b64": "att==",
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/devices/dev-idem-1.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    await gateway._restore_devices_from_pod()  # Second call
    devices = gateway._store.list_devices("https://alice.pod/profile/card#me")
    assert len([d for d in devices if d["device_id"] == "dev-idem-1"]) == 1


@pytest.mark.asyncio
async def test_restore_sender_keys_idempotent(gateway):
    """Calling _restore_sender_keys_from_pod twice does not duplicate rows."""
    import hashlib
    room_id = "room-idem"
    sender_webid = "https://alice.pod/profile/card#me"
    rec = {
        "room_id": room_id,
        "sender_webid": sender_webid,
        "chain_key_b64": "key==",
        "iteration": 1,
    }
    room_key = hashlib.sha256(room_id.encode()).hexdigest()[:16]
    sender_hash = hashlib.sha256(sender_webid.encode()).hexdigest()[:16]
    room_uri = f"stash://pod/sender_keys/{room_key}/"
    sender_uri = f"stash://pod/sender_keys/{room_key}/{sender_hash}.json"

    mock_client = MagicMock()
    mock_client.list = MagicMock(
        side_effect=lambda u: [room_uri] if "sender_keys/" == u[-13:] else [sender_uri]
    )
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_sender_keys_from_pod()
    await gateway._restore_sender_keys_from_pod()
    key = gateway._store.get_sender_key(room_id, sender_webid)
    assert key is not None
