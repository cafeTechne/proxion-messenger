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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Per-thread retention. New messages are always stored. Once a thread grows past
# either ceiling, its oldest-stored messages (insertion order, so a backdated
# timestamp cannot evict a message that just arrived) are pruned together with
# the pins, reactions, edits and receipts that hang off them. The size check
# needs a COUNT/SUM over the thread, so it runs on the first insert into a
# thread after startup and then every THREAD_RETENTION_CHECK_EVERY inserts.
THREAD_RETENTION_MESSAGES = 50_000
THREAD_RETENTION_BYTES = 200 * 1024 * 1024  # 200 MB
THREAD_RETENTION_CHECK_EVERY = 250
# A single message bigger than this is refused outright rather than stored and
# then evicting a large part of the thread's history to make room.
MAX_SINGLE_MESSAGE_BYTES = 50 * 1024 * 1024  # 50 MB
# messages.content_type of a DM whose content is browser ratchet ciphertext.
E2E_CONTENT_TYPE = "e2e"


def fts_literal_query(text: str) -> str:
    """Turn user text into an FTS5 query that matches it literally.

    Each whitespace-separated word becomes a quoted string (embedded quotes
    doubled), so hyphens, dots, apostrophes, colons and words like AND/NOT are
    never parsed as FTS5 syntax. Words are ANDed. Words with no letters or
    digits are dropped; an empty result means "match nothing".
    """
    terms = []
    for word in (text or "").split():
        if not any(ch.isalnum() for ch in word):
            continue
        terms.append('"' + word.replace('"', '""') + '"')
    return " ".join(terms)


