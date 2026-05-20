"""Tests for per-device DM session scoping (Round 20)."""
import time
import pytest
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _insert_session(store, session_id, owner, peer, owner_device="", peer_device=""):
    import sqlite3
    now = time.time()
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """INSERT OR REPLACE INTO dm_sessions
           (session_id, peer_webid, owner_webid, root_key_b64,
            send_chain_key_b64, recv_chain_key_b64,
            send_count, recv_count, created_at, updated_at,
            owner_device_id, peer_device_id)
           VALUES (?, ?, ?, 'rk==', 'sk==', 'rk==', 0, 0, ?, ?, ?, ?)""",
        (session_id, peer, owner, now, now, owner_device, peer_device),
    )
    conn.commit()
    conn.close()


def test_session_state_isolated_per_device_pair(store):
    """Sessions with different device IDs are stored and retrieved independently."""
    _insert_session(store, "s-d1", "alice@example.org", "bob@example.org",
                    "alice-dev-1", "bob-dev-1")
    _insert_session(store, "s-d2", "alice@example.org", "bob@example.org",
                    "alice-dev-2", "bob-dev-1")

    d1_sessions = store.get_dm_sessions_for_device_scope(
        "alice@example.org", "alice-dev-1", "bob@example.org", "bob-dev-1"
    )
    d2_sessions = store.get_dm_sessions_for_device_scope(
        "alice@example.org", "alice-dev-2", "bob@example.org", "bob-dev-1"
    )

    assert len(d1_sessions) == 1
    assert d1_sessions[0]["session_id"] == "s-d1"
    assert len(d2_sessions) == 1
    assert d2_sessions[0]["session_id"] == "s-d2"


def test_cross_device_ratchet_state_does_not_collide(store):
    """Different device-pair scopes do not share session rows."""
    _insert_session(store, "sess-a", "alice@example.org", "bob@example.org",
                    "dev-a", "dev-b")
    _insert_session(store, "sess-c", "carol@example.org", "dave@example.org",
                    "dev-c", "dev-d")

    alice_sessions = store.get_dm_sessions_for_device_scope(
        "alice@example.org", "dev-a", "bob@example.org", "dev-b"
    )
    carol_sessions = store.get_dm_sessions_for_device_scope(
        "carol@example.org", "dev-c", "dave@example.org", "dev-d"
    )

    assert len(alice_sessions) == 1 and alice_sessions[0]["session_id"] == "sess-a"
    assert len(carol_sessions) == 1 and carol_sessions[0]["session_id"] == "sess-c"
    # Alice's scope returns nothing for Carol's scope
    cross = store.get_dm_sessions_for_device_scope(
        "alice@example.org", "dev-a", "dave@example.org", "dev-d"
    )
    assert cross == []


def test_legacy_session_rows_with_blank_device_scope_migrate_safely(store):
    """Rows inserted without device IDs (default '') are retrievable via blank scope."""
    _insert_session(store, "legacy-sess", "alice@example.org", "bob@example.org")

    # Blank scope lookup should return the legacy row
    results = store.get_dm_sessions_for_device_scope(
        "alice@example.org", "", "bob@example.org", ""
    )
    assert len(results) == 1
    assert results[0]["session_id"] == "legacy-sess"

    # Non-blank scope should not see the legacy row
    results_scoped = store.get_dm_sessions_for_device_scope(
        "alice@example.org", "new-device", "bob@example.org", "bob-device"
    )
    assert results_scoped == []
