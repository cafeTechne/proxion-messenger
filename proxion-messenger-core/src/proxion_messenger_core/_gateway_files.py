"""FileTransferMixin — chunked large-file transfer (R39).

Lifts the 512 KB inline limit. Gateways act as forwarders: the sending
browser splits the file into 64 KB chunks (base64 ≈ 88 KB, under the 128 KB
/relay body cap) and the receiving browser reassembles. Each chunk is acked
back to the sender once delivered (cross-gateway: once the peer gateway took
it), the sender keeps only a small window unacked, and the receiver confirms
the whole file with file_received. Gateway memory stays bounded by that window.

The recipient must be online — transfer is real-time, like a voice call.
Small files continue to use the inline store-and-forward path in
``_gateway_dm._handle_send_file``.

In a DM with a keyed peer the browser encrypts the whole file first, so the
chunks are base64 of AES-GCM ciphertext and the offer carries a sealed ratchet
header with the key and real metadata (docs/E2E_ENCRYPTION.md). Otherwise
chunks are base64 of the raw bytes.

Requires on self: _client_webids, _sockets_for(), _resolve_peer_gateway(),
_relay_ephemeral(), clients.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging

logger = logging.getLogger("proxion_messenger_core.gateway")

# Safety bounds
MAX_FILE_BYTES = 25 * 1024 * 1024   # 25 MB ceiling for chunked transfer
MAX_CHUNK_B64_LEN = 96 * 1024       # 96 KB base64 per chunk (64 KB binary + overhead)

# A sealed (E2E) transfer: ciphertext is the file plus a 16-byte AES-GCM tag,
# and the offer declares this placeholder type.
SEALED_TAG_BYTES = 16
SEALED_FILE_MIME = "application/octet-stream"

# Fields forwarded verbatim per control/data message
_OFFER_FIELDS = ("file_id", "filename", "mime_type", "size_bytes", "total_chunks")
_CHUNK_FIELDS = ("file_id", "seq", "data")
_ID_FIELDS = ("file_id",)
_REJECT_FIELDS = ("file_id", "reason")

# Optional natural pixel size of an image/video attachment, so receivers can
# reserve its box before it decodes. Never required; anything that is not an
# int in 1..MAX_MEDIA_DIM (bools excluded) is dropped rather than rejected.
MAX_MEDIA_DIM = 16384


def media_dims(src) -> dict:
    """Return {"width": w, "height": h} when both are valid, else {}."""
    if not isinstance(src, dict):
        return {}
    w, h = src.get("width"), src.get("height")

    def _ok(v) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_MEDIA_DIM

    return {"width": w, "height": h} if _ok(w) and _ok(h) else {}


def sanitize_file_dims(file_obj):
    """Strip width/height from a relayed file object unless both are valid."""
    if not isinstance(file_obj, dict):
        return file_obj
    clean = {k: v for k, v in file_obj.items() if k not in ("width", "height")}
    clean.update(media_dims(file_obj))
    return clean


# Cross-gateway delivery of one transfer's signals. They are relayed one at a
# time in the order the browser sent them, so file_complete can never overtake a
# chunk, and each relay is retried before the transfer is failed. The sender
# keeps a small window of unacked chunks, so a well-behaved client never queues
# more than a few; a client that ignores acks is cut off at the cap.
FILE_RELAY_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0, 8.0)
FILE_RELAY_MAX_QUEUED = 32
_FILE_RELAY_FAILED_MAX = 1024


class FileTransferMixin:

    async def _send_file_ack(self, websocket, data: dict) -> None:
        """Tell the sending browser a chunk has been handed to the recipient (or
        their gateway), so it can release the next one from its window."""
        try:
            await websocket.send(json.dumps({
                "type": "file_chunk_ack", "file_id": data.get("file_id", ""), "seq": data.get("seq"),
            }))
        except Exception:
            pass

    def _queue_file_relay(self, websocket, peer_gw: str, payload: dict) -> None:
        """Relay a file signal to a peer gateway in order with the transfer's
        earlier signals, without blocking this socket's other commands."""
        chains = self.__dict__.setdefault("_file_relay_chains", {})
        failed = self.__dict__.setdefault("_file_relay_failed", {})
        key = (id(websocket), str(payload.get("file_id", "")))
        if key in failed:
            # Already failed: later signals of the same transfer would only
            # arrive out of sequence. The sender was told once.
            if payload.get("content_type") in ("file_complete", "file_reject", "file_received"):
                failed.pop(key, None)
            return
        prev, depth = chains.get(key, (None, 0))
        if depth >= FILE_RELAY_MAX_QUEUED:
            self._mark_file_relay_failed(key)
            asyncio.create_task(self._notify_file_send_failed(websocket, payload, "backpressure"))
            return
        task = asyncio.create_task(self._relay_file_in_order(prev, key, websocket, peer_gw, payload))
        chains[key] = (task, depth + 1)

    def _mark_file_relay_failed(self, key) -> None:
        failed = self.__dict__.setdefault("_file_relay_failed", {})
        if len(failed) >= _FILE_RELAY_FAILED_MAX:
            failed.pop(next(iter(failed)), None)
        failed[key] = True

    async def _notify_file_send_failed(self, websocket, payload: dict, reason: str) -> None:
        try:
            await websocket.send(json.dumps({
                "type": "file_send_failed", "file_id": payload.get("file_id", ""), "reason": reason,
            }))
        except Exception:
            pass

    async def _relay_file_in_order(self, prev, key, websocket, peer_gw: str, payload: dict) -> None:
        chains = self.__dict__.setdefault("_file_relay_chains", {})
        failed = self.__dict__.setdefault("_file_relay_failed", {})
        try:
            if prev is not None:
                try:
                    await prev
                except Exception:
                    pass
            ct = payload.get("content_type")
            if key in failed:
                if ct in ("file_complete", "file_reject", "file_received"):
                    failed.pop(key, None)
                return
            ok = await self._relay_ephemeral(peer_gw, payload)
            for delay in FILE_RELAY_RETRY_DELAYS:
                if ok:
                    break
                await asyncio.sleep(delay)
                ok = await self._relay_ephemeral(peer_gw, payload)
            if not ok:
                logger.info("file relay %s to %s failed after retries", ct, peer_gw)
                if ct not in ("file_complete", "file_reject", "file_received"):
                    self._mark_file_relay_failed(key)
                await self._notify_file_send_failed(websocket, payload, "relay_failed")
                return
            if ct == "file_chunk":
                await self._send_file_ack(websocket, payload)
        finally:
            cur = chains.get(key)
            if cur is not None:
                task, depth = cur
                if task is asyncio.current_task():
                    chains.pop(key, None)
                else:
                    chains[key] = (task, max(depth - 1, 0))

    def _file_delivery_allowed(self, recipient_webid: str, sender_webid: str) -> bool:
        """Whether a LOCAL file-transfer signal may be delivered to recipient_webid.

        The chunked file path forwarded straight to the target's sockets with no
        relationship or block check, so on a multi-account gateway a blocked or
        unrelated user could push file_offer/file_chunk at any victim — the gate
        the DM path (_handle_local_dm) and the cross-gateway file relay
        (_handle_file_relay) already enforce. Same auth gating as those paths:
        only meaningful when registration proved identity. Under loopback
        single-user dev the browser registers under a session DID (not the
        account DID), so the check is both unenforceable and wrong, hence skipped.
        """
        if not self._auth_enforced():
            return True
        if not self._store:
            return True
        if not (sender_webid and self._store.get_relationship_by_did(sender_webid)):
            return False
        if sender_webid in getattr(self, "_revoked_dids", set()):
            return False
        if self._is_blocked_for(recipient_webid, sender_webid):
            return False
        return True

    def _offer_mime_allowed(self, mime_type: str) -> bool:
        """Whether a chunked-transfer offer's declared MIME is in the allowlist the
        inline 512 KB path (_handle_send_file) enforces. The chunked path streams,
        so the content can't be magic-sniffed at offer time — the declared type is
        all we have up front; the first chunk's magic bytes are checked separately."""
        mime = str(mime_type or "").lower().split(";")[0].strip()
        return mime in self._ALLOWED_FILE_MIMES

    def _first_chunk_magic_blocked(self, data: dict) -> bool:
        """True if this is the stream's first chunk (seq 0) and its leading bytes
        match an executable/script signature the inline path always rejects
        (_BLOCKED_MAGIC: MZ, ELF, Mach-O, shebang). The magic lives in the first
        chunk, so the chunked forwarder sniffs seq 0 and refuses the transfer. An
        undecodable first chunk is refused too — a valid chunk is always base64."""
        if data.get("seq") != 0:
            return False
        chunk = data.get("data", "")
        if not isinstance(chunk, str) or not chunk:
            return False
        try:
            head = base64.b64decode(chunk[:64], validate=True)
        except binascii.Error:
            return True
        return any(head[:len(m)] == m for m in self._BLOCKED_MAGIC)

    async def _forward_file_signal(self, websocket, data: dict, content_type: str, fields: tuple) -> None:
        """Forward a file-transfer control/data message to the target webid.

        Local target → deliver to its sockets. Remote target → relay to its
        gateway. Offline target → notify the sender.
        """
        to_webid = data.get("to_webid", "")
        if not to_webid:
            await websocket.send(json.dumps({"type": "error", "message": "missing_to_webid"}))
            return
        sender_webid = self._client_webids.get(websocket, "")

        sockets = self._sockets_for(to_webid)
        if sockets:
            # F5: gate local delivery on the same relationship + block check the
            # DM path and the cross-gateway file relay enforce (auth-gated, so
            # loopback single-user dev is unaffected). Reject silently — the DM
            # path does the same, no block-reveal to the sender.
            if not self._file_delivery_allowed(to_webid, sender_webid):
                return
            event = {"type": content_type, "from_webid": sender_webid}
            for f in fields:
                if f in data:
                    event[f] = data[f]
            payload = json.dumps(event)
            for s in sockets:
                try:
                    await s.send(payload)
                except Exception:
                    pass
            if content_type == "file_chunk":
                await self._send_file_ack(websocket, data)
            return

        peer_gw = self._resolve_peer_gateway(to_webid)
        if peer_gw:
            # from_webid MUST be this gateway's did — the federation identity the
            # peer keys the relationship by. Using the browser/session did (which
            # differs from the gateway did when auth is off) made the receiver's
            # get_relationship_by_did miss → the whole transfer was silently
            # dropped cross-gateway. Same identity model as DM/voice/file-DM relays.
            relay_payload = {"content_type": content_type, "to_webid": to_webid,
                             "from_webid": self._own_gateway_did()}
            for f in fields:
                if f in data:
                    relay_payload[f] = data[f]
            self._queue_file_relay(websocket, peer_gw, relay_payload)
        else:
            await websocket.send(json.dumps({
                "type": "file_unreachable", "file_id": data.get("file_id", ""),
            }))

    async def _handle_file_offer(self, websocket, data: dict) -> None:
        # E2E-sealed offer: the chunks are AES-GCM ciphertext and the real name,
        # type and file key are in the sealed header, which is forwarded to the
        # peer and never parsed. The declared type is then a fixed placeholder.
        from .command_validation import sealed_fields, SEALED_KEYS
        _sealed = sealed_fields(data, max_content=4096)
        if _sealed is None:
            await websocket.send(json.dumps({
                "type": "error", "message": "invalid_e2e_fields", "file_id": data.get("file_id", ""),
            }))
            return
        size = int(data.get("size_bytes", 0) or 0)
        if size > MAX_FILE_BYTES + (SEALED_TAG_BYTES if _sealed else 0):
            await websocket.send(json.dumps({
                "type": "error", "message": "file_too_large",
                "file_id": data.get("file_id", ""),
            }))
            return
        # F6: the chunked path is otherwise a pure forwarder — enforce the same
        # MIME allowlist the inline 512 KB path applies, at offer time (the
        # stream's magic bytes are sniffed on the first chunk, in
        # _handle_file_chunk, since the content isn't present yet here).
        if _sealed:
            data = {**data, **_sealed, "filename": "file", "mime_type": SEALED_FILE_MIME}
            await self._forward_file_signal(websocket, data, "file_offer", _OFFER_FIELDS + SEALED_KEYS)
            return
        if not self._offer_mime_allowed(data.get("mime_type", "")):
            _declared = str(data.get("mime_type", "")).lower().split(";")[0].strip()
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"file_type_not_allowed: {_declared}",
                "file_id": data.get("file_id", ""),
            }))
            return
        await self._forward_file_signal(websocket, data, "file_offer", _OFFER_FIELDS)

    async def _handle_file_accept(self, websocket, data: dict) -> None:
        await self._forward_file_signal(websocket, data, "file_accept", _ID_FIELDS)

    async def _handle_file_reject(self, websocket, data: dict) -> None:
        await self._forward_file_signal(websocket, data, "file_reject", _REJECT_FIELDS)

    async def _handle_file_chunk(self, websocket, data: dict) -> None:
        chunk = data.get("data", "")
        if not isinstance(chunk, str) or len(chunk) > MAX_CHUNK_B64_LEN:
            await websocket.send(json.dumps({"type": "error", "message": "chunk_too_large"}))
            return
        # F6: reject executable/script payloads on the first chunk's magic bytes,
        # mirroring the inline path's _BLOCKED_MAGIC check. Dropping seq 0 leaves
        # the receiver's transfer incomplete (its idle timeout reclaims it), so
        # the payload never reassembles.
        if self._first_chunk_magic_blocked(data):
            await websocket.send(json.dumps({
                "type": "error",
                "message": "file_type_not_allowed: executable/script",
                "file_id": data.get("file_id", ""),
            }))
            return
        await self._forward_file_signal(websocket, data, "file_chunk", _CHUNK_FIELDS)

    async def _handle_file_complete(self, websocket, data: dict) -> None:
        await self._forward_file_signal(websocket, data, "file_complete", _ID_FIELDS)

    async def _handle_file_received(self, websocket, data: dict) -> None:
        # The receiver reassembled (and, if sealed, decrypted) the file. The
        # sender only reports "Sent" once this arrives.
        await self._forward_file_signal(websocket, data, "file_received", _ID_FIELDS)

    async def _handle_file_relay(self, data: dict) -> tuple[str, str]:
        """Inbound relayed file-transfer message → deliver to the local target."""
        content_type = data.get("content_type", "")
        to_webid = data.get("to_webid", "")
        from_webid = data.get("from_webid", "")
        if not to_webid or not content_type:
            return "400 Bad Request", '{"error":"missing_file_relay_fields"}'
        # Anti-spoof: only accept a file transfer from a peer the recipient has a
        # relationship with — otherwise any gateway could spam file offers at any
        # webid and spoof the sender. Unknown senders are ignored (no reveal).
        _target = to_webid
        if self._store and from_webid:
            if not self._store.get_relationship_by_did(from_webid):
                return "202 Accepted", '{"status":"ignored"}'
            _owner = self._store.get_relationship_owner(from_webid) or ""
            if from_webid in getattr(self, "_revoked_dids", set()) or self._is_blocked_for(_owner, from_webid):
                return "202 Accepted", '{"status":"ignored"}'
            # Deliver to the relationship OWNER, never the wire's to_webid: on a
            # multi-account gateway a contact of user X must not be able to push
            # spoofed file offers/chunks at user Y by naming Y in to_webid.
            if _owner:
                _target = _owner
        if content_type == "file_chunk":
            _c = data.get("data", "")
            if not isinstance(_c, str) or len(_c) > MAX_CHUNK_B64_LEN:
                return "400 Bad Request", '{"error":"chunk_too_large"}'
        sockets = self._sockets_for(_target)
        if not sockets:
            return "202 Accepted", '{"status":"offline"}'
        event = {k: v for k, v in data.items() if k != "content_type"}
        event["type"] = content_type
        payload = json.dumps(event)
        delivered = False
        for s in sockets:
            try:
                await s.send(payload)
                delivered = True
            except Exception:
                pass
        return ("200 OK", '{"status":"delivered"}') if delivered else ("202 Accepted", '{"status":"offline"}')

    # ── Stored room files (history) ──────────────────────────────────────────
    # A room file message keeps its bytes in the store (message_files). History
    # carries only the metadata so it stays small; the browser then asks for the
    # bytes with get_files, which is checked against room membership. E2E DM
    # files are never stored: their history entry is marked unavailable and the
    # browser fills it from its own local copy.

    MAX_GET_FILES = 20

    def _attach_history_files(self, thread_id: str, msgs: list, is_room: bool) -> None:
        if not self._store or not msgs:
            return
        metas = {}
        if is_room:
            metas = self._store.get_message_file_meta(
                thread_id, [m.get("message_id", "") for m in msgs])
        for m in msgs:
            mid = m.get("message_id", "")
            if mid in metas:
                m["file"] = {**metas[mid], "stored": True}
            elif mid.startswith("file-") and str(m.get("content", "")).startswith("📎"):
                name = str(m["content"])[len("📎"):].strip()
                m["file"] = {"filename": name, "unavailable": True}

    def _may_read_room_file(self, websocket, thread_id: str) -> bool:
        room = self._local_rooms.get(thread_id)
        if room is None:
            return False
        if websocket in room.get("members", set()):
            return True
        caller = self._client_webids.get(websocket)
        return bool(caller and self._store and caller in self._store.get_room_members(thread_id))

    async def _handle_get_files(self, websocket, data: dict) -> None:
        ids = data.get("message_ids")
        if not isinstance(ids, list):
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "message_ids"}))
            return
        for mid in ids[:self.MAX_GET_FILES]:
            if not isinstance(mid, str) or not mid or len(mid) > 128:
                continue
            rec = self._store.get_message_file(mid) if self._store else None
            # Unknown id and not-a-member answer the same, so the reply does not
            # reveal that a file exists in a room the caller is not in.
            if not rec or not self._may_read_room_file(websocket, rec["thread_id"]):
                await websocket.send(json.dumps({
                    "type": "file_data", "message_id": mid, "available": False}))
                continue
            data_b64 = base64.b64encode(rec.pop("data")).decode()
            thread_id = rec.pop("thread_id")
            await websocket.send(json.dumps({
                "type": "file_data", "message_id": mid, "thread_id": thread_id,
                "available": True, "file": {**rec, "data_b64": data_b64},
            }))
