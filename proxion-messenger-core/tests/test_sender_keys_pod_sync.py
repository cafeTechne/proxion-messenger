"""Tests: group sender keys written to pod on submit and deleted on rekey."""
from __future__ import annotations

import json
import hashlib
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
    gw.dm_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.mark.asyncio
async def test_sync_sender_key_to_pod_correct_path(gateway):
    """_sync_sender_key_to_pod uses hashed room and sender paths."""
    mock_client = _mock_pod_client(gateway)
    room_id = "room-abc"
    sender_webid = "https://alice.pod/profile/card#me"
    await gateway._sync_sender_key_to_pod(room_id, sender_webid, "key_b64", 1)
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    room_key = hashlib.sha256(room_id.encode()).hexdigest()[:16]
    sender_hash = hashlib.sha256(sender_webid.encode()).hexdigest()[:16]
    assert f"stash://pod/sender_keys/{room_key}/{sender_hash}.json" == uri


@pytest.mark.asyncio
async def test_delete_sender_keys_for_room_from_pod(gateway):
    """_delete_sender_keys_for_room_from_pod lists and deletes all sender keys."""
    mock_client = _mock_pod_client(gateway)
    room_id = "room-rekey"
    room_key = hashlib.sha256(room_id.encode()).hexdigest()[:16]
    mock_client.list = MagicMock(
        return_value=[f"stash://pod/sender_keys/{room_key}/sender1.json"]
    )
    await gateway._delete_sender_keys_for_room_from_pod(room_id)
    assert mock_client.delete.called


@pytest.mark.asyncio
async def test_restore_sender_keys_from_pod_populates_sqlite(gateway):
    """_restore_sender_keys_from_pod saves sender keys into SQLite."""
    room_id = "room-restore-sk"
    sender_webid = "https://charlie.pod/profile/card#me"
    rec = {
        "room_id": room_id,
        "sender_webid": sender_webid,
        "chain_key_b64": "chainkey==",
        "iteration": 3,
    }
    room_key = hashlib.sha256(room_id.encode()).hexdigest()[:16]
    sender_hash = hashlib.sha256(sender_webid.encode()).hexdigest()[:16]
    sender_uri = f"stash://pod/sender_keys/{room_key}/{sender_hash}.json"
    room_uri = f"stash://pod/sender_keys/{room_key}/"

    mock_client = MagicMock()

    def list_side(uri):
        if uri == "stash://pod/sender_keys/":
            return [room_uri]
        if uri == room_uri:
            return [sender_uri]
        return []

    mock_client.list = MagicMock(side_effect=list_side)
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_sender_keys_from_pod()
    key = gateway._store.get_sender_key(room_id, sender_webid)
    assert key is not None


@pytest.mark.asyncio
async def test_restore_sender_keys_skips_existing(gateway):
    """_restore_sender_keys_from_pod does not duplicate existing sender keys."""
    room_id = "room-exists-sk"
    sender_webid = "https://dave.pod/profile/card#me"
    gateway._store.save_sender_key(room_id, sender_webid, "existingkey==", 1)
    rec = {
        "room_id": room_id,
        "sender_webid": sender_webid,
        "chain_key_b64": "newkey==",
        "iteration": 5,
    }
    room_key = hashlib.sha256(room_id.encode()).hexdigest()[:16]
    sender_hash = hashlib.sha256(sender_webid.encode()).hexdigest()[:16]
    sender_uri = f"stash://pod/sender_keys/{room_key}/{sender_hash}.json"
    room_uri = f"stash://pod/sender_keys/{room_key}/"

    mock_client = MagicMock()
    mock_client.list = MagicMock(side_effect=lambda u: [room_uri] if u == "stash://pod/sender_keys/" else [sender_uri])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gateway._pod_webid = "https://pod.example/profile/card#me"
    gateway.dm_clients[gateway._pod_webid] = (MagicMock(), mock_client)

    await gateway._restore_sender_keys_from_pod()
    key = gateway._store.get_sender_key(room_id, sender_webid)
    assert key["chain_key_b64"] == "existingkey=="  # Original preserved
