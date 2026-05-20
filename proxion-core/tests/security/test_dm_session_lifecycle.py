"""Tests for DM session lifecycle management (Round 18)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _save_session(store, session_id, owner, peer, offset_seconds=0):
    """Helper: insert a dm_session with updated_at = now + offset_seconds."""
    import sqlite3
    now = time.time() + offset_seconds
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


def test_list_dm_sessions_returns_active_sessions(store):
    """list_dm_sessions returns all sessions for an owner."""
    owner = "alice@example.org"
    _save_session(store, "sess-1", owner, "bob@example.org")
    _save_session(store, "sess-2", owner, "carol@example.org")
    _save_session(store, "sess-3", "other@example.org", "alice@example.org")

    sessions = store.list_dm_sessions(owner)
    assert len(sessions) == 2
    session_ids = {s["session_id"] for s in sessions}
    assert "sess-1" in session_ids
    assert "sess-2" in session_ids
    assert "sess-3" not in session_ids


def test_prune_expired_sessions_removes_stale(store):
    """prune_expired_dm_sessions removes sessions older than max_age_seconds."""
    owner = "alice@example.org"
    # One session updated 200 seconds ago (stale)
    _save_session(store, "old-sess", owner, "bob@example.org", offset_seconds=-200)
    # One session updated now (fresh)
    _save_session(store, "new-sess", owner, "carol@example.org", offset_seconds=0)

    pruned = store.prune_expired_dm_sessions(max_age_seconds=100)
    assert pruned == 1

    remaining = store.list_dm_sessions(owner)
    remaining_ids = {s["session_id"] for s in remaining}
    assert "new-sess" in remaining_ids
    assert "old-sess" not in remaining_ids


def test_get_dm_session_by_id(store):
    """get_dm_session_by_id returns correct session."""
    owner = "alice@example.org"
    _save_session(store, "sess-abc", owner, "bob@example.org")
    session = store.get_dm_session_by_id("sess-abc")
    assert session is not None
    assert session["session_id"] == "sess-abc"
    assert session["owner_webid"] == owner


def test_delete_dm_session(store):
    """delete_dm_session removes the session by ID."""
    owner = "alice@example.org"
    _save_session(store, "sess-del", owner, "bob@example.org")
    assert store.get_dm_session_by_id("sess-del") is not None
    store.delete_dm_session("sess-del")
    assert store.get_dm_session_by_id("sess-del") is None
