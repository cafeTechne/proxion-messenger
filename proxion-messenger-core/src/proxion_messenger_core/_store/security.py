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
    def get_revoked_dids(self) -> set:
        """Return the set of all revoked peer DIDs."""
        with self._conn() as conn:
            rows = conn.execute("SELECT peer_did FROM revocations").fetchall()
            return {r["peer_did"] for r in rows}
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
    def export_all(self, minimize: bool = True) -> dict:
        """Return a JSON-serializable export of all store data (R14.1).

        When minimize=True (default): redacts webhook tokens, truncates message
        content to 4 KiB, and strips security event detail payloads over 512 chars.
        """
        from datetime import datetime, timezone
        _4KiB = 4096
        with self._conn() as conn:
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
        }
    def import_data(self, data: dict, owner_pub_hex: str = "") -> dict:
        """Merge exported data into the store using INSERT OR IGNORE (R14.2).

        Enforces the same per-thread quotas as save_message so that the /import
        endpoint cannot be used to bypass storage limits.  When ``owner_pub_hex``
        is provided, relationships where neither party is the gateway owner are
        skipped to prevent injection of third-party contacts.
        """
        _MAX_MESSAGES_PER_THREAD = 5000
        _MAX_BYTES_PER_THREAD = 50 * 1024 * 1024  # 50 MB
        _MAX_IMPORT_MESSAGES = 10000
        _MAX_IMPORT_RELATIONSHIPS = 2000
        _MAX_CONTENT_BYTES = 16 * 1024  # 16 KiB per message
        counts = {"messages": 0, "relationships": 0, "dm_threads": 0, "display_names": 0}
        with self._conn() as conn:
            _total_msg_imported = 0
            for msg in data.get("messages", []):
                if _total_msg_imported >= _MAX_IMPORT_MESSAGES:
                    break
                try:
                    thread_id = msg.get("thread_id", "")
                    content = msg.get("content", "")
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
                    res = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) FROM messages WHERE thread_id = ?",
                        (thread_id,),
                    ).fetchone()
                    if res[0] >= _MAX_MESSAGES_PER_THREAD:
                        continue
                    if res[1] + len(content) > _MAX_BYTES_PER_THREAD:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO messages "
                        "(message_id, thread_id, thread_type, from_webid, from_display_name, content, timestamp, edited_at, reply_to_id, imported) "
                        "VALUES (?,?,?,?,?,?,?,?,?,1)",
                        (msg.get("message_id"), thread_id, msg.get("thread_type", "relay"),
                         msg.get("from_webid", ""), msg.get("from_display_name"),
                         content, msg.get("timestamp", ""),
                         msg.get("edited_at"), msg.get("reply_to_id")),
                    )
                    counts["messages"] += conn.execute("SELECT changes()").fetchone()[0]
                    _total_msg_imported = counts["messages"]
                except Exception:
                    pass
            _total_rel_imported = 0
            for rel in data.get("relationships", []):
                if _total_rel_imported >= _MAX_IMPORT_RELATIONSHIPS:
                    break
                try:
                    if owner_pub_hex:
                        cert_raw = rel.get("cert_json", "{}")
                        try:
                            cert_data = json.loads(cert_raw) if isinstance(cert_raw, str) else cert_raw
                        except Exception:
                            continue
                        issuer = cert_data.get("issuer", "")
                        subject = cert_data.get("subject", "")
                        if issuer != owner_pub_hex and subject != owner_pub_hex:
                            continue  # skip third-party relationships
                    conn.execute(
                        "INSERT OR IGNORE INTO relationships "
                        "(certificate_id, peer_pub_hex, peer_did, cert_json, created_at, expires_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (rel.get("certificate_id"), rel.get("peer_pub_hex", ""),
                         rel.get("peer_did"), rel.get("cert_json", "{}"),
                         rel.get("created_at", 0), rel.get("expires_at", 0)),
                    )
                    counts["relationships"] += conn.execute("SELECT changes()").fetchone()[0]
                    _total_rel_imported += 1
                except Exception:
                    pass
            for dm in data.get("dm_threads", []):
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_threads "
                        "(thread_id, peer_webid, display_name, owner_webid, created_at) "
                        "VALUES (?,?,?,?,?)",
                        (dm.get("thread_id"), dm.get("peer_webid", ""),
                         dm.get("display_name"), dm.get("owner_webid", ""),
                         dm.get("created_at", 0)),
                    )
                    counts["dm_threads"] += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
            for dn in data.get("display_names", []):
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO display_names (webid, display_name, updated_at) VALUES (?,?,?)",
                        (dn.get("webid"), dn.get("display_name", ""), dn.get("updated_at", 0)),
                    )
                    counts["display_names"] += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        return counts
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
                    "SELECT created_at FROM security_events WHERE id = ?", (cursor,)
                ).fetchone()
                if row:
                    rows = conn.execute(
                        "SELECT * FROM security_events WHERE created_at > ? ORDER BY created_at ASC LIMIT ?",
                        (row[0], limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM security_events ORDER BY created_at ASC LIMIT ?", (limit,)
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM security_events ORDER BY created_at ASC LIMIT ?", (limit,)
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
        self, bucket_key: str, limit: int, window_seconds: float
    ) -> bool:
        """Return True if the request is allowed; False if rate-limited.

        Uses a sliding window reset: if the stored window has expired, reset count to 1.
        """
        now = time.time()
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT count, window_start FROM rate_limit_buckets WHERE bucket_key=?",
                    (bucket_key,),
                ).fetchone()
                if row is None or now - row["window_start"] >= window_seconds:
                    conn.execute(
                        """INSERT OR REPLACE INTO rate_limit_buckets
                           (bucket_key, count, window_start, updated_at) VALUES (?, 1, ?, ?)""",
                        (bucket_key, now, now),
                    )
                    return True
                if row["count"] >= limit:
                    return False
                conn.execute(
                    "UPDATE rate_limit_buckets SET count=count+1, updated_at=? WHERE bucket_key=?",
                    (now, bucket_key),
                )
                return True
            except Exception:
                return True  # fail open on DB error
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
