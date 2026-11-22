"""Bounded opaque inline-media custody, separate from text fanout limits."""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
import time
from datetime import datetime, timezone


class NativeMediaStoreMixin:
    MAX_NATIVE_MEDIA_BYTES = 32 * 1024 * 1024
    MAX_NATIVE_MEDIA_RECEIPTS = 100000
    MAX_NATIVE_MEDIA_PENDING = 10

    @staticmethod
    def _native_header_digest(payload):
        from ..command_validation import SEALED_KEYS
        header = {key: payload[key] for key in SEALED_KEYS}
        return hashlib.sha256(json.dumps(header, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def _native_media_row(cls, row):
        if not row:
            return None
        event = json.loads(row["event_json"]) if row["event_json"] else None
        header_digest = row["header_digest"] or (cls._native_header_digest(event) if event else None)
        return {"header_digest": header_digest, "header_committed": bool(row["header_committed"]),
                "queue_id": row["queue_id"], "from_webid": row["from_webid"],
                "message_id": row["message_id"], "to_webid": row["to_webid"],
                "media_digest": row["media_digest"], "envelope_digest": row["envelope_digest"],
                "incoming": bool(row["incoming"]), "committed": row["status"] == "committed",
                "forwarded": row["status"] == "forwarded", "status": row["status"],
                "event": event}

    def accept_native_media(self, sender, message_id, recipient, thread_id, media_digest,
                            payload, display_name="", *, incoming=False) -> dict | None:
        """Commit bytes before acceptance; an exact retry returns the original row.

        Payload must already have passed the gateway's sealed-file validation.
        Storage still enforces its own shape/size limits and route binding.
        Completed rows retain only content-free conflict/replay evidence.
        """
        if (not all(isinstance(value, str) and 0 < len(value) <= 2048
                    for value in (sender, recipient, thread_id))
                or not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message_id)
                or not isinstance(media_digest, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", media_digest)
                or not isinstance(payload, dict) or payload.get("e2e") is not True
                or not isinstance(payload.get("file"), dict)):
            return None
        from ..command_validation import sealed_fields, SEALED_KEYS
        sealed = sealed_fields(payload, max_content=4096)
        file = payload["file"]
        if (not sealed or set(payload) != {*SEALED_KEYS, "file"}
                or set(file) != {"filename", "mime_type", "size", "data_b64"}
                or file.get("filename") != "file" or file.get("mime_type") != "application/octet-stream"
                or not isinstance(file.get("data_b64"), str) or len(file["data_b64"]) > 699052):
            return None
        try:
            raw = base64.b64decode(file["data_b64"], validate=True)
        except (ValueError, binascii.Error):
            return None
        if (not 17 <= len(raw) <= 512 * 1024 or type(file.get("size")) is not int
                or file["size"] != len(raw) or base64.b64encode(raw).decode() != file["data_b64"]):
            return None
        try:
            encoded = json.dumps({"to_webid": recipient, "thread_id": thread_id,
                                  "media_digest": media_digest, "incoming": bool(incoming), "payload": payload},
                                 sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            return None
        # 512 KiB ciphertext in base64 plus bounded sealed/route metadata.
        if len(encoded.encode("utf-8")) > 710000:
            return None
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        header_digest = self._native_header_digest(payload)
        with self._conn() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM blocks WHERE owner_webid=? AND blocked_webid=?",
                            (recipient, sender)).fetchone():
                return None
            if conn.execute("SELECT 1 FROM revocations WHERE cert_id IN (?,?)",
                            (f"device:{sender}", f"device:{recipient}")).fetchone():
                return None
            row = conn.execute("SELECT * FROM native_media WHERE from_webid=? AND message_id=?",
                               (sender, message_id)).fetchone()
            if row:
                if row["envelope_digest"] != digest or row["status"] == "blocked":
                    return None
                if not row["header_digest"]:
                    conn.execute("UPDATE native_media SET header_digest=? WHERE queue_id=?", (header_digest, row["queue_id"]))
                    row = conn.execute("SELECT * FROM native_media WHERE queue_id=?", (row["queue_id"],)).fetchone()
                return self._native_media_row(row)
            if conn.execute("SELECT COUNT(*) FROM native_media").fetchone()[0] >= self.MAX_NATIVE_MEDIA_RECEIPTS:
                return None
            count = conn.execute("SELECT COUNT(*) FROM native_media WHERE from_webid=? AND event_json IS NOT NULL",
                                 (sender,)).fetchone()[0]
            if count >= self.MAX_NATIVE_MEDIA_PENDING:
                return None
            event = {"type": "message", "source": "local_dm", "thread_id": thread_id,
                     "from_webid": sender, "from_display_name": str(display_name or "")[:64],
                     "message_id": message_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                     "local": True, "native_media_v": 1, **payload}
            wire = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
            size = len(wire.encode("utf-8")) + sum(len(v.encode("utf-8")) for v in (sender, recipient, message_id, media_digest))
            total = conn.execute("SELECT COALESCE(SUM(byte_count),0) FROM native_media").fetchone()[0]
            if total + size > self.MAX_NATIVE_MEDIA_BYTES:
                return None
            cur = conn.execute(
                "INSERT INTO native_media(from_webid,message_id,to_webid,media_digest,envelope_digest,event_json,incoming,byte_count,created_at,header_digest) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sender, message_id, recipient, media_digest, digest, wire, int(incoming), size, time.time(), header_digest))
            row = conn.execute("SELECT * FROM native_media WHERE queue_id=?", (cur.lastrowid,)).fetchone()
        return self._native_media_row(row)

    def get_native_media(self, sender, message_id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM native_media WHERE from_webid=? AND message_id=?",
                               (sender, message_id)).fetchone()
        return self._native_media_row(row)

    def pending_native_media(self, owner, *, outgoing=False, limit=10):
        if not isinstance(owner, str) or type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("invalid native media batch")
        column = "from_webid" if outgoing else "to_webid"
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM native_media WHERE {column}=? AND event_json IS NOT NULL ORDER BY queue_id LIMIT ?",
                (owner, limit)).fetchall()
        return [self._native_media_row(row) for row in rows]

    def commit_native_media(self, owner, queue_id, sender, message_id, digest, *, forwarded=False):
        """Release exact custody for a recipient commit or a verified peer handoff."""
        if type(queue_id) is not int or queue_id < 1:
            return False
        with self._conn() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,event_json,header_digest FROM native_media WHERE queue_id=? AND to_webid=? AND from_webid=? AND message_id=? AND envelope_digest=? AND status IN ('pending','committed','forwarded')",
                               (queue_id, owner, sender, message_id, digest)).fetchone()
            if not row:
                return False
            header_digest = row["header_digest"] or (self._native_header_digest(json.loads(row["event_json"])) if row["event_json"] else None)
            conn.execute("UPDATE native_media SET event_json=NULL,byte_count=0,status=?,header_digest=?,header_committed=MAX(header_committed,?) WHERE queue_id=?",
                         ("committed" if row[0] == "committed" or not forwarded else "forwarded", header_digest, int(not forwarded), queue_id))
        return True

    def commit_native_media_header(self, owner, queue_id, sender, message_id, digest):
        if type(queue_id) is not int or queue_id < 1:
            return False
        with self._conn() as conn:
            row = conn.execute("SELECT event_json,header_digest FROM native_media WHERE queue_id=? AND to_webid=? AND from_webid=? AND message_id=? AND envelope_digest=? AND status IN ('pending','committed','forwarded')",
                               (queue_id, owner, sender, message_id, digest)).fetchone()
            if not row:
                return False
            header_digest = row["header_digest"] or (self._native_header_digest(json.loads(row["event_json"])) if row["event_json"] else None)
            if not header_digest:
                return False
            conn.execute("UPDATE native_media SET header_committed=1,header_digest=? WHERE queue_id=?", (header_digest, queue_id))
        return True

    def record_native_peer_progress(self, sender, message_id, media_digest, header_digest, *, header_committed, committed):
        if (type(header_committed) is not bool or type(committed) is not bool
                or committed and not header_committed or not isinstance(header_digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", header_digest)):
            return False
        with self._conn() as conn:
            changed = conn.execute("UPDATE native_media SET header_committed=MAX(header_committed,?),status=CASE WHEN ? THEN 'committed' ELSE status END WHERE from_webid=? AND message_id=? AND media_digest=? AND header_digest=? AND status IN ('forwarded','committed')",
                                  (int(header_committed), int(committed), sender, message_id, media_digest, header_digest)).rowcount
        return bool(changed)
