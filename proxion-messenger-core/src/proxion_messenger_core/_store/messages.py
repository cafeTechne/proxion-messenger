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




class MessageStoreMixin(object):
    def delete_messages_before(self, thread_id: str, cutoff_iso: str) -> int:
        """Delete messages older than cutoff_iso from a thread. Returns count deleted."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM messages WHERE thread_id = ? AND timestamp < ?",
                (thread_id, cutoff_iso)
            )
            return cur.rowcount
    def save_message(
        self,
        message_id: str,
        thread_id: str,
        thread_type: str,
        from_webid: str,
        from_display_name: Optional[str],
        content: str,
        timestamp: str,
        reply_to_id: Optional[str] = None,
        imported: int = 0,
        seq_num: int = 0,
        prev_hash: str = "",
    ) -> None:
        if from_display_name:
            from_display_name = from_display_name[:64]
        _MAX_MESSAGES_PER_THREAD = 5000
        _MAX_BYTES_PER_THREAD = 50 * 1024 * 1024 # 50MB
        received_at = datetime.now(timezone.utc).isoformat()
        
        with self._conn() as conn:
            # R2: Quota Enforcement
            res = conn.execute(
                "SELECT COUNT(*), SUM(LENGTH(content)) FROM messages WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            count = res[0] or 0
            total_bytes = res[1] or 0
            
            if count >= _MAX_MESSAGES_PER_THREAD:
                logger.warning("Quota exceeded for thread %s: message count %d", thread_id, count)
                return
            
            if total_bytes + len(content) > _MAX_BYTES_PER_THREAD:
                logger.warning("Quota exceeded for thread %s: byte size %d", thread_id, total_bytes)
                return

            conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, thread_id, thread_type, from_webid,
                     from_display_name, content, timestamp, reply_to_id, imported,
                     received_at, seq_num, prev_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    thread_type,
                    from_webid,
                    from_display_name,
                    content,
                    timestamp,
                    reply_to_id,
                    imported,
                    received_at,
                    seq_num,
                    prev_hash,
                ),
            )
    def save_voice_message(
        self,
        message_id: str,
        thread_id: str,
        thread_type: str,
        from_webid: str,
        from_display_name: Optional[str],
        audio_b64: str,
        duration_ms: int,
        timestamp: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, thread_id, thread_type, from_webid,
                     from_display_name, content, content_type, audio_b64, duration_ms, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, thread_id, thread_type, from_webid,
                 from_display_name, "", "audio", audio_b64, duration_ms, timestamp),
            )
    def get_message_sender(self, message_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT from_webid FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return row[0] if row else None
    def get_message(self, message_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            return dict(row) if row else None
    def delete_message(self, message_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
    def update_message(
        self, message_id: str, new_content: str, edited_at: Optional[str] = None,
        editor_webid: str = "",
    ) -> None:
        prev_row = None
        with self._conn() as conn:
            prev_row = conn.execute(
                "SELECT content FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            conn.execute(
                "UPDATE messages SET content = ?, edited_at = ? WHERE message_id = ?",
                (new_content, edited_at, message_id),
            )
        if prev_row and editor_webid:
            import uuid as _uuid
            self.save_edit(
                str(_uuid.uuid4()),
                message_id,
                prev_row["content"],
                new_content,
                editor_webid,
                edited_at or datetime.now(timezone.utc).isoformat(),
            )
    def search_contacts(self, query: str, limit: int = 20, owner_webid: str = "") -> list:
        if not query:
            return []
        pattern = f"%{query}%"
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM contacts WHERE (display_name LIKE ? OR webid LIKE ?) "
                    "AND owner_webid = ? "
                    "ORDER BY last_seen_at DESC LIMIT ?",
                    (pattern, pattern, owner_webid, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def get_messages(
        self,
        thread_id: str,
        after_timestamp: Optional[str] = None,
        before_timestamp: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Return messages for a thread, oldest-first.

        - after_timestamp  → messages strictly after this ISO timestamp
        - before_timestamp → messages strictly before this ISO timestamp (for pagination)
        - default          → most recent `limit` messages
        """
        with self._conn() as conn:
            if after_timestamp:
                rows = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE thread_id = ? AND timestamp > ?
                    ORDER BY timestamp ASC, received_at ASC LIMIT ?
                    """,
                    (thread_id, after_timestamp, limit),
                ).fetchall()
            elif before_timestamp:
                rows = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE thread_id = ? AND timestamp < ?
                    ORDER BY timestamp DESC, received_at DESC LIMIT ?
                    """,
                    (thread_id, before_timestamp, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE thread_id = ?
                    ORDER BY timestamp DESC, received_at DESC LIMIT ?
                    """,
                    (thread_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            return [dict(r) for r in rows]
    def count_messages_after(self, thread_id: str, after_ts: float) -> int:
        """Count messages in thread with timestamp strictly after after_ts (Unix seconds)."""
        from datetime import datetime, timezone as _tz
        iso = datetime.fromtimestamp(after_ts, tz=_tz.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE thread_id = ? AND timestamp > ?",
                (thread_id, iso),
            ).fetchone()
        return row[0] if row else 0
    def count_reactions_by_sender(self, message_id: str, sender_webid: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT emoji) FROM reactions WHERE message_id=? AND sender_webid=?",
                (message_id, sender_webid),
            ).fetchone()
            return row[0] if row else 0
    def count_reactions_total(self, message_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM reactions WHERE message_id=?",
                (message_id,),
            ).fetchone()
            return row[0] if row else 0
    def save_reaction(self, room_id: str, message_id: str, emoji: str, sender_webid: str) -> bool:
        """Insert a reaction. Returns False (without inserting) if the per-user-per-room quota is exceeded."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM reactions WHERE room_id=? AND sender_webid=?",
                (room_id, sender_webid),
            ).fetchone()
            if row and row[0] >= self._REACTION_QUOTA:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO reactions (room_id, message_id, emoji, sender_webid) VALUES (?, ?, ?, ?)",
                (room_id, message_id, emoji, sender_webid),
            )
            return True
    def remove_reaction(self, room_id: str, message_id: str, emoji: str, sender_webid: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM reactions WHERE room_id=? AND message_id=? AND emoji=? AND sender_webid=?",
                (room_id, message_id, emoji, sender_webid),
            )
    def save_relay_message(self, msg_id: str, payload_json: str, target_url: str) -> None:
        """Enqueue an outbound relay message for durable delivery."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO relay_queue (id, payload, target_url, created_at) VALUES (?, ?, ?, ?)",
                (msg_id, payload_json, target_url, int(time.time())),
            )
    def get_pending_relay_messages(self, max_attempts: int = 5) -> list[dict]:
        """Return queued relay messages that haven't exceeded *max_attempts*."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, payload, target_url, attempts FROM relay_queue WHERE attempts < ? ORDER BY created_at ASC",
                (max_attempts,),
            ).fetchall()
            return [dict(r) for r in rows]
    def save_pin(self, thread_id: str, message_id: str, pinned_by: str, content: str = "") -> str:
        pin_id = f"pin-{int(time.time()*1000)}"
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pins (pin_id, thread_id, message_id, pinned_by, pinned_at, content)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (pin_id, thread_id, message_id, pinned_by, time.time(), content),
            )
        return pin_id
    def get_pins(self, thread_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pins WHERE thread_id = ? ORDER BY pinned_at ASC", (thread_id,)
            ).fetchall()
            return [dict(r) for r in rows]
    def remove_pin(self, thread_id: str, message_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM pins WHERE thread_id = ? AND message_id = ?",
                (thread_id, message_id),
            )
    def get_reactions(self, room_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT message_id, emoji, sender_webid FROM reactions WHERE room_id=? ORDER BY created_at ASC",
                (room_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    def save_scheduled_message(self, sched: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scheduled_messages (id, thread_id, from_webid, content, send_at, created_at) VALUES (?,?,?,?,?,?)",
                (sched["id"], sched["thread_id"], sched["from_webid"], sched["content"],
                 sched["send_at"], sched["created_at"])
            )
    def get_due_scheduled_messages(self, now: float) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_messages WHERE send_at <= ? AND cancelled=0",
                (now,)
            ).fetchall()
            return [dict(r) for r in rows]
    def cancel_scheduled_message(self, sched_id: str, from_webid: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE scheduled_messages SET cancelled=1 WHERE id=? AND from_webid=?",
                (sched_id, from_webid)
            )
            return cur.rowcount > 0
    def mark_scheduled_delivered(self, sched_id: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute("UPDATE scheduled_messages SET cancelled=1 WHERE id=?", (sched_id,))
            except Exception:
                pass
    def get_scheduled_messages_for_user(self, from_webid: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_messages WHERE from_webid=? AND cancelled=0 AND send_at > ?",
                (from_webid, time.time())
            ).fetchall()
            return [dict(r) for r in rows]
    def mark_scheduled_sent(self, sched_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE scheduled_messages SET cancelled=1 WHERE id=?", (sched_id,)
            )
    def get_messages_by_ids(self, message_ids: list[str]) -> list[dict]:
        """Fetch multiple messages by their IDs."""
        if not message_ids:
            return []
        placeholders = ",".join("?" * len(message_ids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
        return [dict(r) for r in rows]
    def get_message_identity_binding(self, message_id: str) -> Optional[dict]:
        """Return {from_webid, thread_id} for an existing message_id, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT from_webid, thread_id FROM messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row:
                return {"from_webid": row["from_webid"], "thread_id": row["thread_id"]}
            return None
    def upsert_peer_gateway_pin(
        self,
        peer_did: str,
        pinned_gateway_url: str,
        pinned_at: float,
        last_seen_gateway_url: str,
        last_seen_at: float,
        pending_change: bool = False,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO peer_gateway_pins
                   (peer_did, pinned_gateway_url, pinned_at, last_seen_gateway_url, last_seen_at, pending_change)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (peer_did, pinned_gateway_url, pinned_at, last_seen_gateway_url, last_seen_at, 1 if pending_change else 0),
            )
    def get_peer_gateway_pin(self, peer_did: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM peer_gateway_pins WHERE peer_did = ?", (peer_did,)
            ).fetchone()
            return dict(row) if row else None
    def search_messages(
        self,
        query: str,
        member_thread_ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        *,
        thread_id: str | None = None,
        from_webid: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> list[dict]:
        """FTS search with optional filters and pagination.

        Parameters
        ----------
        query:       FTS5 match expression.
        member_thread_ids: restrict to these thread IDs (ignored if thread_id given).
        limit:       max results (capped at 100).
        offset:      skip first N results (for cursor pagination).
        thread_id:   restrict to a single thread.
        from_webid:  restrict to messages from this sender.
        before:      ISO-8601 timestamp upper bound (exclusive).
        after:       ISO-8601 timestamp lower bound (inclusive).

        Returns
        -------
        list of message dicts, each with a ``next_offset`` key equal to
        ``offset + len(results)`` so callers can build a pagination cursor.
        """
        limit = min(limit, 100)
        if not query.strip():
            return []

        conditions = ["messages_fts MATCH ?"]
        params: list = [query]

        if thread_id:
            conditions.append("m.thread_id = ?")
            params.append(thread_id)
        elif member_thread_ids:
            placeholders = ",".join("?" * len(member_thread_ids))
            conditions.append(f"m.thread_id IN ({placeholders})")
            params.extend(member_thread_ids)

        if from_webid:
            conditions.append("m.from_webid = ?")
            params.append(from_webid)
        if after:
            conditions.append("m.timestamp >= ?")
            params.append(after)
        if before:
            conditions.append("m.timestamp < ?")
            params.append(before)

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        with self._conn() as conn:
            try:
                rows = conn.execute(
                    f"""
                    SELECT m.message_id, m.thread_id, m.content, m.from_webid,
                           m.from_display_name, m.timestamp, m.seq_num, m.prev_hash
                    FROM messages_fts f
                    JOIN messages m ON m.rowid = f.rowid
                    WHERE {where}
                    ORDER BY rank
                    LIMIT ? OFFSET ?
                    """,
                    params,
                ).fetchall()
            except Exception:
                return []

        results = [dict(r) for r in rows]
        next_off = offset + len(results)
        for r in results:
            r["next_offset"] = next_off
        return results
    def save_message_receipt(self, message_id: str, receiver_webid: str, read_at: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO message_receipts (message_id, receiver_webid, delivered_at, read_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(message_id, receiver_webid) DO UPDATE SET read_at = excluded.read_at""",
                    (message_id, receiver_webid, None, read_at),
                )
            except Exception:
                pass
    def get_message_readers(self, message_id: str) -> list:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT receiver_webid, read_at FROM message_receipts
                       WHERE message_id = ? AND read_at IS NOT NULL
                       ORDER BY read_at""",
                    (message_id,),
                ).fetchall()
            except Exception:
                return []
        return [{"receiver_webid": r[0], "read_at": r[1]} for r in rows]
    def rebuild_messages_fts(self) -> None:
        """Rebuild the FTS5 index from scratch (repair stale/partial index)."""
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM messages_fts")
                conn.execute(
                    """INSERT INTO messages_fts(rowid, message_id, thread_id, content,
                       from_webid, from_display_name, timestamp)
                       SELECT rowid, message_id, thread_id, content,
                              from_webid, from_display_name, timestamp
                       FROM messages"""
                )
            except Exception as exc:
                logger.warning("rebuild_messages_fts failed: %s", exc)
    def list_messages_for_thread(self, thread_id: str) -> list[dict]:
        """Return all messages for a thread_id, oldest first."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE thread_id=? ORDER BY timestamp ASC",
                    (thread_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def set_message_delivery_state(
        self, message_id: str, receiver_webid: str, state: str
    ) -> bool:
        """Apply a monotonic delivery state transition. Returns False if rejected."""
        from ..delivery_state import is_valid_transition
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT state FROM message_receipts WHERE message_id=? AND receiver_webid=?",
                    (message_id, receiver_webid),
                ).fetchone()
                current = row["state"] if row else None
                if not is_valid_transition(current, state):
                    return False
                if row is None:
                    conn.execute(
                        """INSERT OR IGNORE INTO message_receipts
                           (message_id, receiver_webid, state)
                           VALUES (?, ?, ?)""",
                        (message_id, receiver_webid, state),
                    )
                else:
                    conn.execute(
                        """UPDATE message_receipts SET state=?
                           WHERE message_id=? AND receiver_webid=?""",
                        (state, message_id, receiver_webid),
                    )
                return True
            except Exception:
                return False
    def get_message_delivery_state(
        self, message_id: str, receiver_webid: str
    ) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM message_receipts WHERE message_id=? AND receiver_webid=?",
                    (message_id, receiver_webid),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def get_messages_since_seq(
        self, thread_id: str, since_seq: int, limit: int = 100
    ) -> list[dict]:
        """Return messages with seq > since_seq, ascending, bounded by limit."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE thread_id=? AND seq IS NOT NULL AND seq > ?
                       ORDER BY seq ASC LIMIT ?""",
                    (thread_id, since_seq, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
