"""LocalStore private keys are encrypted at rest with a wrap key derived from
the unlocked identity (C1-REDESIGN).

A keyless store keeps the historical raw behaviour; a keyed store wraps every
private-key column in an AES-256-GCM envelope. Legacy raw rows stay readable and
are re-wrapped on the next write.
"""
import base64
import os
import sqlite3

import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState


def _b64(n: int = 32) -> str:
    return base64.b64encode(os.urandom(n)).decode("ascii")


def _raw_column(db_path: str, table: str, column: str, where: str = "") -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT {column} FROM {table} {where}").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _session(root, send, recv, owner="alice@example.org", peer="bob@example.org"):
    return {
        "session_id": "sess-1",
        "peer_webid": peer,
        "owner_webid": owner,
        "root_key": root,
        "send_chain_key": send,
        "recv_chain_key": recv,
        "send_count": 0,
        "recv_count": 0,
    }


# ---------------------------------------------------------------------------
# Keyed store — dm_sessions
# ---------------------------------------------------------------------------

def test_dm_session_columns_wrapped_and_roundtrip(tmp_path):
    key = os.urandom(32)
    db = str(tmp_path / "keyed.db")
    store = LocalStore(db, db_wrap_key=key)
    root, send, recv = _b64(), _b64(), _b64()
    store.save_dm_session(_session(root, send, recv))

    # On disk every chain key is an envelope, not the raw base64.
    for col in ("root_key_b64", "send_chain_key_b64", "recv_chain_key_b64"):
        stored = _raw_column(db, "dm_sessions", col)
        assert stored.startswith("{"), f"{col} should be a wrapped envelope"
        assert '"ciphertext_b64"' in stored
        assert root not in stored and send not in stored and recv not in stored

    # Reads decrypt back to the original plaintext.
    got = store.get_dm_session("alice@example.org", "bob@example.org")
    assert got["root_key"] == root
    assert got["send_chain_key"] == send
    assert got["recv_chain_key"] == recv

    by_id = store.get_dm_session_by_id("sess-1")
    assert by_id["root_key"] == root
    assert by_id["send_chain_key"] == send
    assert by_id["recv_chain_key"] == recv


# ---------------------------------------------------------------------------
# Keyed store — dm_prekeys
# ---------------------------------------------------------------------------

def test_prekey_priv_wrapped_and_roundtrip(tmp_path):
    key = os.urandom(32)
    db = str(tmp_path / "keyed.db")
    store = LocalStore(db, db_wrap_key=key)
    owner = "alice@example.org"
    spk_priv, opk_priv = _b64(), _b64()

    store.save_prekey(1, owner, "spkpub==", spk_priv, one_time=False)
    store.save_prekey(2, owner, "opkpub==", opk_priv, one_time=True)

    stored = _raw_column(db, "dm_prekeys", "priv_wrapped_b64", "WHERE prekey_id=1")
    assert stored.startswith("{")
    assert spk_priv not in stored

    spk = store.get_signed_prekey(owner)
    assert spk["priv_wrapped_b64"] == spk_priv

    opk = store.claim_one_time_prekey(owner)
    assert opk["priv_wrapped_b64"] == opk_priv


# ---------------------------------------------------------------------------
# Keyed store — wg_local_identity
# ---------------------------------------------------------------------------

def test_wg_local_identity_priv_wrapped_and_roundtrip(tmp_path):
    key = os.urandom(32)
    db = str(tmp_path / "keyed.db")
    store = LocalStore(db, db_wrap_key=key)
    priv = _b64()
    store.save_wg_local_identity("wgpub==", priv)

    stored = _raw_column(db, "wg_local_identity", "priv_wrapped_b64", "WHERE id=1")
    assert stored.startswith("{")
    assert priv not in stored

    got = store.get_wg_local_identity()
    assert got["priv_wrapped_b64"] == priv


# ---------------------------------------------------------------------------
# Legacy raw rows: readable through a keyed store, re-wrapped on next write
# ---------------------------------------------------------------------------

