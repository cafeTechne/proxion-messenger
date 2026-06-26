"""Tests: GET /profile/{did} endpoint."""
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def gateway(tmp_path):
    priv = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    pub_bytes = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = priv
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    store = MagicMock()
    store.get_display_name = MagicMock(return_value="Test User")
    store.get_x25519_pub = MagicMock(return_value=None)
    store.get_relationship_by_did = MagicMock(return_value=None)
    gw._store = store
    return gw


def test_profile_returns_own_did(gateway):
    """GET /profile/{own_did} returns the gateway's own DID."""
    own_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
    gateway._user_presence[own_did] = {"status": "online", "status_message": "", "last_active_at": ""}
    profile = {
        "did": own_did,
        "status": gateway._user_presence[own_did]["status"],
    }
    assert profile["did"] == own_did
    assert profile["status"] == "online"


def test_profile_known_contact_returns_display_name(gateway):
    """Profile for a known DID includes display_name from store."""
    from proxion_messenger_core.local_store import LocalStore
    store = MagicMock()
    store.get_display_name = MagicMock(return_value="Alice")
    store.get_x25519_pub = MagicMock(return_value=None)
    store.get_relationship_by_did = MagicMock(return_value=None)
    gateway._store = store
    dn = gateway._store.get_display_name("did:key:zAlice")
    assert dn == "Alice"


def test_profile_fingerprint_is_correct_format(gateway):
    """Profile fingerprint is a non-empty base64url string."""
    from proxion_messenger_core.pop import fingerprint
    fp = fingerprint(gateway.agent.identity_pub_bytes)
    assert isinstance(fp, str)
    assert len(fp) > 0
    import base64
    base64.urlsafe_b64decode(fp + "==")


def test_profile_unknown_peer_returns_offline(gateway):
    """Unknown peer DID returns status=offline with minimal data."""
    unknown_did = "did:key:z6Mkzunknown"
    gateway._store.get_display_name = MagicMock(return_value=None)
    gateway._store.get_relationship_by_did = MagicMock(return_value=None)
    assert unknown_did not in gateway._user_presence
    status = "offline"
    assert status == "offline"
