"""Cross-account isolation for contact_verifications and revoked/expired
relationship getters (data-leak fixes A1/A2/A3)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_two_accounts_do_not_clobber_same_peer(store):
    """Two accounts verifying the same peer keep independent rows."""
    store.save_contact_verification("mallory@example.org", "SN-ALICE-VIEW", "alice@example.org")
    store.save_contact_verification("mallory@example.org", "SN-BOB-VIEW", "bob@example.org")

    alice_row = store.get_contact_verification("mallory@example.org", "alice@example.org")
    bob_row = store.get_contact_verification("mallory@example.org", "bob@example.org")

    assert alice_row["safety_numbers"] == "SN-ALICE-VIEW"
    assert bob_row["safety_numbers"] == "SN-BOB-VIEW"


def test_get_scoped_to_caller(store):
    """A caller cannot read another account's verification row."""
    store.save_contact_verification("peer@example.org", "SN-ALICE", "alice@example.org")

    assert store.get_contact_verification("peer@example.org", "alice@example.org") is not None
    assert store.get_contact_verification("peer@example.org", "bob@example.org") is None


def test_list_scoped_to_owner(store):
    """list_contact_verifications only returns the owner's own rows."""
    store.save_contact_verification("p1@example.org", "SN1", "alice@example.org")
    store.save_contact_verification("p2@example.org", "SN2", "alice@example.org")
    store.save_contact_verification("p3@example.org", "SN3", "bob@example.org")

    alice = store.list_contact_verifications("alice@example.org")
    bob = store.list_contact_verifications("bob@example.org")

    assert {r["peer_webid"] for r in alice} == {"p1@example.org", "p2@example.org"}
    assert {r["peer_webid"] for r in bob} == {"p3@example.org"}


def test_sync_scoped_by_owner(store):
    """apply_contact_verification_sync keys on (peer_webid, verified_by)."""
    store.apply_contact_verification_sync({
        "peer_webid": "peer@example.org",
        "safety_numbers": "SN-ALICE",
        "verified_at": time.time(),
        "verified_by": "alice@example.org",
        "verified_on_device_id": "dev-a",
        "verification_version": 5,
    })
    # bob's sync at a lower version must not be ignored: it is a different key.
    store.apply_contact_verification_sync({
        "peer_webid": "peer@example.org",
        "safety_numbers": "SN-BOB",
        "verified_at": time.time(),
        "verified_by": "bob@example.org",
        "verified_on_device_id": "dev-b",
        "verification_version": 1,
    })

    assert store.get_contact_verification("peer@example.org", "alice@example.org")["safety_numbers"] == "SN-ALICE"
    assert store.get_contact_verification("peer@example.org", "bob@example.org")["safety_numbers"] == "SN-BOB"


def _cert(cert_id, peer_did, expires_at):
    return {
        "certificate_id": cert_id,
        "subject": "aa" * 32,
        "peer_did": peer_did,
        "created_at": int(time.time()) - 10,
        "expires_at": expires_at,
    }


def test_getters_exclude_revoked_relationship(store):
    now = int(time.time())
    store.save_relationship(_cert("cert-1", "did:key:zPeer", now + 86400), peer_did="did:key:zPeer")

    assert store.get_relationship_by_cert_id("cert-1") is not None
    assert store.get_relationship_by_did("did:key:zPeer") is not None

    store.revoke_relationship("cert-1")

    assert store.get_relationship_by_cert_id("cert-1") is None
    assert store.get_relationship_by_did("did:key:zPeer") is None


def test_get_by_cert_id_excludes_expired(store):
    now = int(time.time())
    store.save_relationship(_cert("cert-exp", "did:key:zExp", now - 5), peer_did="did:key:zExp")

    assert store.get_relationship_by_cert_id("cert-exp") is None
    assert store.get_relationship_by_did("did:key:zExp") is None
