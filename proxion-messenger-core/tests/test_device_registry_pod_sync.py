"""Tests: device registrations written and deleted from pod; restored on cold start."""
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
    mock_client.delete = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_device_to_pod_puts_correct_path(gateway):
    """_sync_device_to_pod writes to stash://pod/devices/{device_id}.json."""
    mock_client = _mock_pod_client(gateway)
    await gateway._sync_device_to_pod(
        "dev-001", "https://alice.pod/profile/card#me", "pubkey==", "attest==", False
    )
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    assert uri == "stash://pod/devices/dev-001.json"


@pytest.mark.asyncio
async def test_delete_device_from_pod_calls_delete(gateway):
    """_delete_device_from_pod calls client.delete."""
    mock_client = _mock_pod_client(gateway)
    await gateway._delete_device_from_pod("dev-002")
    assert mock_client.delete.called
    uri = mock_client.delete.call_args[0][0]
    assert uri == "stash://pod/devices/dev-002.json"


def _attested_device_record(owner_webid: str, device_id: str, *, is_primary=False) -> dict:
    """Build a pod device record whose attestation re-verifies on restore (D1)."""
    from proxion_messenger_core.device_registry import (
        generate_device_key, sign_device_attestation,
    )
    import base64 as _b64, time as _time
    dk = generate_device_key()
    ts = _time.time()
    sig = sign_device_attestation(
        _b64.b64decode(dk["priv_b64"]), owner_webid, device_id, ts
    )
    return {
        "device_id": device_id,
        "owner_webid": owner_webid,
        "device_pub_b64": dk["pub_b64"],
        "attestation_b64": sig,
        "is_primary": is_primary,
        "attest_timestamp": ts,
    }


@pytest.mark.asyncio
async def test_restore_devices_from_pod_populates_sqlite(gateway):
    """_restore_devices_from_pod saves devices with a verifiable attestation."""
    owner = "https://alice.pod/profile/card#me"
    rec = _attested_device_record(owner, "dev-restore-1")
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/devices/dev-restore-1.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.own_pod_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    device = gateway._store.get_device("dev-restore-1")
    assert device is not None
    assert device["owner_webid"] == owner


@pytest.mark.asyncio
async def test_restore_devices_skips_unverifiable_attestation(gateway):
    """D1: a pod device record without a verifiable attestation is not restored."""
    rec = {
        "device_id": "dev-forged",
        "owner_webid": "https://alice.pod/profile/card#me",
        "device_pub_b64": "pubkey==",
        "attestation_b64": "attest==",
        "is_primary": False,
        # no attest_timestamp, and the signature does not verify
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/devices/dev-forged.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.own_pod_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    assert gateway._store.get_device("dev-forged") is None


@pytest.mark.asyncio
async def test_restore_devices_skips_pending_delete(gateway):
    """A3: a device with a queued pod delete is not re-registered on restore."""
    owner = "https://alice.pod/profile/card#me"
    rec = _attested_device_record(owner, "dev-removed")
    uri = "stash://pod/devices/dev-removed.json"
    gateway._store.record_pending_pod_op(uri, "delete")
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=[uri])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.own_pod_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    assert gateway._store.get_device("dev-removed") is None


@pytest.mark.asyncio
async def test_restore_devices_skips_existing(gateway):
    """_restore_devices_from_pod does not duplicate existing device records."""
    gateway._store.register_device(
        "dev-exists", "https://alice.pod/profile/card#me", "pub==", "att=="
    )
    rec = {
        "device_id": "dev-exists",
        "owner_webid": "https://alice.pod/profile/card#me",
        "device_pub_b64": "newpub==",
        "attestation_b64": "newatt==",
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/devices/dev-exists.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.own_pod_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_devices_from_pod()
    device = gateway._store.get_device("dev-exists")
    assert device["device_pub_b64"] == "pub=="  # Original preserved


@pytest.mark.asyncio
async def test_delete_device_tolerates_404(gateway):
    """_delete_device_from_pod swallows 404."""
    from proxion_messenger_core.solid_client import SolidError
    mock_client = MagicMock()
    mock_client.delete = MagicMock(side_effect=SolidError("not found", status_code=404))
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.own_pod_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    await gateway._delete_device_from_pod("dev-404")  # Should not raise
    # A 404 means the record is already gone: nothing left to retry.
    assert gateway._store.list_pending_pod_ops() == []


@pytest.mark.asyncio
async def test_delete_device_failure_is_durable(gateway):
    """A3: a failed (non-404) pod delete is queued for retry, not swallowed."""
    from proxion_messenger_core.solid_client import SolidError
    mock_client = MagicMock()
    mock_client.delete = MagicMock(side_effect=SolidError("boom", status_code=500))
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.own_pod_clients[gateway._pod_webid] = (MagicMock(), mock_client)
    await gateway._delete_device_from_pod("dev-500")  # Should not raise
    pending = gateway._store.list_pending_pod_ops()
    assert any(op["uri"] == "stash://pod/devices/dev-500.json" and op["op"] == "delete"
               for op in pending)


@pytest.mark.asyncio
async def test_flush_pending_pod_ops_retries_delete(gateway):
    """A queued delete is retried and cleared once the pod delete succeeds."""
    uri = "stash://pod/devices/dev-retry.json"
    gateway._store.record_pending_pod_op(uri, "delete")
    mock_client = _mock_pod_client(gateway)
    await gateway._flush_pending_pod_ops()
    assert mock_client.delete.called
    assert gateway._store.list_pending_pod_ops() == []


def test_register_device_caps_per_owner(tmp_path):
    """B3: register_device rejects a new device once the owner is at the cap."""
    from proxion_messenger_core.local_store import LocalStore
    from proxion_messenger_core._store.devices import MAX_DEVICES_PER_OWNER
    store = LocalStore(str(tmp_path / "cap.db"))
    owner = "https://cap.pod/profile/card#me"
    for i in range(MAX_DEVICES_PER_OWNER):
        assert store.register_device(f"dev-{i}", owner, "pub==", "att==") is True
    # Over the cap: a new device_id is rejected without insertion.
    assert store.register_device("dev-over", owner, "pub==", "att==") is False
    assert store.get_device("dev-over") is None
    assert len(store.list_devices(owner)) == MAX_DEVICES_PER_OWNER
    # Refreshing an already-registered device is still allowed.
    assert store.register_device("dev-0", owner, "pub2==", "att2==") is True


@pytest.mark.asyncio
async def test_restore_rooms_merges_into_live_room(gateway, monkeypatch):
    """D3: a room created/joined during the restore await keeps its live members."""
    _mock_pod_client(gateway)
    room_id = "room-live"
    live_member = "did:key:zLiveMember"

    class FakePodRoomStore:
        def __init__(self, client):
            pass

        def list_room_ids(self):
            return [room_id]

        def read_room_meta(self, rid):
            # Simulate a client joining during the await: populate the live map
            # after the top-of-loop membership check has already passed.
            gateway._local_rooms[rid] = {
                "name": "Live", "code": "LIVE", "invite_url": "",
                "creator_webid": "did:key:zCreator",
                "history_mode": "none", "members": {live_member},
            }
            return {"name": "Pod", "code": "POD", "creator_webid": "",
                    "history_mode": "none", "invite_url": ""}

    monkeypatch.setattr(
        "proxion_messenger_core.pod_room_store.PodRoomStore", FakePodRoomStore
    )
    await gateway._restore_rooms_from_pod()
    assert live_member in gateway._local_rooms[room_id]["members"]
    assert gateway._local_rooms[room_id]["creator_webid"] == "did:key:zCreator"
