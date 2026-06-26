"""SQLite-backed Coordination Store — durable drop-in for MemoryStore.

:class:`SqliteStore` implements the same interface as
:class:`~proxion_messenger_core.store.MemoryStore` but persists messages to a SQLite
database file, so they survive server restarts.

Schema
------
A single table ``messages`` holds every pending message::

    CREATE TABLE messages (
        message_id  TEXT PRIMARY KEY,
        mailbox_id  TEXT NOT NULL,
        envelope_json TEXT NOT NULL,   -- SealedEnvelope.to_dict() as JSON
        posted_at   REAL NOT NULL      -- Unix timestamp (float)
    );
    CREATE INDEX idx_mailbox ON messages (mailbox_id, posted_at);

Quota and TTL semantics are identical to :class:`~proxion_messenger_core.store.MemoryStore`:
quota is checked on every ``put``; TTL-expired messages are silently skipped
on reads and removed by :meth:`expire`.

Thread safety
-------------
SQLite in WAL mode with ``check_same_thread=False`` is safe for concurrent
reads from many threads and a single writer at a time.  A ``threading.Lock``
serialises all writes (``put``, ``take_all``, ``take_by_ids``, ``expire``).

Usage
-----
::

    from proxion_messenger_core.store_sqlite import SqliteStore
    from proxion_messenger_core.store import StoreConfig
    from proxion_messenger_core.store_server import build_app

    store = SqliteStore("proxion-store.db")
    app = build_app(store=store)   # transparent — same interface as MemoryStore

Pass ``":memory:"`` for an in-process ephemeral database (useful for testing).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from typing import List, Optional, Set

from .errors import ProxionError
from .sealed import SealedEnvelope
from .store import QuotaExceededError, StoreConfig, StoredMessage


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    message_id    TEXT PRIMARY KEY,
    mailbox_id    TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    posted_at     REAL NOT NULL,
    expires_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_mailbox ON messages (mailbox_id, posted_at);
"""


# ---------------------------------------------------------------------------
# SqliteStore
# ---------------------------------------------------------------------------

