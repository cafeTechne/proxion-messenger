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




class _StoreBase(object):
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._init_db()
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    def checkpoint(self) -> None:
        """Flush the WAL to the main database file (call before shutdown)."""
        with self._conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _SCHEMA_VERSION = 53
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

                -- Browser-level E2E x25519 keys, kept SEPARATE from x25519_pubs (which
                -- holds gateway store keys used for sealed-sender). Conflating them
                -- breaks either sealing or content E2E. Keyed by the peer's gateway DID.
                CREATE TABLE IF NOT EXISTS e2e_keys (
                    did        TEXT PRIMARY KEY,
                    pub_b64u   TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                -- Multi-device: browser-level E2E x25519 keys per DEVICE. An
                -- account (account_did) may have several devices, each with its
                -- own E2E key; e2e_keys above holds only one per account, so DM
                -- fanout resolves per-device keys here. device_id is the device's
                -- own did:key (== account_did for the primary device).
                CREATE TABLE IF NOT EXISTS device_e2e_keys (
                    account_did TEXT NOT NULL,
                    device_id   TEXT NOT NULL,
                    pub_b64u    TEXT NOT NULL,
                    updated_at  INTEGER NOT NULL,
                    PRIMARY KEY (account_did, device_id)
                );

                -- Server-side thread mute so OFFLINE push respects a muted thread
                -- (mute is otherwise client-side localStorage the gateway can't see).
                -- mute_key is the OTHER party's webid for DMs (symmetric across the
                -- per-side cert_id asymmetry) or the room_id for rooms.
                CREATE TABLE IF NOT EXISTS thread_mutes (
                    owner_webid TEXT NOT NULL,
                    mute_key    TEXT NOT NULL,
                    updated_at  INTEGER NOT NULL,
                    PRIMARY KEY (owner_webid, mute_key)
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
            # 52: room_federated_members table
            """CREATE TABLE IF NOT EXISTS room_federated_members (
                room_id      TEXT NOT NULL,
                member_did   TEXT NOT NULL,
                gateway_url  TEXT NOT NULL,
                joined_at    REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
                PRIMARY KEY (room_id, member_did)
            )""",
            # 53: room bans and mutes
            [
                """CREATE TABLE IF NOT EXISTS room_bans (
                    room_id    TEXT NOT NULL,
                    banned_did TEXT NOT NULL,
                    banned_by  TEXT NOT NULL,
                    banned_at  REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
                    reason     TEXT,
                    PRIMARY KEY (room_id, banned_did)
                )""",
                """CREATE TABLE IF NOT EXISTS room_mutes (
                    room_id    TEXT NOT NULL,
                    muted_did  TEXT NOT NULL,
                    muted_by   TEXT NOT NULL,
                    muted_at   REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
                    expires_at REAL,
                    PRIMARY KEY (room_id, muted_did)
                )""",
            ],
            # 54: relay-node mailbox (sealed store-and-forward for unreachable gateways)
            [
                """CREATE TABLE IF NOT EXISTS relay_mailbox (
                    blob_id       TEXT PRIMARY KEY,
                    recipient_did TEXT NOT NULL,
                    sealed_blob   TEXT NOT NULL,
                    received_at   REAL NOT NULL DEFAULT (unixepoch('now','subsec')),
                    expires_at    REAL NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS idx_relay_mailbox_recipient
                   ON relay_mailbox(recipient_did)""",
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
    _MAILBOX_MAX_PER_DID = 500
    _MAILBOX_MAX_TOTAL = 50_000
    _REACTION_QUOTA = 50  # max distinct reactions per user per room
    _VALID_QUARANTINE_TRANSITIONS: dict = {
        "pending": {"released", "dropped"},
    }
    _REPLAY_CAP_DEFAULT = 50000
