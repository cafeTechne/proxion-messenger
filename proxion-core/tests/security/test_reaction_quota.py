"""Tests for per-user-per-room reaction quota enforcement."""
import os
import tempfile
import pytest

from proxion_messenger_core.local_store import LocalStore

ROOM = "room-quota-test"
ALICE = "https://pod.example.com/alice/profile/card#me"
BOB = "https://pod.example.com/bob/profile/card#me"
QUOTA = LocalStore._REACTION_QUOTA


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "test.db")
    return LocalStore(db)


class TestReactionQuota:
    def test_reactions_up_to_quota_accepted(self, store):
        for i in range(QUOTA):
            ok = store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
            assert ok is True

    def test_reaction_beyond_quota_rejected(self, store):
        for i in range(QUOTA):
            store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
        ok = store.save_reaction(ROOM, "msg-extra", "👎", ALICE)
        assert ok is False

    def test_quota_is_per_user(self, store):
        for i in range(QUOTA):
            store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
        # Bob has an independent quota
        ok = store.save_reaction(ROOM, "msg-0", "❤️", BOB)
        assert ok is True

    def test_quota_is_per_room(self, store):
        other_room = "room-other"
        for i in range(QUOTA):
            store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
        # Different room → fresh quota
        ok = store.save_reaction(other_room, "msg-0", "🎉", ALICE)
        assert ok is True

    def test_remove_reaction_frees_slot(self, store):
        for i in range(QUOTA):
            store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
        # Exceeds quota
        assert store.save_reaction(ROOM, "msg-extra", "👎", ALICE) is False
        # Remove one
        store.remove_reaction(ROOM, "msg-0", "👍", ALICE)
        # Now there's room
        ok = store.save_reaction(ROOM, "msg-extra", "👎", ALICE)
        assert ok is True

    def test_duplicate_reaction_counts_once(self, store):
        # UNIQUE constraint: same (room, msg, emoji, sender) is idempotent
        for _ in range(5):
            store.save_reaction(ROOM, "msg-0", "👍", ALICE)
        # Should only count as 1
        row_count = store.count_reactions_by_sender("msg-0", ALICE)
        assert row_count == 1

    def test_zero_quota_means_no_reactions_allowed(self, store):
        original_quota = LocalStore._REACTION_QUOTA
        LocalStore._REACTION_QUOTA = 0
        try:
            ok = store.save_reaction(ROOM, "msg-0", "👍", ALICE)
            assert ok is False
        finally:
            LocalStore._REACTION_QUOTA = original_quota

    def test_exactly_at_quota_is_allowed(self, store):
        for i in range(QUOTA - 1):
            store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
        ok = store.save_reaction(ROOM, f"msg-{QUOTA - 1}", "👍", ALICE)
        assert ok is True

    def test_one_over_quota_is_rejected(self, store):
        for i in range(QUOTA):
            store.save_reaction(ROOM, f"msg-{i}", "👍", ALICE)
        ok = store.save_reaction(ROOM, f"msg-{QUOTA}", "👍", ALICE)
        assert ok is False
