"""R9: Peer trust dispute lifecycle tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _open_dispute(store, peer_did="did:key:peer1", observed="new_url", expected="old_url"):
    import uuid
    dispute_id = str(uuid.uuid4())
    store.open_peer_trust_dispute(
        id=dispute_id,
        peer_did=peer_did,
        dispute_type="gateway_url_change",
        observed_value=observed,
        expected_value=expected,
        created_at=time.time(),
    )
    return dispute_id


def test_open_dispute_creates_record(store):
    dispute_id = _open_dispute(store)
    dispute = store.get_peer_trust_dispute(dispute_id)
    assert dispute is not None
    assert dispute["status"] == "open"
    assert dispute["peer_did"] == "did:key:peer1"


def test_resolve_dispute_marks_resolved(store):
    dispute_id = _open_dispute(store)
    resolved = store.resolve_peer_trust_dispute(dispute_id, time.time())
    assert resolved is True
    dispute = store.get_peer_trust_dispute(dispute_id)
    assert dispute["status"] == "resolved"


def test_resolve_dispute_returns_false_if_already_resolved(store):
    dispute_id = _open_dispute(store)
    store.resolve_peer_trust_dispute(dispute_id, time.time())
    resolved_again = store.resolve_peer_trust_dispute(dispute_id, time.time())
    assert resolved_again is False


def test_list_open_disputes_returns_only_open(store):
    id1 = _open_dispute(store, "did:key:peer1")
    id2 = _open_dispute(store, "did:key:peer2")
    store.resolve_peer_trust_dispute(id1, time.time())
    open_disputes = store.list_peer_trust_disputes(status="open")
    assert len(open_disputes) == 1
    assert open_disputes[0]["id"] == id2


def test_list_resolved_disputes(store):
    id1 = _open_dispute(store)
    store.resolve_peer_trust_dispute(id1, time.time())
    resolved = store.list_peer_trust_disputes(status="resolved")
    assert len(resolved) == 1


def test_resolve_disputes_for_did(store):
    peer_did = "did:key:batch_peer"
    id1 = _open_dispute(store, peer_did)
    id2 = _open_dispute(store, peer_did)
    count = store.resolve_peer_trust_disputes_for_did(peer_did, time.time())
    assert count == 2
    open_after = store.list_peer_trust_disputes(status="open")
    assert not any(d["peer_did"] == peer_did for d in open_after)


def test_dispute_not_found_returns_none(store):
    result = store.get_peer_trust_dispute("nonexistent-id")
    assert result is None


def test_open_dispute_idempotent(store):
    dispute_id = _open_dispute(store)
    # Opening same id again is ignored (INSERT OR IGNORE)
    store.open_peer_trust_dispute(
        id=dispute_id,
        peer_did="did:key:peer1",
        dispute_type="gateway_url_change",
        observed_value="different",
        expected_value="old_url",
        created_at=time.time(),
    )
    dispute = store.get_peer_trust_dispute(dispute_id)
    assert dispute["observed_value"] == "new_url"  # original value preserved
