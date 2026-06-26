"""Tests for safety numbers / contact verification (Round 18)."""
import os
import pytest
from proxion_messenger_core.safety_numbers import (
    compute_safety_numbers,
    format_safety_numbers,
    verify_safety_numbers,
)
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _random_pub():
    return os.urandom(32)


def test_safety_numbers_deterministic():
    """compute_safety_numbers is deterministic and order-independent."""
    pub_a = _random_pub()
    pub_b = _random_pub()
    sn1 = compute_safety_numbers("alice@example.org", pub_a, "bob@example.org", pub_b)
    sn2 = compute_safety_numbers("bob@example.org", pub_b, "alice@example.org", pub_a)
    assert sn1 == sn2
    assert len(sn1) == 60
    assert sn1.isdigit()


def test_safety_numbers_differ_for_different_peer():
    """Different peer → different safety numbers."""
    pub_a = _random_pub()
    pub_b = _random_pub()
    pub_c = _random_pub()
    sn_ab = compute_safety_numbers("alice@example.org", pub_a, "bob@example.org", pub_b)
    sn_ac = compute_safety_numbers("alice@example.org", pub_a, "carol@example.org", pub_c)
    assert sn_ab != sn_ac


def test_contact_verification_persists(store):
    """save_contact_verification / get_contact_verification round-trip."""
    pub_a = _random_pub()
    pub_b = _random_pub()
    sn = compute_safety_numbers("alice@example.org", pub_a, "bob@example.org", pub_b)

    store.save_contact_verification("bob@example.org", sn, verified_by="alice@example.org")
    record = store.get_contact_verification("bob@example.org")
    assert record is not None
    assert record["safety_numbers"] == sn
    assert record["peer_webid"] == "bob@example.org"
    assert record["verified_by"] == "alice@example.org"

    # list_verified_contacts includes the saved record
    all_records = store.list_verified_contacts()
    assert any(r["peer_webid"] == "bob@example.org" for r in all_records)


def test_format_safety_numbers():
    """format_safety_numbers splits into 12 groups of 5 digits."""
    sn = "0" * 60
    groups = format_safety_numbers(sn)
    assert len(groups) == 12
    assert all(len(g) == 5 for g in groups)


def test_verify_safety_numbers_accepts_correct():
    pub_a = _random_pub()
    pub_b = _random_pub()
    sn = compute_safety_numbers("alice@example.org", pub_a, "bob@example.org", pub_b)
    assert verify_safety_numbers(sn, "alice@example.org", pub_a, "bob@example.org", pub_b)


def test_verify_safety_numbers_rejects_wrong():
    pub_a = _random_pub()
    pub_b = _random_pub()
    sn = compute_safety_numbers("alice@example.org", pub_a, "bob@example.org", pub_b)
    tampered = str(int(sn[:5]) ^ 1).zfill(5) + sn[5:]
    assert not verify_safety_numbers(tampered, "alice@example.org", pub_a, "bob@example.org", pub_b)
