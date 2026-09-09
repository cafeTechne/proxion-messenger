"""Owner-isolation and migration-safety tests for the DM prekey store.

Covers the prekey-store findings:
  FIND-1  cross-account prekey destruction via a colliding, client-minted id
  FIND-5  cross-account prekey expiry via a colliding id
  FIND-11 non-atomic one-time-prekey claim (double-hand-out)
  FIND-6  destructive migration that swallowed a failed copy
"""
import sqlite3
import threading
import time

import pytest

from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


ALICE = "https://alice.example/profile#me"
MALLORY = "https://mallory.example/profile#me"


# ── FIND-1: a colliding prekey_id cannot evict another owner's row ──
def test_colliding_id_upload_cannot_evict_signed_prekey(store):
    store.save_prekey(42, ALICE, "alice_spk_pub==", "alice_spk_priv==", one_time=False)
    # Attacker uploads a prekey with the SAME client-minted id under their own webid.
    store.save_prekey(42, MALLORY, "mallory_pub==", "mallory_priv==", one_time=False)

    victim = store.get_signed_prekey(ALICE)
    assert victim is not None
    assert victim["prekey_id"] == 42
    assert victim["pub_b64"] == "alice_spk_pub=="
    # Victim's bundle still resolves — no DoS of session establishment.
    assert store.get_prekey_bundle(ALICE) is not None


def test_colliding_id_upload_cannot_evict_one_time_prekey(store):
    store.save_prekey(7, ALICE, "alice_opk_pub==", "alice_opk_priv==", one_time=True)
    store.save_prekey(7, MALLORY, "mallory_opk_pub==", "mallory_opk_priv==", one_time=True)

    assert store.count_unused_one_time_prekeys(ALICE) == 1
    assert store.count_unused_one_time_prekeys(MALLORY) == 1
    claimed = store.claim_one_time_prekey(ALICE)
    assert claimed is not None
    assert claimed["pub_b64"] == "alice_opk_pub=="


# ── FIND-5: mark_prekey_expired cannot cross owners ──
def test_mark_prekey_expired_is_owner_scoped(store):
    store.save_prekey(100, ALICE, "alice_spk_pub==", "alice_spk_priv==", one_time=False)

    # Attacker submits the victim's id under their own session.
    store.mark_prekey_expired(100, MALLORY)
    row = _row(store, 100, ALICE)
    assert row["expired"] == 0  # victim untouched

    # The owner can expire their own.
    store.mark_prekey_expired(100, ALICE)
    row = _row(store, 100, ALICE)
    assert row["expired"] == 1


def _row(store, prekey_id, owner):
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT * FROM dm_prekeys WHERE prekey_id=? AND owner_webid=?",
        (prekey_id, owner),
    ).fetchone()
    conn.close()
    return r


# ── FIND-11: atomic claim never hands the same prekey to two claims ──
def test_concurrent_claims_never_double_hand_out(store):
    n = 25
    for i in range(n):
        store.save_prekey(1000 + i, ALICE, f"opk_{i}==", f"priv_{i}==", one_time=True)

    barrier = threading.Barrier(n)
    results: list = []
    lock = threading.Lock()

    def claim():
        barrier.wait()
        got = store.claim_one_time_prekey(ALICE)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=claim) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed_ids = [r["prekey_id"] for r in results if r is not None]
    # No prekey handed out twice, and every prekey was consumed exactly once.
    assert len(claimed_ids) == len(set(claimed_ids))
    assert len(claimed_ids) == n
    assert store.count_unused_one_time_prekeys(ALICE) == 0


# ── migration: an existing pre-58 DB upgrades without losing prekeys ──
def test_migration_58_preserves_rows_and_adds_composite_pk(tmp_path):
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (57)")
    conn.execute(
        """CREATE TABLE dm_prekeys (
            prekey_id        INTEGER PRIMARY KEY,
            owner_webid      TEXT NOT NULL,
            pub_b64          TEXT NOT NULL,
            priv_wrapped_b64 TEXT NOT NULL,
            one_time         INTEGER NOT NULL DEFAULT 1,
            used             INTEGER NOT NULL DEFAULT 0,
            created_at       REAL NOT NULL,
            spk_created_at   REAL DEFAULT 0,
            expired          INTEGER NOT NULL DEFAULT 0
        )"""
    )
    now = time.time()
    conn.executemany(
        """INSERT INTO dm_prekeys
           (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, one_time, used,
            created_at, spk_created_at, expired)
           VALUES (?,?,?,?,?,?,?,?,0)""",
        [
            (42, ALICE, "alice_spk==", "alice_priv==", 0, 0, now, now),
            (7, ALICE, "alice_opk==", "alice_opriv==", 1, 0, now, 0),
            (99, MALLORY, "m_spk==", "m_priv==", 0, 0, now, now),
        ],
    )
    conn.commit()
    conn.close()

    # Opening the store runs migration 58.
    st = LocalStore(db)

    conn = sqlite3.connect(db)
    ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert ver >= 58
    count = conn.execute("SELECT COUNT(*) FROM dm_prekeys").fetchone()[0]
    assert count == 3  # no data dropped
    conn.close()

    assert st.get_signed_prekey(ALICE)["prekey_id"] == 42
    assert st.get_signed_prekey(MALLORY)["prekey_id"] == 99
    assert st.count_unused_one_time_prekeys(ALICE) == 1

    # Composite PK is now in force: the same id under a new owner is a new row,
    # not a replacement of the existing one.
    st.save_prekey(42, MALLORY, "m_spk2==", "m_priv2==", one_time=False)
    assert st.get_signed_prekey(ALICE)["pub_b64"] == "alice_spk=="
    assert st.get_signed_prekey(MALLORY)["pub_b64"] == "m_spk2=="


# ── FIND-6: a rebuild whose copy fails does not drop data or advance version ──
def test_failed_rebuild_preserves_data_and_version(store):
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t_src (x INTEGER)")
    conn.execute("INSERT INTO t_src (x) VALUES (1),(2),(3)")
    conn.execute("CREATE TABLE _ver (v INTEGER)")
    conn.execute("INSERT INTO _ver (v) VALUES (100)")
    conn.commit()

    bad = {
        "rebuild": "t_src",
        "temp": "t_tmp",
        "pre": [
            "DROP TABLE IF EXISTS t_tmp",
            "CREATE TABLE t_tmp (x INTEGER)",
        ],
        # Copies nothing, so the row-count check fails.
        "copy": "INSERT INTO t_tmp (x) SELECT x FROM t_src WHERE 0",
        "post": [],
    }

    # Mirror the migration engine's transaction + version-advance shape.
    raised = False
    try:
        with conn:
            store._run_rebuild_migration(conn, bad)
            conn.execute("UPDATE _ver SET v = 101")
    except RuntimeError:
        raised = True

    assert raised  # copy mismatch surfaced, not swallowed
    # Version never advanced past the failed migration.
    assert conn.execute("SELECT v FROM _ver").fetchone()[0] == 100
    # Original table was not dropped and keeps every row.
    rows = [r[0] for r in conn.execute("SELECT x FROM t_src ORDER BY x").fetchall()]
    assert rows == [1, 2, 3]
    conn.close()
