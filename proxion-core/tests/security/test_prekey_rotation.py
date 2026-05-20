"""Tests for signed prekey rotation (Round 19)."""
import time
import pytest
from proxion_messenger_core.e2e_session import generate_prekey_bundle
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _save_spk(store, prekey_id, owner, created_at_offset=0):
    """Insert a signed prekey with a controlled creation timestamp."""
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    spk_created_at = time.time() + created_at_offset
    conn.execute(
        """INSERT OR REPLACE INTO dm_prekeys
           (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, one_time, used, created_at,
            spk_created_at, expired)
           VALUES (?, ?, 'pub==', 'priv==', 0, 0, ?, ?, 0)""",
        (prekey_id, owner, time.time(), spk_created_at),
    )
    conn.commit()
    conn.close()


def test_spk_created_at_in_bundle():
    """generate_prekey_bundle must include spk_created_at."""
    bundle = generate_prekey_bundle("alice@example.org")
    assert "spk_created_at" in bundle
    assert isinstance(bundle["spk_created_at"], float)
    assert bundle["spk_created_at"] > 0


def test_get_expired_signed_prekeys_returns_stale(store):
    """get_expired_signed_prekeys returns SPKs older than max_age_seconds."""
    owner = "alice@example.org"
    # SPK created 200 seconds ago → older than 100s threshold
    _save_spk(store, 1001, owner, created_at_offset=-200)
    # SPK created just now → not expired
    _save_spk(store, 1002, owner, created_at_offset=0)

    stale = store.get_expired_signed_prekeys(owner, max_age_seconds=100)
    assert len(stale) == 1
    assert stale[0]["prekey_id"] == 1001


def test_mark_prekey_expired_retains_row(store):
    """mark_prekey_expired sets expired=1 but does not delete the row."""
    owner = "bob@example.org"
    _save_spk(store, 2001, owner, created_at_offset=-500)

    store.mark_prekey_expired(2001)

    import sqlite3
    conn = sqlite3.connect(store.db_path)
    row = conn.execute("SELECT expired FROM dm_prekeys WHERE prekey_id=?", (2001,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1  # expired flag set

    # Should no longer appear in get_expired_signed_prekeys (already marked)
    stale = store.get_expired_signed_prekeys(owner, max_age_seconds=100)
    assert not any(r["prekey_id"] == 2001 for r in stale)


def test_expired_spk_hard_deleted_after_48h(store):
    """Rows with expired=1 and spk_created_at older than 48h must be deletable."""
    owner = "carol@example.org"
    _save_spk(store, 3001, owner, created_at_offset=-(48 * 3600 + 1))  # just past 48h
    store.mark_prekey_expired(3001)

    # Simulate the retention purge: delete expired rows older than 48h
    cutoff = time.time() - 48 * 3600
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "DELETE FROM dm_prekeys WHERE expired=1 AND spk_created_at < ?",
        (cutoff,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM dm_prekeys WHERE prekey_id=?", (3001,)).fetchone()
    conn.close()
    assert row is None
