"""On-disk gateway identity: raw ``identity.key`` (Ed25519) and ``store.key`` (X25519).

These are the files run_gateway.py (the Tauri sidecar entry) loads at startup.
POST /restore writes them through :func:`persist_restored_keys` so a restored
identity survives a restart.

Restore has to change two things that cannot be committed together: the key
files and the LocalStore rows wrapped with a key derived from the identity. The
order is:

1. write the new keys next to the live ones as ``*.key.restore`` (fsynced),
2. re-wrap the LocalStore secrets in one SQLite transaction (the commit point),
3. rename ``identity.key.restore`` then ``store.key.restore`` over the live files.

If the process dies part way, :func:`finish_pending_restore` (called by the
launchers before loading keys) checks whether the database transaction
committed and either completes the renames or discards the pending files.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

logger = logging.getLogger(__name__)

IDENTITY_KEY_FILE = "identity.key"
STORE_KEY_FILE = "store.key"
_PENDING_SUFFIX = ".restore"


def _raw(key) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _fsync_dir(path: Path) -> None:
    """Persist a rename on POSIX. Windows cannot open a directory; skip there."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_key_file(path: Path, data: bytes) -> None:
    """Write a private key file owner-only (0600 on POSIX), fully flushed.

    Writes go to a ``.tmp`` sibling first and are renamed into place, so a
    reader never sees a truncated key.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)  # in case the file pre-existed with wider perms
    except OSError:
        pass
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def load_or_create_raw_key(key_path: Path, key_cls, generate_fn):
    key_path = Path(key_path)
    if key_path.exists():
        return key_cls.from_private_bytes(key_path.read_bytes())
    key = generate_fn()
    write_key_file(key_path, _raw(key))
    return key


def load_or_create_agent_keys(data_dir: Path, db_path: Optional[Path] = None):
    """Return (identity_key, store_key) for ``data_dir``, creating them on first run.

    Completes or discards an interrupted restore first.
    """
    data_dir = Path(data_dir)
    finish_pending_restore(data_dir, db_path or (data_dir / "proxion.db"))
    identity = load_or_create_raw_key(
        data_dir / IDENTITY_KEY_FILE, Ed25519PrivateKey, Ed25519PrivateKey.generate)
    store = load_or_create_raw_key(
        data_dir / STORE_KEY_FILE, X25519PrivateKey, X25519PrivateKey.generate)
    return identity, store


def _pending(data_dir: Path, name: str) -> Path:
    return data_dir / (name + _PENDING_SUFFIX)


def persist_restored_keys(data_dir: Path, agent, store=None) -> None:
    """Make ``agent``'s keys the identity that ``data_dir`` loads on restart.

    ``store`` is the gateway's LocalStore (or None). Its wrapped secrets are
    re-wrapped for the new identity in the same step, and its in-memory wrap
    key is switched so later writes use the new key.
    """
    data_dir = Path(data_dir)
    pend_id = _pending(data_dir, IDENTITY_KEY_FILE)
    pend_store = _pending(data_dir, STORE_KEY_FILE)
    write_key_file(pend_id, _raw(agent.identity_key))
    write_key_file(pend_store, _raw(agent.store_key))
    try:
        if store is not None:
            store.rewrap_secrets(agent.db_wrap_key())
    except BaseException:
        for p in (pend_id, pend_store):
            try:
                p.unlink()
            except OSError:
                pass
        raise
    os.replace(pend_id, data_dir / IDENTITY_KEY_FILE)
    os.replace(pend_store, data_dir / STORE_KEY_FILE)
    _fsync_dir(data_dir)


def _db_committed_for(db_path: Path, wrap_key: bytes) -> bool:
    """True when the database's wrapped secrets belong to ``wrap_key``.

    Re-wrapping is one transaction, so any envelope that opens with the new
    key proves it committed. (Rows that were already unreadable before the
    restore are left as they were, so a failure alone proves nothing unless
    no row opens.) A database with no wrapped rows has nothing that
    could become unreadable, so the new identity is safe to adopt.
    """
    import sqlite3

    from ._store._base import WRAPPED_SECRET_COLUMNS
    from .key_envelope import decrypt_key_bundle

    if not Path(db_path).exists():
        return True
    saw_envelope = False
    conn = sqlite3.connect(str(db_path))
    try:
        for table, column in WRAPPED_SECRET_COLUMNS:
            try:
                rows = conn.execute(
                    f"SELECT {column} FROM {table} WHERE {column} LIKE '{{%'"
                ).fetchall()
            except sqlite3.Error:
                continue
            for (value,) in rows:
                try:
                    env = json.loads(value)
                except Exception:
                    continue
                if not isinstance(env, dict) or "ciphertext_b64" not in env:
                    continue
                try:
                    decrypt_key_bundle(env, wrap_key)
                    return True
                except Exception:
                    saw_envelope = True
        return not saw_envelope
    finally:
        conn.close()


def finish_pending_restore(data_dir: Path, db_path: Path) -> None:
    """Complete or roll back a restore that was interrupted before its renames."""
    from .persist import AgentState

    data_dir = Path(data_dir)
    pend_id = _pending(data_dir, IDENTITY_KEY_FILE)
    pend_store = _pending(data_dir, STORE_KEY_FILE)
    if pend_id.exists():
        try:
            identity = Ed25519PrivateKey.from_private_bytes(pend_id.read_bytes())
            wrap_key = AgentState(identity_key=identity, store_key=X25519PrivateKey.generate()).db_wrap_key()
            committed = pend_store.exists() and _db_committed_for(db_path, wrap_key)
        except Exception:
            committed = False
        if committed:
            logger.warning("Completing an interrupted identity restore")
            os.replace(pend_id, data_dir / IDENTITY_KEY_FILE)
            os.replace(pend_store, data_dir / STORE_KEY_FILE)
        else:
            logger.warning("Discarding an identity restore that did not complete")
            for p in (pend_id, pend_store):
                try:
                    p.unlink()
                except OSError:
                    pass
    elif pend_store.exists():
        # identity.key was already renamed, so the restore had committed.
        os.replace(pend_store, data_dir / STORE_KEY_FILE)
    _fsync_dir(data_dir)
