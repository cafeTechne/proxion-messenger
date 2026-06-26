"""Tests for LocalStore pending invites and relationships (handshake persistence)."""
import json
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


# ------------------------------------------------------------------
# Pending Invites Tests
# ------------------------------------------------------------------


def test_save_and_retrieve_pending_invite(store):
    invite = {"id": "inv-001", "code": "abc123", "issuer": "did:key:alice"}
    target_did = "did:key:bob"

    store.save_pending_invite(invite, target_did)
    retrieved = store.get_pending_invite("inv-001")

    assert retrieved is not None
    assert retrieved["id"] == "inv-001"
    assert retrieved["code"] == "abc123"
    assert retrieved["issuer"] == "did:key:alice"


def test_get_pending_invite_returns_none_when_missing(store):
    result = store.get_pending_invite("nonexistent-id")
    assert result is None


def test_mark_invite_status_updates_record(store):
    invite = {"id": "inv-002", "code": "def456"}
    store.save_pending_invite(invite, "did:key:target")

    store.mark_invite_status("inv-002", "accepted")
    # Verify by listing invites with 'accepted' status
    accepted_invites = store.list_pending_invites(status="accepted")
    assert len(accepted_invites) == 1
    assert accepted_invites[0]["id"] == "inv-002"


def test_list_pending_invites_filters_by_status(store):
    inv1 = {"id": "inv-003", "code": "ghi789"}
    inv2 = {"id": "inv-004", "code": "jkl012"}
    inv3 = {"id": "inv-005", "code": "mno345"}

    store.save_pending_invite(inv1, "did:key:target1")
    store.save_pending_invite(inv2, "did:key:target2")
    store.save_pending_invite(inv3, "did:key:target3")

    # Mark inv2 as accepted
    store.mark_invite_status("inv-004", "accepted")

    # List pending (default)
    pending = store.list_pending_invites()
    assert len(pending) == 2
    pending_ids = [i["id"] for i in pending]
    assert "inv-003" in pending_ids
    assert "inv-005" in pending_ids

    # List accepted
    accepted = store.list_pending_invites(status="accepted")
    assert len(accepted) == 1
    assert accepted[0]["id"] == "inv-004"


# ------------------------------------------------------------------
# Relationships Tests
# ------------------------------------------------------------------


def test_save_and_retrieve_relationship(store):
    cert = {
        "id": "cert-001",
        "subject": "abc123def456",
        "issuer": "did:key:alice",
        "expires_at": int(time.time()) + 86400,
    }

    store.save_relationship(cert, peer_did="did:key:bob")
    retrieved = store.get_relationship_by_peer("abc123def456")

    assert retrieved is not None
    assert retrieved["id"] == "cert-001"
    assert retrieved["subject"] == "abc123def456"
    assert retrieved["issuer"] == "did:key:alice"


def test_get_relationship_by_peer_returns_none_when_missing(store):
    result = store.get_relationship_by_peer("nonexistent-peer-hex")
    assert result is None


def test_list_relationships_returns_all(store):
    cert1 = {
        "id": "cert-002",
        "subject": "peer1hex",
        "issuer": "did:key:alice",
        "expires_at": int(time.time()) + 86400,
    }
    cert2 = {
        "id": "cert-003",
        "subject": "peer2hex",
        "issuer": "did:key:alice",
        "expires_at": int(time.time()) + 86400,
    }

    store.save_relationship(cert1)
    store.save_relationship(cert2)

    all_rels = store.list_relationships()
    assert len(all_rels) == 2
    ids = [r["id"] for r in all_rels]
    assert "cert-002" in ids
    assert "cert-003" in ids


def test_save_relationship_overwrites_on_conflict(store):
    cert_v1 = {
        "id": "cert-004",
        "subject": "peer3hex",
        "version": 1,
        "expires_at": int(time.time()) + 86400,
    }
    cert_v2 = {
        "id": "cert-004",
        "subject": "peer3hex",
        "version": 2,
        "expires_at": int(time.time()) + 86400,
    }

    store.save_relationship(cert_v1)
    store.save_relationship(cert_v2)

    # Should have only one cert with id cert-004
    all_rels = store.list_relationships()
    cert_004_entries = [r for r in all_rels if r["id"] == "cert-004"]
    assert len(cert_004_entries) == 1
    assert cert_004_entries[0]["version"] == 2


def test_get_relationship_by_peer_returns_newest_nonexpired(store):
    """get_relationship_by_peer should return the newest non-expired certificate."""
    now = int(time.time())

    # Older cert (should be ignored because newer one exists)
    old_cert = {
        "id": "cert-old",
        "subject": "peer4hex",
        "created_marker": "old",
        "created_at": now - 100,  # created 100 seconds ago
        "expires_at": now + 86400,
    }

    # Newer cert (should be returned)
    new_cert = {
        "id": "cert-new",
        "subject": "peer4hex",
        "created_marker": "new",
        "created_at": now - 50,  # created 50 seconds ago (newer)
        "expires_at": now + 86400,
    }

    store.save_relationship(old_cert)
    store.save_relationship(new_cert)

    retrieved = store.get_relationship_by_peer("peer4hex")
    assert retrieved is not None
    assert retrieved["id"] == "cert-new"
    assert retrieved["created_marker"] == "new"


def test_get_relationship_by_peer_ignores_expired(store):
    """get_relationship_by_peer should ignore expired certificates."""
    now = int(time.time())

    # Expired cert
    expired_cert = {
        "id": "cert-expired",
        "subject": "peer5hex",
        "expires_at": now - 1000,  # already expired
    }

    store.save_relationship(expired_cert)
    retrieved = store.get_relationship_by_peer("peer5hex")

    assert retrieved is None


def test_get_relationship_by_did_returns_none_when_missing(tmp_path):
    store = LocalStore(str(tmp_path / "test.db"))
    assert store.get_relationship_by_did("did:key:zzz") is None


def test_get_relationship_by_did_returns_cert(tmp_path):
    import time
    store = LocalStore(str(tmp_path / "test.db"))
    cert = {
        "certificate_id": "cert-did-1",
        "issuer": "aaa",
        "subject": "bbb",
        "capabilities": [],
        "expires_at": int(time.time()) + 9999,
    }
    store.save_relationship(cert, peer_did="did:key:bob")
    result = store.get_relationship_by_did("did:key:bob")
    assert result is not None
    assert result["certificate_id"] == "cert-did-1"
