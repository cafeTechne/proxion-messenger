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


class LocalStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def checkpoint(self) -> None:
        """Flush the WAL to the main database file (call before shutdown)."""
        with self._conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # Current schema version — increment when adding new migrations below
    _SCHEMA_VERSION = 51

    # Set by _init_db if PRAGMA integrity_check fails — blocks mutating operations
    _integrity_ok: bool = True

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
            """)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version VALUES (0)")
                conn.commit()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS rooms (
                    room_id       TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    code          TEXT NOT NULL UNIQUE,
                    invite_url    TEXT,
                    history_mode  TEXT NOT NULL DEFAULT 'none',
                    creator_webid TEXT NOT NULL DEFAULT '',
                    created_at    REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS room_members (
                    room_id TEXT NOT NULL,
                    webid   TEXT NOT NULL,
                    PRIMARY KEY (room_id, webid)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id        TEXT PRIMARY KEY,
                    thread_id         TEXT NOT NULL,
                    thread_type       TEXT NOT NULL,
                    from_webid        TEXT NOT NULL,
                    from_display_name TEXT,
                    content           TEXT NOT NULL,
                    timestamp         TEXT NOT NULL,
                    edited_at         TEXT,
                    reply_to_id       TEXT,
                    imported          INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_msg_thread_ts
                    ON messages(thread_id, timestamp);

                CREATE TABLE IF NOT EXISTS display_names (
                    webid        TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    updated_at   REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dm_threads (
                    thread_id    TEXT PRIMARY KEY,
                    peer_webid   TEXT NOT NULL,
                    display_name TEXT,
                    owner_webid  TEXT NOT NULL DEFAULT '',
                    created_at   REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_invites (
                    invitation_id  TEXT PRIMARY KEY,
                    invite_json    TEXT NOT NULL,
                    target_did     TEXT NOT NULL,
                    created_at     INTEGER NOT NULL,
                    status         TEXT NOT NULL DEFAULT 'pending'
                );

                CREATE TABLE IF NOT EXISTS reactions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id     TEXT NOT NULL,
                    message_id  TEXT NOT NULL,
                    emoji       TEXT NOT NULL,
                    sender_webid TEXT NOT NULL,
                    created_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
                    UNIQUE(room_id, message_id, emoji, sender_webid)
                );
                CREATE INDEX IF NOT EXISTS idx_reactions_room ON reactions(room_id);

                CREATE TABLE IF NOT EXISTS room_roles (
                    room_id TEXT NOT NULL,
                    webid   TEXT NOT NULL,
                    role    TEXT NOT NULL DEFAULT 'member',
                    PRIMARY KEY (room_id, webid)
                );

                CREATE TABLE IF NOT EXISTS pins (
                    pin_id      TEXT PRIMARY KEY,
                    thread_id   TEXT NOT NULL,
                    message_id  TEXT NOT NULL,
                    pinned_by   TEXT NOT NULL,
                    pinned_at   REAL NOT NULL,
                    content     TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pins_thread ON pins(thread_id);

                CREATE TABLE IF NOT EXISTS last_read (
                    webid       TEXT NOT NULL,
                    channel_id  TEXT NOT NULL,
                    last_read_ts REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (webid, channel_id)
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    certificate_id  TEXT PRIMARY KEY,
                    peer_pub_hex    TEXT NOT NULL,
                    peer_did        TEXT,
                    cert_json       TEXT NOT NULL,
                    created_at      INTEGER NOT NULL,
                    expires_at      INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rel_peer ON relationships(peer_pub_hex);

                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id          TEXT PRIMARY KEY,
                    thread_id   TEXT NOT NULL,
                    from_webid  TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    send_at     REAL NOT NULL,
                    created_at  REAL NOT NULL,
                    cancelled   INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS webhooks (
                    id           TEXT PRIMARY KEY,
                    thread_id    TEXT NOT NULL,
                    owner_webid  TEXT NOT NULL,
                    direction    TEXT NOT NULL,
                    token        TEXT NOT NULL,
                    url          TEXT,
                    bot_name     TEXT DEFAULT 'Bot',
                    created_at   REAL NOT NULL,
                    active       INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS peer_gateways (
                    did         TEXT PRIMARY KEY,
                    gateway_url TEXT NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS x25519_pubs (
                    did        TEXT PRIMARY KEY,
                    pub_b64u   TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_relays (
                    id              TEXT PRIMARY KEY,
                    to_webid        TEXT NOT NULL,
                    to_gateway_url  TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    created_at      REAL NOT NULL,
                    attempt_count   INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at REAL,
                    status          TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS idx_pending_relays_status
                    ON pending_relays(status, last_attempt_at);

                CREATE TABLE IF NOT EXISTS revocations (
                    cert_id    TEXT PRIMARY KEY,
                    peer_did   TEXT NOT NULL,
                    revoked_at REAL NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(
                    message_id UNINDEXED,
                    thread_id  UNINDEXED,
                    content,
                    from_webid UNINDEXED,
                    from_display_name,
                    timestamp UNINDEXED,
                    content='messages',
                    content_rowid='rowid'
                );

                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, message_id, thread_id, content, from_webid, from_display_name, timestamp)
                    VALUES (new.rowid, new.message_id, new.thread_id, new.content, new.from_webid, new.from_display_name, new.timestamp);
                END;

                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, message_id, thread_id, content, from_webid, from_display_name, timestamp)
                    VALUES ('delete', old.rowid, old.message_id, old.thread_id, old.content, old.from_webid, old.from_display_name, old.timestamp);
                END;
            """)
            self._run_migrations(conn)

    def _run_migrations(self, conn: "sqlite3.Connection") -> None:
        """Apply numbered migrations sequentially. Each runs in its own transaction."""
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row else 0

        migrations = [
            # 1: add owner_webid to dm_threads
            "ALTER TABLE dm_threads ADD COLUMN owner_webid TEXT NOT NULL DEFAULT ''",
            # 2: add edited_at to messages
            "ALTER TABLE messages ADD COLUMN edited_at TEXT",
            # 3: add creator_webid to rooms
            "ALTER TABLE rooms ADD COLUMN creator_webid TEXT NOT NULL DEFAULT ''",
            # 4: add reply_to_id to messages
            "ALTER TABLE messages ADD COLUMN reply_to_id TEXT",
            # 5: add imported flag to messages
            "ALTER TABLE messages ADD COLUMN imported INTEGER NOT NULL DEFAULT 0",
            # 6: add disappear_after_ms to rooms
            "ALTER TABLE rooms ADD COLUMN disappear_after_ms INTEGER NOT NULL DEFAULT 0",
            # 7: add content_type, audio_b64, duration_ms to messages
            [
                "ALTER TABLE messages ADD COLUMN content_type TEXT DEFAULT 'text'",
                "ALTER TABLE messages ADD COLUMN audio_b64 TEXT",
                "ALTER TABLE messages ADD COLUMN duration_ms INTEGER DEFAULT 0",
            ],
            # 8: add revocations table
            """CREATE TABLE IF NOT EXISTS revocations (
                cert_id    TEXT PRIMARY KEY,
                peer_did   TEXT NOT NULL,
                revoked_at REAL NOT NULL
            )""",
            # 9: (reserved for future use)
            None,
            # 10: add received_at for server-side ordering
            "ALTER TABLE messages ADD COLUMN received_at TEXT",
            # 11: message edit history audit table
            """CREATE TABLE IF NOT EXISTS message_edits (
                edit_id      TEXT PRIMARY KEY,
                message_id   TEXT NOT NULL,
                prev_content TEXT NOT NULL,
                new_content  TEXT NOT NULL,
                edited_by    TEXT NOT NULL,
                edited_at    TEXT NOT NULL
            )""",
            # 12: room read receipts + unread tracking
            [
                "ALTER TABLE rooms ADD COLUMN unread_count INTEGER NOT NULL DEFAULT 0",
                """CREATE TABLE IF NOT EXISTS room_read_receipts (
                    room_id              TEXT NOT NULL,
                    member_webid         TEXT NOT NULL,
                    last_read_message_id TEXT NOT NULL,
                    last_read_at         TEXT NOT NULL,
                    PRIMARY KEY (room_id, member_webid)
                )""",
            ],
            # 13: contacts index for discovery/search
            """CREATE TABLE IF NOT EXISTS contacts (
                webid        TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                avatar_url   TEXT,
                source       TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )""",
            # 14: rebuild contacts with composite PK (webid, owner_webid)
            [
                """CREATE TABLE IF NOT EXISTS contacts_new (
                    webid        TEXT NOT NULL,
                    owner_webid  TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL,
                    avatar_url   TEXT,
                    source       TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (webid, owner_webid)
                )""",
                "INSERT OR IGNORE INTO contacts_new SELECT webid, '', display_name, avatar_url, source, last_seen_at FROM contacts",
                "DROP TABLE IF EXISTS contacts",
                "ALTER TABLE contacts_new RENAME TO contacts",
                "CREATE INDEX IF NOT EXISTS idx_contacts_owner ON contacts(owner_webid)",
            ],
            # 15: add owner_webid to relationships table
            [
                "ALTER TABLE relationships ADD COLUMN owner_webid TEXT NOT NULL DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_rel_owner ON relationships(owner_webid)",
            ],
            # 16: per-user room unread counts
            """CREATE TABLE IF NOT EXISTS room_unread_counts (
                room_id TEXT NOT NULL,
                webid   TEXT NOT NULL,
                unread  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (room_id, webid)
            )""",
            # 17: seq_num for cryptographic history integrity (Round 13)
            "ALTER TABLE messages ADD COLUMN seq_num INTEGER NOT NULL DEFAULT 0",
            # 18: prev_hash for Merkle chain integrity (Round 14)
            "ALTER TABLE messages ADD COLUMN prev_hash TEXT NOT NULL DEFAULT ''",
            # 19: relay_queue for durable offline relay message storage
            """CREATE TABLE IF NOT EXISTS relay_queue (
                id           TEXT PRIMARY KEY,
                payload      TEXT NOT NULL,
                target_url   TEXT NOT NULL,
                created_at   INTEGER NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                last_attempt INTEGER
            )""",
            # 20: webhook IP allowlist and secret-token hardening
            [
                "ALTER TABLE webhooks ADD COLUMN allowed_ips TEXT",
                "ALTER TABLE webhooks ADD COLUMN secret_token TEXT",
            ],
            # 21: security audit log table
            [
                """CREATE TABLE IF NOT EXISTS audit_logs (
                    id          TEXT PRIMARY KEY,
                    event_type  TEXT NOT NULL,
                    severity    TEXT NOT NULL DEFAULT 'info',
                    webid       TEXT,
                    ip          TEXT,
                    metadata    TEXT,
                    timestamp   REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_event ON audit_logs(event_type, timestamp)",
            ],
            # 22: revocation flag on relationships table
            "ALTER TABLE relationships ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0",
            # 23: HMAC-hashed room invites and join-attempt rate tracking
            [
                """CREATE TABLE IF NOT EXISTS room_invites (
                    invite_id   TEXT PRIMARY KEY,
                    room_id     TEXT NOT NULL,
                    code_hash   TEXT NOT NULL UNIQUE,
                    uses_left   INTEGER NOT NULL DEFAULT 1,
                    created_at  REAL NOT NULL,
                    expires_at  REAL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_room_invites_code ON room_invites(code_hash)",
                """CREATE TABLE IF NOT EXISTS room_join_attempts (
                    id          TEXT PRIMARY KEY,
                    code_hash   TEXT NOT NULL,
                    ip          TEXT NOT NULL DEFAULT '',
                    attempted_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_join_attempts_code ON room_join_attempts(code_hash, attempted_at)",
            ],
            # 24: durable relay nonce dedup (replay protection across restarts)
            [
                """CREATE TABLE IF NOT EXISTS relay_seen_nonces (
                    nonce_key TEXT PRIMARY KEY,
                    seen_at   REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_relay_seen_nonces_seen_at ON relay_seen_nonces(seen_at)",
            ],
            # 25: message-ID dedup + per-room scoped join attempts v2
            [
                """CREATE TABLE IF NOT EXISTS relay_seen_ids (
                    dedup_key TEXT PRIMARY KEY,
                    seen_at   REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_relay_seen_ids_seen_at ON relay_seen_ids(seen_at)",
                """CREATE TABLE IF NOT EXISTS room_join_attempts_v2 (
                    ip          TEXT NOT NULL,
                    room_hint   TEXT NOT NULL,
                    attempted_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_room_join_attempts_v2_ip_ts ON room_join_attempts_v2(ip, attempted_at)",
                "CREATE INDEX IF NOT EXISTS idx_room_join_attempts_v2_room ON room_join_attempts_v2(room_hint, attempted_at)",
            ],
            # 26: audit chain integrity + relay expiry + security events
            [
                "ALTER TABLE audit_logs ADD COLUMN prev_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE audit_logs ADD COLUMN entry_hash TEXT NOT NULL DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs(timestamp)",
                "ALTER TABLE pending_relays ADD COLUMN expires_at REAL NOT NULL DEFAULT 0",
                """CREATE TABLE IF NOT EXISTS security_events (
                    id         TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    severity   TEXT NOT NULL DEFAULT 'info',
                    webid      TEXT,
                    ip         TEXT,
                    details    TEXT,
                    created_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_security_events_created ON security_events(created_at)",
            ],
            # 27: invite nonce dedup + webhook rotation + per-endpoint rate tracking
            [
                """CREATE TABLE IF NOT EXISTS invite_seen_nonces (
                    nonce   TEXT PRIMARY KEY,
                    seen_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_invite_seen_nonces_seen_at ON invite_seen_nonces(seen_at)",
                "ALTER TABLE webhooks ADD COLUMN rotated_at REAL",
                "ALTER TABLE webhooks ADD COLUMN previous_token TEXT",
            ],
            # 28: cert policy audit columns + DPoP JTI replay cache
            [
                "ALTER TABLE relationships ADD COLUMN cert_policy_version INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE relationships ADD COLUMN cert_validated_at REAL",
                "CREATE INDEX IF NOT EXISTS idx_relationships_policy ON relationships(cert_policy_version, cert_validated_at)",
                """CREATE TABLE IF NOT EXISTS dpop_seen_jti (
                    jti     TEXT PRIMARY KEY,
                    seen_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_dpop_seen_jti_seen_at ON dpop_seen_jti(seen_at)",
            ],
            # 29: message-ID collision index + webhook delivery audit
            [
                """CREATE INDEX IF NOT EXISTS idx_messages_id_from_thread
                   ON messages(message_id, from_webid, thread_id)""",
                """CREATE TABLE IF NOT EXISTS webhook_delivery_logs (
                    id           TEXT PRIMARY KEY,
                    webhook_id   TEXT NOT NULL,
                    thread_id    TEXT NOT NULL,
                    status_code  INTEGER,
                    success      INTEGER NOT NULL,
                    latency_ms   INTEGER,
                    created_at   REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_wh
                   ON webhook_delivery_logs(webhook_id, created_at)""",
            ],
            # 30: thread integrity state + import provenance + webhook circuit breaker columns
            [
                """CREATE TABLE IF NOT EXISTS thread_integrity_state (
                    thread_id    TEXT PRIMARY KEY,
                    last_seq_num INTEGER NOT NULL DEFAULT 0,
                    last_prev_hash TEXT NOT NULL DEFAULT '',
                    checked_at   REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_thread_integrity_checked_at ON thread_integrity_state(checked_at)",
                """CREATE TABLE IF NOT EXISTS import_provenance (
                    id           TEXT PRIMARY KEY,
                    source       TEXT,
                    body_sha256  TEXT,
                    imported_by  TEXT,
                    imported_at  REAL NOT NULL,
                    dry_run      INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_import_provenance_at ON import_provenance(imported_at)",
                "ALTER TABLE webhook_delivery_logs ADD COLUMN circuit_open INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE webhook_delivery_logs ADD COLUMN failure_streak INTEGER NOT NULL DEFAULT 0",
            ],
            # 31: peer gateway pinning, relay delivery chain, invite abuse counters, recovery ops
            [
                """CREATE TABLE IF NOT EXISTS peer_gateway_pins (
                    peer_did TEXT PRIMARY KEY,
                    pinned_gateway_url TEXT NOT NULL,
                    pinned_at REAL NOT NULL,
                    last_seen_gateway_url TEXT NOT NULL,
                    last_seen_at REAL NOT NULL,
                    pending_change INTEGER NOT NULL DEFAULT 0
                )""",
                """CREATE TABLE IF NOT EXISTS peer_gateway_change_requests (
                    id TEXT PRIMARY KEY,
                    peer_did TEXT NOT NULL,
                    old_gateway_url TEXT NOT NULL,
                    new_gateway_url TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0,
                    approved_at REAL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_peer_gateway_change_requests_peer ON peer_gateway_change_requests(peer_did, observed_at)",
                """CREATE TABLE IF NOT EXISTS relay_delivery_chain (
                    relay_id TEXT PRIMARY KEY,
                    peer_did TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prev_hash TEXT NOT NULL DEFAULT '',
                    entry_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_relay_delivery_chain_peer ON relay_delivery_chain(peer_did, created_at)",
                """CREATE TABLE IF NOT EXISTS invite_pair_counters (
                    pair_key TEXT NOT NULL,
                    bucket_start REAL NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (pair_key, bucket_start)
                )""",
                """CREATE TABLE IF NOT EXISTS invite_source_counters (
                    source_ip TEXT NOT NULL,
                    bucket_start REAL NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (source_ip, bucket_start)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_invite_source_counters_ip ON invite_source_counters(source_ip, bucket_start)",
                """CREATE TABLE IF NOT EXISTS recovery_operations (
                    op_id TEXT PRIMARY KEY,
                    op_type TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    confirmed_at REAL,
                    expires_at REAL NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_recovery_operations_expires ON recovery_operations(expires_at)",
            ],
            # 32: peer trust disputes, table checksums, federation quarantine,
            #     recovery operation fingerprint + consumed_at columns
            [
                """CREATE TABLE IF NOT EXISTS peer_trust_disputes (
                    id TEXT PRIMARY KEY,
                    peer_did TEXT NOT NULL,
                    dispute_type TEXT NOT NULL,
                    observed_value TEXT NOT NULL,
                    expected_value TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at REAL NOT NULL,
                    resolved_at REAL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_peer_trust_disputes_peer ON peer_trust_disputes(peer_did, created_at)",
                """CREATE TABLE IF NOT EXISTS table_checksums (
                    table_name TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    computed_at REAL NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS federation_quarantine (
                    id TEXT PRIMARY KEY,
                    item_type TEXT NOT NULL,
                    source_identity TEXT,
                    payload_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    released INTEGER NOT NULL DEFAULT 0,
                    dropped INTEGER NOT NULL DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_federation_quarantine_created ON federation_quarantine(created_at)",
                "ALTER TABLE recovery_operations ADD COLUMN requester_fingerprint TEXT",
                "ALTER TABLE recovery_operations ADD COLUMN consumed_at REAL",
            ],
            # 33: credential anomalies, identity key history + rollover events, retention locks
            [
                """CREATE TABLE IF NOT EXISTS credential_anomalies (
                    id TEXT PRIMARY KEY,
                    anomaly_type TEXT NOT NULL,
                    identity TEXT,
                    detail TEXT,
                    created_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_credential_anomalies_created ON credential_anomalies(created_at)",
                """CREATE TABLE IF NOT EXISTS identity_key_history (
                    identity TEXT NOT NULL,
                    pubkey_hex TEXT NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    trusted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(identity, pubkey_hex)
                )""",
                """CREATE TABLE IF NOT EXISTS identity_rollover_events (
                    id TEXT PRIMARY KEY,
                    identity TEXT NOT NULL,
                    old_pubkey_hex TEXT,
                    new_pubkey_hex TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    resolved_at REAL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_identity_rollover_events_identity ON identity_rollover_events(identity, created_at)",
                """CREATE TABLE IF NOT EXISTS retention_locks (
                    lock_name TEXT PRIMARY KEY,
                    locked_until REAL NOT NULL,
                    created_at REAL NOT NULL
                )""",
            ],
            # 34: trust revocations, dual-control admin actions, snapshot chain, operation budgets
            [
                """CREATE TABLE IF NOT EXISTS trust_revocations (
                    id TEXT PRIMARY KEY,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    revoked_by TEXT NOT NULL,
                    revoked_at REAL NOT NULL,
                    expires_at REAL,
                    active INTEGER NOT NULL DEFAULT 1
                )""",
                "CREATE INDEX IF NOT EXISTS idx_trust_revocations_subject ON trust_revocations(subject_type, subject_id, active, revoked_at)",
                """CREATE TABLE IF NOT EXISTS trust_revocation_links (
                    revocation_id TEXT NOT NULL,
                    related_subject_type TEXT NOT NULL,
                    related_subject_id TEXT NOT NULL,
                    PRIMARY KEY (revocation_id, related_subject_type, related_subject_id)
                )""",
                """CREATE TABLE IF NOT EXISTS pending_admin_actions (
                    action_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    confirmed_by TEXT,
                    confirmed_at REAL,
                    expires_at REAL NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_pending_admin_actions_exp ON pending_admin_actions(expires_at, confirmed, consumed)",
                """CREATE TABLE IF NOT EXISTS security_snapshot_chain (
                    snapshot_id TEXT PRIMARY KEY,
                    prev_hash TEXT NOT NULL DEFAULT '',
                    snapshot_hash TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    signer_key_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_security_snapshot_chain_created ON security_snapshot_chain(created_at)",
                """CREATE TABLE IF NOT EXISTS operation_budgets (
                    op_type TEXT NOT NULL,
                    day_key TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (op_type, day_key)
                )""",
            ],
            # 35: compromise recovery, thread participant bindings, policy change log,
            #     quarantine forensics columns, event stream
            [
                """CREATE TABLE IF NOT EXISTS compromise_recovery_sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    initiated_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_compromise_sessions_status ON compromise_recovery_sessions(status, updated_at)",
                """CREATE TABLE IF NOT EXISTS compromise_recovery_steps (
                    session_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    step_status TEXT NOT NULL,
                    detail TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (session_id, step_name)
                )""",
                """CREATE TABLE IF NOT EXISTS thread_participant_bindings (
                    thread_id TEXT NOT NULL,
                    webid TEXT NOT NULL,
                    binding_source TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (thread_id, webid)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_thread_participant_bindings_webid ON thread_participant_bindings(webid, updated_at)",
                """CREATE TABLE IF NOT EXISTS policy_change_log (
                    id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    loaded_from TEXT,
                    changed_by TEXT,
                    changed_at REAL NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS idx_policy_change_log_time ON policy_change_log(changed_at)",
                # Quarantine forensics columns
                "ALTER TABLE federation_quarantine ADD COLUMN payload_sha256 TEXT",
                "ALTER TABLE federation_quarantine ADD COLUMN source_ip TEXT",
                "ALTER TABLE federation_quarantine ADD COLUMN released_at REAL",
                "ALTER TABLE federation_quarantine ADD COLUMN dropped_at REAL",
            ],
            # 36: pod capability profiles + notification fallback events
            [
                """CREATE TABLE IF NOT EXISTS pod_capability_profiles (
                    pod_origin TEXT PRIMARY KEY,
                    notifications_supported INTEGER NOT NULL DEFAULT 0,
                    channel_types_json TEXT NOT NULL DEFAULT '[]',
                    auth_requirements_json TEXT NOT NULL DEFAULT '[]',
                    last_verified_at REAL NOT NULL,
                    verification_source TEXT NOT NULL DEFAULT 'runtime_probe'
                )""",
                """CREATE TABLE IF NOT EXISTS notification_fallback_events (
                    id TEXT PRIMARY KEY,
                    pod_origin TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    detail TEXT,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_notification_fallback_events_origin
                   ON notification_fallback_events(pod_origin, created_at)""",
            ],
            # 37: peer attestations, scoped operation budgets, policy tier transitions,
            #     event stream cursors
            [
                """CREATE TABLE IF NOT EXISTS peer_attestations (
                    peer_did TEXT PRIMARY KEY,
                    attestation_json TEXT NOT NULL,
                    attestation_hash TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    verified_at REAL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_peer_attestations_expires
                   ON peer_attestations(expires_at)""",
                """CREATE TABLE IF NOT EXISTS operation_budget_scopes (
                    op_type TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    day_key TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (op_type, scope_key, day_key)
                )""",
                """CREATE INDEX IF NOT EXISTS idx_operation_budget_scopes_day
                   ON operation_budget_scopes(day_key, op_type)""",
                """CREATE TABLE IF NOT EXISTS policy_tier_transitions (
                    id TEXT PRIMARY KEY,
                    from_tier TEXT NOT NULL,
                    to_tier TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_detail TEXT,
                    actor_webid TEXT,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_policy_tier_transitions_time
                   ON policy_tier_transitions(created_at)""",
                """CREATE TABLE IF NOT EXISTS event_stream_cursors (
                    consumer_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )""",
            ],
            # 38: security SLO snapshots + drill results
            [
                """CREATE TABLE IF NOT EXISTS security_slo_snapshots (
                    id TEXT PRIMARY KEY,
                    window_start REAL NOT NULL,
                    window_end REAL NOT NULL,
                    metrics_json TEXT NOT NULL,
                    evaluated_at REAL NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS security_drill_results (
                    drill_id TEXT PRIMARY KEY,
                    drill_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_seconds INTEGER,
                    findings_json TEXT NOT NULL,
                    executed_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_security_drill_results_time
                   ON security_drill_results(executed_at)""",
            ],
            # 39: evidence verification records for WORM + external auditor support
            [
                """CREATE TABLE IF NOT EXISTS evidence_verification_records (
                    id TEXT PRIMARY KEY,
                    evidence_type TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    verifier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    verified_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_evidence_verification_records_time
                   ON evidence_verification_records(verified_at)""",
            ],
            # 40: DM forward-secret session tables (X3DH + chain ratchet)
            [
                """CREATE TABLE IF NOT EXISTS dm_sessions (
                    session_id TEXT PRIMARY KEY,
                    peer_webid TEXT NOT NULL,
                    owner_webid TEXT NOT NULL,
                    root_key_b64 TEXT NOT NULL,
                    send_chain_key_b64 TEXT NOT NULL,
                    recv_chain_key_b64 TEXT NOT NULL,
                    send_count INTEGER NOT NULL DEFAULT 0,
                    recv_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_dm_sessions_peer_owner
                   ON dm_sessions(peer_webid, owner_webid)""",
                """CREATE TABLE IF NOT EXISTS dm_prekeys (
                    prekey_id INTEGER PRIMARY KEY,
                    owner_webid TEXT NOT NULL,
                    pub_b64 TEXT NOT NULL,
                    priv_wrapped_b64 TEXT NOT NULL,
                    one_time INTEGER NOT NULL DEFAULT 1,
                    used INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_dm_prekeys_owner_used
                   ON dm_prekeys(owner_webid, used)""",
            ],
            # 41: persistent rate-limit buckets (survive process restart)
            [
                """CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                    bucket_key TEXT PRIMARY KEY,
                    count INTEGER NOT NULL,
                    window_start REAL NOT NULL,
                    updated_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_rate_limit_buckets_updated
                   ON rate_limit_buckets(updated_at)""",
            ],
            # 42: delivery and read receipts
            [
                """CREATE TABLE IF NOT EXISTS message_receipts (
                    message_id TEXT NOT NULL,
                    receiver_webid TEXT NOT NULL,
                    delivered_at TEXT,
                    read_at TEXT,
                    PRIMARY KEY (message_id, receiver_webid)
                )""",
                """CREATE INDEX IF NOT EXISTS idx_message_receipts_read_at
                   ON message_receipts(read_at)""",
            ],
            # 43: contact verification (safety numbers)
            [
                """CREATE TABLE IF NOT EXISTS contact_verifications (
                    peer_webid TEXT PRIMARY KEY,
                    safety_numbers TEXT NOT NULL,
                    verified_at REAL NOT NULL,
                    verified_by TEXT NOT NULL
                )""",
            ],
            # 44: sender keys (group E2E) + WebPush subscriptions
            [
                """CREATE TABLE IF NOT EXISTS sender_keys (
                    room_id TEXT NOT NULL,
                    sender_webid TEXT NOT NULL,
                    chain_key_b64 TEXT NOT NULL,
                    iteration INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (room_id, sender_webid)
                )""",
                """CREATE INDEX IF NOT EXISTS idx_sender_keys_room
                   ON sender_keys(room_id)""",
                """CREATE TABLE IF NOT EXISTS push_subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    owner_webid TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    p256dh_b64 TEXT NOT NULL,
                    auth_b64 TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_push_subscriptions_owner
                   ON push_subscriptions(owner_webid)""",
            ],
            # 45: device registrations, SPK rotation columns, message seq numbers
            [
                """CREATE TABLE IF NOT EXISTS device_registrations (
                    device_id TEXT PRIMARY KEY,
                    owner_webid TEXT NOT NULL,
                    device_pub_b64 TEXT NOT NULL,
                    attestation_b64 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen_at REAL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_device_registrations_owner
                   ON device_registrations(owner_webid)""",
                "ALTER TABLE dm_prekeys ADD COLUMN spk_created_at REAL DEFAULT 0",
                "ALTER TABLE dm_prekeys ADD COLUMN expired INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE messages ADD COLUMN seq INTEGER",
                """CREATE INDEX IF NOT EXISTS idx_messages_thread_seq
                   ON messages(thread_id, seq)""",
                """CREATE TABLE IF NOT EXISTS room_seq_counters (
                    thread_id TEXT PRIMARY KEY,
                    next_seq INTEGER NOT NULL DEFAULT 0
                )""",
            ],
            # 46: multi-device sessions, device deliveries, sender-key epochs,
            #     idempotency ops, contact verification sync, catch-up watermarks
            [
                "ALTER TABLE dm_sessions ADD COLUMN owner_device_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE dm_sessions ADD COLUMN peer_device_id TEXT NOT NULL DEFAULT ''",
                """CREATE INDEX IF NOT EXISTS idx_dm_sessions_device_scope
                   ON dm_sessions(owner_webid, owner_device_id, peer_webid, peer_device_id, updated_at)""",
                """CREATE TABLE IF NOT EXISTS dm_device_deliveries (
                    message_id TEXT NOT NULL,
                    to_webid TEXT NOT NULL,
                    to_device_id TEXT NOT NULL,
                    delivered_at TEXT,
                    read_at TEXT,
                    PRIMARY KEY (message_id, to_webid, to_device_id)
                )""",
                """CREATE INDEX IF NOT EXISTS idx_dm_device_deliveries_to
                   ON dm_device_deliveries(to_webid, to_device_id, delivered_at)""",
                "ALTER TABLE sender_keys ADD COLUMN epoch INTEGER NOT NULL DEFAULT 1",
                """CREATE INDEX IF NOT EXISTS idx_sender_keys_room_epoch
                   ON sender_keys(room_id, sender_webid, epoch, updated_at)""",
                """CREATE TABLE IF NOT EXISTS idempotency_ops (
                    op_id TEXT PRIMARY KEY,
                    op_type TEXT NOT NULL,
                    actor_webid TEXT NOT NULL,
                    actor_device_id TEXT,
                    result_code TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_idempotency_ops_actor
                   ON idempotency_ops(actor_webid, actor_device_id, created_at)""",
                "ALTER TABLE contact_verifications ADD COLUMN verified_on_device_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE contact_verifications ADD COLUMN verification_version INTEGER NOT NULL DEFAULT 1",
                """CREATE TABLE IF NOT EXISTS catchup_watermarks (
                    owner_webid TEXT NOT NULL,
                    owner_device_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (owner_webid, owner_device_id, thread_id)
                )""",
            ],
            # 47: session recovery attempts, device recovery codes,
            #     message delivery state, device primary flag
            [
                """CREATE TABLE IF NOT EXISTS dm_session_recovery_attempts (
                    thread_id TEXT NOT NULL,
                    session_id TEXT,
                    actor_webid TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (thread_id, actor_webid, attempt_no)
                )""",
                """CREATE INDEX IF NOT EXISTS idx_dm_session_recovery_attempts_thread
                   ON dm_session_recovery_attempts(thread_id, updated_at)""",
                """CREATE TABLE IF NOT EXISTS device_recovery_codes (
                    code_id TEXT PRIMARY KEY,
                    owner_webid TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    used_at REAL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_device_recovery_codes_owner
                   ON device_recovery_codes(owner_webid, created_at)""",
                "ALTER TABLE device_registrations ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE message_receipts ADD COLUMN state TEXT",
            ],
            # 48: WireGuard overlay state — local identity, peers, connectivity events
            [
                """CREATE TABLE IF NOT EXISTS wg_local_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    pubkey_b64 TEXT NOT NULL,
                    priv_wrapped_b64 TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS wg_peers (
                    peer_webid TEXT PRIMARY KEY,
                    peer_pubkey_b64 TEXT NOT NULL,
                    endpoint_hint TEXT,
                    allowed_ips TEXT NOT NULL,
                    path_mode TEXT NOT NULL DEFAULT 'unknown',
                    last_handshake_at REAL,
                    updated_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_wg_peers_mode
                   ON wg_peers(path_mode, updated_at)""",
                """CREATE TABLE IF NOT EXISTS wg_connectivity_events (
                    id TEXT PRIMARY KEY,
                    peer_webid TEXT NOT NULL,
                    old_mode TEXT,
                    new_mode TEXT NOT NULL,
                    reason TEXT,
                    created_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_wg_connectivity_events_peer
                   ON wg_connectivity_events(peer_webid, created_at)""",
            ],
            # 49: STUN sessions + UDP hole punch attempt tracking
            [
                """CREATE TABLE IF NOT EXISTS stun_sessions (
                    id TEXT PRIMARY KEY,
                    external_ip TEXT NOT NULL,
                    external_port INTEGER NOT NULL,
                    stun_server TEXT NOT NULL,
                    discovered_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_stun_sessions_expires
                   ON stun_sessions(expires_at)""",
                """CREATE TABLE IF NOT EXISTS hole_punch_attempts (
                    id TEXT PRIMARY KEY,
                    peer_webid TEXT NOT NULL,
                    local_ip TEXT NOT NULL,
                    local_port INTEGER NOT NULL,
                    peer_ip TEXT,
                    peer_port INTEGER,
                    state TEXT NOT NULL DEFAULT 'pending',
                    initiated_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_hole_punch_attempts_peer
                   ON hole_punch_attempts(peer_webid, state, updated_at)""",
            ],
            # 50: actor binding for hole punch attempts + owner binding for STUN sessions
            [
                "ALTER TABLE hole_punch_attempts ADD COLUMN initiator_webid TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE hole_punch_attempts ADD COLUMN responder_webid TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE hole_punch_attempts ADD COLUMN attempt_nonce TEXT NOT NULL DEFAULT ''",
                """CREATE INDEX IF NOT EXISTS idx_hole_punch_attempts_actor
                   ON hole_punch_attempts(initiator_webid, responder_webid, state, updated_at)""",
                "ALTER TABLE stun_sessions ADD COLUMN owner_webid TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE stun_sessions ADD COLUMN owner_device_id TEXT NOT NULL DEFAULT ''",
                """CREATE INDEX IF NOT EXISTS idx_stun_sessions_owner_expires
                   ON stun_sessions(owner_webid, expires_at)""",
            ],
            # 51: E2E session pod checkpoint ETag tracking
            [
                "ALTER TABLE dm_sessions ADD COLUMN pod_checkpoint_etag TEXT",
            ],
        ]

        for version, migration in enumerate(migrations, start=1):
            if current >= version:
                continue
            try:
                with conn:
                    if migration is None:
                        pass
                    elif isinstance(migration, list):
                        for stmt in migration:
                            try:
                                conn.execute(stmt)
                            except Exception:
                                pass
                    else:
                        try:
                            conn.execute(migration)
                        except Exception:
                            pass
                    conn.execute("UPDATE schema_version SET version = ?", (version,))
                current = version
            except Exception as exc:
                import logging as _log
                _log.getLogger(__name__).warning("Migration %d failed: %s", version, exc)
                break

        # R8: startup integrity check — sets _integrity_ok; callers may gate writes on this
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] == "ok":
                self._integrity_ok = True
            else:
                self._integrity_ok = False
                _ic_detail = result[0] if result else "no result"
                logger.error("Database integrity check failed: %s", _ic_detail)
                try:
                    self.save_security_event("db_integrity_failed", "critical", details=_ic_detail)
                except Exception:
                    pass
        except Exception as _ic_exc:
            self._integrity_ok = False
            logger.error("Database integrity check raised: %s", _ic_exc)
            try:
                self.save_security_event("db_integrity_failed", "critical", details=str(_ic_exc))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Rooms
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Room members
    # ------------------------------------------------------------------

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

    def get_rooms_for_member(self, webid: str) -> list[str]:
        """Return all room_ids that this webid is a member of."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT room_id FROM room_members WHERE webid = ?", (webid,)
            ).fetchall()
            return [r["room_id"] for r in rows]

    # ------------------------------------------------------------------
    # HMAC-hashed room invites (schema v23)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Relay nonce dedup (durable replay protection — schema v24)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Relay message-ID dedup (durable, schema v25)
    # ------------------------------------------------------------------

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

    # --- Invite nonce dedup ---

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

    # --- Webhook rotation ---

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

    # ------------------------------------------------------------------
    # Per-room scoped join attempts v2 (schema v25)
    # ------------------------------------------------------------------

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

    def delete_messages_before(self, thread_id: str, cutoff_iso: str) -> int:
        """Delete messages older than cutoff_iso from a thread. Returns count deleted."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM messages WHERE thread_id = ? AND timestamp < ?",
                (thread_id, cutoff_iso)
            )
            return cur.rowcount

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

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

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
            # R13: message_fts is auto-updated via triggers if configured, 
            # but usually we handle it manually in some SQLite versions.
            # Here we assume triggers or manual update happens elsewhere.

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

    # ------------------------------------------------------------------
    # Edit history (R13.11)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Room read receipts (R13.12)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Contacts (R13.14)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Display names
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # X25519 pub keys (browser-side E2E keys, announced via register/relay)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Unread message counting
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # DM threads
    # ------------------------------------------------------------------

    def save_dm_thread(
        self,
        thread_id: str,
        peer_webid: str,
        display_name: Optional[str] = None,
        owner_webid: str = '',
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO dm_threads
                    (thread_id, peer_webid, display_name, owner_webid, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, peer_webid, display_name, owner_webid, time.time()),
            )
            if display_name:
                conn.execute(
                    "UPDATE dm_threads SET display_name = ? WHERE thread_id = ?",
                    (display_name, thread_id),
                )

    def get_dm_threads(self, owner_webid: str = '') -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM dm_threads WHERE owner_webid = ? ORDER BY created_at ASC",
                (owner_webid,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Pending invites
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Revocations (R12)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

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

    _REACTION_QUOTA = 50  # max distinct reactions per user per room

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

    # ------------------------------------------------------------------
    # Relay queue (durable offline delivery)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Audit logs
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # DPoP JTI replay cache (durable, schema v28)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Scheduled messages
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Room roles
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Last-read tracking
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Peer gateway URLs
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Pending outbound relay queue
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Message-ID collision detection (Round 6)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Webhook delivery logging (Round 6)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Thread integrity state (schema v30)
    # ------------------------------------------------------------------

    def get_thread_integrity_state(self, thread_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT thread_id, last_seq_num, last_prev_hash, checked_at FROM thread_integrity_state WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_thread_integrity_state(
        self, thread_id: str, last_seq_num: int, last_prev_hash: str, checked_at: float
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO thread_integrity_state
                   (thread_id, last_seq_num, last_prev_hash, checked_at)
                   VALUES (?, ?, ?, ?)""",
                (thread_id, last_seq_num, last_prev_hash, checked_at),
            )

    # ------------------------------------------------------------------
    # Import provenance (schema v30)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Peer gateway pinning (schema v31)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Relay delivery chain (schema v31)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Invite abuse counters (schema v31)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Recovery operations (schema v31)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Peer trust disputes (schema v32)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Table checksum ledger (schema v32)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Federation quarantine (schema v32)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Abuse signal rollups (schema v31)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Export/Import (R14)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Credential anomalies (schema v33)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Identity key history and rollover events (schema v33)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Retention locks (schema v33)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Trust revocations (schema v34)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Dual-control admin actions (schema v34)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Security snapshot chain (schema v34)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Operation budgets (schema v34)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Federation quarantine status/transition guards (schema v34)
    # ------------------------------------------------------------------

    @staticmethod
    def _quarantine_status(item: dict) -> str:
        if item.get("released"):
            return "released"
        if item.get("dropped"):
            return "dropped"
        return "pending"

    _VALID_QUARANTINE_TRANSITIONS: dict = {
        "pending": {"released", "dropped"},
    }

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

    # ------------------------------------------------------------------
    # Compromise recovery sessions/steps (schema v35)
    # ------------------------------------------------------------------

    def create_compromise_recovery_session(
        self,
        session_id: str,
        reason: str,
        initiated_by: str,
        steps: list,
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO compromise_recovery_sessions
                   (session_id, status, reason, initiated_by, created_at, updated_at)
                   VALUES (?, 'active', ?, ?, ?, ?)""",
                (session_id, reason, initiated_by, now, now),
            )
            for step in steps:
                conn.execute(
                    """INSERT OR IGNORE INTO compromise_recovery_steps
                       (session_id, step_name, step_status, updated_at)
                       VALUES (?, ?, 'pending', ?)""",
                    (session_id, step, now),
                )

    def get_compromise_recovery_session(self, session_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM compromise_recovery_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = dict(row)
            steps = conn.execute(
                "SELECT * FROM compromise_recovery_steps WHERE session_id = ? ORDER BY rowid",
                (session_id,),
            ).fetchall()
            session["steps"] = [dict(s) for s in steps]
            return session

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

    def list_compromise_recovery_sessions(self, status: Optional[str] = None, limit: int = 50) -> list:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM compromise_recovery_sessions WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM compromise_recovery_sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Thread participant bindings (schema v35)
    # ------------------------------------------------------------------

    def upsert_thread_participant_binding(self, thread_id: str, webid: str, binding_source: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO thread_participant_bindings
                   (thread_id, webid, binding_source, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (thread_id, webid, binding_source, now),
            )

    def get_thread_participants(self, thread_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT webid FROM thread_participant_bindings WHERE thread_id = ?", (thread_id,)
            ).fetchall()
            return [r[0] for r in rows]

    def is_thread_participant_binding(self, thread_id: str, webid: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM thread_participant_bindings WHERE thread_id = ? AND webid = ? LIMIT 1",
                (thread_id, webid),
            ).fetchone()
            return row is not None

    # ------------------------------------------------------------------
    # Policy change log (schema v35)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Event stream cursor-based access (schema v35)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Replay cache cardinality pruning (schema v35)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pod capability profiles + notification fallback events (R14)
    # ------------------------------------------------------------------

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

    _REPLAY_CAP_DEFAULT = 50000

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

    # ------------------------------------------------------------------
    # Peer attestations (schema v37)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Scoped operation budgets (schema v37)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Policy tier transitions (schema v37)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Event stream cursors (schema v37)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Security SLO snapshots (schema v38)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Security drill results (schema v38)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Exit gate helpers (schema v38)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Evidence verification records (schema v39)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # DM session helpers (schema v40)
    # ------------------------------------------------------------------

    def save_dm_session(self, session: dict) -> None:
        """Persist a DM session state dict (from e2e_session.session_to_dict)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO dm_sessions
                   (session_id, peer_webid, owner_webid, root_key_b64,
                    send_chain_key_b64, recv_chain_key_b64,
                    send_count, recv_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session["session_id"],
                    session["peer_webid"],
                    session["owner_webid"],
                    session["root_key"],
                    session["send_chain_key"],
                    session["recv_chain_key"],
                    session.get("send_count", 0),
                    session.get("recv_count", 0),
                    time.time(),
                    time.time(),
                ),
            )

    def get_dm_session(self, owner_webid: str, peer_webid: str) -> dict | None:
        """Return the most recent session state for an owner/peer pair."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT * FROM dm_sessions
                       WHERE owner_webid = ? AND peer_webid = ?
                       ORDER BY updated_at DESC LIMIT 1""",
                    (owner_webid, peer_webid),
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                return {
                    "session_id": d["session_id"],
                    "peer_webid": d["peer_webid"],
                    "owner_webid": d["owner_webid"],
                    "root_key": d["root_key_b64"],
                    "send_chain_key": d["send_chain_key_b64"],
                    "recv_chain_key": d["recv_chain_key_b64"],
                    "send_count": d["send_count"],
                    "recv_count": d["recv_count"],
                }
            except Exception:
                return None

    def save_prekey(
        self,
        prekey_id: int,
        owner_webid: str,
        pub_b64: str,
        priv_wrapped_b64: str,
        one_time: bool = True,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO dm_prekeys
                   (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, one_time, used, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, 1 if one_time else 0, time.time()),
            )

    def get_signed_prekey(self, owner_webid: str) -> dict | None:
        """Return the stored signed prekey (one_time=0) for an owner."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM dm_prekeys WHERE owner_webid=? AND one_time=0 ORDER BY created_at DESC LIMIT 1",
                    (owner_webid,),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None

    def claim_one_time_prekey(self, owner_webid: str) -> dict | None:
        """Return and mark-used one unused one-time prekey for owner_webid."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM dm_prekeys WHERE owner_webid=? AND one_time=1 AND used=0 ORDER BY created_at ASC LIMIT 1",
                    (owner_webid,),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE dm_prekeys SET used=1 WHERE prekey_id=?",
                    (row["prekey_id"],),
                )
                return dict(row)
            except Exception:
                return None

    def get_prekey_bundle(self, owner_webid: str) -> dict | None:
        """Return public prekey bundle (no private halves) for session initiation."""
        spk = self.get_signed_prekey(owner_webid)
        if not spk:
            return None
        opk = self.claim_one_time_prekey(owner_webid)
        bundle: dict = {
            "owner_webid": owner_webid,
            "signed_prekey_id": spk["prekey_id"],
            "signed_prekey_pub_b64": spk["pub_b64"],
        }
        if opk:
            bundle["one_time_prekey_id"] = opk["prekey_id"]
            bundle["one_time_prekey_pub_b64"] = opk["pub_b64"]
        return bundle

    # ------------------------------------------------------------------
    # Persistent rate-limit buckets (schema v41)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Delivery / read receipts (schema v42)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Prekey helpers (schema v40) — extended for replenishment
    # ------------------------------------------------------------------

    def count_unused_one_time_prekeys(self, owner_webid: str) -> int:
        """Return the number of unused one-time prekeys available for owner_webid."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM dm_prekeys "
                    "WHERE owner_webid=? AND one_time=1 AND used=0",
                    (owner_webid,),
                ).fetchone()
                return row[0] if row else 0
            except Exception:
                return 0

    # ------------------------------------------------------------------
    # DM session lifecycle (schema v40) — extended
    # ------------------------------------------------------------------

    def delete_dm_session(self, session_id: str) -> None:
        """Delete a DM session by its session_id."""
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM dm_sessions WHERE session_id=?", (session_id,))
            except Exception:
                pass

    def get_dm_session_by_id(self, session_id: str) -> dict | None:
        """Return a DM session state dict for the given session_id."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM dm_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                return {
                    "session_id": d["session_id"],
                    "peer_webid": d["peer_webid"],
                    "owner_webid": d["owner_webid"],
                    "root_key": d["root_key_b64"],
                    "send_chain_key": d["send_chain_key_b64"],
                    "recv_chain_key": d["recv_chain_key_b64"],
                    "send_count": d["send_count"],
                    "recv_count": d["recv_count"],
                    "updated_at": d["updated_at"],
                }
            except Exception:
                return None

    def set_dm_session_checkpoint_etag(self, session_id: str, etag: str) -> None:
        """Store the ETag from the last successful pod checkpoint for a DM session."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE dm_sessions SET pod_checkpoint_etag=? WHERE session_id=?",
                    (etag, session_id),
                )
            except Exception:
                pass

    def get_dm_session_checkpoint_etag(self, session_id: str) -> str | None:
        """Return the stored pod checkpoint ETag for a DM session, or None."""
        with self._conn() as conn:
            try:
                row = conn.execute(
                    "SELECT pod_checkpoint_etag FROM dm_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return None
                return row["pod_checkpoint_etag"]
            except Exception:
                return None

    def list_dm_sessions(self, owner_webid: str) -> list[dict]:
        """Return all DM sessions owned by owner_webid, newest first."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT session_id, peer_webid, owner_webid,
                              send_count, recv_count, created_at, updated_at
                       FROM dm_sessions WHERE owner_webid=?
                       ORDER BY updated_at DESC""",
                    (owner_webid,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []

    def list_dm_thread_ids(self, owner_webid: str) -> list[str]:
        """Return distinct DM thread_ids for messages owned by owner_webid."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT DISTINCT thread_id FROM messages
                       WHERE thread_type='dm' AND from_webid=?""",
                    (owner_webid,),
                ).fetchall()
                return [r["thread_id"] for r in rows]
            except Exception:
                return []

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

    def prune_expired_dm_sessions(self, max_age_seconds: float = 7_776_000.0) -> int:
        """Delete dm_sessions not updated within max_age_seconds. Returns count deleted."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM dm_sessions WHERE updated_at < ?", (cutoff,))
                return conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                return 0

    # ------------------------------------------------------------------
    # Contact verification / safety numbers (schema v43)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Sender keys for group E2E (schema v44)
    # ------------------------------------------------------------------

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

    def delete_sender_keys_for_room(self, room_id: str) -> None:
        with self._conn() as conn:
            try:
                conn.execute("DELETE FROM sender_keys WHERE room_id=?", (room_id,))
            except Exception:
                pass

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

    # ------------------------------------------------------------------
    # Per-device DM session scope (schema v46)
    # ------------------------------------------------------------------

    def get_dm_sessions_for_device_scope(
        self,
        owner_webid: str,
        owner_device_id: str,
        peer_webid: str,
        peer_device_id: str,
    ) -> list[dict]:
        """Return sessions matching the given device-pair scope, newest first."""
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM dm_sessions
                       WHERE owner_webid=? AND owner_device_id=?
                         AND peer_webid=? AND peer_device_id=?
                       ORDER BY updated_at DESC""",
                    (owner_webid, owner_device_id, peer_webid, peer_device_id),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []

    # ------------------------------------------------------------------
    # Per-device DM delivery tracking (schema v46)
    # ------------------------------------------------------------------

    def record_dm_delivery(
        self, message_id: str, to_webid: str, to_device_id: str
    ) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO dm_device_deliveries
                       (message_id, to_webid, to_device_id)
                       VALUES (?, ?, ?)""",
                    (message_id, to_webid, to_device_id),
                )
            except Exception:
                pass

    def mark_dm_delivered(
        self,
        message_id: str,
        to_webid: str,
        to_device_id: str,
        *,
        read: bool = False,
    ) -> None:
        with self._conn() as conn:
            try:
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if read:
                    conn.execute(
                        """UPDATE dm_device_deliveries
                           SET delivered_at = COALESCE(delivered_at, ?), read_at = ?
                           WHERE message_id=? AND to_webid=? AND to_device_id=?""",
                        (now, now, message_id, to_webid, to_device_id),
                    )
                else:
                    conn.execute(
                        """UPDATE dm_device_deliveries
                           SET delivered_at = COALESCE(delivered_at, ?)
                           WHERE message_id=? AND to_webid=? AND to_device_id=?""",
                        (now, message_id, to_webid, to_device_id),
                    )
            except Exception:
                pass

    def get_dm_deliveries(self, message_id: str) -> list[dict]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM dm_device_deliveries WHERE message_id=?",
                    (message_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []

    # ------------------------------------------------------------------
    # Idempotency ledger (schema v46)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Contact verification sync (schema v46)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Catch-up watermarks (schema v46)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Session recovery attempts (schema v47)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Device recovery codes (schema v47)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Message delivery state (schema v47 + delivery_state module)
    # ------------------------------------------------------------------

    def set_message_delivery_state(
        self, message_id: str, receiver_webid: str, state: str
    ) -> bool:
        """Apply a monotonic delivery state transition. Returns False if rejected."""
        from .delivery_state import is_valid_transition
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

    # ------------------------------------------------------------------
    # Idempotency TTL cleanup (schema v46)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # WebPush subscriptions (schema v44)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Device registrations (schema v45)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # SPK rotation helpers (schema v45)
    # ------------------------------------------------------------------

    def get_expired_signed_prekeys(
        self, owner_webid: str, max_age_seconds: float
    ) -> list[dict]:
        """Return signed (non-one-time) prekeys older than max_age_seconds."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """SELECT * FROM dm_prekeys
                       WHERE owner_webid=? AND one_time=0 AND expired=0
                             AND spk_created_at > 0 AND spk_created_at < ?""",
                    (owner_webid, cutoff),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                return []

    def mark_prekey_expired(self, prekey_id: int) -> None:
        """Mark a signed prekey as expired (retained for 48h, then hard-deleted by purge loop)."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE dm_prekeys SET expired=1 WHERE prekey_id=?",
                    (prekey_id,),
                )
            except Exception:
                pass

    def save_prekey_with_timestamp(
        self,
        prekey_id: int,
        owner_webid: str,
        pub_b64: str,
        priv_wrapped_b64: str,
        one_time: bool = True,
        spk_created_at: float = 0.0,
    ) -> None:
        """Like save_prekey but also stores spk_created_at for SPK rotation."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO dm_prekeys
                   (prekey_id, owner_webid, pub_b64, priv_wrapped_b64, one_time, used,
                    created_at, spk_created_at, expired)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0)""",
                (
                    prekey_id, owner_webid, pub_b64, priv_wrapped_b64,
                    1 if one_time else 0, time.time(),
                    spk_created_at or time.time(),
                ),
            )

    # ------------------------------------------------------------------
    # Message sequence numbers (schema v45)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # WireGuard overlay state (schema v48)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # STUN sessions (schema v49)
    # ------------------------------------------------------------------

    def save_stun_session(
        self,
        session_id: str,
        external_ip: str,
        external_port: int,
        stun_server: str,
        ttl_seconds: int = 300,
        owner_webid: str = "",
        owner_device_id: str = "",
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO stun_sessions
                       (id, external_ip, external_port, stun_server, discovered_at, expires_at,
                        owner_webid, owner_device_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, external_ip, external_port, stun_server, now, now + ttl_seconds,
                     owner_webid, owner_device_id),
                )
            except Exception:
                pass

    def get_latest_stun_session_for_owner(
        self,
        owner_webid: str,
        owner_device_id: str = "",
    ) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT * FROM stun_sessions
                       WHERE owner_webid=? AND owner_device_id=? AND expires_at > ?
                       ORDER BY discovered_at DESC LIMIT 1""",
                    (owner_webid, owner_device_id, time.time()),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None

    def get_latest_stun_session(self) -> dict | None:
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """SELECT * FROM stun_sessions WHERE expires_at > ?
                       ORDER BY discovered_at DESC LIMIT 1""",
                    (time.time(),),
                ).fetchone()
                return dict(row) if row else None
            except Exception:
                return None

    def prune_expired_stun_sessions(self) -> int:
        with self._conn() as conn:
            try:
                cur = conn.execute(
                    "DELETE FROM stun_sessions WHERE expires_at <= ?", (time.time(),)
                )
                return cur.rowcount
            except Exception:
                return 0

    # ------------------------------------------------------------------
    # Hole punch attempts (schema v49)
    # ------------------------------------------------------------------

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
