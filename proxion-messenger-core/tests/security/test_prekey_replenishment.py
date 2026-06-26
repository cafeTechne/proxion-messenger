"""Tests for prekey replenishment protocol (Round 18)."""
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _add_prekeys(store, owner_webid, n_one_time=5):
    """Helper: add a signed prekey + n one-time prekeys for owner_webid."""
    store.save_prekey(9001, owner_webid, "spk_pub_b64==", "spk_priv_b64==", one_time=False)
    for i in range(n_one_time):
        store.save_prekey(i + 1, owner_webid, f"opk_pub_{i}==", f"opk_priv_{i}==", one_time=True)


def test_count_unused_one_time_prekeys(store):
    """count_unused_one_time_prekeys returns correct count before and after claim."""
    owner = "alice@example.org"
    _add_prekeys(store, owner, n_one_time=5)

    assert store.count_unused_one_time_prekeys(owner) == 5

    # Claim one prekey
    claimed = store.claim_one_time_prekey(owner)
    assert claimed is not None
    assert store.count_unused_one_time_prekeys(owner) == 4


def test_count_unused_returns_zero_for_unknown_owner(store):
    """Returns 0 for a webid with no prekeys."""
    assert store.count_unused_one_time_prekeys("nobody@example.org") == 0


def test_count_excludes_signed_prekey(store):
    """Signed prekey (one_time=False) must NOT be counted in one-time pool."""
    owner = "bob@example.org"
    store.save_prekey(9001, owner, "spk_pub==", "spk_priv==", one_time=False)
    assert store.count_unused_one_time_prekeys(owner) == 0
    _add_prekeys(store, owner, n_one_time=3)
    assert store.count_unused_one_time_prekeys(owner) == 3
