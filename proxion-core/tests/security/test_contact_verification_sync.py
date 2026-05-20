"""Tests for cross-device contact verification sync (Round 20)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_list_contact_verifications_returns_all_for_owner(store):
    """list_contact_verifications returns all records where verified_by matches."""
    store.save_contact_verification("bob@example.org", "SN-BOB-123", "alice@example.org")
    store.save_contact_verification("carol@example.org", "SN-CAROL-456", "alice@example.org")
    store.save_contact_verification("dave@example.org", "SN-DAVE-789", "bob@example.org")

    alice_records = store.list_contact_verifications("alice@example.org")
    bob_records = store.list_contact_verifications("bob@example.org")

    assert len(alice_records) == 2
    peers = {r["peer_webid"] for r in alice_records}
    assert peers == {"bob@example.org", "carol@example.org"}
    assert len(bob_records) == 1
    assert bob_records[0]["peer_webid"] == "dave@example.org"


def test_apply_sync_higher_version_wins(store):
    """apply_contact_verification_sync replaces when incoming version is higher."""
    store.apply_contact_verification_sync({
        "peer_webid": "eve@example.org",
        "safety_numbers": "SN-OLD",
        "verified_at": time.time() - 100,
        "verified_by": "alice@example.org",
        "verified_on_device_id": "dev-1",
        "verification_version": 1,
    })
    store.apply_contact_verification_sync({
        "peer_webid": "eve@example.org",
        "safety_numbers": "SN-NEW",
        "verified_at": time.time(),
        "verified_by": "alice@example.org",
        "verified_on_device_id": "dev-2",
        "verification_version": 2,
    })

    record = store.get_contact_verification("eve@example.org")
    assert record["safety_numbers"] == "SN-NEW"
    assert record["verification_version"] == 2
    assert record["verified_on_device_id"] == "dev-2"


def test_apply_sync_lower_version_ignored(store):
    """apply_contact_verification_sync keeps existing record when incoming version is lower."""
    store.apply_contact_verification_sync({
        "peer_webid": "frank@example.org",
        "safety_numbers": "SN-CURRENT",
        "verified_at": time.time(),
        "verified_by": "alice@example.org",
        "verified_on_device_id": "dev-2",
        "verification_version": 3,
    })
    store.apply_contact_verification_sync({
        "peer_webid": "frank@example.org",
        "safety_numbers": "SN-STALE",
        "verified_at": time.time() - 200,
        "verified_by": "alice@example.org",
        "verified_on_device_id": "dev-1",
        "verification_version": 2,
    })

    record = store.get_contact_verification("frank@example.org")
    assert record["safety_numbers"] == "SN-CURRENT"
    assert record["verification_version"] == 3