class SqliteStore:
    """Durable Coordination Store backed by a SQLite database.

    Parameters
    ----------
    db_path:
        File path for the SQLite database, or ``":memory:"`` for a temporary
        in-process database.
    config:
        Per-store limits (quota, TTL).  Defaults to the same values as
        :class:`~proxion_messenger_core.store.MemoryStore`.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        config: Optional[StoreConfig] = None,
    ) -> None:
        self._config = config or StoreConfig()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions explicitly
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_DDL)
        # Migrate: add expires_at column if it doesn't exist
        try:
            self._conn.execute("ALTER TABLE messages ADD COLUMN expires_at REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_stored(self, row: tuple) -> StoredMessage:
        message_id, _, envelope_json, posted_at = row[:4]
        expires_at = row[4] if len(row) > 4 else None
        envelope = SealedEnvelope.from_dict(json.loads(envelope_json))
        return StoredMessage(
            message_id=message_id,
            envelope=envelope,
            posted_at=posted_at,
            expires_at=expires_at,
        )

    def _live_rows(self, mailbox_id: str, cursor: sqlite3.Cursor) -> List[tuple]:
        """Return all non-expired rows for *mailbox_id*, oldest first.

        Respects both per-message expires_at and global message_ttl.
        """
        cursor.execute(
            "SELECT message_id, mailbox_id, envelope_json, posted_at, expires_at "
            "FROM messages WHERE mailbox_id = ? ORDER BY posted_at ASC",
            (mailbox_id,),
        )
        rows = cursor.fetchall()
        now = time.time()
        ttl = self._config.message_ttl

        live = []
        for r in rows:
            expires_at = r[4]
            if expires_at is not None:
                # Per-message TTL
                if now >= expires_at:
                    continue
            elif ttl is not None:
                # Global TTL
                posted_at = r[3]
                if now - posted_at > ttl:
                    continue
            live.append(r)
        return live

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def put(self, mailbox_id: str, envelope: SealedEnvelope, ttl_seconds: Optional[int] = None) -> str:
        """Post a sealed envelope to a mailbox.

        Parameters
        ----------
        mailbox_id:
            The recipient's mailbox ID.
        envelope:
            The sealed message.
        ttl_seconds:
            Optional per-message TTL in seconds.

        Raises
        ------
        QuotaExceededError
            If the mailbox would exceed ``max_messages`` or ``max_bytes`` or ``max_bytes_per_mailbox``.
        """
        cfg = self._config
        msg_id = secrets.token_urlsafe(16)
        envelope_json = json.dumps(envelope.to_dict())
        posted_at = time.time()
        expires_at = None
        if ttl_seconds is not None:
            expires_at = posted_at + ttl_seconds

        with self._lock:
            cur = self._conn.cursor()
            with self._conn:   # transaction
                live = self._live_rows(mailbox_id, cur)
                if len(live) >= cfg.max_messages:
                    raise QuotaExceededError(
                        f"mailbox {mailbox_id[:8]}… has reached the "
                        f"{cfg.max_messages}-message limit"
                    )
                current_bytes = sum(
                    len(r[2].encode("utf-8")) for r in live   # envelope_json bytes
                )
                new_bytes = len(envelope_json.encode("utf-8"))
                if current_bytes + new_bytes > cfg.max_bytes:
                    raise QuotaExceededError(
                        f"mailbox {mailbox_id[:8]}… would exceed the "
                        f"{cfg.max_bytes // 1024} KB byte quota"
                    )
                # Per-mailbox byte quota check
                if cfg.max_bytes_per_mailbox is not None:
                    if current_bytes + new_bytes > cfg.max_bytes_per_mailbox:
                        raise QuotaExceededError(
                            f"mailbox {mailbox_id[:8]}… would exceed the "
                            f"{cfg.max_bytes_per_mailbox // 1024} KB byte quota"
                        )
                cur.execute(
                    "INSERT INTO messages (message_id, mailbox_id, envelope_json, posted_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (msg_id, mailbox_id, envelope_json, posted_at, expires_at),
                )
        return msg_id

    # ------------------------------------------------------------------
    # Read / drain
    # ------------------------------------------------------------------

    def list_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Return all non-expired messages without removing them."""
        cur = self._conn.cursor()
        live = self._live_rows(mailbox_id, cur)
        return [self._row_to_stored(r) for r in live]

    def take_by_ids(self, mailbox_id: str, message_ids: Set[str]) -> List[StoredMessage]:
        """Remove and return messages whose ``message_id`` is in *message_ids*."""
        if not message_ids:
            return []

        with self._lock:
            cur = self._conn.cursor()
            with self._conn:
                live = self._live_rows(mailbox_id, cur)
                ttl = self._config.message_ttl
                now = time.time()
                # Purge any globally expired rows
                if ttl is not None:
                    cutoff = time.time() - ttl
                    cur.execute(
                        "DELETE FROM messages WHERE mailbox_id = ? AND posted_at < ?",
                        (mailbox_id, cutoff),
                    )
                # Purge any per-message expired rows
                cur.execute(
                    "DELETE FROM messages WHERE mailbox_id = ? AND expires_at IS NOT NULL AND expires_at <= ?",
                    (mailbox_id, now),
                )
                # Delete the requested IDs
                placeholders = ",".join("?" * len(message_ids))
                cur.execute(
                    f"DELETE FROM messages WHERE mailbox_id = ? AND message_id IN ({placeholders})",
                    (mailbox_id, *message_ids),
                )

        # Return the subset of live rows that matched
        return [self._row_to_stored(r) for r in live if r[0] in message_ids]

    def take_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Retrieve and remove all non-expired messages from a mailbox."""
        with self._lock:
            cur = self._conn.cursor()
            with self._conn:
                live = self._live_rows(mailbox_id, cur)
                # Delete all rows for this mailbox (including expired ones)
                cur.execute(
                    "DELETE FROM messages WHERE mailbox_id = ?",
                    (mailbox_id,),
                )
        return [self._row_to_stored(r) for r in live]

    def peek(self, mailbox_id: str) -> dict:
        """Return summary metadata without removing messages."""
        cur = self._conn.cursor()
        live = self._live_rows(mailbox_id, cur)
        if not live:
            return {"count": 0, "bytes": 0, "oldest_age_s": None}
        total_bytes = sum(len(r[2].encode("utf-8")) for r in live)
        oldest_age = time.time() - live[0][3]
        return {"count": len(live), "bytes": total_bytes, "oldest_age_s": oldest_age}

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def expire(self) -> int:
        """Delete all expired messages (global or per-message TTL).  Returns count removed."""
        now = time.time()
        with self._lock:
            with self._conn:
                removed = 0
                # Delete per-message expired
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (now,)
                )
                removed += cur.rowcount
                # Delete globally expired
                ttl = self._config.message_ttl
                if ttl is not None:
                    cutoff = now - ttl
                    cur = self._conn.execute(
                        "DELETE FROM messages WHERE expires_at IS NULL AND posted_at < ?",
                        (cutoff,)
                    )
                    removed += cur.rowcount
                return removed

    def mailbox_count(self) -> int:
        """Total number of distinct non-empty mailboxes."""
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT mailbox_id) FROM messages"
        )
        return cur.fetchone()[0]

    def global_stats(self) -> dict:
        """Return aggregate statistics for the entire store."""
        cur = self._conn.cursor()
        # Count distinct mailboxes
        mailbox_count_result = cur.execute(
            "SELECT COUNT(DISTINCT mailbox_id) FROM messages"
        ).fetchone()
        mailbox_count = mailbox_count_result[0] if mailbox_count_result else 0

        # Count total messages
        total_msgs_result = cur.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()
        total_messages = total_msgs_result[0] if total_msgs_result else 0

        # Sum total bytes
        total_bytes_result = cur.execute(
            "SELECT SUM(LENGTH(envelope_json)) FROM messages"
        ).fetchone()
        total_bytes = total_bytes_result[0] if total_bytes_result and total_bytes_result[0] else 0

        return {
            "mailbox_count": mailbox_count,
            "total_messages": total_messages,
            "total_bytes": total_bytes,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()
