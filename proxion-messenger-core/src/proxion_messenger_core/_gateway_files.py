"""FileTransferMixin — chunked large-file transfer (R39).

Lifts the 512 KB inline limit. Gateways act as pure forwarders: the sending
browser splits the file into 64 KB chunks (base64 ≈ 88 KB, under the 128 KB
relay cap) and the receiving browser reassembles. No server-side buffering,
so gateway memory is bounded regardless of file size.

The recipient must be online — transfer is real-time, like a voice call.
Small files continue to use the inline store-and-forward path in
``_gateway_dm._handle_send_file``.

Consistency note: like the existing inline file relay, chunks travel as
base64 of the raw bytes (files are not E2E-encrypted on the wire today).
Client-side chunk encryption can be layered on later without changing this
transport.

Requires on self: _client_webids, _sockets_for(), _resolve_peer_gateway(),
_relay_ephemeral(), clients.
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger("proxion_messenger_core.gateway")

# Safety bounds
MAX_FILE_BYTES = 25 * 1024 * 1024   # 25 MB ceiling for chunked transfer
MAX_CHUNK_B64_LEN = 96 * 1024       # 96 KB base64 per chunk (64 KB binary + overhead)

# Fields forwarded verbatim per control/data message
_OFFER_FIELDS = ("file_id", "filename", "mime_type", "size_bytes", "total_chunks")
_CHUNK_FIELDS = ("file_id", "seq", "data")
_ID_FIELDS = ("file_id",)
_REJECT_FIELDS = ("file_id", "reason")


class FileTransferMixin:

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
            asyncio.create_task(self._relay_ephemeral(peer_gw, relay_payload))
        else:
            await websocket.send(json.dumps({
                "type": "file_unreachable", "file_id": data.get("file_id", ""),
            }))

    async def _handle_file_offer(self, websocket, data: dict) -> None:
        size = int(data.get("size_bytes", 0) or 0)
        if size > MAX_FILE_BYTES:
            await websocket.send(json.dumps({
                "type": "error", "message": "file_too_large",
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
        await self._forward_file_signal(websocket, data, "file_chunk", _CHUNK_FIELDS)

    async def _handle_file_complete(self, websocket, data: dict) -> None:
        await self._forward_file_signal(websocket, data, "file_complete", _ID_FIELDS)

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
        if self._store and from_webid:
            if not self._store.get_relationship_by_did(from_webid):
                return "202 Accepted", '{"status":"ignored"}'
            if from_webid in getattr(self, "_revoked_dids", set()) or self.blocklist.is_blocked(from_webid):
                return "202 Accepted", '{"status":"ignored"}'
        if content_type == "file_chunk":
            _c = data.get("data", "")
            if not isinstance(_c, str) or len(_c) > MAX_CHUNK_B64_LEN:
                return "400 Bad Request", '{"error":"chunk_too_large"}'
        sockets = self._sockets_for(to_webid)
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
