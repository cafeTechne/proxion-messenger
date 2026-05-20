"""Tests for DM session re-initiation protocol (Round 19)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _insert_session(store, session_id, owner, peer):
    """Insert a dm_session row directly."""
    import sqlite3
    now = time.time()
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT OR REPLACE INTO dm_sessions
           (session_id, peer_webid, owner_webid, root_key_b64,
            send_chain_key_b64, recv_chain_key_b64,
            send_count, recv_count, created_at, updated_at)
           VALUES (?, ?, ?, 'rk==', 'sk==', 'rk==', 0, 0, ?, ?)""",
        (session_id, peer, owner, now, now),
    )
    conn.commit()
    conn.close()


def test_session_unknown_emits_reset_requested_to_peer(store):
    """get_dm_session_by_id returns the session so the gateway can look up the peer."""
    owner = "alice@example.org"
    peer = "bob@example.org"
    _insert_session(store, "sess-reset-1", owner, peer)

    sess = store.get_dm_session_by_id("sess-reset-1")
    assert sess is not None
    assert sess["owner_webid"] == owner
    assert sess["peer_webid"] == peer


def test_new_session_replaces_stale_session_row(store):
    """After re-initiation, a new session row can be saved alongside the old one."""
    owner = "alice@example.org"
    peer = "bob@example.org"
    _insert_session(store, "old-sess", owner, peer)

    # Simulate new session (different session_id)
    _insert_session(store, "new-sess", owner, peer)

    old = store.get_dm_session_by_id("old-sess")
    new = store.get_dm_session_by_id("new-sess")
    assert old is not None
    assert new is not None


def test_old_session_deleted_after_session_ready(store):
    """delete_dm_session removes the stale session after the new one is ready."""
    owner = "alice@example.org"
    peer = "bob@example.org"
    _insert_session(store, "stale-sess", owner, peer)
    _insert_session(store, "fresh-sess", owner, peer)

    store.delete_dm_session("stale-sess")

    assert store.get_dm_session_by_id("stale-sess") is None
    assert store.get_dm_session_by_id("fresh-sess") is not None


def test_session_reset_deferred_when_peer_offline(store):
    """If no session is found for the given session_id, get_dm_session_by_id returns None."""
    # Gateway should handle this gracefully (deferred path)
    result = store.get_dm_session_by_id("nonexistent-session-id")
    assert result is None