def test_legacy_raw_row_readable_and_rewrapped(tmp_path):
    key = os.urandom(32)
    db = str(tmp_path / "keyed.db")
    store = LocalStore(db, db_wrap_key=key)
    root, send, recv = _b64(), _b64(), _b64()

    # Simulate a row written by an older keyless build: raw base64 in place.
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO dm_sessions
           (session_id, peer_webid, owner_webid, root_key_b64,
            send_chain_key_b64, recv_chain_key_b64, send_count, recv_count,
            created_at, updated_at)
           VALUES ('sess-1', 'bob@example.org', 'alice@example.org', ?, ?, ?, 0, 0, 0, 0)""",
        (root, send, recv),
    )
    conn.commit()
    conn.close()

    # Legacy raw is detected and returned unchanged.
    got = store.get_dm_session("alice@example.org", "bob@example.org")
    assert got["root_key"] == root
    assert got["send_chain_key"] == send
    assert got["recv_chain_key"] == recv

    # A subsequent write re-wraps it.
    store.save_dm_session(_session(root, send, recv))
    stored = _raw_column(db, "dm_sessions", "root_key_b64")
    assert stored.startswith("{")
    assert store.get_dm_session("alice@example.org", "bob@example.org")["root_key"] == root


# ---------------------------------------------------------------------------
# Keyless store: raw storage, no envelope
# ---------------------------------------------------------------------------

def test_keyless_store_stays_raw(tmp_path):
    db = str(tmp_path / "keyless.db")
    store = LocalStore(db)  # no db_wrap_key
    root, send, recv = _b64(), _b64(), _b64()
    store.save_dm_session(_session(root, send, recv))

    assert _raw_column(db, "dm_sessions", "root_key_b64") == root
    assert not _raw_column(db, "dm_sessions", "root_key_b64").startswith("{")

    got = store.get_dm_session("alice@example.org", "bob@example.org")
    assert got["root_key"] == root


# ---------------------------------------------------------------------------
# Wrong wrap key does not silently return wrong bytes
# ---------------------------------------------------------------------------

def test_wrong_wrap_key_fails_to_unwrap(tmp_path):
    db = str(tmp_path / "keyed.db")
    root, send, recv = _b64(), _b64(), _b64()

    store_a = LocalStore(db, db_wrap_key=os.urandom(32))
    store_a.save_dm_session(_session(root, send, recv))

    store_b = LocalStore(db, db_wrap_key=os.urandom(32))
    got = store_b.get_dm_session("alice@example.org", "bob@example.org")
    assert got["root_key"] is None
    assert got["send_chain_key"] is None
    assert got["recv_chain_key"] is None


def test_wrapped_row_unreadable_without_key(tmp_path):
    db = str(tmp_path / "keyed.db")
    root, send, recv = _b64(), _b64(), _b64()
    LocalStore(db, db_wrap_key=os.urandom(32)).save_dm_session(_session(root, send, recv))

    keyless = LocalStore(db)  # misconfiguration: envelope present, no key
    got = keyless.get_dm_session("alice@example.org", "bob@example.org")
    assert got["root_key"] is None


# ---------------------------------------------------------------------------
# AgentState.db_wrap_key derivation
# ---------------------------------------------------------------------------

def test_db_wrap_key_deterministic_and_per_identity():
    a = AgentState.generate()
    k1 = a.db_wrap_key()
    k2 = a.db_wrap_key()
    assert isinstance(k1, bytes) and len(k1) == 32
    assert k1 == k2  # stable across calls

    b = AgentState.generate()
    assert b.db_wrap_key() != k1  # differs for a different identity


def test_db_wrap_key_survives_store_key_rotation():
    # The wrap key is bound to the identity key, not the store key, so a routine
    # store-key rotation must NOT change it (else every wrapped row would orphan).
    a = AgentState.generate()
    before = a.db_wrap_key()
    a.rotate_store_key()
    assert a.db_wrap_key() == before
