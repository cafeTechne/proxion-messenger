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




class FederationStoreMixin(object):
    def seen_relay_nonce(self, nonce_key: str, ttl_seconds: int = 600) -> bool:
        """Return True if *nonce_key* was recorded within *ttl_seconds* seconds."""
        cutoff = time.time() - ttl_seconds
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT seen_at FROM relay_seen_nonces WHERE nonce_key = ?",
                    (nonce_key,),
                ).fetchone()
                return row is not None and row["seen_at"] > cutoff
            except Exception:
                return False
    def record_relay_nonce(self, nonce_key: str) -> None:
        """Insert *nonce_key* with the current timestamp (ignore if already present)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO relay_seen_nonces (nonce_key, seen_at) VALUES (?, ?)",
                    (nonce_key, time.time()),
                )
            except Exception:
                pass
    def prune_relay_nonces(self, cutoff: float) -> None:
        """Delete nonce entries older than *cutoff* (Unix timestamp)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM relay_seen_nonces WHERE seen_at < ?", (cutoff,)
                )
            except Exception:
                pass
    def has_seen_relay_id(self, dedup_key: str, ttl_seconds: int = 600) -> bool:
        """Return True if *dedup_key* was recorded within *ttl_seconds* seconds."""
        cutoff = time.time() - ttl_seconds
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT seen_at FROM relay_seen_ids WHERE dedup_key = ?",
                    (dedup_key,),
                ).fetchone()
                return row is not None and row["seen_at"] > cutoff
            except Exception:
                return False
    def record_relay_id(self, dedup_key: str) -> None:
        """Record *dedup_key* with the current timestamp (ignore if already present)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO relay_seen_ids (dedup_key, seen_at) VALUES (?, ?)",
                    (dedup_key, time.time()),
                )
            except Exception:
                pass
    def prune_seen_relay_ids(self, cutoff_ts: float) -> None:
        """Delete dedup entries older than *cutoff_ts* (Unix timestamp)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM relay_seen_ids WHERE seen_at < ?", (cutoff_ts,)
                )
            except Exception:
                pass
    def has_seen_invite_nonce(self, nonce: str, ttl_seconds: int = 86400) -> bool:
        cutoff = time.time() - ttl_seconds
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT seen_at FROM invite_seen_nonces WHERE nonce = ?", (nonce,)
                ).fetchone()
                return row is not None and row["seen_at"] > cutoff
            except Exception:
                return False
    def record_invite_nonce(self, nonce: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO invite_seen_nonces (nonce, seen_at) VALUES (?, ?)",
                    (nonce, time.time()),
                )
            except Exception:
                pass
    def prune_invite_nonces(self, cutoff: float) -> None:
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM invite_seen_nonces WHERE seen_at < ?", (cutoff,))
            except Exception:
                pass
    def enqueue_mailbox(self, blob_id: str, recipient_did: str, sealed_blob: str,
                        expires_at: float) -> bool:
        """Store a sealed blob for a recipient. Returns False if a quota is exceeded."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM relay_mailbox").fetchone()[0]
            if total >= self._MAILBOX_MAX_TOTAL:
                return False
            per = conn.execute(
                "SELECT COUNT(*) FROM relay_mailbox WHERE recipient_did=?", (recipient_did,)
            ).fetchone()[0]
            if per >= self._MAILBOX_MAX_PER_DID:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO relay_mailbox (blob_id, recipient_did, sealed_blob, expires_at) VALUES (?,?,?,?)",
                (blob_id, recipient_did, sealed_blob, expires_at),
            )
        return True
    def drain_mailbox(self, recipient_did: str, limit: int = 200) -> list[dict]:
        """Return and DELETE up to *limit* non-expired blobs for the recipient."""
        import time as _t
        now = _t.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT blob_id, sealed_blob FROM relay_mailbox WHERE recipient_did=? AND expires_at > ? ORDER BY received_at LIMIT ?",
                (recipient_did, now, limit),
            ).fetchall()
            result = [{"blob_id": r[0], "sealed_blob": r[1]} for r in rows]
            if result:
                ids = [r["blob_id"] for r in result]
                conn.executemany("DELETE FROM relay_mailbox WHERE blob_id=?", [(i,) for i in ids])
        return result
    def mailbox_count(self, recipient_did: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM relay_mailbox WHERE recipient_did=?", (recipient_did,)
            ).fetchone()[0]
    def purge_expired_mailbox(self) -> int:
        import time as _t
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM relay_mailbox WHERE expires_at <= ?", (_t.time(),))
            return cur.rowcount or 0
    def save_pending_invite(self, invite_dict: dict, target_did: str) -> None:
        """Save a pending invite."""
        with self._conn() as conn:
            invitation_id = invite_dict.get("invitation_id") or invite_dict.get("id", str(int(time.time() * 1000)))
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_invites
                    (invitation_id, invite_json, target_did, created_at, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invitation_id,
                    json.dumps(invite_dict),
                    target_did,
                    int(time.time()),
                    "pending",
                ),
            )
    def get_pending_invite(self, invitation_id: str) -> Optional[dict]:
        """Returns invite_json parsed as dict, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT invite_json FROM pending_invites WHERE invitation_id = ?",
                (invitation_id,),
            ).fetchone()
            if row:
                return json.loads(row["invite_json"])
            return None
    def list_pending_invites(self, status: str = "pending") -> list[dict]:
        """Returns list of invite_json dicts filtered by status."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT invite_json FROM pending_invites WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
            return [json.loads(r["invite_json"]) for r in rows]
    def mark_invite_status(self, invitation_id: str, status: str) -> None:
        """UPDATE status."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_invites SET status = ? WHERE invitation_id = ?",
                (status, invitation_id),
            )
    def mark_relay_delivered(self, msg_id: str) -> None:
        """Remove a successfully delivered message from the queue."""
        with self._conn() as conn:
            conn.execute("DELETE FROM relay_queue WHERE id=?", (msg_id,))
    def increment_relay_attempts(self, msg_id: str) -> None:
        """Record a failed delivery attempt."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE relay_queue SET attempts = attempts + 1, last_attempt = ? WHERE id=?",
                (int(time.time()), msg_id),
            )
    def has_seen_dpop_jti(self, jti: str, ttl_seconds: int = 120) -> bool:
        """Return True if *jti* was recorded within *ttl_seconds* seconds."""
        cutoff = time.time() - ttl_seconds
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT seen_at FROM dpop_seen_jti WHERE jti = ?", (jti,)
                ).fetchone()
                if row and row["seen_at"] > cutoff:
                    return True
                return False
            except Exception:
                return False
    def save_peer_gateway(self, did: str, gateway_url: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO peer_gateways (did, gateway_url, updated_at) VALUES (?, ?, ?)",
                (did, gateway_url, time.time()),
            )
    def get_peer_gateway(self, did: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT gateway_url FROM peer_gateways WHERE did = ?", (did,)
            ).fetchone()
            return row["gateway_url"] if row else None
    def get_all_peer_gateways(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT did, gateway_url FROM peer_gateways").fetchall()
            return {r["did"]: r["gateway_url"] for r in rows}
    def enqueue_relay(self, relay_id: str, to_webid: str, to_gateway_url: str, payload: dict) -> None:
        with self._conn() as conn:
            _now = time.time()
            conn.execute(
                """
                INSERT OR IGNORE INTO pending_relays
                    (id, to_webid, to_gateway_url, payload_json, created_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """,
                (relay_id, to_webid, to_gateway_url, json.dumps(payload), _now, _now + 86400),
            )
    def get_pending_relays(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_relays
                WHERE status = 'pending'
                  AND (expires_at = 0 OR expires_at > ?)
                  AND attempt_count < 10
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (time.time(), limit),
            ).fetchall()
            return [dict(r) for r in rows]
    def mark_relay_delivered(self, relay_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_relays SET status = 'delivered' WHERE id = ?",
                (relay_id,),
            )
    def mark_relay_permanently_failed(self, relay_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_relays SET status = 'failed' WHERE id = ?",
                (relay_id,),
            )
    def record_peer_gateway_change_request(
        self,
        id: str,
        peer_did: str,
        old_gateway_url: str,
        new_gateway_url: str,
        observed_at: float,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO peer_gateway_change_requests
                   (id, peer_did, old_gateway_url, new_gateway_url, observed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (id, peer_did, old_gateway_url, new_gateway_url, observed_at),
            )
    def approve_peer_gateway_change(self, peer_did: str) -> bool:
        """Approve the pending gateway change for peer_did. Returns True if a pin was updated."""
        with self._conn() as conn:
            pin = conn.execute(
                "SELECT * FROM peer_gateway_pins WHERE peer_did = ? AND pending_change = 1", (peer_did,)
            ).fetchone()
            if not pin:
                return False
            conn.execute(
                """UPDATE peer_gateway_pins SET
                   pinned_gateway_url = last_seen_gateway_url,
                   pinned_at = last_seen_at,
                   pending_change = 0
                   WHERE peer_did = ?""",
                (peer_did,),
            )
            conn.execute(
                "UPDATE peer_gateway_change_requests SET approved = 1, approved_at = ? WHERE peer_did = ? AND approved = 0",
                (time.time(), peer_did),
            )
            return True
    def list_peer_gateway_change_requests(self, peer_did: Optional[str] = None, limit: int = 100) -> list:
        with self._conn() as conn:
            if peer_did:
                rows = conn.execute(
                    "SELECT * FROM peer_gateway_change_requests WHERE peer_did = ? ORDER BY observed_at DESC LIMIT ?",
                    (peer_did, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM peer_gateway_change_requests ORDER BY observed_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    def append_relay_delivery_event(self, relay_id: str, peer_did: str, status: str) -> None:
        import hashlib as _hl
        with self._conn() as conn:
            prev_row = conn.execute(
                "SELECT entry_hash FROM relay_delivery_chain WHERE peer_did = ? ORDER BY created_at DESC LIMIT 1",
                (peer_did,),
            ).fetchone()
            prev_hash = prev_row[0] if prev_row else ""
            now = time.time()
            raw = f"{relay_id}|{peer_did}|{status}|{prev_hash}|{now}"
            entry_hash = _hl.sha256(raw.encode()).hexdigest()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO relay_delivery_chain
                       (relay_id, peer_did, status, prev_hash, entry_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (relay_id, peer_did, status, prev_hash, entry_hash, now),
                )
            except Exception:
                pass
    def verify_relay_delivery_chain(self, peer_did: str, limit: int = 5000) -> dict:
        import hashlib as _hl
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT relay_id, peer_did, status, prev_hash, entry_hash, created_at "
                "FROM relay_delivery_chain WHERE peer_did = ? ORDER BY created_at ASC LIMIT ?",
                (peer_did, limit),
            ).fetchall()
        if not rows:
            return {"valid": True, "entries": 0, "broken_at": None}
        prev_hash = ""
        for i, row in enumerate(rows):
            raw = f"{row[0]}|{row[1]}|{row[2]}|{row[3]}|{row[5]}"
            expected = _hl.sha256(raw.encode()).hexdigest()
            if row[4] != expected or row[3] != prev_hash:
                return {"valid": False, "entries": len(rows), "broken_at": i}
            prev_hash = row[4]
        return {"valid": True, "entries": len(rows), "broken_at": None}
    def _invite_bucket_start(self, now: float, window_seconds: int) -> float:
        return now - (now % window_seconds)
    def increment_invite_pair_counter(self, from_did: str, to_did: str, now: float) -> int:
        """Increment DID-pair invite counter and return new count."""
        pair_key = f"{from_did}:{to_did}"
        bucket = self._invite_bucket_start(now, 86400)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO invite_pair_counters (pair_key, bucket_start, count) VALUES (?, ?, 1) "
                "ON CONFLICT(pair_key, bucket_start) DO UPDATE SET count = count + 1",
                (pair_key, bucket),
            )
            row = conn.execute(
                "SELECT SUM(count) FROM invite_pair_counters WHERE pair_key = ? AND bucket_start >= ?",
                (pair_key, now - 86400),
            ).fetchone()
            return (row[0] or 0) if row else 0
    def check_invite_pair_counter(self, from_did: str, to_did: str, now: float) -> int:
        """Return current 24h count for a DID pair (without incrementing)."""
        pair_key = f"{from_did}:{to_did}"
        with self._conn() as conn:
            row = conn.execute(
                "SELECT SUM(count) FROM invite_pair_counters WHERE pair_key = ? AND bucket_start >= ?",
                (pair_key, now - 86400),
            ).fetchone()
            return (row[0] or 0) if row else 0
    def increment_invite_source_counter(self, source_ip: str, now: float) -> int:
        """Increment source-IP invite-accept counter and return new count."""
        bucket = self._invite_bucket_start(now, 3600)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO invite_source_counters (source_ip, bucket_start, count) VALUES (?, ?, 1) "
                "ON CONFLICT(source_ip, bucket_start) DO UPDATE SET count = count + 1",
                (source_ip, bucket),
            )
            row = conn.execute(
                "SELECT SUM(count) FROM invite_source_counters WHERE source_ip = ? AND bucket_start >= ?",
                (source_ip, now - 3600),
            ).fetchone()
            return (row[0] or 0) if row else 0
    def prune_invite_counters(self, now: float) -> None:
        """Remove invite counter rows older than their tracking window."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM invite_pair_counters WHERE bucket_start < ?", (now - 86400,)
            )
            conn.execute(
                "DELETE FROM invite_source_counters WHERE bucket_start < ?", (now - 3600,)
            )
    def add_quarantine_item(
        self,
        id: str,
        item_type: str,
        source_identity: Optional[str],
        payload_json: str,
        reason: str,
        created_at: float,
        payload_sha256: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> None:
        import hashlib as _hl_qi
        _sha = payload_sha256 or _hl_qi.sha256(payload_json.encode()).hexdigest()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO federation_quarantine
                   (id, item_type, source_identity, payload_json, reason, created_at, payload_sha256, source_ip)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, item_type, source_identity, payload_json, reason, created_at, _sha, source_ip),
            )
    def has_duplicate_quarantine_payload(self, item_type: str, payload_sha256: str, window_s: float = 86400) -> bool:
        """Return True if an identical payload was quarantined within the window."""
        cutoff = time.time() - window_s
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id FROM federation_quarantine
                   WHERE item_type = ? AND payload_sha256 = ? AND created_at >= ?
                   LIMIT 1""",
                (item_type, payload_sha256, cutoff),
            ).fetchone()
            return row is not None
    def list_quarantine_items(self, limit: int = 200) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM federation_quarantine WHERE released = 0 AND dropped = 0 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    def get_quarantine_item(self, id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM federation_quarantine WHERE id = ?", (id,)
            ).fetchone()
            return dict(row) if row else None
    def release_quarantine_item(self, id: str) -> bool:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE federation_quarantine SET released = 1, released_at = ? WHERE id = ? AND released = 0 AND dropped = 0",
                (now, id),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def drop_quarantine_item(self, id: str) -> bool:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE federation_quarantine SET dropped = 1, dropped_at = ? WHERE id = ? AND released = 0 AND dropped = 0",
                (now, id),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def increment_relay_attempt(self, relay_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE pending_relays
                SET attempt_count = attempt_count + 1, last_attempt_at = ?
                WHERE id = ?
                """,
                (time.time(), relay_id),
            )
    @staticmethod
    def _quarantine_status(item: dict) -> str:
        if item.get("released"):
            return "released"
        if item.get("dropped"):
            return "dropped"
        return "pending"
    def transition_quarantine_item(self, id: str, action: str) -> str:
        """Apply action (release|drop) to a quarantine item.

        Returns the new status string, or raises ValueError on invalid transition.
        """
        item = self.get_quarantine_item(id)
        if item is None:
            raise ValueError(f"quarantine item {id!r} not found")
        current = self._quarantine_status(item)
        allowed = self._VALID_QUARANTINE_TRANSITIONS.get(current, set())
        target = "released" if action == "release" else "dropped" if action == "drop" else action
        if target not in allowed:
            raise ValueError(f"invalid quarantine transition {current!r} → {target!r}")
        if action == "release":
            ok = self.release_quarantine_item(id)
        else:
            ok = self.drop_quarantine_item(id)
        if not ok:
            raise ValueError(f"quarantine_already_processed: item {id!r} was modified concurrently")
        return target
    def get_quarantine_item(self, id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM federation_quarantine WHERE id = ?", (id,)
            ).fetchone()
            return dict(row) if row else None
    def save_peer_attestation(
        self,
        peer_did: str,
        attestation_json: str,
        attestation_hash: str,
        expires_at: float,
        verified: bool = False,
        verified_at: Optional[float] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO peer_attestations
                   (peer_did, attestation_json, attestation_hash, verified, verified_at, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (peer_did, attestation_json, attestation_hash,
                 1 if verified else 0, verified_at, expires_at, time.time()),
            )
    def get_peer_attestation(self, peer_did: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM peer_attestations WHERE peer_did = ?", (peer_did,)
            ).fetchone()
            return dict(row) if row else None
    def prune_expired_peer_attestations(self) -> int:
        cutoff = time.time()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM peer_attestations WHERE expires_at < ?", (cutoff,)
            )
            return conn.execute("SELECT changes()").fetchone()[0]
