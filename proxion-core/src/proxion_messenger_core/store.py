"""Proxion Coordination Store — in-memory reference implementation.

The Coordination Store (spec §7) is an honest-but-curious asynchronous
mailbox.  The store:

* forwards opaque :class:`~proxion_messenger_core.sealed.SealedEnvelope` blobs between
  agents;
* enforces per-mailbox message-count and byte-size quotas;
* expires messages after a configurable TTL;
* **never** sees plaintext — decryption happens on the recipient's device.

This module provides:

* :class:`StoreConfig` — tunable limits.
* :class:`StoredMessage` — a message with metadata.
* :class:`MemoryStore` — the reference in-memory implementation.

Replacing the backend
---------------------
Any EI can swap :class:`MemoryStore` for a different backend (Redis, SQLite,
an HTTP relay) by reimplementing the same ``put`` / ``take_all`` / ``info`` /
``expire`` interface.  The sealed-box layer in :mod:`sealed` is transport-
agnostic and does not change.

Thread safety
-------------
:class:`MemoryStore` uses a ``threading.Lock`` and is safe for concurrent use
within a single process.  Cross-process or distributed deployments need a
persistent backend.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .errors import ProxionError
from .sealed import SealedEnvelope


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class StoreError(ProxionError):
    """Raised when the store rejects an operation."""


class QuotaExceededError(StoreError):
    """Raised when a mailbox has reached its message-count or byte-size quota."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoreConfig:
    """Per-store limits applied uniformly across all mailboxes.

    Attributes
    ----------
    max_messages:
        Maximum number of pending messages per mailbox.
    max_bytes:
        Maximum total ciphertext bytes per mailbox.
    max_bytes_per_mailbox:
        Maximum total ciphertext bytes per individual mailbox.
        Set to ``None`` to disable per-mailbox byte limits (default).
    message_ttl:
        Seconds after which a message is silently dropped on retrieval.
        Set to ``None`` to disable TTL expiry (not recommended for production).
    """

    max_messages: int = 200
    max_bytes: int = 4 * 1024 * 1024   # 4 MB per mailbox
    max_bytes_per_mailbox: Optional[int] = None
    message_ttl: Optional[float] = 7 * 24 * 3600   # 7 days
    max_mailboxes: int = 10_000


