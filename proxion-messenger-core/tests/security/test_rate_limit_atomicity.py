"""C3: atomic rate-limit check-and-increment; fail-closed on error."""
import threading

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "rl.db"))


def test_limit_not_exceeded_under_concurrency(tmp_path):
    db = str(tmp_path / "rlc.db")
    LocalStore(db)  # ensure schema exists before threads race
    bucket = "auth:concurrent"
    limit = 20
    threads_n = 12
    per_thread = 10
    total = threads_n * per_thread

    allowed = []
    lock = threading.Lock()

    def worker():
        # Each thread uses its OWN store instance / connection.
        s = LocalStore(db)
        local_ok = 0
        for _ in range(per_thread):
            if s.rate_limit_check_and_increment(bucket, limit=limit, window_seconds=300):
                local_ok += 1
        with lock:
            allowed.append(local_ok)

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_allowed = sum(allowed)
    # Never allow more than the limit, and never deny within the limit.
    assert total_allowed == min(limit, total)


def test_sequential_semantics_preserved(store):
    bucket = "b:limit2"
    assert store.rate_limit_check_and_increment(bucket, limit=2, window_seconds=60) is True
    assert store.rate_limit_check_and_increment(bucket, limit=2, window_seconds=60) is True
    assert store.rate_limit_check_and_increment(bucket, limit=2, window_seconds=60) is False


def test_error_path_fails_closed(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "_conn", boom)
    # Security default: deny when the store cannot be reached.
    assert store.rate_limit_check_and_increment("b:err", limit=5, window_seconds=60) is False
    # Explicit opt-in fails open for non-security buckets.
    assert store.rate_limit_check_and_increment(
        "b:err", limit=5, window_seconds=60, fail_open=True
    ) is True
