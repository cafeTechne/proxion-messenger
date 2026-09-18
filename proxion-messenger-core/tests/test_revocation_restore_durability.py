"""Revocation durability across the pod-restore path.

A UI/CLI revoke must set the relationships.revoked column (not only the
revocations table + in-memory set), and a revoked contact must not be
re-imported from the pod on a cold start or watchdog reconnect. A pod
revocation tombstone is the durable authority read first on restore.
"""
from __future__ import annotations

import json
import time

import pytest
from unittest.mock import AsyncMock, MagicMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _mock_pod_client(gw):
    mock_client = MagicMock()
    mock_client.put = MagicMock(return_value=None)
    mock_client.delete = MagicMock(return_value=None)
    mock_client.list = MagicMock(return_value=[])
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (MagicMock(), mock_client)
    return mock_client


@pytest.fixture
def restore_gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x11" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "rev.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "rev.db"))
    return gw


@pytest.fixture
def handler_gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "revh.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_revoke_contact_sets_revoked_column(handler_gateway, monkeypatch):
    """A1: revoke_contact sets relationships.revoked so relationship_is_revoked
    is True and get_relationship_by_did/_by_cert_id return None."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw = handler_gateway
    priv = Ed25519PrivateKey.generate()
    ws = _mock_ws()
    await _register(gw, ws, _did(priv))

    cert_id = "cert-revoke-1"
    peer_did = "did:key:zPeerRevoke"
    now = int(time.time())
    gw._store.save_relationship(
        {"certificate_id": cert_id, "subject": "aa" * 32,
         "created_at": now, "expires_at": now + 86400},
        peer_did=peer_did,
    )
    # Sanity: valid before the revoke.
    assert gw._store.get_relationship_by_cert_id(cert_id) is not None
    assert gw._store.get_relationship_by_did(peer_did) is not None

    await gw.process_command(ws, {"cmd": "revoke_contact", "cert_id": cert_id})

    assert gw._store.relationship_is_revoked(cert_id) is True
    assert gw._store.get_relationship_by_cert_id(cert_id) is None
    assert gw._store.get_relationship_by_did(peer_did) is None
    assert peer_did in gw._revoked_dids


@pytest.mark.asyncio
async def test_revoked_cert_not_reimported_from_pod(restore_gateway):
    """A2: a revoked contact's cert on the pod is not re-imported (harasser out)."""
    gw = restore_gateway
    cert_id = "cert-harasser"
    peer_did = "did:key:zHarasser"
    gw._store.mark_revoked(cert_id, peer_did)

    cert_dict = {
        "certificate_id": cert_id,
        "peer_did": peer_did,
        "subject": "bb" * 32,
        "issuer": "cc" * 32,
    }
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/relationships/cert-harasser.json"])
    mock_client.get = MagicMock(return_value=json.dumps(cert_dict).encode())
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (MagicMock(), mock_client)

    await gw._restore_relationships_from_pod()

    assert gw._store.get_relationship_by_cert_id(cert_id) is None
    assert all(r.get("certificate_id") != cert_id
               for r in gw._store.list_relationships(include_revoked=True))


@pytest.mark.asyncio
async def test_restore_revocations_from_pod_hydrates_local(restore_gateway):
    """The pod tombstone is read into the local revocations table + set."""
    gw = restore_gateway
    rec = {"cert_id": "cert-tomb", "peer_did": "did:key:zTomb", "revoked_at": time.time()}
    mock_client = MagicMock()
    mock_client.list = MagicMock(return_value=["stash://pod/revocations/cert-tomb.json"])
    mock_client.get = MagicMock(return_value=json.dumps(rec).encode())
    gw._pod_webid = "https://pod.example/profile/card#me"
    gw.own_pod_clients[gw._pod_webid] = (MagicMock(), mock_client)

    await gw._restore_revocations_from_pod()

    assert gw._store.is_cert_revoked("cert-tomb") is True
    assert gw._store.is_revoked("did:key:zTomb") is True
    assert "did:key:zTomb" in gw._revoked_dids


@pytest.mark.asyncio
async def test_sync_revocation_queues_when_pod_unavailable(restore_gateway):
    """A lost tombstone write is queued for retry rather than dropped."""
    gw = restore_gateway  # no pod client attached
    await gw._sync_revocation_to_pod("cert-queued", "did:key:zQ")
    pending = gw._store.list_pending_pod_ops()
    assert any(op["uri"] == "stash://pod/revocations/cert-queued.json"
               and op["op"] == "put" for op in pending)


@pytest.mark.asyncio
async def test_sync_revocation_writes_tombstone(restore_gateway):
    """When the pod is reachable the tombstone is written and not left pending."""
    gw = restore_gateway
    mock_client = _mock_pod_client(gw)
    await gw._sync_revocation_to_pod("cert-ok", "did:key:zOk")
    assert mock_client.put.called
    uri = mock_client.put.call_args[0][0]
    assert uri == "stash://pod/revocations/cert-ok.json"
    assert gw._store.list_pending_pod_ops() == []
