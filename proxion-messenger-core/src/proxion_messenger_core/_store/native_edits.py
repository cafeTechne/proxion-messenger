"""Bounded opaque caption actions, separate from an immutable file envelope."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone


class NativeEditStoreMixin:
    MAX_NATIVE_EDITS = 100  # Existing gateway per-message edit ceiling.

    @staticmethod
    def _native_edit_id(value):
        return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value))

    def _native_edit_parent(self, conn, sender, file_id, recipient, thread_id, incoming):
        parent = conn.execute("""SELECT * FROM native_media WHERE from_webid=? AND message_id=?
            AND to_webid=? AND incoming=? AND status='committed' AND transport IN ('inline','chunks')""",
            (sender, file_id, recipient, int(incoming))).fetchone()
        if not parent or not parent['header_digest'] or self._native_chunk_policy_blocked(conn, sender, recipient):
            return None
        if incoming:
            return parent if thread_id == sender else None
        # The gateway's authenticated handler supplies this owner's thread.
        # Do not authorise an edit using the file UUID alone or another peer's
        # thread. Old retired file receipts need not invent a lost route string.
        thread = conn.execute('SELECT peer_webid FROM dm_threads WHERE owner_webid=? AND thread_id=?',
                              (sender, thread_id)).fetchone()
        return parent if thread and thread['peer_webid'] == recipient else None

    def native_edit_next_sequence(self, sender, file_id, recipient, thread_id, *, incoming=False):
        if (not self._native_edit_id(file_id) or not all(isinstance(value, str) and 0 < len(value) <= 2048
                for value in (sender, recipient, thread_id))):
            return None
        with self._conn() as conn:
            if not self._native_edit_parent(conn, sender, file_id, recipient, thread_id, incoming):
                return None
            current = conn.execute("SELECT COALESCE(MAX(native_sequence),0) FROM native_media WHERE from_webid=? AND native_parent_id=? AND transport='edit'",
                                   (sender, file_id)).fetchone()[0]
            return current + 1 if current < self.MAX_NATIVE_EDITS else None

    def accept_native_edit(self, sender, action_id, recipient, thread_id, media_digest, file_id,
                           sequence, parent_header_digest, header, recipient_pub, display_name='', *, incoming=False):
        """Admit one exact opaque action after checking original ownership.

        The original file receipt and bytes remain unchanged. Admission is
        custody only; the existing header/full commit primitives remain the
        boundaries for eventual browser journal/history consumption.
        """
        from ..command_validation import SEALED_KEYS, sealed_fields
        if (not all(isinstance(value, str) and 0 < len(value) <= 2048 for value in (sender, recipient, thread_id))
                or not self._native_edit_id(action_id) or not self._native_edit_id(file_id) or action_id == file_id
                or type(sequence) is not int or not 1 <= sequence <= self.MAX_NATIVE_EDITS
                or not isinstance(header, dict) or set(header) != set(SEALED_KEYS) or not sealed_fields(header)
                or not isinstance(media_digest, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', media_digest)
                or not isinstance(parent_header_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', parent_header_digest)
                or not isinstance(recipient_pub, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', recipient_pub)):
            return None
        encoded = json.dumps({'transport': 'edit', 'to_webid': recipient, 'thread_id': thread_id, 'incoming': bool(incoming),
            'media_digest': media_digest, 'file_id': file_id, 'sequence': sequence,
            'parent_header_digest': parent_header_digest, 'header': header, 'recipient_pub': recipient_pub},
            sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self._conn() as conn:
            if not conn.in_transaction:
                conn.execute('BEGIN IMMEDIATE')
            parent = self._native_edit_parent(conn, sender, file_id, recipient, thread_id, incoming)
            if (not parent or parent['header_digest'] != parent_header_digest
                    or (parent['recipient_pub'] and parent['recipient_pub'] != recipient_pub)):
                return None
            prior = conn.execute('SELECT * FROM native_media WHERE from_webid=? AND message_id=?', (sender, action_id)).fetchone()
            if prior:
                return (self._native_media_row(prior) if prior['transport'] == 'edit'
                        and prior['envelope_digest'] == digest and prior['status'] in ('pending', 'forwarded', 'committed') else None)
            latest = conn.execute("SELECT COALESCE(MAX(native_sequence),0) FROM native_media WHERE from_webid=? AND native_parent_id=? AND transport='edit'",
                                  (sender, file_id)).fetchone()[0]
            if sequence != latest + 1:
                return None
            if conn.execute('SELECT COUNT(*) FROM native_media').fetchone()[0] >= self.MAX_NATIVE_MEDIA_RECEIPTS:
                return None
            if conn.execute('SELECT COUNT(*) FROM native_media WHERE from_webid=? AND event_json IS NOT NULL', (sender,)).fetchone()[0] >= self.MAX_NATIVE_MEDIA_PENDING:
                return None
            event = {'type': 'native_edit', 'native_edit_v': 1, 'message_id': action_id, 'file_id': file_id,
                'native_sequence': sequence, 'parent_header_digest': parent_header_digest, 'recipient_pub': recipient_pub,
                'from_webid': sender, 'thread_id': thread_id, 'timestamp': datetime.now(timezone.utc).isoformat(),
                'from_display_name': str(display_name or '')[:64], **header}
            wire = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
            size = len(wire.encode()) + sum(len(value.encode()) for value in (sender, recipient, action_id, media_digest))
            total = conn.execute('SELECT COALESCE(SUM(byte_count),0) FROM native_media').fetchone()[0]
            if total + size > self.MAX_NATIVE_MEDIA_BYTES:
                return None
            cur = conn.execute("""INSERT INTO native_media(from_webid,message_id,to_webid,media_digest,envelope_digest,
                event_json,incoming,byte_count,created_at,header_digest,transport,recipient_pub,native_parent_id,native_sequence)
                VALUES(?,?,?,?,?,?,?,?,?,?,'edit',?,?,?)""", (sender, action_id, recipient, media_digest, digest, wire,
                int(incoming), size, time.time(), self._native_header_digest(header), recipient_pub, file_id, sequence))
            return self._native_media_row(conn.execute('SELECT * FROM native_media WHERE queue_id=?', (cur.lastrowid,)).fetchone())
