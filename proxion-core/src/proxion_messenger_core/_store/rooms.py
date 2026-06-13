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




class RoomStoreMixin(object):
    def save_room(
        self,
        room_id: str,
        name: str,
        code: str,
        invite_url: str,
        history_mode: str,
        creator_webid: str = "",
    ) -> None:
        name = (name or "")[:64]
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rooms
                    (room_id, name, code, invite_url, history_mode, creator_webid, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (room_id, name, code, invite_url, history_mode, creator_webid, time.time()),
            )
    def get_all_rooms(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rooms ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]
    def add_room_member(self, room_id: str, webid: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO room_members (room_id, webid) VALUES (?, ?)",
                (room_id, webid),
            )
    def get_room_members(self, room_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT webid FROM room_members WHERE room_id = ?", (room_id,)
            ).fetchall()
            return [r["webid"] for r in rows]
    def add_federated_room_member(self, room_id: str, member_did: str, gateway_url: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO room_federated_members (room_id, member_did, gateway_url) VALUES (?,?,?)",
                (room_id, member_did, gateway_url),
            )
    def remove_federated_room_member(self, room_id: str, member_did: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM room_federated_members WHERE room_id=? AND member_did=?",
                (room_id, member_did),
            )
    def get_federated_room_members(self, room_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT member_did, gateway_url FROM room_federated_members WHERE room_id=?",
                (room_id,),
            ).fetchall()
        return [{"member_did": r[0], "gateway_url": r[1]} for r in rows]
    def get_rooms_for_member(self, webid: str) -> list[str]:
        """Return all room_ids that this webid is a member of."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT room_id FROM room_members WHERE webid = ?", (webid,)
            ).fetchall()
            return [r["room_id"] for r in rows]
    def create_room_invite(
        self,
        invite_id: str,
        room_id: str,
        code_hash: str,
        uses_left: int = 1,
        expires_at: Optional[float] = None,
    ) -> None:
        """Store a new invite with only its HMAC-SHA256 hash — never the raw code."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO room_invites
                   (invite_id, room_id, code_hash, uses_left, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (invite_id, room_id, code_hash, uses_left, time.time(), expires_at),
            )
    def consume_room_invite(self, code_hash: str) -> Optional[str]:
        """Atomically decrement uses_left for *code_hash*; return room_id or None."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT invite_id, room_id, uses_left, expires_at
                   FROM room_invites WHERE code_hash = ? AND uses_left > 0""",
                (code_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] is not None and time.time() > row["expires_at"]:
                return None
            conn.execute(
                "UPDATE room_invites SET uses_left = uses_left - 1 WHERE invite_id = ?",
                (row["invite_id"],),
            )
            return row["room_id"]
    def rotate_webhook_token(self, wh_id: str, owner_webid: str) -> Optional[str]:
        """Rotate webhook token; returns new token or None if not found/not owner."""
        import secrets as _secrets
        new_token = _secrets.token_urlsafe(32)
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT token FROM webhooks WHERE id = ? AND owner_webid = ? AND active = 1",
                    (wh_id, owner_webid),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    """UPDATE webhooks
                       SET previous_token = token, token = ?, rotated_at = ?
                       WHERE id = ? AND owner_webid = ?""",
                    (new_token, time.time(), wh_id, owner_webid),
                )
                return new_token
            except Exception:
                return None
    def get_webhook_by_token_with_rotation(
        self, token: str, allow_previous_within_seconds: int = 300
    ) -> Optional[dict]:
        """Return webhook matching token (current or recent previous) using constant-time logic."""
        import hmac as _hmac
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM webhooks WHERE active = 1"
                ).fetchall()
            except Exception:
                return None
        cutoff = time.time() - allow_previous_within_seconds
        token_b = token.encode()
        for row in rows:
            r = dict(row)
            if _hmac.compare_digest(r.get("token", "").encode(), token_b):
                return r
            prev = r.get("previous_token") or ""
            rotated_at = r.get("rotated_at") or 0
            if prev and _hmac.compare_digest(prev.encode(), token_b) and rotated_at > cutoff:
                return r
        return None
    def set_room_disappear_timer(self, room_id: str, ms: int) -> None:
        """Set the disappear_after_ms timer for a room."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE rooms SET disappear_after_ms = ? WHERE room_id = ?",
                (ms, room_id)
            )
    def get_room_disappear_timer(self, room_id: str) -> int:
        """Get the disappear_after_ms timer for a room (0 = disabled)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT disappear_after_ms FROM rooms WHERE room_id = ?", (room_id,)
            ).fetchone()
            return row["disappear_after_ms"] if row else 0
    def remove_room_member(self, room_id: str, webid: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM room_members WHERE room_id = ? AND webid = ?",
                (room_id, webid),
            )
    def update_room_creator(self, room_id: str, new_creator_webid: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE rooms SET creator_webid = ? WHERE room_id = ?",
                (new_creator_webid, room_id),
            )
    def delete_room(self, room_id: str) -> None:
        """Delete a room and all associated data (members, messages, reactions, pins)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM pins WHERE thread_id = ?", (room_id,))
            conn.execute("DELETE FROM reactions WHERE room_id = ?", (room_id,))
            conn.execute("DELETE FROM messages WHERE thread_id = ?", (room_id,))
            conn.execute("DELETE FROM room_members WHERE room_id = ?", (room_id,))
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
    def ban_room_member(self, room_id: str, banned_did: str, banned_by: str, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO room_bans (room_id, banned_did, banned_by, reason) VALUES (?,?,?,?)",
                (room_id, banned_did, banned_by, reason),
            )
    def unban_room_member(self, room_id: str, banned_did: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM room_bans WHERE room_id=? AND banned_did=?", (room_id, banned_did))
    def get_room_bans(self, room_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT banned_did, banned_by, banned_at, reason FROM room_bans WHERE room_id=? ORDER BY banned_at DESC",
                (room_id,),
            ).fetchall()
        return [{"banned_did": r[0], "banned_by": r[1], "banned_at": r[2], "reason": r[3] or ""} for r in rows]
    def is_room_banned(self, room_id: str, did: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM room_bans WHERE room_id=? AND banned_did=?", (room_id, did)
            ).fetchone()
        return bool(row)
    def mute_room_member(self, room_id: str, muted_did: str, muted_by: str, expires_at: float | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO room_mutes (room_id, muted_did, muted_by, expires_at) VALUES (?,?,?,?)",
                (room_id, muted_did, muted_by, expires_at),
            )
    def unmute_room_member(self, room_id: str, muted_did: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM room_mutes WHERE room_id=? AND muted_did=?", (room_id, muted_did))
    def is_room_muted(self, room_id: str, did: str) -> bool:
        import time as _t
        with self._conn() as conn:
            row = conn.execute(
                "SELECT expires_at FROM room_mutes WHERE room_id=? AND muted_did=?", (room_id, did)
            ).fetchone()
        if not row:
            return False
        expires_at = row[0]
        if expires_at is not None and _t.time() > expires_at:
            return False
        return True
    def mark_room_read(
        self, room_id: str, member_webid: str, message_id: str, read_at: str,
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO room_read_receipts
                       (room_id, member_webid, last_read_message_id, last_read_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(room_id, member_webid) DO UPDATE SET
                           last_read_message_id = excluded.last_read_message_id,
                           last_read_at = excluded.last_read_at""",
                    (room_id, member_webid, message_id, read_at),
                )
                conn.execute(
                    "UPDATE room_unread_counts SET unread = 0 WHERE room_id = ? AND webid = ?",
                    (room_id, member_webid),
                )
            except Exception:
                pass
    def get_room_last_read(self, room_id: str, member_webid: str) -> Optional[dict]:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM room_read_receipts WHERE room_id = ? AND member_webid = ?",
                    (room_id, member_webid),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def get_room_unread_count(self, room_id: str, webid: str = "") -> int:
        with self._conn() as conn:
            try:
                if webid:
                    row = conn.execute(
                        "SELECT unread FROM room_unread_counts WHERE room_id = ? AND webid = ?",
                        (room_id, webid),
                    ).fetchone()
                    return int(row["unread"]) if row else 0
                else:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(unread), 0) FROM room_unread_counts WHERE room_id = ?",
                        (room_id,),
                    ).fetchone()
                    return int(row[0]) if row else 0
            except Exception:
                return 0
    def increment_room_unread(self, room_id: str, member_webids: Optional[list] = None) -> None:
        if not member_webids:
            return
        with self._conn() as conn:
            try:
                for webid in member_webids:
                    if webid:
                        conn.execute(
                            """INSERT INTO room_unread_counts (room_id, webid, unread)
                               VALUES (?, ?, 1)
                               ON CONFLICT(room_id, webid) DO UPDATE SET unread = unread + 1""",
                            (room_id, webid),
                        )
            except Exception:
                pass
    def reset_room_unread(self, room_id: str, webid: str = "") -> None:
        with self._conn() as conn:
            try:
                if webid:
                    conn.execute(
                        "UPDATE room_unread_counts SET unread = 0 WHERE room_id = ? AND webid = ?",
                        (room_id, webid),
                    )
                else:
                    conn.execute(
                        "UPDATE room_unread_counts SET unread = 0 WHERE room_id = ?",
                        (room_id,),
                    )
            except Exception:
                pass
    def set_room_role(self, room_id: str, webid: str, role: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO room_roles (room_id, webid, role) VALUES (?, ?, ?)",
                (room_id, webid, role)
            )
    def get_room_role(self, room_id: str, webid: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT role FROM room_roles WHERE room_id = ? AND webid = ?",
                (room_id, webid)
            ).fetchone()
            return row["role"] if row else "member"
    def get_all_room_roles(self, room_id: str) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT webid, role FROM room_roles WHERE room_id = ?", (room_id,)
            ).fetchall()
            return {r["webid"]: r["role"] for r in rows}
    def create_webhook(self, wh: dict) -> str:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO webhooks (id, thread_id, owner_webid, direction, token, url, bot_name, created_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (wh["id"], wh["thread_id"], wh["owner_webid"], wh["direction"],
                 wh["token"], wh.get("url", ""), wh.get("bot_name", "Bot"), wh["created_at"]),
            )
        return wh["id"]
    def get_webhook_by_token(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM webhooks WHERE token = ?", (token,)
            ).fetchone()
        return dict(row) if row else None
    def get_webhooks_for_thread(self, thread_id: str, direction: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE thread_id = ? AND direction = ? AND active = 1",
                (thread_id, direction),
            ).fetchall()
        return [dict(r) for r in rows]
    def deactivate_webhook(self, wh_id: str, owner_webid: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE webhooks SET active = 0 WHERE id = ? AND owner_webid = ?",
                (wh_id, owner_webid),
            )
        return cur.rowcount > 0
    def save_webhook_delivery_log(
        self,
        webhook_id: str,
        thread_id: str,
        status_code: Optional[int] = None,
        success: bool = False,
        latency_ms: Optional[int] = None,
    ) -> None:
        import uuid as _uuid
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO webhook_delivery_logs
                   (id, webhook_id, thread_id, status_code, success, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_uuid.uuid4().hex, webhook_id, thread_id,
                 status_code, 1 if success else 0, latency_ms, time.time()),
            )
    def get_webhook_delivery_logs(self, webhook_id: str, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM webhook_delivery_logs
                   WHERE webhook_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (webhook_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    def delete_sender_keys_for_room(self, room_id: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM sender_keys WHERE room_id=?", (room_id,))
            except Exception:
                pass
