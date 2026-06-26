"""Tests: sealed managed-relay mailbox fallback (R38)."""
from __future__ import annotations
import base64
import json
import os
import time
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.sealed_relay import seal_relay_payload, unseal_relay_payload


@pytest.fixture
def relay_node(tmp_path, monkeypatch):
    """A gateway running in relay-node mode."""
    monkeypatch.setenv("PROXION_RELAY_NODE", "1")
    key = Ed25519PrivateKey.generate()
    agent = AgentState(identity_key=key, store_key=None)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9000, db_path=str(tmp_path / "node.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "node.db"))
    return gw


# ── store-level quota/expiry ──

def test_mailbox_quota_per_did(tmp_path):
    store = LocalStore(str(tmp_path / "q.db"))
    store._MAILBOX_MAX_PER_DID = 3
    future = time.time() + 1000
    assert store.enqueue_mailbox("a", "did:key:zBob", "x", future)
    assert store.enqueue_mailbox("b", "did:key:zBob", "x", future)
    assert store.enqueue_mailbox("c", "did:key:zBob", "x", future)
    assert store.enqueue_mailbox("d", "did:key:zBob", "x", future) is False  # over quota


def test_mailbox_expired_not_drained(tmp_path):
    store = LocalStore(str(tmp_path / "e.db"))
    store.enqueue_mailbox("old", "did:key:zBob", "x", time.time() - 10)
    store.enqueue_mailbox("new", "did:key:zBob", "y", time.time() + 1000)
    drained = store.drain_mailbox("did:key:zBob")
    ids = [d["blob_id"] for d in drained]
    assert "new" in ids and "old" not in ids


def test_purge_expired(tmp_path):
    store = LocalStore(str(tmp_path / "p.db"))
    store.enqueue_mailbox("old", "did:key:zBob", "x", time.time() - 10)
    assert store.purge_expired_mailbox() == 1


# ── relay-node endpoints ──

@pytest.mark.asyncio
async def test_mailbox_store_then_authenticated_drain(relay_node):
    gw = relay_node
    # A recipient with a known Ed25519 key
    recipient_key = Ed25519PrivateKey.generate()
    recipient_did = pub_key_to_did(recipient_key.public_key().public_bytes_raw())

    # Store a sealed blob
    status, resp = await gw._handle_mailbox_store(
        recipient_did, json.dumps({"sealed_blob": "opaqueblob"}).encode())
    assert status.startswith("200")

    # Drain with a valid signature proving control of recipient_did
    ts = datetime.now(timezone.utc).isoformat()
    nonce = "aabbccdd"
    sig = sign_relay_message(recipient_key, recipient_did, recipient_did,
                             "mailbox-drain", "", ts, nonce)
    status, resp = await gw._handle_mailbox_drain(recipient_did, sig, ts, nonce)
    assert status.startswith("200")
    blobs = json.loads(resp)["blobs"]
    assert len(blobs) == 1
    assert blobs[0]["sealed_blob"] == "opaqueblob"


@pytest.mark.asyncio
async def test_mailbox_drain_rejects_bad_signature(relay_node):
    gw = relay_node
    recipient_key = Ed25519PrivateKey.generate()
    recipient_did = pub_key_to_did(recipient_key.public_key().public_bytes_raw())
    other_key = Ed25519PrivateKey.generate()  # attacker

    gw._store.enqueue_mailbox("b1", recipient_did, "secret", time.time() + 1000)

    ts = datetime.now(timezone.utc).isoformat()
    nonce = "11223344"
    # Sign with the WRONG key
    bad_sig = sign_relay_message(other_key, recipient_did, recipient_did,
                                 "mailbox-drain", "", ts, nonce)
    status, _ = await gw._handle_mailbox_drain(recipient_did, bad_sig, ts, nonce)
    assert status.startswith("401")
    # Blob must remain (not drained)
    assert gw._store.mailbox_count(recipient_did) == 1


@pytest.mark.asyncio
async def test_mailbox_store_disabled_when_not_relay_node(tmp_path, monkeypatch):
    monkeypatch.delenv("PROXION_RELAY_NODE", raising=False)
    key = Ed25519PrivateKey.generate()
    agent = AgentState(identity_key=key, store_key=None)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9001, db_path=str(tmp_path / "n.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "n.db"))
    status, _ = await gw._handle_mailbox_store("did:key:zBob", json.dumps({"sealed_blob": "x"}).encode())
    assert status.startswith("404")


# ── round-trip: seal → mailbox → drain → unseal ──

@pytest.mark.asyncio
async def test_sealed_roundtrip_through_mailbox(relay_node):
    """A blob sealed to a recipient gateway key is recoverable only by that key."""
    gw = relay_node
    recipient_key = Ed25519PrivateKey.generate()
    recipient_did = pub_key_to_did(recipient_key.public_key().public_bytes_raw())
    # Recipient gateway X25519 keypair
    x_priv = X25519PrivateKey.generate()
    x_pub_b64 = base64.urlsafe_b64encode(x_priv.public_key().public_bytes_raw()).rstrip(b"=").decode()

    inner = {"from_webid": "did:key:zAlice", "to_webid": recipient_did,
             "content_type": "room_message", "room_id": "r1", "content": "hi"}
    sealed = seal_relay_payload(inner, x_pub_b64)

    await gw._handle_mailbox_store(recipient_did, json.dumps({"sealed_blob": sealed}).encode())

    ts = datetime.now(timezone.utc).isoformat(); nonce = "deadbeef"
    sig = sign_relay_message(recipient_key, recipient_did, recipient_did, "mailbox-drain", "", ts, nonce)
    _, resp = await gw._handle_mailbox_drain(recipient_did, sig, ts, nonce)
    blobs = json.loads(resp)["blobs"]

    # The relay node stored only an opaque blob — recover with recipient's X25519 priv
    recovered = unseal_relay_payload(blobs[0]["sealed_blob"], x_priv.private_bytes_raw())
    assert recovered["content"] == "hi"
    assert recovered["from_webid"] == "did:key:zAlice"
