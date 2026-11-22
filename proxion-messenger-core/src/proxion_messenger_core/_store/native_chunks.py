"""Transactional native ciphertext manifests and parts; no socket ACK authority."""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
import time
from datetime import datetime, timezone


class NativeChunkStoreMixin:
    NATIVE_CHUNK_BYTES = 64 * 1024
    MAX_NATIVE_FILE_BYTES = 25 * 1024 * 1024

    @classmethod
    def validate_native_manifest(cls, manifest):
        from ..command_validation import SEALED_KEYS, sealed_fields
        if (not isinstance(manifest, dict) or set(manifest) != {*SEALED_KEYS, 'size', 'cipher_digest', 'chunk_count', 'recipient_pub'}
                or not sealed_fields(manifest, max_content=4096)
                or type(manifest['size']) is not int or not 17 <= manifest['size'] <= cls.MAX_NATIVE_FILE_BYTES
                or type(manifest['chunk_count']) is not int
                or manifest['chunk_count'] != (manifest['size'] + cls.NATIVE_CHUNK_BYTES - 1) // cls.NATIVE_CHUNK_BYTES
                or not isinstance(manifest['cipher_digest'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', manifest['cipher_digest'])
                or not isinstance(manifest['recipient_pub'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', manifest['recipient_pub'])):
            return None
        return json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False)

    def begin_native_media_chunks(self, sender, message_id, recipient, thread_id, media_digest,
                                  manifest, display_name='', *, incoming=False):
        """Reserve complete capacity before accepting the first opaque part.

        A manifest receipt alone must not be reported as complete custody. All
        original route/header/size/digest fields remain immutable across retries.
        """
        if (not all(isinstance(value, str) and 0 < len(value) <= 2048 for value in (sender, recipient, thread_id))
                or not isinstance(message_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message_id)
                or not isinstance(media_digest, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', media_digest)):
            return None
        canonical = self.validate_native_manifest(manifest)
        if canonical is None:
            return None
        manifest_digest = hashlib.sha256(canonical.encode()).hexdigest()
        envelope = json.dumps({'to_webid': recipient, 'thread_id': thread_id, 'incoming': bool(incoming),
            'media_digest': media_digest, 'transport': 'chunks', 'manifest': manifest},
            sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(envelope.encode()).hexdigest()
        with self._conn() as conn:
            if not conn.in_transaction:
                conn.execute('BEGIN IMMEDIATE')
            if self._native_chunk_policy_blocked(conn, sender, recipient):
                return None
            row = conn.execute('SELECT * FROM native_media WHERE from_webid=? AND message_id=?', (sender, message_id)).fetchone()
            if row:
                if row['transport'] == 'inline':
                    return self._adapt_native_inline(conn, row, recipient, thread_id, media_digest, manifest, manifest_digest, digest, incoming)
                if (row['transport'] != 'chunks' or (row['chunk_identity_digest'] or row['envelope_digest']) != digest
                        or row['status'] == 'blocked'):
                    return None
                return self._native_media_row(row)
            if conn.execute('SELECT COUNT(*) FROM native_media').fetchone()[0] >= self.MAX_NATIVE_MEDIA_RECEIPTS:
                return None
            if conn.execute('SELECT COUNT(*) FROM native_media WHERE from_webid=? AND event_json IS NOT NULL',
                            (sender,)).fetchone()[0] >= self.MAX_NATIVE_MEDIA_PENDING:
                return None
            event = {'type': 'native_media_offer', 'native_chunks_v': 1, 'source': 'local_dm', 'local': True,
                'from_webid': sender, 'message_id': message_id, 'thread_id': thread_id,
                'from_display_name': str(display_name or '')[:64],
                'timestamp': datetime.now(timezone.utc).isoformat(), **manifest}
            wire = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
            # Include the bounded per-part hash/index metadata as well as bytes.
            reserved = manifest['size'] + manifest['chunk_count'] * 128 + len(wire.encode()) + sum(len(value.encode()) for value in (sender, recipient, message_id, media_digest))
            total = conn.execute('SELECT COALESCE(SUM(byte_count),0) FROM native_media').fetchone()[0]
            if total + reserved > self.MAX_NATIVE_MEDIA_BYTES:
                return None
            cur = conn.execute('''INSERT INTO native_media(from_webid,message_id,to_webid,media_digest,
                envelope_digest,event_json,incoming,byte_count,created_at,header_digest,transport,
                cipher_digest,total_bytes,chunk_count,custody_complete,manifest_digest,recipient_pub)
                VALUES(?,?,?,?,?,?,?,?,?,?,'chunks',?,?,?,0,?,?)''',
                (sender, message_id, recipient, media_digest, digest, wire, int(incoming), reserved, time.time(),
                 self._native_header_digest(manifest), manifest['cipher_digest'], manifest['size'], manifest['chunk_count'], manifest_digest, manifest['recipient_pub']))
            row = conn.execute('SELECT * FROM native_media WHERE queue_id=?', (cur.lastrowid,)).fetchone()
        return self._native_media_row(row)

    def _adapt_native_inline(self, conn, row, recipient, thread_id, media_digest, manifest, manifest_digest, digest, incoming):
        """Slice already accepted bytes atomically, preserving the original token.

        Missing historical ciphertext cannot be reconstructed from an opaque
        media digest. Completed/forwarded inline receipts use signed progress
        reconciliation instead of inventing a manifest for discarded bytes.
        """
        from ..command_validation import SEALED_KEYS
        if (row['status'] != 'pending' or not row['event_json'] or row['to_webid'] != recipient
                or row['media_digest'] != media_digest or bool(row['incoming']) != bool(incoming)):
            return None
        try:
            event = json.loads(row['event_json'])
            raw = base64.b64decode(event['file']['data_b64'], validate=True)
            if (event['thread_id'] != thread_id or any(event[key] != manifest[key] for key in SEALED_KEYS)
                    or event['file']['size'] != manifest['size'] or len(raw) != manifest['size']
                    or hashlib.sha256(raw).hexdigest() != manifest['cipher_digest']):
                return None
        except (KeyError, TypeError, ValueError, binascii.Error):
            return None
        offer = {key: value for key, value in event.items() if key not in {*SEALED_KEYS, 'file', 'native_media_v'}}
        offer.update(type='native_media_offer', native_chunks_v=1, **manifest)
        wire = json.dumps(offer, sort_keys=True, separators=(',', ':'), allow_nan=False)
        reserved = len(raw) + manifest['chunk_count'] * 128 + len(wire.encode()) + sum(
            len(value.encode()) for value in (row['from_webid'], recipient, row['message_id'], media_digest))
        total = conn.execute('SELECT COALESCE(SUM(byte_count),0) FROM native_media').fetchone()[0]
        if total - row['byte_count'] + reserved > self.MAX_NATIVE_MEDIA_BYTES:
            return None
        conn.execute("""UPDATE native_media SET transport='chunks',event_json=?,byte_count=?,cipher_digest=?,
            total_bytes=?,chunk_count=?,custody_complete=1,manifest_digest=?,recipient_pub=?,chunk_identity_digest=?
            WHERE queue_id=?""", (wire, reserved, manifest['cipher_digest'], len(raw), manifest['chunk_count'],
            manifest_digest, manifest['recipient_pub'], digest, row['queue_id']))
        for seq, offset in enumerate(range(0, len(raw), self.NATIVE_CHUNK_BYTES)):
            part = raw[offset:offset + self.NATIVE_CHUNK_BYTES]
            conn.execute('INSERT INTO native_media_parts(queue_id,seq,data,digest) VALUES(?,?,?,?)',
                         (row['queue_id'], seq, part, hashlib.sha256(part).hexdigest()))
        return self._native_media_row(conn.execute('SELECT * FROM native_media WHERE queue_id=?', (row['queue_id'],)).fetchone())

    @staticmethod
    def _native_chunk_policy_blocked(conn, sender, recipient):
        return (conn.execute('''SELECT 1 FROM blocks WHERE (owner_webid=? AND blocked_webid=?)
                OR (owner_webid=? AND blocked_webid=?)''', (recipient, sender, sender, recipient)).fetchone()
            or conn.execute('SELECT 1 FROM revocations WHERE cert_id IN (?,?)',
                            (f'device:{sender}', f'device:{recipient}')).fetchone())

    def put_native_media_part(self, sender, message_id, manifest_digest, seq, raw):
        """Commit one exact part, or reject conflicts without replacing bytes.

        Only the full ordered digest can set custody_complete. A failed final
        digest leaves previously accepted parts intact and rejects that new part.
        """
        if (type(seq) is not int or seq < 0 or not isinstance(raw, bytes)
                or not 1 <= len(raw) <= self.NATIVE_CHUNK_BYTES
                or not isinstance(manifest_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', manifest_digest)):
            return None
        with self._conn() as conn:
            if not conn.in_transaction:
                conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('''SELECT * FROM native_media WHERE from_webid=? AND message_id=?
                AND transport='chunks' AND manifest_digest=? AND status='pending' AND event_json IS NOT NULL''',
                (sender, message_id, manifest_digest)).fetchone()
            if not row or seq >= row['chunk_count'] or self._native_chunk_policy_blocked(conn, sender, row['to_webid']):
                return None
            expected = min(self.NATIVE_CHUNK_BYTES, row['total_bytes'] - seq * self.NATIVE_CHUNK_BYTES)
            if len(raw) != expected:
                return None
            prior = conn.execute('SELECT data FROM native_media_parts WHERE queue_id=? AND seq=?', (row['queue_id'], seq)).fetchone()
            if prior:
                return self._native_media_row(row) if prior[0] == raw else None
            digest = hashlib.sha256(raw).hexdigest()
            conn.execute('INSERT INTO native_media_parts(queue_id,seq,data,digest) VALUES(?,?,?,?)', (row['queue_id'], seq, raw, digest))
            count = conn.execute('SELECT COUNT(*) FROM native_media_parts WHERE queue_id=?', (row['queue_id'],)).fetchone()[0]
            if count == row['chunk_count']:
                full = hashlib.sha256()
                for part in conn.execute('SELECT data FROM native_media_parts WHERE queue_id=? ORDER BY seq', (row['queue_id'],)):
                    full.update(part[0])
                if full.hexdigest() != row['cipher_digest']:
                    conn.execute('DELETE FROM native_media_parts WHERE queue_id=? AND seq=?', (row['queue_id'], seq))
                    return None
                conn.execute('UPDATE native_media SET custody_complete=1 WHERE queue_id=?', (row['queue_id'],))
                row = conn.execute('SELECT * FROM native_media WHERE queue_id=?', (row['queue_id'],)).fetchone()
        return self._native_media_row(row)

    def native_media_missing_parts(self, sender, message_id, manifest_digest, *, after=-1, limit=8):
        if type(after) is not int or after < -1 or type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError('invalid native part batch')
        with self._conn() as conn:
            row = conn.execute('''SELECT * FROM native_media WHERE from_webid=? AND message_id=?
                AND transport='chunks' AND manifest_digest=? AND status='pending' AND event_json IS NOT NULL''',
                (sender, message_id, manifest_digest)).fetchone()
            if not row or self._native_chunk_policy_blocked(conn, sender, row['to_webid']):
                return None
            known = {part[0] for part in conn.execute('SELECT seq FROM native_media_parts WHERE queue_id=?', (row['queue_id'],))}
            return [seq for seq in range(after + 1, row['chunk_count']) if seq not in known][:limit]

    def get_native_media_part(self, sender, message_id, manifest_digest, seq):
        if type(seq) is not int or seq < 0:
            return None
        with self._conn() as conn:
            row = conn.execute('''SELECT n.from_webid,n.to_webid,p.data,p.digest FROM native_media n
                JOIN native_media_parts p ON n.queue_id=p.queue_id
                WHERE n.from_webid=? AND n.message_id=? AND n.manifest_digest=? AND p.seq=?
                AND n.transport='chunks' AND n.status='pending' AND n.event_json IS NOT NULL''',
                (sender, message_id, manifest_digest, seq)).fetchone()
            if not row or self._native_chunk_policy_blocked(conn, sender, row['to_webid']):
                return None
            if hashlib.sha256(row['data']).hexdigest() != row['digest']:
                return None
            return {'seq': seq, 'data': row['data'], 'digest': row['digest']}
