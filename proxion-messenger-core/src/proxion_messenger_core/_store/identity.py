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




class IdentityStoreMixin(object):
    def upsert_contact(
        self, webid: str, display_name: str,
        avatar_url: Optional[str] = None, source: str = "dm",
        owner_webid: str = "",
    ) -> None:
        if not webid or not display_name:
            return
        display_name = display_name[:64]
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO contacts (webid, owner_webid, display_name, avatar_url, source, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(webid, owner_webid) DO UPDATE SET
                           display_name = excluded.display_name,
                           avatar_url   = COALESCE(excluded.avatar_url, contacts.avatar_url),
                           source       = excluded.source,
                           last_seen_at = excluded.last_seen_at""",
                    (webid, owner_webid, display_name, avatar_url, source, now),
                )
            except Exception:
                pass
    def get_all_contacts(self, limit: int = 100, owner_webid: str = "") -> list:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM contacts WHERE owner_webid = ? ORDER BY last_seen_at DESC LIMIT ?",
                    (owner_webid, limit)
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def save_display_name(self, webid: str, display_name: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO display_names (webid, display_name, updated_at)
                VALUES (?, ?, ?)
                """,
                (webid, display_name, time.time()),
            )
    def get_display_name(self, webid: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT display_name FROM display_names WHERE webid = ?", (webid,)
            ).fetchone()
            return row["display_name"] if row else None
    def save_x25519_pub(self, did: str, pub_b64u: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO x25519_pubs (did, pub_b64u, updated_at) VALUES (?,?,?)",
                (did, pub_b64u, int(time.time())),
            )
    def get_x25519_pub(self, did: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT pub_b64u FROM x25519_pubs WHERE did = ?", (did,)
            ).fetchone()
        return row["pub_b64u"] if row else None

    def save_e2e_key(self, did: str, pub_b64u: str) -> None:
        """Persist a peer's BROWSER-level E2E x25519 pub (separate from the gateway
        store key in x25519_pubs, which is used for sealed-sender)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO e2e_keys (did, pub_b64u, updated_at) VALUES (?,?,?)",
                (did, pub_b64u, int(time.time())),
            )

    def get_e2e_key(self, did: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT pub_b64u FROM e2e_keys WHERE did = ?", (did,)
            ).fetchone()
        return row["pub_b64u"] if row else None

    def save_device_e2e_key(self, account_did: str, device_id: str, pub_b64u: str) -> None:
        """Persist one device's browser-level E2E x25519 pub under its account.

        Used for multi-device DM fanout: the sender encrypts a separate copy to
        each of a peer account's devices.
        """
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO device_e2e_keys (account_did, device_id, pub_b64u, updated_at) "
                "VALUES (?,?,?,?)",
                (account_did, device_id, pub_b64u, int(time.time())),
            )

    def delete_device_e2e_key(self, account_did: str, device_id: str) -> None:
        """Remove one device's E2E key (device revoked) so DM fanout stops
        encrypting copies to it."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM device_e2e_keys WHERE account_did = ? AND device_id = ?",
                (account_did, device_id),
            )

    def list_device_e2e_keys(self, account_did: str) -> list[dict]:
        """Return [{device_id, pub_b64u}] for every device of an account."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT device_id, pub_b64u FROM device_e2e_keys WHERE account_did = ? "
                "ORDER BY updated_at ASC",
                (account_did,),
            ).fetchall()
        return [{"device_id": r["device_id"], "pub_b64u": r["pub_b64u"]} for r in rows]

    def list_all_device_e2e_keys(self) -> list[dict]:
        """Every device E2E key regardless of account — the one-gateway-per-user
        fallback for the /devices roster when a request addresses the GATEWAY
        identity did (which no account row is keyed under). Mirrors the
        _sockets_for own-gateway-did fallback semantics."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT account_did, device_id, pub_b64u FROM device_e2e_keys "
                "ORDER BY updated_at ASC",
            ).fetchall()
        return [{"account_did": r["account_did"], "device_id": r["device_id"],
                 "pub_b64u": r["pub_b64u"]} for r in rows]

    def get_relationship_owner_by_cert_id(self, certificate_id: str) -> str:
        """The LOCAL account (owner_webid) holding this relationship cert — ''
        when unknown (older rows saved without an owner). Used to scope
        pod-polled DM entries to the user the conversation belongs to."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT owner_webid FROM relationships WHERE certificate_id = ? LIMIT 1",
                (certificate_id,),
            ).fetchone()
        return (row["owner_webid"] or "") if row else ""

    def get_relationship_owner(self, peer_did: str) -> str:
        """The LOCAL account (owner_webid) that holds the newest non-expired
        relationship with peer_did — '' when unknown (older rows saved without
        an owner). Used to normalize 'this gateway's did' -> the local account
        whose device roster a related peer may fetch."""
        now = int(time.time())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT owner_webid FROM relationships "
                "WHERE peer_did = ? AND expires_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (peer_did, now),
            ).fetchone()
        return (row["owner_webid"] or "") if row else ""
    def save_relationship(
        self, cert_dict: dict, peer_did: Optional[str] = None, owner_webid: str = ""
    ) -> None:
        """INSERT OR REPLACE into relationships. peer_pub_hex = cert_dict["subject"]."""
        with self._conn() as conn:
            certificate_id = cert_dict.get("certificate_id") or cert_dict.get("id") or str(int(time.time() * 1000))
            peer_pub_hex = cert_dict.get("subject")
            created_at = cert_dict.get("created_at", int(time.time()))
            expires_at = cert_dict.get("expires_at", created_at + 86400)

            conn.execute(
                """
                INSERT OR REPLACE INTO relationships
                    (certificate_id, peer_pub_hex, peer_did, cert_json, created_at, expires_at, owner_webid, cert_policy_version, cert_validated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    certificate_id,
                    peer_pub_hex,
                    peer_did,
                    json.dumps(cert_dict),
                    created_at,
                    expires_at,
                    owner_webid,
                    1,
                    time.time(),
                ),
            )
    def get_relationship_by_peer(self, peer_pub_hex: str) -> Optional[dict]:
        """Returns cert_json parsed as dict for the newest non-expired cert, or None."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT cert_json FROM relationships
                WHERE peer_pub_hex = ? AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (peer_pub_hex, int(time.time())),
            ).fetchone()
            if row:
                return json.loads(row["cert_json"])
            return None
    def revoke_relationship(self, cert_id: str) -> None:
        """Mark a relationship certificate as revoked."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE relationships SET revoked=1 WHERE certificate_id=?",
                (cert_id,),
            )
    def list_relationships(
        self,
        owner_webid: Optional[str] = None,
        include_revoked: bool = False,
    ) -> list[dict]:
        """Returns cert_json dicts (with peer_did injected) ordered by created_at DESC.

        Revoked relationships are excluded by default.  Pass
        ``include_revoked=True`` to include them (e.g. for audit displays).
        """
        revoked_clause = "" if include_revoked else " AND revoked=0"
        with self._conn() as conn:
            if owner_webid is not None:
                rows = conn.execute(
                    f"SELECT cert_json, peer_did FROM relationships "
                    f"WHERE (owner_webid = ? OR owner_webid = ''){revoked_clause} "
                    "ORDER BY created_at DESC",
                    (owner_webid,),
                ).fetchall()
            else:
                where = f" WHERE revoked=0" if not include_revoked else ""
                rows = conn.execute(
                    f"SELECT cert_json, peer_did FROM relationships{where} ORDER BY created_at DESC"
                ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["cert_json"])
            d["peer_did"] = r["peer_did"]
            result.append(d)
        return result
    def get_relationship_by_cert_id(self, certificate_id: str) -> Optional[dict]:
        """Return cert_json dict (with peer_did injected) for the given certificate_id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cert_json, peer_did FROM relationships WHERE certificate_id = ? LIMIT 1",
                (certificate_id,),
            ).fetchone()
        if row is None:
            return None
        d = json.loads(row["cert_json"])
        d["peer_did"] = row["peer_did"]
        return d
    def get_relationship_by_did(self, peer_did: str) -> Optional[dict]:
        """Return cert_json dict for the newest non-expired cert matching peer_did."""
        now = int(time.time())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cert_json FROM relationships "
                "WHERE peer_did = ? AND expires_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (peer_did, now),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["cert_json"])
    def record_identity_key_seen(self, identity: str, pubkey_hex: str, trusted: bool = False) -> None:
        now = time.time()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT trusted FROM identity_key_history WHERE identity = ? AND pubkey_hex = ?",
                (identity, pubkey_hex),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO identity_key_history
                       (identity, pubkey_hex, first_seen_at, last_seen_at, trusted)
                       VALUES (?, ?, ?, ?, ?)""",
                    (identity, pubkey_hex, now, now, 1 if trusted else 0),
                )
            else:
                new_trusted = 1 if (trusted or existing[0]) else 0
                conn.execute(
                    "UPDATE identity_key_history SET last_seen_at = ?, trusted = ? "
                    "WHERE identity = ? AND pubkey_hex = ?",
                    (now, new_trusted, identity, pubkey_hex),
                )
    def get_identity_key_history(self, identity: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM identity_key_history WHERE identity = ? ORDER BY first_seen_at",
                (identity,),
            ).fetchall()
            return [dict(r) for r in rows]
    def is_trusted_identity_key(self, identity: str, pubkey_hex: str) -> Optional[bool]:
        """Return True if key is trusted, False if known-untrusted, None if never seen."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT trusted FROM identity_key_history WHERE identity = ? AND pubkey_hex = ?",
                (identity, pubkey_hex),
            ).fetchone()
            if row is None:
                return None
            return bool(row[0])
    def trust_identity_key(self, identity: str, pubkey_hex: str) -> bool:
        with self._conn() as conn:
            conn.execute(
                "UPDATE identity_key_history SET trusted = 1 WHERE identity = ? AND pubkey_hex = ?",
                (identity, pubkey_hex),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def open_identity_rollover_event(
        self,
        id: str,
        identity: str,
        old_pubkey_hex: Optional[str],
        new_pubkey_hex: str,
        created_at: Optional[float] = None,
    ) -> None:
        if created_at is None:
            created_at = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO identity_rollover_events
                   (id, identity, old_pubkey_hex, new_pubkey_hex, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (id, identity, old_pubkey_hex, new_pubkey_hex, created_at),
            )
    def resolve_identity_rollover_event(self, id: str, status: str = "approved") -> bool:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE identity_rollover_events SET status = ?, resolved_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, now, id),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    def list_identity_rollover_events(self, status: str = "pending", limit: int = 100) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM identity_rollover_events WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    def get_identity_rollover_event(self, id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM identity_rollover_events WHERE id = ?", (id,)
            ).fetchone()
            return dict(row) if row else None
    def get_pending_rollover_for_identity(self, identity: str) -> Optional[dict]:
        """Return the most recent pending rollover event for the given identity."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM identity_rollover_events WHERE identity = ? AND status = 'pending' "
                "ORDER BY created_at DESC LIMIT 1",
                (identity,),
            ).fetchone()
            return dict(row) if row else None
    def save_pod_capability_profile(
        self,
        pod_origin: str,
        notifications_supported: bool,
        channel_types: list,
        auth_requirements: list,
        verification_source: str = "runtime_probe",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pod_capability_profiles
                   (pod_origin, notifications_supported, channel_types_json,
                    auth_requirements_json, last_verified_at, verification_source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    pod_origin,
                    int(notifications_supported),
                    json.dumps(channel_types),
                    json.dumps(auth_requirements),
                    time.time(),
                    verification_source,
                ),
            )
    def get_pod_capability_profile(self, pod_origin: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pod_capability_profiles WHERE pod_origin = ?",
                (pod_origin,),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["channel_types"] = json.loads(d.pop("channel_types_json", "[]"))
            d["auth_requirements"] = json.loads(d.pop("auth_requirements_json", "[]"))
            return d
    def save_evidence_verification_record(
        self,
        record_id: str,
        evidence_type: str,
        evidence_id: str,
        verifier: str,
        status: str,
        detail: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO evidence_verification_records
                   (id, evidence_type, evidence_id, verifier, status, detail, verified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record_id, evidence_type, evidence_id, verifier, status, detail, time.time()),
            )
    def list_evidence_verification_records(
        self, evidence_type: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        with self._conn() as conn:
            if evidence_type:
                rows = conn.execute(
                    """SELECT * FROM evidence_verification_records
                       WHERE evidence_type = ?
                       ORDER BY verified_at DESC LIMIT ?""",
                    (evidence_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM evidence_verification_records
                       ORDER BY verified_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    def save_contact_verification(
        self,
        peer_webid: str,
        safety_numbers: str,
        verified_by: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO contact_verifications
                   (peer_webid, safety_numbers, verified_at, verified_by)
                   VALUES (?, ?, ?, ?)""",
                (peer_webid, safety_numbers, time.time(), verified_by),
            )
    def get_contact_verification(self, peer_webid: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM contact_verifications WHERE peer_webid=?",
                    (peer_webid,),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def list_verified_contacts(self) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM contact_verifications ORDER BY verified_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def list_contact_verifications(self, owner_webid: str) -> list[dict]:
        """Return all contact verification records for a given owner (verified_by)."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM contact_verifications
                       WHERE verified_by=? ORDER BY verified_at DESC""",
                    (owner_webid,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def apply_contact_verification_sync(self, record: dict) -> None:
        """Upsert a contact verification; higher verification_version wins."""
        peer_webid = record.get("peer_webid", "")
        if not peer_webid:
            return
        with self._conn() as conn:
            try:
                existing = conn.execute(
                    "SELECT verification_version FROM contact_verifications WHERE peer_webid=?",
                    (peer_webid,),
                ).fetchone()
                incoming_version = int(record.get("verification_version", 1))
                if existing is None or incoming_version > existing["verification_version"]:
                    conn.execute(
                        """INSERT OR REPLACE INTO contact_verifications
                           (peer_webid, safety_numbers, verified_at, verified_by,
                            verified_on_device_id, verification_version)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            peer_webid,
                            record.get("safety_numbers", ""),
                            record.get("verified_at", time.time()),
                            record.get("verified_by", ""),
                            record.get("verified_on_device_id", ""),
                            incoming_version,
                        ),
                    )
            except Exception:
                pass
    def save_wg_local_identity(self, pubkey_b64: str, priv_wrapped_b64: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO wg_local_identity
                       (id, pubkey_b64, priv_wrapped_b64, created_at)
                       VALUES (1, ?, ?, COALESCE(
                           (SELECT created_at FROM wg_local_identity WHERE id=1), ?
                       ))""",
                    (pubkey_b64, priv_wrapped_b64, time.time()),
                )
            except Exception:
                pass
    def get_wg_local_identity(self) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM wg_local_identity WHERE id=1"
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def upsert_wg_peer(
        self,
        peer_webid: str,
        peer_pubkey_b64: str,
        endpoint_hint: str | None,
        allowed_ips: str,
        path_mode: str = "unknown",
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO wg_peers
                       (peer_webid, peer_pubkey_b64, endpoint_hint, allowed_ips, path_mode, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(peer_webid) DO UPDATE SET
                           peer_pubkey_b64=excluded.peer_pubkey_b64,
                           endpoint_hint=excluded.endpoint_hint,
                           allowed_ips=excluded.allowed_ips,
                           path_mode=excluded.path_mode,
                           updated_at=excluded.updated_at""",
                    (peer_webid, peer_pubkey_b64, endpoint_hint, allowed_ips, path_mode, now),
                )
            except Exception:
                pass
    def update_wg_peer_path_mode(
        self, peer_webid: str, path_mode: str, last_handshake_at: float | None = None
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            try:
                if last_handshake_at is not None:
                    conn.execute(
                        """UPDATE wg_peers SET path_mode=?, last_handshake_at=?, updated_at=?
                           WHERE peer_webid=?""",
                        (path_mode, last_handshake_at, now, peer_webid),
                    )
                else:
                    conn.execute(
                        "UPDATE wg_peers SET path_mode=?, updated_at=? WHERE peer_webid=?",
                        (path_mode, now, peer_webid),
                    )
            except Exception:
                pass
    def get_wg_peer(self, peer_webid: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM wg_peers WHERE peer_webid=?", (peer_webid,)
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def get_wg_peers_by_mode(self, path_mode: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM wg_peers WHERE path_mode=? ORDER BY updated_at DESC",
                    (path_mode,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def log_wg_connectivity_event(
        self, peer_webid: str, old_mode: str | None, new_mode: str, reason: str = ""
    ) -> None:
        import uuid as _uuid_wg
        event_id = str(_uuid_wg.uuid4())
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO wg_connectivity_events
                       (id, peer_webid, old_mode, new_mode, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (event_id, peer_webid, old_mode, new_mode, reason, time.time()),
                )
            except Exception:
                pass
    def get_wg_connectivity_events(
        self, peer_webid: str, limit: int = 50
    ) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM wg_connectivity_events
                       WHERE peer_webid=? ORDER BY created_at DESC LIMIT ?""",
                    (peer_webid, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def create_hole_punch_attempt(
        self,
        attempt_id: str,
        peer_webid: str,
        local_ip: str,
        local_port: int,
        initiator_webid: str = "",
        responder_webid: str = "",
        attempt_nonce: str = "",
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO hole_punch_attempts
                       (id, peer_webid, local_ip, local_port, state, initiated_at, updated_at,
                        initiator_webid, responder_webid, attempt_nonce)
                       VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
                    (attempt_id, peer_webid, local_ip, local_port, now, now,
                     initiator_webid, responder_webid, attempt_nonce),
                )
            except Exception:
                pass
    def get_hole_punch_attempt_for_actor(
        self, attempt_id: str, actor_webid: str
    ) -> dict | None:
        """Return the attempt only if actor_webid is initiator or responder."""
        attempt = self.get_hole_punch_attempt(attempt_id)
        if attempt is None:
            return None
        if actor_webid in (attempt.get("initiator_webid", ""), attempt.get("responder_webid", "")):
            return attempt
        return None
    def update_hole_punch_attempt(self, attempt_id: str, **kwargs) -> None:
        if not kwargs:
            return
        allowed = {"peer_ip", "peer_port", "state", "updated_at", "completed_at"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        fields.setdefault("updated_at", time.time())
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [attempt_id]
        with self._conn() as conn:
            try:
                conn.execute(
                    f"UPDATE hole_punch_attempts SET {set_clause} WHERE id=?",
                    values,
                )
            except Exception:
                pass
    def get_hole_punch_attempt(self, attempt_id: str) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM hole_punch_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None
    def get_hole_punch_attempts_for_peer(self, peer_webid: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM hole_punch_attempts
                       WHERE peer_webid=? ORDER BY initiated_at DESC""",
                    (peer_webid,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []
    def expire_stale_hole_punch_attempts(self, timeout_seconds: int = 30) -> int:
        cutoff = time.time() - timeout_seconds
        with self._conn() as conn:
            try:
                cur = conn.execute(
                    """UPDATE hole_punch_attempts
                       SET state='expired', updated_at=?
                       WHERE state NOT IN ('succeeded', 'failed', 'expired')
                         AND initiated_at <= ?""",
                    (time.time(), cutoff),
                )
                return cur.rowcount
            except Exception:
                return 0
