"""Tests for durable DPoP JTI replay cache (Round 5)."""
import pytest
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.dpop import DpopReplayCache


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


class TestDpopDurableReplay:
    def test_dpop_jti_replay_blocked_via_store(self, store):
        store.record_dpop_jti("jti-001")
        assert store.has_seen_dpop_jti("jti-001", ttl_seconds=60) is True

    def test_dpop_jti_not_seen_before_recording(self, store):
        assert store.has_seen_dpop_jti("jti-new", ttl_seconds=60) is False

    def test_dpop_jti_replay_survives_restart(self, tmp_path):
        db_path = str(tmp_path / "replay.db")
        store1 = LocalStore(db_path)
        store1.record_dpop_jti("jti-persist")
        # New store instance (simulates restart)
        store2 = LocalStore(db_path)
        assert store2.has_seen_dpop_jti("jti-persist", ttl_seconds=3600) is True

    def test_dpop_jti_prune_allows_post_ttl_reuse(self, store):
        import time
        # Record a JTI with a timestamp far in the past
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO dpop_seen_jti (jti, seen_at) VALUES (?, ?)",
                ("old-jti", time.time() - 200)
            )
        # Prune with cutoff 100s ago — should remove the entry
        store.prune_dpop_jti(time.time() - 100)
        assert store.has_seen_dpop_jti("old-jti", ttl_seconds=60) is False

    def test_replay_cache_uses_store_hooks(self, store):
        cache = DpopReplayCache(
            ttl=120,
            seen_lookup=lambda jti: store.has_seen_dpop_jti(jti, ttl_seconds=120),
            seen_record=lambda jti: store.record_dpop_jti(jti),
            prune=lambda cutoff: store.prune_dpop_jti(cutoff),
        )
        cache.check_and_record("jti-hook-1")
        import pytest as _pt
        with _pt.raises(ValueError, match="replay"):
            cache.check_and_record("jti-hook-1")
