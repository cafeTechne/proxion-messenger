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

# Per-message edit-history cap. Mirrors _MAX_EDITS_PER_MESSAGE in _gateway_rooms.py:
# every edit path (the room handler and the DM update_message) converges on
# save_edit with a fresh edit_id, so INSERT OR IGNORE never dedupes. The store is
# the authoritative cap; save_edit keeps at most this many rows per message.
_MAX_EDITS_PER_MESSAGE = 100




class SecurityStoreMixin(object):
    def record_join_attempt(self, code_hash: str, ip: str = "") -> None:
        """Record a join attempt for rate-limit auditing."""
        import uuid as _u
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO room_join_attempts (id, code_hash, ip, attempted_at) VALUES (?, ?, ?, ?)",
                (str(_u.uuid4()), code_hash, ip, time.time()),
            )
    def count_recent_join_attempts(self, code_hash: str, ip: str, window_s: float = 60.0) -> int:
        """Return number of attempts for *code_hash* from *ip* in the last *window_s* seconds."""
        since = time.time() - window_s
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM room_join_attempts WHERE code_hash = ? AND ip = ? AND attempted_at >= ?",
                (code_hash, ip, since),
            ).fetchone()
            return row[0] if row else 0
    def record_join_attempt_v2(self, ip: str, room_hint: str) -> None:
        """Record a per-room join attempt for scoped rate limiting."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO room_join_attempts_v2 (ip, room_hint, attempted_at) VALUES (?, ?, ?)",
                    (ip, room_hint, time.time()),
                )
            except Exception:
                pass
    def count_recent_join_attempts_v2(
        self, ip: str, room_hint: str, window_s: float = 60.0
    ) -> int:
        """Count failed attempts for *ip* in *room_hint* within *window_s* seconds."""
        since = time.time() - window_s
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM room_join_attempts_v2 "
                    "WHERE ip = ? AND room_hint = ? AND attempted_at >= ?",
                    (ip, room_hint, since),
                ).fetchone()
                return row[0] if row else 0
            except Exception:
                return 0
    def count_recent_join_attempts_global_v2(self, ip: str, window_s: float = 60.0) -> int:
        """Count all failed join attempts for *ip* within *window_s* seconds (across rooms)."""
        since = time.time() - window_s
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM room_join_attempts_v2 "
                    "WHERE ip = ? AND attempted_at >= ?",
                    (ip, since),
                ).fetchone()
                return row[0] if row else 0
            except Exception:
                return 0
    def prune_room_join_attempts(self, cutoff_ts: float) -> int:
        """Delete join-attempt rows older than *cutoff_ts*. Returns number deleted.

        Both room_join_attempts tables are append-per-attempt on an unauthenticated,
        IP-driven path, so without a prune they grow without bound (disk-exhaustion
        DoS, and the COUNT() rate-limit queries degrade). Only rows inside the
        rate-limit window matter; anything older is dead weight. The table names are
        hardcoded literals (no user input), so the f-string is injection-safe.
        """
        deleted = 0
        with self._conn() as conn:
            for _tbl in ("room_join_attempts", "room_join_attempts_v2"):
                try:
                    conn.execute(f"DELETE FROM {_tbl} WHERE attempted_at < ?", (cutoff_ts,))
                    deleted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        return deleted

    def save_edit(
        self, edit_id: str, message_id: str, prev_content: str,
        new_content: str, edited_by: str, edited_at: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO message_edits "
                "(edit_id, message_id, prev_content, new_content, edited_by, edited_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (edit_id, message_id, prev_content, new_content, edited_by, edited_at),
            )
            # Bound the per-message edit history. The DM edit path reaches here via
            # update_message with a fresh uuid per edit, so INSERT OR IGNORE never
            # dedupes and the table would grow without limit. Keep only the most
            # recent _MAX_EDITS_PER_MESSAGE rows (a bounded ring) so history stays
            # useful; the message content update itself already applied.
            count = conn.execute(
                "SELECT COUNT(*) FROM message_edits WHERE message_id = ?", (message_id,)
            ).fetchone()[0]
            if count > _MAX_EDITS_PER_MESSAGE:
                conn.execute(
                    "DELETE FROM message_edits WHERE message_id = ? AND edit_id IN "
                    "(SELECT edit_id FROM message_edits WHERE message_id = ? "
                    "ORDER BY edited_at ASC, rowid ASC LIMIT ?)",
                    (message_id, message_id, count - _MAX_EDITS_PER_MESSAGE),
                )
    def get_edits(self, message_id: str) -> list:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM message_edits WHERE message_id = ? ORDER BY edited_at ASC",
                    (message_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def get_max_seq_num(self, thread_id: str) -> int:
        """Return the maximum seq_num stored for thread_id (0 if none)."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT MAX(seq_num) FROM messages WHERE thread_id = ?", (thread_id,)
                ).fetchone()
                return int(row[0]) if row and row[0] is not None else 0
            except Exception:
                return 0
    def is_revoked(self, peer_did: str) -> bool:
        """Return True if the peer_did is in the revocations table."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM revocations WHERE peer_did = ?", (peer_did,)
            ).fetchone()
            return row is not None
    def mark_revoked(self, cert_id: str, peer_did: str) -> None:
        """Insert a revocation record."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO revocations (cert_id, peer_did, revoked_at) VALUES (?,?,?)",
                (cert_id, peer_did, time.time()),
            )
    def contact_revocation_cert_ids(self, peer_did: str) -> list:
        """The cert_ids of peer_did's contact revocations (never "device:" rows),
        i.e. the pod tombstones a re-add has to remove."""
        if not peer_did:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT cert_id FROM revocations WHERE peer_did = ? AND cert_id NOT LIKE 'device:%'",
                (peer_did,)).fetchall()
        return [r["cert_id"] for r in rows if r["cert_id"]]
    def clear_revocation_for_peer(self, peer_did: str) -> bool:
        """Drop the contact revocation rows for peer_did. Only for an explicit
        re-add by the owner (a new relationship they consented to); the old
        certificates keep their own revoked flag. A device revocation
        ("device:" row) is never lifted here. Returns True when no revocation
        of any kind is left for peer_did."""
        if not peer_did:
            return False
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM revocations WHERE peer_did = ? AND cert_id NOT LIKE 'device:%'",
                (peer_did,))
            left = conn.execute(
                "SELECT 1 FROM revocations WHERE peer_did = ? LIMIT 1", (peer_did,)).fetchone()
        return left is None
    def get_revoked_dids(self) -> set:
        """Return the set of all revoked peer DIDs."""
        with self._conn() as conn:
            rows = conn.execute("SELECT peer_did FROM revocations").fetchall()
            return {r["peer_did"] for r in rows}
    def is_cert_revoked(self, cert_id: str) -> bool:
        """True if a revocation row exists for this certificate_id.

        Restore paths consult this (alongside relationship_is_revoked and
        is_revoked) before re-importing a cert from the pod, so a revoked
        contact whose cert still lives on the pod is not resurrected."""
        if not cert_id:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM revocations WHERE cert_id = ?", (cert_id,)
            ).fetchone()
            return row is not None
    def _ensure_pod_pending_ops_table(self, conn: "sqlite3.Connection") -> None:
        """Create the durable pod-op retry queue on first use.

        Kept as a lazy CREATE-IF-NOT-EXISTS rather than a numbered migration so
        the queue lives in the same SQLite file without a schema-version bump."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pod_pending_ops (
                uri             TEXT PRIMARY KEY,
                op              TEXT NOT NULL,
                body            TEXT,
                content_type    TEXT NOT NULL DEFAULT 'application/json',
                created_at      REAL NOT NULL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_attempt_at REAL
            )"""
        )
    def record_pending_pod_op(
        self, uri: str, op: str, body: Optional[str] = None,
        content_type: str = "application/json",
    ) -> None:
        """Queue a pod write/delete that failed (or could not be attempted) so it
        is retried on the next reconnect rather than silently lost — a dropped
        device delete or revocation tombstone would otherwise resurrect on the
        next pod restore. The attempt counter survives re-queueing."""
        if not uri or not op:
            return
        now = time.time()
        with self._conn() as conn:
            try:
                self._ensure_pod_pending_ops_table(conn)
                conn.execute(
                    """INSERT OR REPLACE INTO pod_pending_ops
                       (uri, op, body, content_type, created_at, attempts, last_attempt_at)
                       VALUES (?, ?, ?, ?, ?,
                           COALESCE((SELECT attempts FROM pod_pending_ops WHERE uri=?), 0),
                           ?)""",
                    (uri, op, body, content_type, now, uri, now),
                )
            except Exception:
                pass
    def clear_pending_pod_op(self, uri: str) -> None:
        """Drop a queued pod op once it has landed (or is no longer needed)."""
        if not uri:
            return
        with self._conn() as conn:
            try:
                self._ensure_pod_pending_ops_table(conn)
                conn.execute("DELETE FROM pod_pending_ops WHERE uri=?", (uri,))
            except Exception:
                pass
    def list_pending_pod_ops(self, limit: int = 500) -> list[dict]:
        """Return queued pod ops oldest first (for retry on reconnect)."""
        with self._conn() as conn:
            try:
                self._ensure_pod_pending_ops_table(conn)
                rows = conn.execute(
                    "SELECT * FROM pod_pending_ops ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def bump_pending_pod_op_attempt(self, uri: str) -> None:
        """Record another failed retry for a queued pod op."""
        if not uri:
            return
        with self._conn() as conn:
            try:
                self._ensure_pod_pending_ops_table(conn)
                conn.execute(
                    "UPDATE pod_pending_ops SET attempts = attempts + 1, last_attempt_at=? "
                    "WHERE uri=?",
                    (time.time(), uri),
                )
            except Exception:
                pass
    def save_audit_log(
        self,
        event_type: str,
        severity: str = "info",
        webid: Optional[str] = None,
        ip: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Persist a security event to the audit_logs table."""
        import secrets as _secrets
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (id, event_type, severity, webid, ip, metadata, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _secrets.token_hex(8),
                    event_type,
                    severity,
                    webid,
                    ip,
                    json.dumps(metadata) if metadata is not None else None,
                    time.time(),
                ),
            )
    def get_audit_logs(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent audit log entries, newest first."""
        with self._conn() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM audit_logs WHERE event_type=? ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except Exception:
                        pass
                result.append(d)
            return result
    def get_last_audit_hash(self) -> str:
        """Return the entry_hash of the most recent audit log entry, or empty string."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT entry_hash FROM audit_logs ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                return row["entry_hash"] if row else ""
            except Exception:
                return ""
    def save_audit_log_chained(
        self,
        event_type: str,
        severity: str = "info",
        webid: Optional[str] = None,
        ip: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Persist a tamper-evident chained audit entry."""
        import hashlib as _hl
        import secrets as _secrets
        entry_id = _secrets.token_hex(8)
        ts = time.time()
        prev_hash = self.get_last_audit_hash()
        meta_str = json.dumps(metadata) if metadata is not None else ""
        raw = f"{prev_hash}|{event_type}|{severity}|{webid or ''}|{ip or ''}|{meta_str}|{ts}"
        entry_hash = _hl.sha256(raw.encode()).hexdigest()
        with self._conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO audit_logs
                        (id, event_type, severity, webid, ip, metadata, timestamp, prev_hash, entry_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (entry_id, event_type, severity, webid, ip,
                     meta_str if meta_str else None, ts, prev_hash, entry_hash),
                )
            except Exception:
                pass
    def verify_audit_chain(self, limit: int = 5000) -> dict:
        """Verify that audit log entries form an unbroken hash chain.

        Returns dict with keys: ok (bool), break_at (int|None), error (str|None).
        """
        import hashlib as _hl
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM audit_logs ORDER BY timestamp ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            except Exception as exc:
                return {"ok": False, "break_at": None, "error": str(exc)}
        prev_hash = ""
        for i, row in enumerate(rows):
            r = dict(row)
            stored_prev = r.get("prev_hash", "")
            if stored_prev != prev_hash:
                return {"ok": False, "break_at": i, "error": "prev_hash mismatch"}
            meta_str = r.get("metadata") or ""
            raw = f"{prev_hash}|{r['event_type']}|{r['severity']}|{r.get('webid') or ''}|{r.get('ip') or ''}|{meta_str}|{r['timestamp']}"
            expected_hash = _hl.sha256(raw.encode()).hexdigest()
            if r.get("entry_hash", "") != expected_hash:
                return {"ok": False, "break_at": i, "error": "entry_hash mismatch"}
            prev_hash = r["entry_hash"]
        return {"ok": True, "break_at": None, "error": None}
    def save_security_event(
        self,
        event_type: str,
        severity: str = "info",
        webid: Optional[str] = None,
        ip: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """Persist a security telemetry event."""
        import secrets as _secrets
        with self._conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO security_events (id, event_type, severity, webid, ip, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_secrets.token_hex(8), event_type, severity, webid, ip, details, time.time()),
                )
            except Exception:
                pass
    def get_security_events(
        self,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> list:
        """Return recent security events, newest first."""
        with self._conn() as conn:
            try:
                if event_type:
                    rows = conn.execute(
                        "SELECT * FROM security_events WHERE event_type=? ORDER BY created_at DESC LIMIT ?",
                        (event_type, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def purge_old_audit_logs(self, cutoff_ts: float) -> int:
        """Delete audit log entries older than cutoff_ts; returns count deleted.
        Respects active retention locks — will not delete entries newer than locked_until."""
        effective_cutoff = self._effective_purge_cutoff(cutoff_ts, "audit_logs")
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM audit_logs WHERE timestamp < ?", (effective_cutoff,))
                return conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                return 0
    def purge_old_security_events(self, cutoff_ts: float) -> int:
        """Delete security event entries older than cutoff_ts; returns count deleted.
        Respects active retention locks — will not delete entries newer than locked_until."""
        effective_cutoff = self._effective_purge_cutoff(cutoff_ts, "security_events")
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM security_events WHERE created_at < ?", (effective_cutoff,))
                return conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                return 0
    def _effective_purge_cutoff(self, requested_cutoff: float, table_name: str) -> float:
        """Return the most conservative (smallest) cutoff, honoring any active retention lock."""
        try:
            with self._conn() as conn:
                now = time.time()
                lock_row = conn.execute(
                    "SELECT locked_until FROM retention_locks WHERE lock_name = ? AND locked_until > ?",
                    (table_name, now),
                ).fetchone()
                if lock_row:
                    return min(requested_cutoff, lock_row[0])
                # Also check the wildcard lock "all"
                lock_row = conn.execute(
                    "SELECT locked_until FROM retention_locks WHERE lock_name = 'all' AND locked_until > ?",
                    (now,),
                ).fetchone()
                if lock_row:
                    return min(requested_cutoff, lock_row[0])
        except Exception:
            pass
        return requested_cutoff
    def record_dpop_jti(self, jti: str) -> None:
        """Record *jti* with the current timestamp (insert or replace)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO dpop_seen_jti (jti, seen_at) VALUES (?, ?)",
                    (jti, time.time()),
                )
            except Exception:
                pass
    def prune_dpop_jti(self, cutoff_ts: float) -> None:
        """Delete JTI entries older than *cutoff_ts* (Unix timestamp)."""
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM dpop_seen_jti WHERE seen_at <= ?", (cutoff_ts,))
            except Exception:
                pass
    def save_background_health_event(self, loop_name: str, failure_count: int, detail: str = "") -> None:
        """Save a degraded background loop event to security_events."""
        self.save_security_event(
            "background_loop_degraded", "warning",
            details=f"loop={loop_name} failures={failure_count} {detail}".strip(),
        )
    def set_last_read(self, webid: str, channel_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO last_read (webid, channel_id, last_read_ts) VALUES (?, ?, ?)",
                (webid, channel_id, time.time()),
            )
    def get_last_read(self, webid: str, channel_id: str) -> float:
        """Return last-read Unix timestamp for this webid+channel, or 0.0 if never read."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_read_ts FROM last_read WHERE webid = ? AND channel_id = ?",
                (webid, channel_id),
            ).fetchone()
        return row["last_read_ts"] if row else 0.0
    def set_last_read_ts(self, webid: str, channel_id: str, ts: float) -> None:
        """Set last-read timestamp to an explicit value (used for pod restore)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO last_read (webid, channel_id, last_read_ts) VALUES (?, ?, ?)",
                (webid, channel_id, ts),
            )
    def get_all_last_reads(self, webid: str) -> dict[str, float]:
        """Return {channel_id: last_read_ts} for all channels this webid has read."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT channel_id, last_read_ts FROM last_read WHERE webid = ?",
                (webid,),
            ).fetchall()
        return {row["channel_id"]: row["last_read_ts"] for row in rows}
    def get_security_summary(self, hours: int = 24) -> dict:
        """Return 24h (or custom hours) rollups of high-signal security metrics."""
        hours = max(1, min(hours, 168))
        cutoff = time.time() - hours * 3600
        with self._conn() as conn:
            def _count(table: str, event_type: str, ts_col: str) -> int:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE event_type = ? AND {ts_col} >= ?",
                        (event_type, cutoff),
                    ).fetchone()
                    return row[0] if row else 0
                except Exception:
                    return 0

            def _count_any(table: str, ts_col: str) -> int:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {ts_col} >= ?",
                        (cutoff,),
                    ).fetchone()
                    return row[0] if row else 0
                except Exception:
                    return 0

            rate_limits = _count("audit_logs", "rate_limit_exceeded", "timestamp")
            schema_rejects = _count("security_events", "schema_reject", "created_at")
            relay_replay_rejects = _count("audit_logs", "relay_replay_rejected", "timestamp")
            auth_lockouts = _count("security_events", "auth_lockout", "created_at")
            try:
                wh_fail_row = conn.execute(
                    "SELECT COUNT(*) FROM webhook_delivery_logs WHERE success = 0 AND created_at >= ?",
                    (cutoff,),
                ).fetchone()
                webhook_failures = wh_fail_row[0] if wh_fail_row else 0
            except Exception:
                webhook_failures = 0

        return {
            "hours": hours,
            "rate_limits_triggered": rate_limits,
            "schema_rejects": schema_rejects,
            "relay_replay_rejects": relay_replay_rejects,
            "auth_lockouts": auth_lockouts,
            "webhook_failures": webhook_failures,
        }
    def save_import_provenance(
        self,
        id: str,
        source: Optional[str],
        body_sha256: Optional[str],
        imported_by: Optional[str],
        imported_at: float,
        dry_run: bool,
        summary_json: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO import_provenance
                   (id, source, body_sha256, imported_by, imported_at, dry_run, summary_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (id, source, body_sha256, imported_by, imported_at, 1 if dry_run else 0, summary_json),
            )
    def list_import_provenance(self, limit: int = 100) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM import_provenance ORDER BY imported_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    def create_recovery_operation(
        self,
        op_id: str,
        op_type: str,
        requested_by: str,
        requested_at: float,
        expires_at: float,
        requester_fingerprint: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO recovery_operations
                   (op_id, op_type, requested_by, requested_at, expires_at, requester_fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (op_id, op_type, requested_by, requested_at, expires_at, requester_fingerprint),
            )
    def confirm_recovery_operation(self, op_id: str, confirmed_at: float) -> bool:
        """Mark a pending recovery operation as confirmed. Returns True if found and updated."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE recovery_operations SET confirmed = 1, confirmed_at = ? "
                "WHERE op_id = ? AND confirmed = 0 AND used = 0 AND expires_at > ?",
                (confirmed_at, op_id, confirmed_at),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            return changed > 0
    def get_recovery_operation(self, op_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM recovery_operations WHERE op_id = ?", (op_id,)
            ).fetchone()
            return dict(row) if row else None
    def consume_recovery_operation(self, op_id: str) -> bool:
        """Mark a confirmed, unexpired operation as used. Returns True if it was valid."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE recovery_operations SET used = 1, consumed_at = ? "
                "WHERE op_id = ? AND confirmed = 1 AND used = 0 AND expires_at > ?",
                (now, op_id, now),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            return changed > 0
    def prune_recovery_operations(self, now: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM recovery_operations WHERE expires_at < ?", (now - 3600,)
            )
    def open_peer_trust_dispute(
        self,
        id: str,
        peer_did: str,
        dispute_type: str,
        observed_value: str,
        expected_value: str,
        created_at: float,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO peer_trust_disputes
                   (id, peer_did, dispute_type, observed_value, expected_value, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (id, peer_did, dispute_type, observed_value, expected_value, created_at),
            )
    def resolve_peer_trust_dispute(self, id: str, resolved_at: float) -> bool:
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_trust_disputes SET status = 'resolved', resolved_at = ? WHERE id = ? AND status = 'open'",
                (resolved_at, id),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            return changed > 0
    def resolve_peer_trust_disputes_for_did(self, peer_did: str, resolved_at: float) -> int:
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_trust_disputes SET status = 'resolved', resolved_at = ? WHERE peer_did = ? AND status = 'open'",
                (resolved_at, peer_did),
            )
            return conn.execute("SELECT changes()").fetchone()[0]
    def list_peer_trust_disputes(self, status: str = "open", limit: int = 200) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM peer_trust_disputes WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    def get_peer_trust_dispute(self, id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM peer_trust_disputes WHERE id = ?", (id,)
            ).fetchone()
            return dict(row) if row else None
    def compute_table_checksum(self, table_name: str) -> dict:
        """Compute a SHA-256 checksum over all rows of a table."""
        import hashlib as _hl
        with self._conn() as conn:
            try:
                rows = conn.execute(f"SELECT * FROM {table_name} ORDER BY rowid").fetchall()  # nosec: table_name is controlled
            except Exception:
                rows = []
            row_count = len(rows)
            h = _hl.sha256()
            for row in rows:
                h.update(repr(tuple(row)).encode("utf-8"))
            checksum = h.hexdigest()
        return {
            "table_name": table_name,
            "checksum": checksum,
            "row_count": row_count,
            "computed_at": time.time(),
        }
    def snapshot_security_checksums(self, tables: list) -> None:
        """Compute and store checksums for the listed tables."""
        with self._conn() as conn:
            for table in tables:
                info = self.compute_table_checksum(table)
                conn.execute(
                    """INSERT OR REPLACE INTO table_checksums
                       (table_name, checksum, row_count, computed_at)
                       VALUES (?, ?, ?, ?)""",
                    (info["table_name"], info["checksum"], info["row_count"], info["computed_at"]),
                )
    def verify_security_checksums(self, tables: list) -> list:
        """Compare current checksums against stored snapshots. Returns mismatch list."""
        mismatches = []
        with self._conn() as conn:
            for table in tables:
                stored_row = conn.execute(
                    "SELECT checksum, row_count FROM table_checksums WHERE table_name = ?",
                    (table,),
                ).fetchone()
                if stored_row is None:
                    continue  # no baseline yet
                current = self.compute_table_checksum(table)
                if current["checksum"] != stored_row[0]:
                    mismatches.append({
                        "table": table,
                        "expected_checksum": stored_row[0],
                        "actual_checksum": current["checksum"],
                        "expected_rows": stored_row[1],
                        "actual_rows": current["row_count"],
                    })
        return mismatches
    def get_abuse_signal_rollups(self, hours: int = 1) -> dict:
        """Aggregate abuse signals from security_events and relay_delivery_chain."""
        since = time.time() - (hours * 3600)
        with self._conn() as conn:
            def _count(table: str, condition: str, params: tuple = ()) -> int:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {condition}", params
                    ).fetchone()
                    return row[0] if row else 0
                except Exception:
                    return 0

            schema_rejects = _count(
                "security_events", "event_type = 'schema_validation_failed' AND created_at >= ?", (since,)
            )
            auth_lockouts = _count(
                "security_events", "event_type = 'auth_lockout' AND created_at >= ?", (since,)
            )
            replay_rejects = _count(
                "security_events", "event_type = 'replay_detected' AND created_at >= ?", (since,)
            )
            relay_conflicts = _count(
                "security_events", "event_type LIKE '%relay_conflict%' AND created_at >= ?", (since,)
            )
            db_integrity_events = _count(
                "security_events", "event_type = 'db_integrity_failed' AND created_at >= ?", (since,)
            )
            relay_failed = _count(
                "relay_delivery_chain", "status IN ('failed', 'rejected') AND created_at >= ?", (since,)
            )
            try:
                invite_rate_limits = _count(
                    "invite_pair_counters", "bucket_start >= ?", (since,)
                )
            except Exception:
                invite_rate_limits = 0

        return {
            "hours": hours,
            "since": since,
            "schema_rejects": schema_rejects,
            "auth_lockouts": auth_lockouts,
            "replay_rejects": replay_rejects,
            "invite_rate_limit_hits": invite_rate_limits,
            "relay_conflict_rejects": relay_conflicts,
            "db_integrity_events": db_integrity_events,
            "relay_failed": relay_failed,
        }
    def local_account_owners(self) -> set:
        """Accounts that own data here (dm_threads / relationships owners)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT owner_webid FROM dm_threads WHERE owner_webid != '' "
                "UNION SELECT owner_webid FROM relationships WHERE owner_webid != ''"
            ).fetchall()
        return {r[0] for r in rows}

    # Rows that belong to one account, by (table, column). Room membership and
    # the account's messages in rooms are left to the room leave path: those
    # rooms are shared with other people and stay theirs.
    _ACCOUNT_ROWS = (
        ("relationships", "owner_webid"), ("contacts", "owner_webid"),
        ("blocks", "owner_webid"), ("thread_mutes", "owner_webid"),
        ("scheduled_messages", "from_webid"), ("webhooks", "owner_webid"),
        ("push_subscriptions", "owner_webid"), ("device_registrations", "owner_webid"),
        ("device_recovery_codes", "owner_webid"), ("dm_sessions", "owner_webid"),
        ("dm_prekeys", "owner_webid"), ("catchup_watermarks", "owner_webid"),
        ("stun_sessions", "owner_webid"), ("device_e2e_keys", "account_did"),
        ("x25519_pubs", "did"), ("display_names", "webid"), ("last_read", "webid"),
        ("room_unread_counts", "webid"), ("pending_relays", "to_webid"),
        ("dm_device_deliveries", "to_webid"), ("message_receipts", "receiver_webid"),
        ("account_sync", "owner_webid"),
    )

    def delete_account_data(self, webid: str) -> dict:
        """Delete everything this gateway keeps for one local account: its DM
        threads with their messages, pins, reactions, edits, files and timers,
        and the per-account rows in _ACCOUNT_ROWS. Returns rows deleted per table."""
        if not webid:
            return {}
        counts: dict = {}
        with self._conn() as conn:
            threads = [r[0] for r in conn.execute(
                "SELECT thread_id FROM dm_threads WHERE owner_webid = ?", (webid,)).fetchall()]
            for i in range(0, len(threads), 500):
                chunk = threads[i:i + 500]
                ph = ",".join("?" * len(chunk))
                for table, col in (("message_edits", None), ("message_files", "thread_id"),
                                   ("pins", "thread_id"), ("reactions", "room_id"),
                                   ("dm_disappear_timers", "thread_id"), ("disappear_timer_since", "thread_id"),
                                   ("messages", "thread_id")):
                    try:
                        if col is None:
                            cur = conn.execute(
                                f"DELETE FROM {table} WHERE message_id IN "
                                f"(SELECT message_id FROM messages WHERE thread_id IN ({ph}))", chunk)
                        else:
                            cur = conn.execute(f"DELETE FROM {table} WHERE {col} IN ({ph})", chunk)
                        counts[table] = counts.get(table, 0) + cur.rowcount
                    except sqlite3.OperationalError:
                        pass
            counts["dm_threads"] = conn.execute(
                "DELETE FROM dm_threads WHERE owner_webid = ?", (webid,)).rowcount
            for table, col in self._ACCOUNT_ROWS:
                try:
                    counts[table] = counts.get(table, 0) + conn.execute(
                        f"DELETE FROM {table} WHERE {col} = ?", (webid,)).rowcount
                except sqlite3.OperationalError:
                    pass  # table not present in this schema version
        return counts

    def export_all(self, minimize: bool = True, owner_webid: Optional[str] = None) -> dict:
        """Return a JSON-serializable export of all store data (R14.1).

        When minimize=True (default): redacts webhook tokens, truncates message
        content to 4 KiB, and strips security event detail payloads over 512 chars.

        With *owner_webid*, export only that account's data: its DM threads and
        contacts, messages in threads it is party to (or wrote), its scheduled
        messages, and the display names of those people. Other local accounts'
        conversations are left out.
        """
        from datetime import datetime, timezone
        _4KiB = 4096
        with self._conn() as conn:
            if owner_webid is None:
                messages = [dict(r) for r in conn.execute(
                    "SELECT * FROM messages ORDER BY timestamp"
                ).fetchall()]
                relationships = [dict(r) for r in conn.execute(
                    "SELECT * FROM relationships"
                ).fetchall()]
                dm_threads = [dict(r) for r in conn.execute(
                    "SELECT * FROM dm_threads"
                ).fetchall()]
                scheduled = [dict(r) for r in conn.execute(
                    "SELECT * FROM scheduled_messages WHERE cancelled = 0"
                ).fetchall()]
                display_names = [dict(r) for r in conn.execute(
                    "SELECT * FROM display_names"
                ).fetchall()]
            else:
                dm_threads = [dict(r) for r in conn.execute(
                    "SELECT * FROM dm_threads WHERE owner_webid = ?", (owner_webid,)
                ).fetchall()]
                # Owner-less relationships are legacy single-account rows, shown
                # to any account (list_relationships default), so export them too.
                relationships = [dict(r) for r in conn.execute(
                    "SELECT * FROM relationships WHERE owner_webid = ? OR owner_webid = ''",
                    (owner_webid,),
                ).fetchall()]
                threads = {t["thread_id"] for t in dm_threads}
                # Threads another local account opened with this one.
                threads.update(r[0] for r in conn.execute(
                    "SELECT thread_id FROM dm_threads WHERE peer_webid = ?", (owner_webid,)
                ).fetchall())
                for rel in relationships:
                    for k in ("certificate_id", "peer_did"):
                        if rel.get(k):
                            threads.add(rel[k])
                threads.update(r[0] for r in conn.execute(
                    "SELECT room_id FROM room_members WHERE webid = ?", (owner_webid,)
                ).fetchall())
                messages = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM messages ORDER BY timestamp"
                    ).fetchall()
                    if r["thread_id"] in threads or r["from_webid"] == owner_webid
                ]
                scheduled = [dict(r) for r in conn.execute(
                    "SELECT * FROM scheduled_messages WHERE cancelled = 0 AND from_webid = ?",
                    (owner_webid,),
                ).fetchall()]
                people = {owner_webid}
                people.update(t["peer_webid"] for t in dm_threads if t.get("peer_webid"))
                people.update(r["peer_did"] for r in relationships if r.get("peer_did"))
                people.update(m["from_webid"] for m in messages if m.get("from_webid"))
                display_names = [
                    dict(r) for r in conn.execute("SELECT * FROM display_names").fetchall()
                    if r["webid"] in people
                ]
            # Removed contacts and blocks travel with the backup, or importing it
            # would authorize people the owner had removed or blocked.
            revocations = [dict(r) for r in conn.execute(
                "SELECT cert_id, peer_did, revoked_at FROM revocations"
            ).fetchall()]
            blocks = [dict(r) for r in conn.execute(
                "SELECT owner_webid, blocked_webid, created_at FROM blocks"
            ).fetchall()
                if owner_webid is None or r["owner_webid"] == owner_webid]
            # The account's rooms with their members, roles, bans and mutes, and
            # the pins, reactions, mutes and timers of every exported thread.
            rooms = [dict(r) for r in conn.execute("SELECT * FROM rooms").fetchall()]
            if owner_webid is not None:
                _mine = {r[0] for r in conn.execute(
                    "SELECT room_id FROM room_members WHERE webid = ?", (owner_webid,)
                ).fetchall()}
                rooms = [r for r in rooms if r["room_id"] in _mine]
            for r in rooms:
                r.pop("unread_count", None)
            room_ids = {r["room_id"] for r in rooms}

            def _rows(sql, key, keep):
                return [dict(r) for r in conn.execute(sql).fetchall()
                        if owner_webid is None or r[key] in keep]
            room_members = _rows("SELECT room_id, webid FROM room_members", "room_id", room_ids)
            room_roles = _rows("SELECT room_id, webid, role FROM room_roles", "room_id", room_ids)
            room_bans = _rows("SELECT room_id, banned_did, banned_by, banned_at, reason FROM room_bans",
                              "room_id", room_ids)
            room_mutes = _rows("SELECT room_id, muted_did, muted_by, muted_at, expires_at FROM room_mutes",
                               "room_id", room_ids)
            _threads = {t["thread_id"] for t in dm_threads} | room_ids
            if owner_webid is not None:
                _threads |= threads
            pins = _rows("SELECT pin_id, thread_id, message_id, pinned_by, pinned_at, content FROM pins",
                         "thread_id", _threads)
            reactions = _rows("SELECT room_id, message_id, emoji, sender_webid, created_at FROM reactions",
                              "room_id", _threads)
            thread_mutes = _rows("SELECT owner_webid, mute_key, updated_at FROM thread_mutes",
                                 "owner_webid", {owner_webid})
            dm_timers = _rows("SELECT thread_id, ms FROM dm_disappear_timers", "thread_id", _threads)

        if minimize:
            _REDACTED = "[redacted]"
            _TOKEN_FIELDS = {"token", "webhook_token", "secret", "previous_token", "prev_token"}
            for msg in messages:
                if "content" in msg and isinstance(msg["content"], str):
                    raw = msg["content"].encode("utf-8", errors="replace")
                    if len(raw) > _4KiB:
                        msg["content"] = raw[:_4KiB].decode("utf-8", errors="replace") + "…"
                for _tf in _TOKEN_FIELDS:
                    if _tf in msg:
                        msg[_tf] = _REDACTED
            for rel in relationships:
                for _tf in _TOKEN_FIELDS:
                    if _tf in rel:
                        rel[_tf] = _REDACTED

        return {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "minimized": minimize,
            "messages": messages,
            "relationships": relationships,
            "dm_threads": dm_threads,
            "scheduled": scheduled,
            "display_names": display_names,
            "revocations": revocations,
            "blocks": blocks,
            "rooms": rooms,
            "room_members": room_members,
            "room_roles": room_roles,
            "room_bans": room_bans,
            "room_mutes": room_mutes,
            "pins": pins,
            "reactions": reactions,
            "thread_mutes": thread_mutes,
            "dm_disappear_timers": dm_timers,
        }
    def import_data(
        self, data: dict, owner_pub_hex: str = "", owner_webid: str = "",
        operator: bool = False,
    ) -> dict:
        """Merge exported data into the store using INSERT OR IGNORE (R14.2).

        Imported threads are subject to the same retention as save_message
        (oldest messages pruned past the ceiling) so that the /import endpoint
        cannot be used to bypass storage limits.  The file content is
        attacker-controllable, so each relationship certificate's issuer
        signature is verified before insertion and the peer identity is derived
        from the verified cert, not the sibling JSON fields.  When
        ``owner_pub_hex`` is provided, certificates where the owner is neither
        issuer nor subject are skipped to prevent injection of third-party
        contacts.

        dm_threads and relationships keep their embedded owner only when it is
        an account that already owns data on this gateway; otherwise they are
        stamped with ``owner_webid`` (the importing account). Display names are
        limited to those owners and their contacts.

        Rooms are only created, never merged into: a room id or code already
        here is skipped, and members, roles, bans and mutes are restored only for
        rooms this import created. A room must list the importing account as a
        member unless ``operator`` (the gateway operator restoring a full
        backup). Pins and reactions by other people are restored only in threads
        this import created; in existing threads only the importer's own.
        """
        _MAX_IMPORT_MESSAGES = 10000
        _MAX_IMPORT_RELATIONSHIPS = 2000
        _MAX_IMPORT_DM_THREADS = 2000
        _MAX_IMPORT_DISPLAY_NAMES = 5000
        _MAX_CONTENT_BYTES = 16 * 1024  # 16 KiB per message
        _MAX_IMPORT_REVOCATIONS = 5000
        _MAX_IMPORT_BLOCKS = 5000
        _MAX_IMPORT_ROOMS = 1000
        _MAX_IMPORT_ROOM_ROWS = 20000  # members, roles, bans, mutes, pins, reactions each
        _MAX_AUDIO_B64 = 700_000       # the live voice-message limit
        _CONTENT_TYPES = {"text", "audio", "e2e"}
        from .identity import stored_cert_expiry
        counts = {"messages": 0, "relationships": 0, "dm_threads": 0, "display_names": 0,
                  "revocations": 0, "blocks": 0, "relationships_skipped_revoked": 0}
        _touched_threads: set = set()
        _new_threads: set = set()
        # A row keeps the account it belongs to when that account is one of
        # this gateway's (a per-account export restored here); any other value
        # is replaced by the importing owner, so a backup cannot create rows
        # under an account that does not exist here.
        _local_owners = self.local_account_owners()
        if owner_webid:
            _local_owners.add(owner_webid)

        def _row_owner(row: dict) -> str:
            _o = row.get("owner_webid") or ""
            return _o if _o in _local_owners else owner_webid

        _import_owners = {owner_webid} if owner_webid else set()

        from .dms import _rows_fit_pair
        with self._conn() as conn:
            # dm_threads first so imported messages can be scoped against them.
            _dm_seen = 0
            for dm in data.get("dm_threads", []):
                if _dm_seen >= _MAX_IMPORT_DM_THREADS:
                    break
                _dm_seen += 1
                try:
                    _dm_owner = _row_owner(dm)
                    _dm_peer = dm.get("peer_webid", "")
                    # Never file a thread under an id another pair's DM uses.
                    _existing = conn.execute(
                        "SELECT owner_webid, peer_webid FROM dm_threads WHERE thread_id = ?",
                        (dm.get("thread_id"),),
                    ).fetchall()
                    if _dm_owner and not _rows_fit_pair(_existing, _dm_owner, _dm_peer):
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_threads "
                        "(thread_id, peer_webid, display_name, owner_webid, created_at) "
                        "VALUES (?,?,?,?,?)",
                        (dm.get("thread_id"), _dm_peer,
                         dm.get("display_name"), _dm_owner,
                         dm.get("created_at", 0)),
                    )
                    _added = conn.execute("SELECT changes()").fetchone()[0]
                    counts["dm_threads"] += _added
                    if _added:
                        _new_threads.add(dm.get("thread_id"))
                    if _dm_owner:
                        _import_owners.add(_dm_owner)
                except Exception:
                    pass
            # Revocations and blocks before relationships, so a contact the owner
            # removed is not authorized again by restoring a backup. These rows
            # only take authorization away, so a hostile file gains nothing here.
            def _ok_id(v):
                return isinstance(v, str) and 0 < len(v) <= 1024
            _rev_seen = 0
            for rv in data.get("revocations", []) or []:
                if _rev_seen >= _MAX_IMPORT_REVOCATIONS:
                    break
                _rev_seen += 1
                if not isinstance(rv, dict):
                    continue
                _cid, _pdid = rv.get("cert_id"), rv.get("peer_did")
                if not (_ok_id(_cid) and _ok_id(_pdid)):
                    continue
                _rat = rv.get("revoked_at")
                if not isinstance(_rat, (int, float)):
                    _rat = time.time()
                conn.execute(
                    "INSERT OR IGNORE INTO revocations (cert_id, peer_did, revoked_at) VALUES (?,?,?)",
                    (_cid, _pdid, _rat),
                )
                counts["revocations"] += conn.execute("SELECT changes()").fetchone()[0]
                conn.execute("UPDATE relationships SET revoked=1 WHERE certificate_id=?", (_cid,))
                if not _cid.startswith("device:"):
                    conn.execute("UPDATE relationships SET revoked=1 WHERE peer_did=?", (_pdid,))
            _blk_seen = 0
            for bk in data.get("blocks", []) or []:
                if _blk_seen >= _MAX_IMPORT_BLOCKS:
                    break
                _blk_seen += 1
                if not isinstance(bk, dict):
                    continue
                _bo, _bb = _row_owner(bk), bk.get("blocked_webid")
                if not (_ok_id(_bo) and _ok_id(_bb)):
                    continue
                _bat = bk.get("created_at")
                conn.execute(
                    "INSERT OR IGNORE INTO blocks (owner_webid, blocked_webid, created_at) VALUES (?,?,?)",
                    (_bo, _bb, int(_bat) if isinstance(_bat, (int, float)) else int(time.time())),
                )
                counts["blocks"] += conn.execute("SELECT changes()").fetchone()[0]
            _revoked_peers = {r["peer_did"] for r in conn.execute(
                "SELECT peer_did FROM revocations").fetchall()}
            _revoked_certs = {r["cert_id"] for r in conn.execute(
                "SELECT cert_id FROM revocations").fetchall()}

            _rel_seen = 0
            for rel in data.get("relationships", []):
                if _rel_seen >= _MAX_IMPORT_RELATIONSHIPS:
                    break
                _rel_seen += 1
                try:
                    from ..federation import RelationshipCertificate, cert_authorizes_owner
                    from ..handshake import _ed25519_verify
                    from ..didkey import pub_key_to_did
                    cert_raw = rel.get("cert_json", "{}")
                    try:
                        cert_data = json.loads(cert_raw) if isinstance(cert_raw, str) else cert_raw
                        cert = RelationshipCertificate.from_dict(cert_data)
                    except Exception:
                        continue
                    # The stored row IS the authorization, so require owner consent
                    # before insertion. Owner-as-issuer: the issuer signature
                    # suffices. Owner-as-subject: require the durable subject
                    # counter-signature by the owner's own key (R113) — an
                    # issuer-only cert naming the owner as subject (which an
                    # attacker-controlled backup can contain) is refused. With no
                    # owner_pub_hex the owner filter is skipped but the issuer
                    # signature is still verified.
                    if not cert_authorizes_owner(cert, owner_pub_hex, _ed25519_verify):
                        continue
                    # Derive the peer identity from the verified cert, never the
                    # sibling JSON.
                    if owner_pub_hex and owner_pub_hex == cert.subject:
                        peer_key_hex = cert.issuer
                    else:
                        peer_key_hex = cert.subject
                    try:
                        peer_did = pub_key_to_did(bytes.fromhex(peer_key_hex))
                    except Exception:
                        continue
                    # A removed contact stays removed: skip a cert that was revoked
                    # when exported, or whose cert or peer is revoked here.
                    if (rel.get("revoked") or cert.certificate_id in _revoked_certs
                            or peer_did in _revoked_peers):
                        if rel.get("revoked"):
                            conn.execute(
                                "UPDATE relationships SET revoked=1 WHERE certificate_id=?",
                                (cert.certificate_id,))
                        counts["relationships_skipped_revoked"] += 1
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO relationships "
                        "(certificate_id, peer_pub_hex, peer_did, cert_json, created_at, expires_at, owner_webid) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (cert.certificate_id, cert.subject, peer_did,
                         json.dumps(cert.to_dict()), cert.created_at,
                         stored_cert_expiry(cert.created_at, cert.expires_at),
                         _row_owner(rel)),
                    )
                    counts["relationships"] += conn.execute("SELECT changes()").fetchone()[0]
                    if _row_owner(rel):
                        _import_owners.add(_row_owner(rel))
                except Exception:
                    pass
            _total_msg_imported = 0
            for msg in data.get("messages", []):
                if _total_msg_imported >= _MAX_IMPORT_MESSAGES:
                    break
                try:
                    thread_id = msg.get("thread_id", "")
                    content = msg.get("content", "")
                    # Imported messages keep imported=1 provenance (below). We do
                    # NOT owner-scope by thread here: a legitimate restore includes
                    # relay/federated DM history whose participation is a
                    # relationship (not a dm_thread/room_member row), so scoping
                    # would silently drop it; and the only injection that reaches
                    # other users is into a room the importer is already a member of,
                    # which such scoping would not stop anyway.
                    # Reject oversized content
                    if len(str(content).encode("utf-8", errors="replace")) > _MAX_CONTENT_BYTES:
                        continue
                    # Reject invalid timestamp
                    ts_str = msg.get("timestamp", "")
                    try:
                        from datetime import datetime as _dt
                        _dt.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        continue
                    _ctype = msg.get("content_type") or "text"
                    if _ctype not in _CONTENT_TYPES:
                        continue
                    _audio = msg.get("audio_b64")
                    if _audio is not None and not (
                            isinstance(_audio, str) and len(_audio) <= _MAX_AUDIO_B64):
                        continue
                    _dur = msg.get("duration_ms")
                    _dur = int(_dur) if isinstance(_dur, (int, float)) and _dur >= 0 else 0
                    conn.execute(
                        "INSERT OR IGNORE INTO messages "
                        "(message_id, thread_id, thread_type, from_webid, from_display_name, content, timestamp, edited_at, reply_to_id, imported, "
                        "content_type, audio_b64, duration_ms) "
                        "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                        (msg.get("message_id"), thread_id, msg.get("thread_type", "relay"),
                         msg.get("from_webid", ""), msg.get("from_display_name"),
                         content, msg.get("timestamp", ""),
                         msg.get("edited_at"), msg.get("reply_to_id"),
                         _ctype, _audio, _dur),
                    )
                    counts["messages"] += conn.execute("SELECT changes()").fetchone()[0]
                    _total_msg_imported = counts["messages"]
                    _touched_threads.add(thread_id)
                except Exception:
                    pass
            self._import_rooms_and_threads(
                conn, data, owner_webid, operator, _new_threads, _row_owner, counts,
                _MAX_IMPORT_ROOMS, _MAX_IMPORT_ROOM_ROWS)
            _dn_seen = 0
            _allowed_dn = None
            for dn in data.get("display_names", []):
                if _dn_seen >= _MAX_IMPORT_DISPLAY_NAMES:
                    break
                _dn_seen += 1
                try:
                    # Scope to the importing owner's own webid plus its contacts
                    # (DM peers and relationship peers); skip unrelated webids.
                    if owner_webid:
                        if _allowed_dn is None:
                            # The importer, plus any local account whose rows
                            # this file restored, and their contacts.
                            _allowed_dn = set(_import_owners)
                            _ph = ",".join("?" * len(_import_owners))
                            _ow = tuple(_import_owners)
                            for r in conn.execute(
                                f"SELECT peer_webid FROM dm_threads WHERE owner_webid IN ({_ph})",
                                _ow,
                            ).fetchall():
                                if r["peer_webid"]:
                                    _allowed_dn.add(r["peer_webid"])
                            for r in conn.execute(
                                f"SELECT peer_did FROM relationships WHERE owner_webid IN ({_ph})",
                                _ow,
                            ).fetchall():
                                if r["peer_did"]:
                                    _allowed_dn.add(r["peer_did"])
                        if dn.get("webid") not in _allowed_dn:
                            continue
                    conn.execute(
                        "INSERT OR IGNORE INTO display_names (webid, display_name, updated_at) VALUES (?,?,?)",
                        (dn.get("webid"), dn.get("display_name", ""), dn.get("updated_at", 0)),
                    )
                    counts["display_names"] += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        for _tid in _touched_threads:
            try:
                self.prune_thread(_tid)
            except Exception:
                pass
        return counts

    @staticmethod
    def _import_rooms_and_threads(conn, data, owner_webid, operator, new_threads,
                                  row_owner, counts, max_rooms, max_rows) -> None:
        """Restore rooms (create-only) and per-thread pins, reactions, mutes and
        disappearing timers. See import_data for the rules."""
        def _id(v, n=1024):
            return isinstance(v, str) and 0 < len(v) <= n

        def _num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        def _rows(key):
            rows = data.get(key) or []
            if not isinstance(rows, list):
                return []
            return [r for r in rows[:max_rows] if isinstance(r, dict)]

        members_by_room: dict = {}
        for m in _rows("room_members"):
            if _id(m.get("room_id")) and _id(m.get("webid")):
                members_by_room.setdefault(m["room_id"], set()).add(m["webid"])

        new_rooms: set = set()
        counts.setdefault("rooms", 0)
        for room in _rows("rooms")[:max_rooms]:
            rid, code = room.get("room_id"), room.get("code")
            if not (_id(rid) and _id(code, 256)):
                continue
            members = members_by_room.get(rid, set())
            creator = room.get("creator_webid") or ""
            if not isinstance(creator, str) or (creator and creator not in members):
                continue
            if not operator and owner_webid not in members:
                continue
            if conn.execute("SELECT 1 FROM rooms WHERE room_id = ? OR code = ?",
                            (rid, code)).fetchone():
                continue
            mode = room.get("history_mode")
            ms = room.get("disappear_after_ms")
            created = room.get("created_at")
            conn.execute(
                "INSERT INTO rooms (room_id, name, code, invite_url, history_mode, creator_webid, "
                "created_at, disappear_after_ms, topic) VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, str(room.get("name") or "")[:64], code, str(room.get("invite_url") or "")[:2048],
                 mode if isinstance(mode, str) and 0 < len(mode) <= 32 else "none", creator,
                 float(created) if _num(created) else time.time(),
                 int(ms) if _num(ms) and ms > 0 else 0,
                 str(room.get("topic") or "")[:1024]),
            )
            new_rooms.add(rid)
            counts["rooms"] += 1
        for rid in new_rooms:
            for w in members_by_room.get(rid, ()):
                conn.execute("INSERT OR IGNORE INTO room_members (room_id, webid) VALUES (?,?)", (rid, w))
        for r in _rows("room_roles"):
            if r.get("room_id") in new_rooms and _id(r.get("webid")) and _id(r.get("role"), 32):
                conn.execute("INSERT OR IGNORE INTO room_roles (room_id, webid, role) VALUES (?,?,?)",
                             (r["room_id"], r["webid"], r["role"]))
        for b in _rows("room_bans"):
            if b.get("room_id") in new_rooms and _id(b.get("banned_did")):
                at = b.get("banned_at")
                conn.execute(
                    "INSERT OR IGNORE INTO room_bans (room_id, banned_did, banned_by, banned_at, reason) "
                    "VALUES (?,?,?,?,?)",
                    (b["room_id"], b["banned_did"], str(b.get("banned_by") or "")[:1024],
                     float(at) if _num(at) else time.time(), str(b.get("reason") or "")[:512]))
        for m in _rows("room_mutes"):
            if m.get("room_id") in new_rooms and _id(m.get("muted_did")):
                at, exp = m.get("muted_at"), m.get("expires_at")
                conn.execute(
                    "INSERT OR IGNORE INTO room_mutes (room_id, muted_did, muted_by, muted_at, expires_at) "
                    "VALUES (?,?,?,?,?)",
                    (m["room_id"], m["muted_did"], str(m.get("muted_by") or "")[:1024],
                     float(at) if _num(at) else time.time(), float(exp) if _num(exp) else None))

        created_here = new_threads | new_rooms
        # Threads the importer is party to, for its own pins and reactions.
        mine = {r[0] for r in conn.execute(
            "SELECT thread_id FROM dm_threads WHERE owner_webid = ?", (owner_webid,)).fetchall()}
        mine |= {r[0] for r in conn.execute(
            "SELECT room_id FROM room_members WHERE webid = ?", (owner_webid,)).fetchall()}

        def _may(thread_id, actor):
            return thread_id in created_here or (actor == owner_webid and thread_id in mine)

        counts.setdefault("pins", 0)
        for p in _rows("pins"):
            tid, mid, by = p.get("thread_id"), p.get("message_id"), p.get("pinned_by")
            if not (_id(tid) and _id(mid) and _id(by)) or not _may(tid, by):
                continue
            if conn.execute("SELECT 1 FROM pins WHERE thread_id = ? AND message_id = ?",
                            (tid, mid)).fetchone():
                continue
            pid = p.get("pin_id") if _id(p.get("pin_id"), 128) else ""
            if not pid or conn.execute("SELECT 1 FROM pins WHERE pin_id = ?", (pid,)).fetchone():
                pid = f"pin-{tid}-{mid}"
            at = p.get("pinned_at")
            conn.execute(
                "INSERT OR IGNORE INTO pins (pin_id, thread_id, message_id, pinned_by, pinned_at, content) "
                "VALUES (?,?,?,?,?,?)",
                (pid, tid, mid, by, float(at) if _num(at) else time.time(),
                 str(p.get("content") or "")[:16384]))
            counts["pins"] += conn.execute("SELECT changes()").fetchone()[0]
        counts.setdefault("reactions", 0)
        for r in _rows("reactions"):
            tid, mid, emoji, by = r.get("room_id"), r.get("message_id"), r.get("emoji"), r.get("sender_webid")
            if not (_id(tid) and _id(mid) and _id(emoji, 256) and _id(by)) or not _may(tid, by):
                continue
            at = r.get("created_at")
            conn.execute(
                "INSERT OR IGNORE INTO reactions (room_id, message_id, emoji, sender_webid, created_at) "
                "VALUES (?,?,?,?,?)",
                (tid, mid, emoji, by, float(at) if _num(at) else time.time()))
            counts["reactions"] += conn.execute("SELECT changes()").fetchone()[0]
        for m in _rows("thread_mutes"):
            owner = row_owner(m)
            if owner and owner == owner_webid and _id(m.get("mute_key")):
                at = m.get("updated_at")
                conn.execute(
                    "INSERT OR IGNORE INTO thread_mutes (owner_webid, mute_key, updated_at) VALUES (?,?,?)",
                    (owner, m["mute_key"], int(at) if _num(at) else int(time.time())))
        # Pending scheduled sends go out as their author, so only the importer's
        # own (any author for the operator's full restore).
        counts.setdefault("scheduled", 0)
        for sm in _rows("scheduled"):
            frm, tid, content = sm.get("from_webid"), sm.get("thread_id"), sm.get("content")
            if not (_id(sm.get("id"), 256) and _id(frm) and _id(tid) and isinstance(content, str)
                    and len(content.encode("utf-8", errors="replace")) <= 16384
                    and _num(sm.get("send_at"))):
                continue
            if frm != owner_webid and not operator:
                continue
            ca = sm.get("created_at")
            conn.execute(
                "INSERT OR IGNORE INTO scheduled_messages (id, thread_id, from_webid, content, send_at, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (sm["id"], tid, frm, content, float(sm["send_at"]), float(ca) if _num(ca) else time.time()))
            counts["scheduled"] += conn.execute("SELECT changes()").fetchone()[0]
        for t in _rows("dm_disappear_timers"):
            tid, ms = t.get("thread_id"), t.get("ms")
            if tid in new_threads and _num(ms) and ms > 0:
                conn.execute("INSERT OR IGNORE INTO dm_disappear_timers (thread_id, ms) VALUES (?,?)",
                             (tid, int(ms)))
    def save_credential_anomaly(
        self,
        id: str,
        anomaly_type: str,
        identity: Optional[str] = None,
        detail: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> None:
        if created_at is None:
            created_at = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO credential_anomalies
                   (id, anomaly_type, identity, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (id, anomaly_type, identity, detail, created_at),
            )
    def list_credential_anomalies(self, limit: int = 200) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM credential_anomalies ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    def set_retention_lock(self, lock_name: str, locked_until: float) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO retention_locks (lock_name, locked_until, created_at)
                   VALUES (?, ?, ?)""",
                (lock_name, locked_until, now),
            )
    def get_retention_lock(self, lock_name: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM retention_locks WHERE lock_name = ?", (lock_name,)
            ).fetchone()
            return dict(row) if row else None
    def list_retention_locks(self) -> list:
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM retention_locks WHERE locked_until > ? ORDER BY created_at DESC",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]
    def clear_retention_lock(self, lock_name: str) -> bool:
        with self._conn() as conn:
            conn.execute("DELETE FROM retention_locks WHERE lock_name = ?", (lock_name,))
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def create_trust_revocation(
        self,
        id: str,
        subject_type: str,
        subject_id: str,
        reason: str,
        revoked_by: str,
        revoked_at: float,
        expires_at: Optional[float] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trust_revocations
                   (id, subject_type, subject_id, reason, revoked_by, revoked_at, expires_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (id, subject_type, subject_id, reason, revoked_by, revoked_at, expires_at),
            )
    def is_subject_revoked(self, subject_type: str, subject_id: str, now_ts: Optional[float] = None) -> bool:
        now = now_ts if now_ts is not None else time.time()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id FROM trust_revocations
                   WHERE subject_type = ? AND subject_id = ? AND active = 1
                     AND (expires_at IS NULL OR expires_at > ?)
                   LIMIT 1""",
                (subject_type, subject_id, now),
            ).fetchone()
            return row is not None
    def list_active_trust_revocations(self, limit: int = 500) -> list:
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM trust_revocations
                   WHERE active = 1 AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY revoked_at DESC LIMIT ?""",
                (now, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    def expire_trust_revocations(self, cutoff_ts: float) -> int:
        with self._conn() as conn:
            conn.execute(
                "UPDATE trust_revocations SET active = 0 WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (cutoff_ts,),
            )
            return conn.execute("SELECT changes()").fetchone()[0]
    def create_pending_admin_action(
        self,
        action_id: str,
        action_type: str,
        payload_json: str,
        requested_by: str,
        expires_at: float,
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO pending_admin_actions
                   (action_id, action_type, payload_json, requested_by, requested_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (action_id, action_type, payload_json, requested_by, now, expires_at),
            )
    def get_pending_admin_action(self, action_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_admin_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            return dict(row) if row else None
    def confirm_admin_action(self, action_id: str, confirmed_by: str, now_ts: Optional[float] = None) -> bool:
        now = now_ts if now_ts is not None else time.time()
        with self._conn() as conn:
            conn.execute(
                """UPDATE pending_admin_actions
                   SET confirmed = 1, confirmed_by = ?, confirmed_at = ?
                   WHERE action_id = ? AND confirmed = 0 AND consumed = 0 AND expires_at > ?""",
                (confirmed_by, now, action_id, now),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def consume_admin_action(self, action_id: str) -> bool:
        with self._conn() as conn:
            conn.execute(
                """UPDATE pending_admin_actions SET consumed = 1
                   WHERE action_id = ? AND confirmed = 1 AND consumed = 0""",
                (action_id,),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def append_security_snapshot_chain_entry(
        self,
        snapshot_id: str,
        prev_hash: str,
        snapshot_hash: str,
        signature: str,
        signer_key_id: str,
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO security_snapshot_chain
                   (snapshot_id, prev_hash, snapshot_hash, signature, signer_key_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot_id, prev_hash, snapshot_hash, signature, signer_key_id, now),
            )
    def get_latest_security_snapshot_chain_entry(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM security_snapshot_chain ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
    def verify_security_snapshot_chain(self, limit: int = 5000) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM security_snapshot_chain ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
        entries = [dict(r) for r in rows]
        if not entries:
            return {"ok": True, "entries_checked": 0, "errors": []}
        errors = []
        for i in range(1, len(entries)):
            expected_prev = entries[i - 1]["snapshot_hash"]
            if entries[i]["prev_hash"] != expected_prev:
                errors.append({
                    "index": i,
                    "snapshot_id": entries[i]["snapshot_id"],
                    "expected_prev_hash": expected_prev,
                    "actual_prev_hash": entries[i]["prev_hash"],
                })
        return {"ok": len(errors) == 0, "entries_checked": len(entries), "errors": errors}
    @staticmethod
    def _utc_day_key() -> str:
        from datetime import datetime, timezone as _tz
        return datetime.now(_tz.utc).strftime("%Y-%m-%d")
    def get_operation_budget_count(self, op_type: str, day_key: Optional[str] = None) -> int:
        key = day_key or self._utc_day_key()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count FROM operation_budgets WHERE op_type = ? AND day_key = ?",
                (op_type, key),
            ).fetchone()
            return row[0] if row else 0
    def increment_operation_budget(self, op_type: str, day_key: Optional[str] = None) -> int:
        key = day_key or self._utc_day_key()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO operation_budgets (op_type, day_key, count) VALUES (?, ?, 1)
                   ON CONFLICT(op_type, day_key) DO UPDATE SET count = count + 1""",
                (op_type, key),
            )
            return conn.execute(
                "SELECT count FROM operation_budgets WHERE op_type = ? AND day_key = ?",
                (op_type, key),
            ).fetchone()[0]
    def check_operation_budget(self, op_type: str, limit: int, day_key: Optional[str] = None) -> bool:
        """Return True if the operation is within budget (current count < limit)."""
        return self.get_operation_budget_count(op_type, day_key) < limit
    def update_compromise_recovery_step(
        self, session_id: str, step_name: str, step_status: str, detail: Optional[str] = None
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO compromise_recovery_steps
                   (session_id, step_name, step_status, detail, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, step_name, step_status, detail, now),
            )
            conn.execute(
                "UPDATE compromise_recovery_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
    def set_compromise_recovery_status(self, session_id: str, status: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE compromise_recovery_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status, now, session_id),
            )
    def append_policy_change_log(
        self,
        policy_id: str,
        policy_version: str,
        policy_sha256: str,
        loaded_from: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> None:
        import secrets as _sec_pcl
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO policy_change_log
                   (id, policy_id, policy_version, policy_sha256, loaded_from, changed_by, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_sec_pcl.token_hex(8), policy_id, policy_version, policy_sha256, loaded_from, changed_by, now),
            )
    def list_policy_change_log(self, limit: int = 100) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM policy_change_log ORDER BY changed_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    def get_security_events_after(self, cursor: str = "", limit: int = 100) -> list:
        """Return security events after the given cursor (event id), oldest first."""
        limit = min(limit, 1000)
        with self._conn() as conn:
            if cursor:
                row = conn.execute(
                    "SELECT created_at, rowid FROM security_events WHERE id = ?", (cursor,)
                ).fetchone()
                if row:
                    # Events written in the same clock tick share created_at;
                    # page on (created_at, rowid) so none are skipped.
                    rows = conn.execute(
                        "SELECT * FROM security_events WHERE created_at > ? "
                        "OR (created_at = ? AND rowid > ?) "
                        "ORDER BY created_at ASC, rowid ASC LIMIT ?",
                        (row[0], row[0], row[1], limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM security_events ORDER BY created_at ASC, rowid ASC LIMIT ?", (limit,)
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM security_events ORDER BY created_at ASC, rowid ASC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
    def record_notification_fallback(
        self,
        pod_origin: str,
        reason_code: str,
        detail: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> str:
        import uuid as _uuid
        eid = event_id or str(_uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO notification_fallback_events
                   (id, pod_origin, reason_code, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (eid, pod_origin, reason_code, detail, time.time()),
            )
        return eid
    def get_notification_fallback_events(
        self, pod_origin: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        with self._conn() as conn:
            if pod_origin:
                rows = conn.execute(
                    """SELECT * FROM notification_fallback_events
                       WHERE pod_origin = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (pod_origin, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM notification_fallback_events
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    def prune_replay_table_by_cardinality(
        self, table: str, ts_column: str, max_rows: Optional[int] = None
    ) -> int:
        """Remove oldest rows from a replay table when cardinality exceeds max_rows."""
        cap = max_rows or self._REPLAY_CAP_DEFAULT
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if total <= cap:
                return 0
            excess = total - cap
            conn.execute(
                f"DELETE FROM {table} WHERE rowid IN "
                f"(SELECT rowid FROM {table} ORDER BY {ts_column} ASC LIMIT ?)",
                (excess,),
            )
            return conn.execute("SELECT changes()").fetchone()[0]
    def check_scoped_budget(self, op_type: str, scope_key: str, day_key: str, limit: int) -> bool:
        """Return True if the scoped budget has not been exceeded."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT count FROM operation_budget_scopes WHERE op_type=? AND scope_key=? AND day_key=?",
                    (op_type, scope_key, day_key),
                ).fetchone()
                return (row[0] if row else 0) < limit
            except Exception:
                return True
    def increment_scoped_budget(self, op_type: str, scope_key: str, day_key: str) -> int:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO operation_budget_scopes (op_type, scope_key, day_key, count)
                       VALUES (?, ?, ?, 1)
                       ON CONFLICT(op_type, scope_key, day_key)
                       DO UPDATE SET count = count + 1""",
                    (op_type, scope_key, day_key),
                )
                row = conn.execute(
                    "SELECT count FROM operation_budget_scopes WHERE op_type=? AND scope_key=? AND day_key=?",
                    (op_type, scope_key, day_key),
                ).fetchone()
                return row[0] if row else 1
            except Exception:
                return 1
    def save_policy_tier_transition(
        self,
        transition_id: str,
        from_tier: str,
        to_tier: str,
        trigger_type: str,
        trigger_detail: str = "",
        actor_webid: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO policy_tier_transitions
                   (id, from_tier, to_tier, trigger_type, trigger_detail, actor_webid, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (transition_id, from_tier, to_tier, trigger_type,
                 trigger_detail or None, actor_webid or None, time.time()),
            )
    def get_recent_policy_tier_transitions(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM policy_tier_transitions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    def upsert_stream_cursor(self, consumer_id: str, last_sequence: int) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO event_stream_cursors (consumer_id, last_sequence, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(consumer_id) DO UPDATE SET last_sequence=excluded.last_sequence, updated_at=excluded.updated_at""",
                (consumer_id, last_sequence, time.time()),
            )
    def get_stream_cursor(self, consumer_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM event_stream_cursors WHERE consumer_id = ?", (consumer_id,)
            ).fetchone()
            return dict(row) if row else None
    def save_slo_snapshot(
        self,
        snapshot_id: str,
        window_start: float,
        window_end: float,
        metrics: dict,
        evaluated_at: Optional[float] = None,
    ) -> None:
        import json as _json
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO security_slo_snapshots
                   (id, window_start, window_end, metrics_json, evaluated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (snapshot_id, window_start, window_end,
                 _json.dumps(metrics), evaluated_at if evaluated_at is not None else window_end),
            )
    def get_slo_snapshots_in_window(
        self, window_start: float, window_end: float
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM security_slo_snapshots
                   WHERE evaluated_at >= ? AND evaluated_at <= ?
                   ORDER BY evaluated_at ASC""",
                (window_start, window_end),
            ).fetchall()
            return [dict(r) for r in rows]
    def save_drill_result(
        self,
        drill_id: str,
        drill_type: str,
        status: str,
        findings: dict,
        duration_seconds: Optional[int] = None,
    ) -> None:
        import json as _json
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO security_drill_results
                   (drill_id, drill_type, status, duration_seconds, findings_json, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (drill_id, drill_type, status, duration_seconds,
                 _json.dumps(findings), time.time()),
            )
    def get_drill_results_in_window(
        self, window_start: float, window_end: float
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM security_drill_results
                   WHERE executed_at >= ? AND executed_at <= ?
                   ORDER BY executed_at ASC""",
                (window_start, window_end),
            ).fetchall()
            return [dict(r) for r in rows]
    def get_open_security_events_by_severity(
        self, severities: list, limit: int = 10
    ) -> list[dict]:
        placeholders = ",".join("?" * len(severities))
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    f"SELECT * FROM security_events WHERE severity IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
                    (*severities, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def count_security_events_since(self, event_type: str, since: float) -> int:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM security_events WHERE event_type=? AND created_at >= ?",
                    (event_type, since),
                ).fetchone()
                return row[0] if row else 0
            except Exception:
                return 0
    def rate_limit_check_and_increment(
        self,
        bucket_key: str,
        limit: int,
        window_seconds: float,
        fail_open: bool = False,
    ) -> bool:
        """Return True if the request is allowed; False if rate-limited.

        Uses a sliding window reset: if the stored window has expired, the count
        is reset to 1.  The check-and-increment runs as a single atomic UPSERT
        (INSERT ... ON CONFLICT DO UPDATE ... RETURNING) so concurrent callers on
        separate connections cannot race past the limit.  On a DB error the
        bucket fails closed (denies) by default; pass ``fail_open=True`` only for
        non-security buckets that should degrade to allowing traffic.
        """
        now = time.time()
        try:
            with self._conn() as conn:
                conn.execute("PRAGMA busy_timeout=5000")
                row = conn.execute(
                    """INSERT INTO rate_limit_buckets
                           (bucket_key, count, window_start, updated_at)
                       VALUES (?, 1, ?, ?)
                       ON CONFLICT(bucket_key) DO UPDATE SET
                           count = CASE
                               WHEN ? - window_start >= ? THEN 1
                               ELSE count + 1
                           END,
                           window_start = CASE
                               WHEN ? - window_start >= ? THEN ?
                               ELSE window_start
                           END,
                           updated_at = ?
                       RETURNING count""",
                    (
                        bucket_key, now, now,
                        now, window_seconds,
                        now, window_seconds, now,
                        now,
                    ),
                ).fetchone()
            return (row["count"] if row else 1) <= limit
        except Exception:
            return True if fail_open else False
    def prune_rate_limit_buckets(self, cutoff_ts: float) -> int:
        """Delete expired rate-limit buckets. Returns number deleted."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM rate_limit_buckets WHERE updated_at < ?", (cutoff_ts,)
                )
                return conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                return 0
    def save_receipt(
        self,
        message_id: str,
        receiver_webid: str,
        delivered_at: str | None = None,
        read_at: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO message_receipts (message_id, receiver_webid, delivered_at, read_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(message_id, receiver_webid) DO UPDATE SET
                     delivered_at = COALESCE(excluded.delivered_at, message_receipts.delivered_at),
                     read_at = COALESCE(excluded.read_at, message_receipts.read_at)""",
                (message_id, receiver_webid, delivered_at, read_at),
            )
    def get_receipts(self, message_id: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM message_receipts WHERE message_id=?",
                    (message_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def record_operation_result(
        self,
        op_id: str,
        op_type: str,
        actor_webid: str,
        actor_device_id: str | None,
        result_code: str,
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO idempotency_ops
                       (op_id, op_type, actor_webid, actor_device_id, result_code, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (op_id, op_type, actor_webid, actor_device_id, result_code, time.time()),
                )
            except Exception:
                pass
    def get_operation_result(self, op_id: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM idempotency_ops WHERE op_id=?", (op_id,)
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def record_recovery_attempt(
        self,
        thread_id: str,
        session_id: str | None,
        actor_webid: str,
        attempt_no: int,
        status: str = "pending",
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO dm_session_recovery_attempts
                       (thread_id, session_id, actor_webid, attempt_no, status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (thread_id, session_id, actor_webid, attempt_no, status, now, now),
                )
            except Exception:
                pass
    def update_recovery_attempt(
        self,
        thread_id: str,
        actor_webid: str,
        attempt_no: int,
        status: str,
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """UPDATE dm_session_recovery_attempts
                       SET status=?, updated_at=?
                       WHERE thread_id=? AND actor_webid=? AND attempt_no=?""",
                    (status, time.time(), thread_id, actor_webid, attempt_no),
                )
            except Exception:
                pass
    def get_recovery_attempts(
        self, thread_id: str, actor_webid: str
    ) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM dm_session_recovery_attempts
                       WHERE thread_id=? AND actor_webid=?
                       ORDER BY attempt_no ASC""",
                    (thread_id, actor_webid),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def prune_expired_idempotency_ops(self, retention_hours: int = 72) -> int:
        """Delete idempotency_ops records older than retention_hours. Returns count."""
        cutoff = time.time() - retention_hours * 3600
        with self._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM idempotency_ops WHERE created_at < ?", (cutoff,)
                )
                return conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                return 0
    def get_next_seq(self, thread_id: str) -> int:
        """Atomically increment and return the next sequence number for a thread."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO room_seq_counters (thread_id, next_seq)
                   VALUES (?, 1)
                   ON CONFLICT(thread_id) DO UPDATE SET next_seq = next_seq + 1""",
                (thread_id,),
            )
            row = conn.execute(
                "SELECT next_seq FROM room_seq_counters WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            return row["next_seq"] if row else 1
