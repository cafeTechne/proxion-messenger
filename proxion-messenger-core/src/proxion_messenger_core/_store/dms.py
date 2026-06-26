"""
SQLite-backed persistence for local (pod-free) relay mode.

Stores rooms, room membership, messages, display names, and DM threads so
that gateway restarts and browser reloads can fully restore session state.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)




class DmStoreMixin(object):
    def save_dm_thread(
        self,
        thread_id: str,
        peer_webid: str,
        display_name: Optional[str] = None,
        owner_webid: str = '',
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO dm_threads
                    (thread_id, peer_webid, display_name, owner_webid, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, peer_webid, display_name, owner_webid, time.time()),
            )
            if display_name:
                conn.execute(
                    "UPDATE dm_threads SET display_name = ? WHERE thread_id = ?",
                    (display_name, thread_id),
                )
    def get_dm_threads(self, owner_webid: str = '') -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM dm_threads WHERE owner_webid = ? ORDER BY created_at ASC",
                (owner_webid,)
            ).fetchall()
            return [dict(r) for r in rows]
    def get_thread_integrity_state(self, thread_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT thread_id, last_seq_num, last_prev_hash, checked_at FROM thread_integrity_state WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            return dict(row) if row else None
    def upsert_thread_integrity_state(
        self, thread_id: str, last_seq_num: int, last_prev_hash: str, checked_at: float
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO thread_integrity_state
                   (thread_id, last_seq_num, last_prev_hash, checked_at)
                   VALUES (?, ?, ?, ?)""",
                (thread_id, last_seq_num, last_prev_hash, checked_at),
            )
    def create_compromise_recovery_session(
        self,
        session_id: str,
        reason: str,
        initiated_by: str,
        steps: list,
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO compromise_recovery_sessions
                   (session_id, status, reason, initiated_by, created_at, updated_at)
                   VALUES (?, 'active', ?, ?, ?, ?)""",
                (session_id, reason, initiated_by, now, now),
            )
            for step in steps:
                conn.execute(
                    """INSERT OR IGNORE INTO compromise_recovery_steps
                       (session_id, step_name, step_status, updated_at)
                       VALUES (?, ?, 'pending', ?)""",
                    (session_id, step, now),
                )
    def get_compromise_recovery_session(self, session_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM compromise_recovery_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = dict(row)
            steps = conn.execute(
                "SELECT * FROM compromise_recovery_steps WHERE session_id = ? ORDER BY rowid",
                (session_id,),
            ).fetchall()
            session["steps"] = [dict(s) for s in steps]
            return session
    def list_compromise_recovery_sessions(self, status: Optional[str] = None, limit: int = 50) -> list:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM compromise_recovery_sessions WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM compromise_recovery_sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    def upsert_thread_participant_binding(self, thread_id: str, webid: str, binding_source: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO thread_participant_bindings
                   (thread_id, webid, binding_source, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (thread_id, webid, binding_source, now),
            )
    def get_thread_participants(self, thread_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT webid FROM thread_participant_bindings WHERE thread_id = ?", (thread_id,)
            ).fetchall()
            return [r[0] for r in rows]
    def is_thread_participant_binding(self, thread_id: str, webid: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM thread_participant_bindings WHERE thread_id = ? AND webid = ? LIMIT 1",
                (thread_id, webid),
            ).fetchone()
            return row is not None
    def save_dm_session(self, session: dict) -> None:
        """Persist a DM session state dict (from e2e_session.session_to_dict)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO dm_sessions
                   (session_id, peer_webid, owner_webid, root_key_b64,
                    send_chain_key_b64, recv_chain_key_b64,
                    send_count, recv_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session["session_id"],
                    session["peer_webid"],
                    session["owner_webid"],
                    session["root_key"],
                    session["send_chain_key"],
                    session["recv_chain_key"],
                    session.get("send_count", 0),
                    session.get("recv_count", 0),
                    time.time(),
                    time.time(),
                ),
            )
    def get_dm_session(self, owner_webid: str, peer_webid: str) -> dict | None:
        """Return the most recent session state for an owner/peer pair."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT * FROM dm_sessions
                       WHERE owner_webid = ? AND peer_webid = ?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (owner_webid, peer_webid),
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                return {
                    "session_id": d["session_id"],
                    "peer_webid": d["peer_webid"],
                    "owner_webid": d["owner_webid"],
                    "root_key": d["root_key_b64"],
                    "send_chain_key": d["send_chain_key_b64"],
                    "recv_chain_key": d["recv_chain_key_b64"],
                    "send_count": d["send_count"],
                    "recv_count": d["recv_count"],
                }
            except Exception:
                return None
    def save_prekey(
        self,
        prekey_id: int,
        owner_webid: str,
        pub_b64: str,
        priv_wrapped_b64: str,
        one_time: bool = True,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO dm_prekeys
                   (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, one_time, used, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, 1 if one_time else 0, time.time()),
            )
    def get_signed_prekey(self, owner_webid: str) -> dict | None:
        """Return the stored signed prekey (one_time=0) for an owner."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM dm_prekeys WHERE owner_webid=? AND one_time=0 ORDER BY created_at DESC LIMIT 1",
                    (owner_webid,),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def claim_one_time_prekey(self, owner_webid: str) -> dict | None:
        """Return and mark-used one unused one-time prekey for owner_webid."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM dm_prekeys WHERE owner_webid=? AND one_time=1 AND used=0 ORDER BY created_at ASC LIMIT 1",
                    (owner_webid,),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE dm_prekeys SET used=1 WHERE prekey_id=?",
                    (row["prekey_id"],),
                )
                return dict(row)
            except Exception:
                return None
    def get_prekey_bundle(self, owner_webid: str) -> dict | None:
        """Return public prekey bundle (no private halves) for session initiation."""
        spk = self.get_signed_prekey(owner_webid)
        if not spk:
            return None
        opk = self.claim_one_time_prekey(owner_webid)
        bundle: dict = {
            "owner_webid": owner_webid,
            "signed_prekey_id": spk["prekey_id"],
            "signed_prekey_pub_b64": spk["pub_b64"],
        }
        if opk:
            bundle["one_time_prekey_id"] = opk["prekey_id"]
            bundle["one_time_prekey_pub_b64"] = opk["pub_b64"]
        return bundle
    def count_unused_one_time_prekeys(self, owner_webid: str) -> int:
        """Return the number of unused one-time prekeys available for owner_webid."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM dm_prekeys "
                    "WHERE owner_webid=? AND one_time=1 AND used=0",
                    (owner_webid,),
                ).fetchone()
                return row[0] if row else 0
            except Exception:
                return 0
    def delete_dm_session(self, session_id: str) -> None:
        """Delete a DM session by its session_id."""
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM dm_sessions WHERE session_id=?", (session_id,))
            except Exception:
                pass
    def get_dm_session_by_id(self, session_id: str) -> dict | None:
        """Return a DM session state dict for the given session_id."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM dm_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                return {
                    "session_id": d["session_id"],
                    "peer_webid": d["peer_webid"],
                    "owner_webid": d["owner_webid"],
                    "root_key": d["root_key_b64"],
                    "send_chain_key": d["send_chain_key_b64"],
                    "recv_chain_key": d["recv_chain_key_b64"],
                    "send_count": d["send_count"],
                    "recv_count": d["recv_count"],
                    "updated_at": d["updated_at"],
                }
            except Exception:
                return None
    def set_dm_session_checkpoint_etag(self, session_id: str, etag: str) -> None:
        """Store the ETag from the last successful pod checkpoint for a DM session."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE dm_sessions SET pod_checkpoint_etag=? WHERE session_id=?",
                    (etag, session_id),
                )
            except Exception:
                pass
    def get_dm_session_checkpoint_etag(self, session_id: str) -> str | None:
        """Return the stored pod checkpoint ETag for a DM session, or None."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT pod_checkpoint_etag FROM dm_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return None
                return row["pod_checkpoint_etag"]
            except Exception:
                return None
    def list_dm_sessions(self, owner_webid: str) -> list[dict]:
        """Return all DM sessions owned by owner_webid, newest first."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT session_id, peer_webid, owner_webid,
                              send_count, recv_count, created_at, updated_at
                       FROM dm_sessions WHERE owner_webid=?
                       ORDER BY updated_at DESC""",
                    (owner_webid,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def list_dm_thread_ids(self, owner_webid: str) -> list[str]:
        """Return distinct DM thread_ids for messages owned by owner_webid."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT DISTINCT thread_id FROM messages
                       WHERE thread_type='dm' AND from_webid=?""",
                    (owner_webid,),
                ).fetchall()
                return [r["thread_id"] for r in rows]
            except Exception:
                return []
    def prune_expired_dm_sessions(self, max_age_seconds: float = 7_776_000.0) -> int:
        """Delete dm_sessions not updated within max_age_seconds. Returns count deleted."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM dm_sessions WHERE updated_at < ?", (cutoff,))
                return conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                return 0
    def get_dm_sessions_for_device_scope(
        self,
        owner_webid: str,
        owner_device_id: str,
        peer_webid: str,
        peer_device_id: str,
    ) -> list[dict]:
        """Return sessions matching the given device-pair scope, newest first."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM dm_sessions
                       WHERE owner_webid=? AND owner_device_id=?
                         AND peer_webid=? AND peer_device_id=?
                       ORDER BY updated_at DESC""",
                    (owner_webid, owner_device_id, peer_webid, peer_device_id),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def record_dm_delivery(
        self, message_id: str, to_webid: str, to_device_id: str
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO dm_device_deliveries
                       (message_id, to_webid, to_device_id)
                       VALUES (?, ?, ?)""",
                    (message_id, to_webid, to_device_id),
                )
            except Exception:
                pass
    def mark_dm_delivered(
        self,
        message_id: str,
        to_webid: str,
        to_device_id: str,
        *,
        read: bool = False,
    ) -> None:
        with self._conn() as conn:
            try:
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if read:
                    conn.execute(
                        """UPDATE dm_device_deliveries
                           SET delivered_at = COALESCE(delivered_at, ?), read_at = ?
                           WHERE message_id=? AND to_webid=? AND to_device_id=?""",
                        (now, now, message_id, to_webid, to_device_id),
                    )
                else:
                    conn.execute(
                        """UPDATE dm_device_deliveries
                           SET delivered_at = COALESCE(delivered_at, ?)
                           WHERE message_id=? AND to_webid=? AND to_device_id=?""",
                        (now, message_id, to_webid, to_device_id),
                    )
            except Exception:
                pass
    def get_dm_deliveries(self, message_id: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM dm_device_deliveries WHERE message_id=?",
                    (message_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def get_expired_signed_prekeys(
        self, owner_webid: str, max_age_seconds: float
    ) -> list[dict]:
        """Return signed (non-one-time) prekeys older than max_age_seconds."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM dm_prekeys
                       WHERE owner_webid=? AND one_time=0 AND expired=0
                             AND spk_created_at > 0 AND spk_created_at < ?""",
                    (owner_webid, cutoff),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def mark_prekey_expired(self, prekey_id: int) -> None:
        """Mark a signed prekey as expired (retained for 48h, then hard-deleted by purge loop)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE dm_prekeys SET expired=1 WHERE prekey_id=?",
                    (prekey_id,),
                )
            except Exception:
                pass
    def save_prekey_with_timestamp(
        self,
        prekey_id: int,
        owner_webid: str,
        pub_b64: str,
        priv_wrapped_b64: str,
        one_time: bool = True,
        spk_created_at: float = 0.0,
    ) -> None:
        """Like save_prekey but also stores spk_created_at for SPK rotation."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO dm_prekeys
                   (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, one_time, used,
                    created_at, spk_created_at, expired)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0)""",
                (
                    prekey_id, owner_webid, pub_b64, priv_wrapped_b64,
                    1 if one_time else 0, time.time(),
                    spk_created_at or time.time(),
                ),
            )
    def save_stun_session(
        self,
        session_id: str,
        external_ip: str,
        external_port: int,
        stun_server: str,
        ttl_seconds: int = 300,
        owner_webid: str = "",
        owner_device_id: str = "",
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO stun_sessions
                       (id, external_ip, external_port, stun_server, discovered_at, expires_at,
                        owner_webid, owner_device_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, external_ip, external_port, stun_server, now, now + ttl_seconds,
                     owner_webid, owner_device_id),
                )
            except Exception:
                pass
    def get_latest_stun_session_for_owner(
        self,
        owner_webid: str,
        owner_device_id: str = "",
    ) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT * FROM stun_sessions
                       WHERE owner_webid=? AND owner_device_id=? AND expires_at > ?
                       ORDER BY discovered_at DESC LIMIT 1""",
                    (owner_webid, owner_device_id, time.time()),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def get_latest_stun_session(self) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT * FROM stun_sessions WHERE expires_at > ?
                       ORDER BY discovered_at DESC LIMIT 1""",
                    (time.time(),),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def prune_expired_stun_sessions(self) -> int:
        with self._conn() as conn:
            try:
                cur = conn.execute(
                    "DELETE FROM stun_sessions WHERE expires_at <= ?", (time.time(),)
                )
                return cur.rowcount
            except Exception:
                return 0
