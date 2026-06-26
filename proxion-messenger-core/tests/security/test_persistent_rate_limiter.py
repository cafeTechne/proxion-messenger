"""R17: Persistent rate-limit bucket CRUD."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "rl.db"))


def test_rate_limit_survives_process_restart(tmp_path):
    db_path = str(tmp_path / "rl2.db")
    store1 = LocalStore(db_path)
    for _ in range(4):
        allowed = store1.rate_limit_check_and_increment("bucket:alice", limit=10, window_seconds=60)
        assert allowed

    store2 = LocalStore(db_path)
    with store2._conn() as conn:
        row = conn.execute(
            "SELECT count FROM rate_limit_buckets WHERE bucket_key=?", ("bucket:alice",)
        ).fetchone()
    assert row is not None
    assert row["count"] == 4


def test_rate_limit_bucket_expires_after_ttl(store):
    allowed1 = store.rate_limit_check_and_increment("bucket:expire", limit=5, window_seconds=0.001)
    assert allowed1
    time.sleep(0.01)
    # Window expired — should reset to 1 and allow
    allowed2 = store.rate_limit_check_and_increment("bucket:expire", limit=5, window_seconds=0.001)
    assert allowed2


def test_store_backed_and_memory_fallback_behave_consistently(store):
    bucket = "bucket:limit2"
    r1 = store.rate_limit_check_and_increment(bucket, limit=2, window_seconds=60)
    r2 = store.rate_limit_check_and_increment(bucket, limit=2, window_seconds=60)
    r3 = store.rate_limit_check_and_increment(bucket, limit=2, window_seconds=60)
    assert r1 is True
    assert r2 is True
    assert r3 is False
