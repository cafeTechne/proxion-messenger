"""Round 8: invite flood control tests."""
import time
import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_invite_pair_limit_enforced(store):
    from_did = "did:key:spammer"
    to_did = "did:key:victim"
    now = time.time()

    counts = []
    for _ in range(12):
        count = store.increment_invite_pair_counter(from_did, to_did, now)
        counts.append(count)

    # First 10 are under the limit; 11th and 12th exceed 10
    assert counts[9] == 10
    assert counts[10] == 11
    assert counts[11] == 12


def test_invite_pair_counter_resets_after_window(store):
    from_did = "did:key:reset-sender"
    to_did = "did:key:reset-target"
    now = time.time()

    # Fill up the window
    for _ in range(10):
        store.increment_invite_pair_counter(from_did, to_did, now)

    # Check count is 10
    assert store.check_invite_pair_counter(from_did, to_did, now) == 10

    # Simulate time passing beyond 24h window
    future = now + 86401
    store.prune_invite_counters(future)
    assert store.check_invite_pair_counter(from_did, to_did, future) == 0


def test_invite_accept_source_ip_limit_enforced(store):
    source_ip = "1.2.3.4"
    now = time.time()

    counts = []
    for _ in range(52):
        count = store.increment_invite_source_counter(source_ip, now)
        counts.append(count)

    assert counts[49] == 50
    assert counts[50] == 51


def test_invite_counters_pruned_after_window(store):
    source_ip = "5.6.7.8"
    now = time.time()

    for _ in range(5):
        store.increment_invite_source_counter(source_ip, now)

    future = now + 3601  # past the 1-hour window
    store.prune_invite_counters(future)

    # After prune, counter should start fresh
    count = store.increment_invite_source_counter(source_ip, future)
    assert count == 1


def test_different_did_pairs_are_independent(store):
    now = time.time()
    for _ in range(5):
        store.increment_invite_pair_counter("did:key:a", "did:key:target", now)

    count_ab = store.check_invite_pair_counter("did:key:a", "did:key:target", now)
    count_cb = store.check_invite_pair_counter("did:key:c", "did:key:target", now)
    assert count_ab == 5
    assert count_cb == 0