class MessageStoreMixin(object):
    def _note_thread_insert(self, thread_id: str) -> None:
        counters = self.__dict__.setdefault("_thread_insert_counts", {})
        n = counters.get(thread_id, 0)
        counters[thread_id] = n + 1
        if n % THREAD_RETENTION_CHECK_EVERY == 0:
            try:
                self.prune_thread(thread_id)
            except Exception:
                logger.warning("Retention prune failed for thread %s", thread_id, exc_info=True)

    def prune_thread(
        self,
        thread_id: str,
        max_messages: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> int:
        """Delete the oldest messages of a thread until it fits the retention
        ceilings. Returns the number of messages removed."""
        max_messages = THREAD_RETENTION_MESSAGES if max_messages is None else max_messages
        max_bytes = THREAD_RETENTION_BYTES if max_bytes is None else max_bytes
        size_sql = "LENGTH(COALESCE(content, '')) + LENGTH(COALESCE(audio_b64, ''))"
        with self._conn() as conn:
            count, total = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM({size_sql}), 0) FROM messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if count <= max_messages and total <= max_bytes:
                return 0
            excess_count = max(0, count - max_messages)
            excess_bytes = max(0, total - max_bytes)
            victims: list[str] = []
            freed = 0
            for mid, size in conn.execute(
                f"SELECT message_id, {size_sql} FROM messages WHERE thread_id = ? "
                "ORDER BY rowid ASC",
                (thread_id,),
            ):
                if len(victims) >= excess_count and freed >= excess_bytes:
                    break
                victims.append(mid)
                freed += size or 0
            for i in range(0, len(victims), 500):
                chunk = victims[i:i + 500]
                marks = ",".join("?" * len(chunk))
                conn.execute(f"DELETE FROM messages WHERE message_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM pins WHERE message_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM reactions WHERE message_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM message_edits WHERE message_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM message_receipts WHERE message_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM message_files WHERE message_id IN ({marks})", chunk)
        if victims:
            logger.info("Pruned %d oldest messages from thread %s", len(victims), thread_id)
        return len(victims)

    def delete_messages_before(self, thread_id: str, cutoff_iso: str, since_iso: str = "") -> int:
        """Delete messages older than cutoff_iso from a thread (and, with
        since_iso, not older than it). Returns count deleted."""
        where = "thread_id = ? AND timestamp < ?" + (" AND timestamp >= ?" if since_iso else "")
        params = (thread_id, cutoff_iso) + ((since_iso,) if since_iso else ())
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM message_files WHERE message_id IN ("
                f"SELECT message_id FROM messages WHERE {where})",
                params,
            )
            cur = conn.execute(f"DELETE FROM messages WHERE {where}", params)
            return cur.rowcount
    def select_message_ids_before(
        self, thread_id: str, cutoff_iso: str, limit: int = 0, since_iso: str = ""
    ) -> list[str]:
        """Return message_ids in a thread older than cutoff_iso, oldest first.

        Used to enumerate expiring disappearing messages so their pod objects can
        be purged alongside the SQLite rows. A positive *limit* bounds the batch
        so a thread with many expired ids does not wedge the expiry loop.
        """
        sql = ("SELECT message_id FROM messages WHERE thread_id = ? AND timestamp < ? "
               + ("AND timestamp >= ? " if since_iso else "") + "ORDER BY timestamp ASC")
        params: list = [thread_id, cutoff_iso] + ([since_iso] if since_iso else [])
        if limit and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]
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
        e2e: bool = False,
    ) -> None:
        """Store one message. ``e2e`` marks content as browser ratchet
        ciphertext (content_type 'e2e'): the gateway cannot read it, so history,
        pins and search must never show or match it as text."""
        if from_display_name:
            from_display_name = from_display_name[:64]
        if len(content or "") > MAX_SINGLE_MESSAGE_BYTES:
            logger.warning("Message %s too large for thread %s", message_id, thread_id)
            return
        received_at = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (message_id, thread_id, thread_type, from_webid,
                     from_display_name, content, timestamp, reply_to_id, imported,
                     received_at, seq_num, prev_hash, content_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    E2E_CONTENT_TYPE if e2e else "text",
                ),
            )
        self._note_thread_insert(thread_id)
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
        if from_display_name:
            from_display_name = from_display_name[:64]
        if len(audio_b64 or "") > MAX_SINGLE_MESSAGE_BYTES:
            logger.warning("Voice message %s too large for thread %s", message_id, thread_id)
            return
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
        self._note_thread_insert(thread_id)
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
        # Delete the message row (the FTS trigger drops its index entry) AND the
        # dependent rows that carry copies of the content: a pin holds the pinned
        # text, an edit holds prev+new content, and receipts hold reader state.
        # Leaving those behind let a disappeared pinned/edited message be
        # recovered, so purge them in the same transaction.
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
            conn.execute("DELETE FROM pins WHERE message_id = ?", (message_id,))
            conn.execute("DELETE FROM message_edits WHERE message_id = ?", (message_id,))
            conn.execute("DELETE FROM message_receipts WHERE message_id = ?", (message_id,))
            conn.execute("DELETE FROM message_files WHERE message_id = ?", (message_id,))

    # Room file attachments (the message_files table). The per-file cap is the
    # room send limit (_gateway_files.ROOM_FILE_BYTES); the per-thread cap
    # matches the message byte quota.
    MAX_MESSAGE_FILE_BYTES = 2 * 1024 * 1024
    MAX_THREAD_FILE_BYTES = 50 * 1024 * 1024

    def save_message_file(
        self, message_id: str, thread_id: str, filename: str, mime_type: str,
        data: bytes, width: Optional[int] = None, height: Optional[int] = None,
        spoiler: bool = False,
    ) -> bool:
        """Keep a room file's bytes with its message. False when refused (too
        large, thread over quota, or no such message in that thread)."""
        if not data or len(data) > self.MAX_MESSAGE_FILE_BYTES:
            return False
        with self._conn() as conn:
            if not conn.execute(
                "SELECT 1 FROM messages WHERE message_id = ? AND thread_id = ?",
                (message_id, thread_id),
            ).fetchone():
                return False
            used = conn.execute(
                "SELECT COALESCE(SUM(size), 0) FROM message_files WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()[0]
            if used + len(data) > self.MAX_THREAD_FILE_BYTES:
                logger.warning("File quota exceeded for thread %s", thread_id)
                return False
            conn.execute(
                "INSERT OR IGNORE INTO message_files (message_id, thread_id, filename,"
                " mime_type, size, width, height, spoiler, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, thread_id, filename, mime_type, len(data),
                 width, height, 1 if spoiler else 0, sqlite3.Binary(data)),
            )
        return True

    @staticmethod
    def _file_meta(row) -> dict:
        meta = {"filename": row["filename"], "mime_type": row["mime_type"],
                "size": row["size"]}
        if row["width"] and row["height"]:
            meta["width"], meta["height"] = row["width"], row["height"]
        if row["spoiler"]:
            meta["spoiler"] = True
        return meta

    def get_message_file_meta(self, thread_id: str, message_ids: list) -> dict:
        """{message_id: metadata} for the stored files among *message_ids* (no bytes)."""
        ids = [m for m in message_ids if m]
        out: dict = {}
        with self._conn() as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows = conn.execute(
                    "SELECT message_id, filename, mime_type, size, width, height, spoiler"
                    " FROM message_files WHERE thread_id = ? AND message_id IN"
                    f" ({','.join('?' * len(chunk))})",
                    [thread_id, *chunk],
                ).fetchall()
                for r in rows:
                    out[r["message_id"]] = self._file_meta(r)
        return out

    def get_message_file(self, message_id: str) -> Optional[dict]:
        """Stored file for a message: metadata plus thread_id and the raw bytes."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM message_files WHERE message_id = ?", (message_id,)
            ).fetchone()
        if not row:
            return None
        return {**self._file_meta(row), "thread_id": row["thread_id"],
                "data": bytes(row["data"])}

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
    def count_unread_by_thread(
        self, webid: str, thread_ids: list[str], last_reads: dict[str, float], cap: int = 100,
    ) -> dict[str, int]:
        """{thread_id: messages from others newer than webid's read marker}, one connection.

        Capped at *cap* per thread (the client shows 99+). Lets the connect
        snapshot carry unread counts, so the client no longer fetches every
        thread's history just to count them.
        """
        from datetime import datetime, timezone as _tz
        out: dict[str, int] = {}
        if not thread_ids:
            return out
        with self._conn() as conn:
            for tid in thread_ids:
                iso = datetime.fromtimestamp(float(last_reads.get(tid, 0) or 0), tz=_tz.utc).isoformat()
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM (SELECT 1 FROM messages WHERE thread_id = ? "
                        "AND timestamp > ? AND from_webid != ? LIMIT ?)",
                        (tid, iso, webid or "", cap),
                    ).fetchone()
                    out[tid] = int(row[0]) if row else 0
                except Exception:
                    out[tid] = 0
        return out
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
        with self._conn() as conn:
            # The pins table has no UNIQUE(thread_id, message_id) and the pin_id is
            # time-based, so re-pinning the same message would mint a new row each
            # time. Re-use the existing pin instead of growing the table.
            existing = conn.execute(
                "SELECT pin_id FROM pins WHERE thread_id = ? AND message_id = ?",
                (thread_id, message_id),
            ).fetchone()
            if existing:
                return existing[0]
            # Unique per pin: a bare millisecond stamp let two pins in the same
            # millisecond share an id, and INSERT OR IGNORE dropped the second.
            pin_id = f"pin-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
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
    def count_pending_scheduled(self, from_webid: str) -> int:
        """Count a user's pending (uncancelled) scheduled messages, for per-user cap."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM scheduled_messages WHERE from_webid=? AND cancelled=0",
                (from_webid,),
            ).fetchone()
        return row[0] if row else 0
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
        query:       user search text, matched literally (see fts_literal_query).
        member_thread_ids: restrict to these thread IDs (ignored if thread_id
                     given). An empty list matches nothing; None is unscoped.
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
        match = fts_literal_query(query)
        if not match:
            return []
        # An explicit but empty scope means the caller belongs to no thread:
        # match nothing. Only None (internal callers) searches every thread.
        if not thread_id and member_thread_ids is not None and not member_thread_ids:
            return []

        # Sealed E2E rows are ciphertext: any match against them is noise.
        conditions = ["messages_fts MATCH ?",
                      f"COALESCE(m.content_type, 'text') != '{E2E_CONTENT_TYPE}'"]
        params: list = [match]

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
                # External-content table: 'rebuild' discards the index and
                # rereads messages. A plain DELETE would look up the current
                # content and leave stale tokens in place.
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
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
