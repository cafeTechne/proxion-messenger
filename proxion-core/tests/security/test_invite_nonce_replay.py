"""Round 4: Invite nonce deduplication."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "inv.db"))


def test_invite_nonce_replay_rejected(store):
    """Recording a nonce and checking again returns True (replay detected)."""
    store.record_invite_nonce("inv-nonce-001")
    assert store.has_seen_invite_nonce("inv-nonce-001", ttl_seconds=86400)


def test_invite_nonce_ttl_expiry_allows_new_invite(store):
    """Nonce with ttl_seconds=0 is treated as expired."""
    store.record_invite_nonce("inv-nonce-002")
    assert not store.has_seen_invite_nonce("inv-nonce-002", ttl_seconds=0)


def test_invite_nonce_persistence_survives_restart(tmp_path):
    """Nonce recorded in one store instance is seen after re-opening."""
    db = str(tmp_path / "inv2.db")
    s1 = LocalStore(db)
    s1.record_invite_nonce("inv-nonce-003")
    s2 = LocalStore(db)
    assert s2.has_seen_invite_nonce("inv-nonce-003", ttl_seconds=86400)
