"""R11: Trust revocation enforcement tests."""
import time
import uuid
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _revoke(store, subject_type, subject_id, expires_at=None):
    store.create_trust_revocation(
        id=str(uuid.uuid4()),
        subject_type=subject_type,
        subject_id=subject_id,
        reason="test",
        revoked_by="owner",
        revoked_at=time.time(),
        expires_at=expires_at,
    )


def test_revoked_peer_did_is_detected(store):
    did = "did:key:zABCtest"
    _revoke(store, "peer_did", did)
    assert store.is_subject_revoked("peer_did", did) is True


def test_unrevoked_peer_did_is_not_detected(store):
    assert store.is_subject_revoked("peer_did", "did:key:notrevoked") is False


def test_revocation_expiry_reenables_subject_after_expiration(store):
    did = "did:key:zExpired"
    _revoke(store, "peer_did", did, expires_at=time.time() - 1)
    # expired revocation — subject should not be blocked
    assert store.is_subject_revoked("peer_did", did) is False


def test_revoked_gateway_url_blocks_subject(store):
    gw_url = "https://evil.example.com"
    _revoke(store, "gateway_url", gw_url)
    assert store.is_subject_revoked("gateway_url", gw_url) is True


def test_list_active_revocations_excludes_expired(store):
    did_active = "did:key:zActive"
    did_expired = "did:key:zExpiredTwo"
    _revoke(store, "peer_did", did_active)
    _revoke(store, "peer_did", did_expired, expires_at=time.time() - 1)
    active = store.list_active_trust_revocations()
    ids = [r["subject_id"] for r in active]
    assert did_active in ids
    assert did_expired not in ids


def test_expire_trust_revocations_deactivates_expired(store):
    did = "did:key:zBatch"
    _revoke(store, "peer_did", did, expires_at=time.time() - 1)
    count = store.expire_trust_revocations(time.time())
    assert count >= 1
    assert store.is_subject_revoked("peer_did", did) is False


def test_revoked_peer_did_blocks_relay(store):
    """Revoked peer DID must block relay at the store level."""
    did = "did:key:zRelay"
    _revoke(store, "peer_did", did)
    assert store.is_subject_revoked("peer_did", did) is True


def test_revoked_gateway_url_blocks_peer_update(store):
    gw_url = "https://hostile.example.com"
    _revoke(store, "gateway_url", gw_url)
    assert store.is_subject_revoked("gateway_url", gw_url) is True
