"""Opt-in durable native inline files. No legacy echo is an acceptance proof."""
from __future__ import annotations

import hashlib
import asyncio
import logging
import json
import re
import time
import os


logger = logging.getLogger("proxion_messenger_core.gateway")


class NativeMediaMixin:
    def _native_storage_writable(self):
        return (bool(self._store) and str(self._store.db_path) != ":memory:"
                and getattr(self._store, "_integrity_ok", True)
                and os.environ.get("PROXION_SAFE_MODE") != "1"
                and not self.__dict__.get("_native_media_stopping", False)
                and not self._stop_event.is_set())

    def _native_capable(self, ws):
        owner = self._client_webids.get(ws, "")
        return (str(owner).startswith("did:key:") and not self._session_device_did.get(ws)
                and type((self._session_meta.get(ws) or {}).get("native_media_v")) is int
                and (self._session_meta.get(ws) or {}).get("native_media_v") == 1)

    @staticmethod
    def _native_status(row):
        return {"type": "native_media_status", "v": 1, "message_id": row["message_id"],
                "media_digest": row["media_digest"],
                "status": row["status"] if row["committed"] or row["forwarded"] else "pending"}

    async def _handle_native_inline(self, ws, data, sealed, file_bytes):
        sender = self._client_webids.get(ws, "")
        if not self._native_capable(ws) or not self._native_storage_writable():
            await ws.send(json.dumps({"type": "error", "message": "native_media_unavailable"}))
            return
        thread = next((t for t in self._store.get_dm_threads(owner_webid=sender)
                       if t["thread_id"] == data.get("cert_id")), None)
        if (not sealed or not thread or data.get("room_id") or data.get("cert_id") in self.dm_clients
                or not str(thread["peer_webid"]).startswith("did:key:")):
            await ws.send(json.dumps({"type": "error", "message": "native_media_invalid_target"}))
            return
        peer = thread["peer_webid"]
        if self._is_blocked_for(peer, sender) or self._is_blocked_for(sender, peer):
            return
        if (self._resolve_peer_gateway(peer) and not self._sockets_for_exact(peer)
                and not self._is_owner_account(ws, sender)):
            # Remote peers identify this gateway by its signing key. A guest
            # cannot publish a file as that gateway's owner/primary identity.
            await ws.send(json.dumps({"type": "error", "message": "native_media_invalid_target"}))
            return
        payload = {**sealed, "file": {"filename": "file", "mime_type": "application/octet-stream",
                                      "size": len(file_bytes), "data_b64": data["data_b64"]}}
        row = self._store.accept_native_media(sender, data.get("message_id"), peer, thread["thread_id"],
                                             data.get("media_digest"), payload, self._name_for(ws, sender))
        if not row:
            await ws.send(json.dumps({"type": "error", "message": "native_media_not_accepted",
                                      "message_id": data.get("message_id")}))
            return
        await ws.send(json.dumps(self._native_status(row)))  # committed before this write
        if row["event"]:
            self._schedule_native_media(row)

    def _schedule_native_media(self, row):
        # A slow/absent peer must not hold the source websocket's command loop.
        # Custody stays in SQLite; omitted work is picked up by the next bounded
        # recovery request. Keep at most sixteen active delivery attempts globally.
        if not self._native_storage_writable():
            return
        tasks = self.__dict__.setdefault("_native_media_tasks", {})
        key = row["queue_id"]
        if key in tasks or len(tasks) >= 16:
            return
        task = asyncio.create_task(self._deliver_native_media(row))
        tasks[key] = task
        def completed(done):
            if tasks.get(key) is done:
                tasks.pop(key, None)
            if not done.cancelled() and done.exception():
                logger.warning("Native media custody retained after delivery failure: %s", done.exception())
        task.add_done_callback(completed)

    async def _stop_native_media(self):
        self._native_media_stopping = True
        tasks = list(self.__dict__.get("_native_media_tasks", {}).values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _deliver_native_media(self, row):
        if not self._native_storage_writable():
            return
        latest = self._store.get_native_media(row["from_webid"], row["message_id"])
        if not latest or latest["queue_id"] != row["queue_id"]:
            return
        row = latest
        peer, sender = row["to_webid"], row["from_webid"]
        if (not row["event"] or self._is_blocked_for(peer, sender) or self._is_blocked_for(sender, peer)
                or self._store.is_cert_revoked(f"device:{sender}") or self._store.is_cert_revoked(f"device:{peer}")):
            return
        # Native base-peer file crypto targets the primary account, not arbitrary
        # linked devices with independent keys. An unsupported device cannot drain.
        sockets = [ws for ws in self._device_sockets(peer, peer) if self._native_capable(ws)]
        if sockets:
            if ((row["incoming"] or self._auth_enforced())
                    and self._store.get_relationship_owner(sender) != peer):
                return
            if not self._file_delivery_allowed(peer, sender):
                return
            event = {**row["event"], "native_commit": {"v": 1, "queue_id": row["queue_id"],
                                                       "envelope_digest": row["envelope_digest"]}}
            for ws in sockets:
                live = self._store.get_native_media(sender, row["message_id"])
                if not live or not live["event"] or self._is_blocked_for(peer, sender):
                    break
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    pass  # retain custody until client commit
            return
        # An incoming foreign envelope stays in local custody while its owner is
        # offline. Never bounce it back to the sender's gateway.
        if row["incoming"]:
            return
        target = self._resolve_peer_gateway(peer)
        if not target:
            return
        if await self._relay_native_media(target, row) and self._native_storage_writable():
            self._store.commit_native_media(peer, row["queue_id"], sender, row["message_id"],
                                            row["envelope_digest"], forwarded=True)

    async def _relay_native_media(self, target, row):
        """Release only for a signed exact-request custody proof, never HTTP 2xx alone."""
        from .relay import sign_relay_envelope, verify_relay_envelope
        from .network import async_safe_request
        from datetime import datetime, timezone
        import secrets
        event = row["event"]
        payload = {"content_type": "native_inline", "from_webid": self._own_gateway_did(),
                   "to_webid": row["to_webid"], "message_id": row["message_id"],
                   "media_digest": row["media_digest"],
                   "payload": {key: event[key] for key in ("e2e", "e2e_v", "content", "nonce", "msg_num", "pn", "ratchet_pub", "file")},
                   "relay_sig_did": self._own_gateway_did(),
                   "relay_ts": datetime.now(timezone.utc).isoformat(), "relay_nonce": secrets.token_hex(16)}
        payload["signature"] = sign_relay_envelope(self.agent.identity_key, payload)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if len(body) > 131072:
            return False  # existing HTTP cap; large remote files need chunk transport
        request_digest = hashlib.sha256(body).hexdigest()
        try:
            url = target.replace("wss://", "https://").replace("ws://", "http://").rstrip("/") + "/relay"
            code, _, raw = await async_safe_request("POST", url, body=body,
                headers={"Content-Type": "application/json"}, timeout=10, max_bytes=4096)
            proof = json.loads(raw)
            return (code == 200 and type(proof.get("v")) is int and proof["v"] == 1 and proof.get("content_type") == "native_media_custody"
                    and proof.get("from_webid") == row["to_webid"]
                    and proof.get("relay_sig_did") == row["to_webid"]
                    and proof.get("to_webid") == self._own_gateway_did()
                    and proof.get("request_digest") == request_digest
                    and proof.get("message_id") == row["message_id"]
                    and proof.get("media_digest") == row["media_digest"]
                    and verify_relay_envelope(proof))
        except Exception:
            return False

    async def _handle_native_inline_relay(self, data):
        # The HTTP router verifies the full envelope and requires signer==sender.
        from .relay import sign_relay_envelope
        from datetime import datetime, timezone
        import secrets
        if not self._native_storage_writable():
            return "503 Service Unavailable", '{"error":"native_media_unavailable"}'
        sender = data.get("from_webid", "")
        owner = self._store.get_relationship_owner(sender) if self._store else None
        if (not str(owner or "").startswith("did:key:") or data.get("to_webid") != self._own_gateway_did()
                or self._is_blocked_for(owner, sender) or sender in self._revoked_dids
                or self._store.is_revoked(sender)):
            return "200 OK", '{"status":"received"}'  # no receipt, no block reveal
        # Client-side normalization maps this peer id to the owner's current
        # sidebar thread; a mutable UI thread must not change retry identity.
        row = self._store.accept_native_media(sender, data.get("message_id"), owner, sender,
                                             data.get("media_digest"), data.get("payload"), incoming=True)
        if not row:
            return "503 Service Unavailable", '{"error":"native_media_not_accepted"}'
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        proof = {"content_type": "native_media_custody", "v": 1,
                 "from_webid": self._own_gateway_did(), "to_webid": sender,
                 "message_id": row["message_id"], "media_digest": row["media_digest"],
                 "request_digest": hashlib.sha256(canonical).hexdigest(),
                 "relay_sig_did": self._own_gateway_did(),
                 "relay_ts": datetime.now(timezone.utc).isoformat(), "relay_nonce": secrets.token_hex(16)}
        proof["signature"] = sign_relay_envelope(self.agent.identity_key, proof)
        # The browser's next native recovery request drains the committed row;
        # replying does not depend on any socket write or live tab.
        return "200 OK", json.dumps(proof)

    async def _handle_native_media_commit(self, ws, data):
        owner = self._client_webids.get(ws, "")
        if (not self._native_capable(ws) or not self._native_storage_writable()
                or type(data.get("v")) is not int or data["v"] != 1):
            return
        if self._store.is_cert_revoked(f"device:{owner}"):
            return
        sender, message = data.get("from_webid"), data.get("message_id")
        digest, receive_id = data.get("envelope_digest"), data.get("receive_id")
        if (not all(isinstance(v, str) for v in (sender, message, digest, receive_id))
                or not re.fullmatch(r"[a-f0-9]{64}", receive_id)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or not 0 < len(sender) <= 2048 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message)):
            return
        if self._store.commit_native_media(owner, data.get("queue_id"), sender, message, digest):
            await ws.send(json.dumps({"type": "native_media_committed", "v": 1,
                "queue_id": data["queue_id"], "from_webid": sender, "message_id": message,
                "envelope_digest": digest, "receive_id": receive_id}))

    async def _handle_native_media_recover(self, ws, data):
        owner = self._client_webids.get(ws, "")
        if not self._native_capable(ws) or not self._native_storage_writable():
            return
        meta = self._session_meta[ws]
        now = time.monotonic()
        if now - meta.get("native_replay_at", 0) < 4:
            return
        ids = data.get("message_ids", [])
        if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", i) for i in ids):
            return
        meta["native_replay_at"] = now
        if self._store.is_cert_revoked(f"device:{owner}"):
            return
        for message_id in ids:
            row = self._store.get_native_media(owner, message_id)
            status = self._native_status(row) if row else {"type": "native_media_status", "v": 1, "message_id": message_id, "status": "missing"}
            await ws.send(json.dumps(status))
        for outgoing in (False, True):
            for row in self._store.pending_native_media(owner, outgoing=outgoing):
                if self._client_webids.get(ws) != owner or not self._native_capable(ws):
                    return
                self._schedule_native_media(row)
