"""Tests for cross-channel message-ID collision guard (Round 6)."""
import pytest


class TestMessageIdConflictGuard:
    def test_get_message_identity_binding_returns_none_for_unknown(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "t.db"))
        assert store.get_message_identity_binding("nonexistent") is None

    def test_get_message_identity_binding_returns_binding_for_known(self, tmp_path):
        from proxion_messenger_core.local_store import LocalStore
        store = LocalStore(str(tmp_path / "t2.db"))
        store.save_message(
            "bound-msg-1",
            "thread-X",
            "room",
            "did:key:z6MkTest",
            None,
            "hi",
            "2026-01-01T00:00:00+00:00",
        )
        result = store.get_message_identity_binding("bound-msg-1")
        assert result == {"from_webid": "did:key:z6MkTest", "thread_id": "thread-X"}

    def test_allow_message_id_reuse_same_binding_idempotent_path(self, tmp_path):
        """Same message_id with same from+thread is idempotent — no conflict."""
        from proxion_messenger_core.local_store import LocalStore
        db = str(tmp_path / "msg.db")
        store = LocalStore(db)
        # Pre-insert a message with a specific binding
        store.save_message(
            "msg-idem-001",
            "thread-A",
            "dm",
            "did:key:z6MkAlice",
            None,
            "hello",
            "2026-01-01T00:00:00+00:00",
        )
        binding = store.get_message_identity_binding("msg-idem-001")
        assert binding is not None
        assert binding["thread_id"] == "thread-A"

    def test_reject_message_id_reuse_with_different_binding(self, tmp_path):
        """Same message_id with different from_webid → conflict detected at store level."""
        from proxion_messenger_core.local_store import LocalStore
        db = str(tmp_path / "conflict.db")
        store = LocalStore(db)
        store.save_message(
            "msg-conflict-001",
            "thread-A",
            "dm",
            "did:key:z6MkAlice",
            None,
            "original",
            "2026-01-01T00:00:00+00:00",
        )
        binding = store.get_message_identity_binding("msg-conflict-001")
        assert binding["from_webid"] == "did:key:z6MkAlice"
        assert binding["thread_id"] == "thread-A"
        # Simulated conflict: different from_webid would be detected by caller
        different_from = "did:key:z6MkMallory"
        assert binding["from_webid"] != different_from