# ---------------------------------------------------------------------------
# Stored message
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoredMessage:
    """A sealed envelope sitting in a mailbox, plus delivery metadata.

    Attributes
    ----------
    message_id:
        Randomly generated identifier — useful for deduplication if a sender
        retries a delivery.
    envelope:
        The opaque sealed message.  The store never decrypts this.
    posted_at:
        Unix timestamp (float) when the message was accepted by the store.
    expires_at:
        Optional Unix timestamp (float) for per-message TTL expiry.
        If None, the store's global TTL applies.
    """

    message_id: str
    envelope: SealedEnvelope
    posted_at: float
    expires_at: Optional[float] = None


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """In-memory Coordination Store — the EI0 reference implementation.

    Usage
    -----
    ::

        store = MemoryStore()

        # Sender seals a message and posts it
        envelope = seal_json(payload, recipient_pub_bytes)
        store.put(mailbox_id_for(recipient_pub_bytes), envelope)

        # Recipient drains their mailbox and decrypts each message
        msgs = store.take_all(mailbox_id)
        for sm in msgs:
            data = open_sealed_json(sm.envelope, my_x25519_priv)
    """

    def __init__(self, config: Optional[StoreConfig] = None) -> None:
        self._config = config or StoreConfig()
        self._lock = threading.Lock()
        # mailbox_id -> list[StoredMessage]  (ordered by insertion)
        self._mailboxes: Dict[str, List[StoredMessage]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def put(self, mailbox_id: str, envelope: SealedEnvelope, ttl_seconds: Optional[int] = None) -> str:
        """Post a sealed message to a mailbox.

        Parameters
        ----------
        mailbox_id:
            The recipient's opaque mailbox address (from
            :func:`~proxion_messenger_core.sealed.mailbox_id_for`).
        envelope:
            The sealed message.  The store does not inspect the contents.
        ttl_seconds:
            Optional per-message TTL in seconds. If provided, this message
            will expire at (now + ttl_seconds) instead of using the global TTL.

        Returns
        -------
        str
            The assigned ``message_id``.

        Raises
        ------
        QuotaExceededError
            If the mailbox would exceed ``max_messages`` or ``max_bytes`` or ``max_bytes_per_mailbox``.
        """
        cfg = self._config
        with self._lock:
            # Global mailbox-count cap — prevents OOM DoS via mailbox explosion
            if mailbox_id not in self._mailboxes and len(self._mailboxes) >= cfg.max_mailboxes:
                raise QuotaExceededError(
                    f"store has reached the {cfg.max_mailboxes}-mailbox limit"
                )
            box = self._mailboxes.setdefault(mailbox_id, [])

            # Quota checks
            if len(box) >= cfg.max_messages:
                raise QuotaExceededError(
                    f"mailbox {mailbox_id[:8]}… has reached the "
                    f"{cfg.max_messages}-message limit"
                )
            current_bytes = sum(m.envelope.byte_size for m in box)
            if current_bytes + envelope.byte_size > cfg.max_bytes:
                raise QuotaExceededError(
                    f"mailbox {mailbox_id[:8]}… would exceed the "
                    f"{cfg.max_bytes // 1024} KB byte quota"
                )
            # Per-mailbox byte quota check
            if cfg.max_bytes_per_mailbox is not None:
                if current_bytes + envelope.byte_size > cfg.max_bytes_per_mailbox:
                    raise QuotaExceededError(
                        f"mailbox {mailbox_id[:8]}… would exceed the "
                        f"{cfg.max_bytes_per_mailbox // 1024} KB byte quota"
                    )

            msg_id = secrets.token_urlsafe(16)
            posted_at = time.time()
            expires_at: Optional[float] = None
            if ttl_seconds is not None:
                expires_at = posted_at + ttl_seconds
            box.append(
                StoredMessage(
                    message_id=msg_id,
                    envelope=envelope,
                    posted_at=posted_at,
                    expires_at=expires_at,
                )
            )
            return msg_id

    # ------------------------------------------------------------------
    # Read / drain
    # ------------------------------------------------------------------

    def _is_expired(self, msg: StoredMessage, now: float) -> bool:
        """Check if a message has expired based on per-message or global TTL."""
        if msg.expires_at is not None:
            return now >= msg.expires_at
        ttl = self._config.message_ttl
        if ttl is not None:
            return (now - msg.posted_at) > ttl
        return False

    def list_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Return all non-expired messages **without** removing them.

        Use this together with :meth:`take_by_ids` to implement selective
        draining: decrypt all messages, process the ones your layer understands,
        then call :meth:`take_by_ids` with only those message IDs.  Messages
        belonging to other protocol layers are left in the mailbox for the
        next receiver to process.

        This is the correct primitive for mixed-type mailboxes — all protocol
        message types share a single mailbox per agent, so each layer must
        consume only its own messages.

        Returns
        -------
        list[StoredMessage]
            Oldest-first snapshot; still sealed.  Does **not** advance any
            internal cursor — calling this twice returns the same list.
        """
        now = time.time()
        with self._lock:
            box = list(self._mailboxes.get(mailbox_id, []))
        box = [m for m in box if not self._is_expired(m, now)]
        return box

    def take_by_ids(self, mailbox_id: str, message_ids: set) -> List[StoredMessage]:
        """Remove and return messages whose ``message_id`` is in *message_ids*.

        Used in conjunction with :meth:`list_all`: after decrypting a batch of
        messages with :meth:`list_all`, pass the IDs of those you successfully
        processed to this method to remove them from the mailbox.

        Messages whose IDs are not in *message_ids* remain in the mailbox
        untouched, available for other receive functions.

        Parameters
        ----------
        mailbox_id:
            The target mailbox address.
        message_ids:
            A set of ``message_id`` strings to remove.

        Returns
        -------
        list[StoredMessage]
            The messages that were removed (same order as stored).
        """
        now = time.time()
        with self._lock:
            box = self._mailboxes.get(mailbox_id, [])
            kept: List[StoredMessage] = []
            removed: List[StoredMessage] = []
            for m in box:
                expired = self._is_expired(m, now)
                if expired or m.message_id in message_ids:
                    removed.append(m)
                else:
                    kept.append(m)
            if kept:
                self._mailboxes[mailbox_id] = kept
            elif mailbox_id in self._mailboxes:
                del self._mailboxes[mailbox_id]
        return removed

    def take_all(self, mailbox_id: str) -> List[StoredMessage]:
        """Retrieve and remove all non-expired messages from a mailbox.

        Expired messages (beyond ``message_ttl`` or per-message ``expires_at``)
        are dropped silently.
        The returned messages are **still sealed** — the caller must call
        :func:`~proxion_messenger_core.sealed.open_sealed` to decrypt each one.

        Parameters
        ----------
        mailbox_id:
            The recipient's opaque mailbox address.

        Returns
        -------
        list[StoredMessage]
            Oldest-first list of pending messages (may be empty).
        """
        now = time.time()

        with self._lock:
            box = self._mailboxes.pop(mailbox_id, [])

        box = [m for m in box if not self._is_expired(m, now)]

        return box

    def peek(self, mailbox_id: str) -> dict:
        """Return summary metadata without removing messages.

        The store operator can call this for monitoring without touching the
        sealed content.

        Returns
        -------
        dict
            ``{"count": int, "bytes": int, "oldest_age_s": float | None}``
        """
        now = time.time()
        with self._lock:
            box = list(self._mailboxes.get(mailbox_id, []))

        if not box:
            return {"count": 0, "bytes": 0, "oldest_age_s": None}

        return {
            "count": len(box),
            "bytes": sum(m.envelope.byte_size for m in box),
            "oldest_age_s": now - box[0].posted_at,
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def expire(self) -> int:
        """Sweep all mailboxes and evict expired messages (global or per-message TTL).

        Returns the number of messages removed.  Call this periodically from a
        background thread or scheduler.
        """
        now = time.time()
        removed = 0

        with self._lock:
            for mailbox_id in list(self._mailboxes):
                before = self._mailboxes[mailbox_id]
                after = [m for m in before if not self._is_expired(m, now)]
                removed += len(before) - len(after)
                if after:
                    self._mailboxes[mailbox_id] = after
                else:
                    del self._mailboxes[mailbox_id]

        return removed

    def mailbox_count(self) -> int:
        """Total number of non-empty mailboxes (for monitoring)."""
        with self._lock:
            return len(self._mailboxes)

    def global_stats(self) -> dict:
        """Return aggregate statistics for the entire store."""
        with self._lock:
            mailbox_count = len(self._mailboxes)
            total_messages = sum(len(v) for v in self._mailboxes.values())
            total_bytes = sum(
                m.envelope.byte_size
                for v in self._mailboxes.values()
                for m in v
            )
        return {
            "mailbox_count": mailbox_count,
            "total_messages": total_messages,
            "total_bytes": total_bytes,
        }
