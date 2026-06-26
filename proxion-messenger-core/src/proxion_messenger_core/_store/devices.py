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




class DeviceStoreMixin(object):
    def save_sender_key(
        self,
        room_id: str,
        sender_webid: str,
        chain_key_b64: str,
        iteration: int,
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sender_keys
                   (room_id, sender_webid, chain_key_b64, iteration, created_at, updated_at)
                   VALUES (?, ?, ?, ?, COALESCE(
                       (SELECT created_at FROM sender_keys WHERE room_id=? AND sender_webid=?), ?
                   ), ?)""",
                (room_id, sender_webid, chain_key_b64, iteration, room_id, sender_webid, now, now),
            )
    def get_sender_key(self, room_id: str, sender_webid: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM sender_keys WHERE room_id=? AND sender_webid=?",
                    (room_id, sender_webid),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def bump_sender_key_epoch(self, room_id: str, sender_webid: str) -> int:
        """Increment epoch for a sender key row. Returns new epoch value."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE sender_keys SET epoch = epoch + 1 WHERE room_id=? AND sender_webid=?",
                    (room_id, sender_webid),
                )
                row = conn.execute(
                    "SELECT epoch FROM sender_keys WHERE room_id=? AND sender_webid=?",
                    (room_id, sender_webid),
                ).fetchone()
                return row["epoch"] if row else 1
            except Exception:
                return 1
    def get_sender_key_epoch(self, room_id: str, sender_webid: str) -> int:
        """Return current epoch for a sender key row (default 1)."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT epoch FROM sender_keys WHERE room_id=? AND sender_webid=?",
                    (room_id, sender_webid),
                ).fetchone()
                return row["epoch"] if row else 1
            except Exception:
                return 1
    def get_catchup_watermark(
        self, owner_webid: str, owner_device_id: str, thread_id: str
    ) -> int:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT last_seq FROM catchup_watermarks
                       WHERE owner_webid=? AND owner_device_id=? AND thread_id=?""",
                    (owner_webid, owner_device_id, thread_id),
                ).fetchone()
                return row["last_seq"] if row else 0
            except Exception:
                return 0
    def set_catchup_watermark(
        self,
        owner_webid: str,
        owner_device_id: str,
        thread_id: str,
        last_seq: int,
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO catchup_watermarks
                       (owner_webid, owner_device_id, thread_id, last_seq, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(owner_webid, owner_device_id, thread_id)
                       DO UPDATE SET last_seq=excluded.last_seq, updated_at=excluded.updated_at""",
                    (owner_webid, owner_device_id, thread_id, last_seq, time.time()),
                )
            except Exception:
                pass
    def set_device_primary(self, device_id: str, owner_webid: str) -> None:
        """Mark device_id as primary; clear is_primary on all other devices for owner."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE device_registrations SET is_primary=0 WHERE owner_webid=?",
                    (owner_webid,),
                )
                conn.execute(
                    "UPDATE device_registrations SET is_primary=1 WHERE device_id=? AND owner_webid=?",
                    (device_id, owner_webid),
                )
            except Exception:
                pass
    def save_device_recovery_code(
        self, code_id: str, owner_webid: str, code_hash: str
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO device_recovery_codes
                       (code_id, owner_webid, code_hash, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (code_id, owner_webid, code_hash, time.time()),
                )
            except Exception:
                pass
    def use_device_recovery_code(self, code_id: str) -> bool:
        """Mark recovery code as used. Returns False if not found or already used."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT used_at FROM device_recovery_codes WHERE code_id=?",
                    (code_id,),
                ).fetchone()
                if row is None or row["used_at"] is not None:
                    return False
                conn.execute(
                    "UPDATE device_recovery_codes SET used_at=? WHERE code_id=?",
                    (time.time(), code_id),
                )
                return True
            except Exception:
                return False
    def get_device_recovery_code(self, code_id: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM device_recovery_codes WHERE code_id=?", (code_id,)
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def save_push_subscription(
        self,
        subscription_id: str,
        owner_webid: str,
        endpoint: str,
        p256dh_b64: str,
        auth_b64: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO push_subscriptions
                   (subscription_id, owner_webid, endpoint, p256dh_b64, auth_b64, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (subscription_id, owner_webid, endpoint, p256dh_b64, auth_b64, time.time()),
            )
    def get_push_subscriptions(self, owner_webid: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM push_subscriptions WHERE owner_webid=?",
                    (owner_webid,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def delete_push_subscription(self, subscription_id: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM push_subscriptions WHERE subscription_id=?",
                    (subscription_id,),
                )
            except Exception:
                pass
    def register_device(
        self,
        device_id: str,
        owner_webid: str,
        device_pub_b64: str,
        attestation_b64: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO device_registrations
                   (device_id, owner_webid, device_pub_b64, attestation_b64, created_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (device_id, owner_webid, device_pub_b64, attestation_b64, time.time(), time.time()),
            )
    def get_device(self, device_id: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM device_registrations WHERE device_id=?",
                    (device_id,),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def list_devices(self, owner_webid: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM device_registrations WHERE owner_webid=? ORDER BY created_at ASC",
                    (owner_webid,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def unregister_device(self, device_id: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM device_registrations WHERE device_id=?",
                    (device_id,),
                )
            except Exception:
                pass
    def touch_device(self, device_id: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE device_registrations SET last_seen_at=? WHERE device_id=?",
                    (time.time(), device_id),
                )
            except Exception:
                pass
