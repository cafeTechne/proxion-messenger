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
                "status": row["status"] if row["committed"] or row["forwarded"] or row["status"] in ("blocked", "declined") else "pending",
                "header_digest": row["header_digest"], "header_committed": row["header_committed"]}

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
        return self._schedule_native_task(row["queue_id"], lambda: self._deliver_native_media(row))

    def _schedule_native_task(self, key, work):
        if not self._native_storage_writable():
            return False
        tasks = self.__dict__.setdefault("_native_media_tasks", {})
        if key in tasks or len(tasks) >= 16:
            return False
        task = asyncio.create_task(work())
        tasks[key] = task
        def completed(done):
            if tasks.get(key) is done:
                tasks.pop(key, None)
            if not done.cancelled() and done.exception():
                logger.warning("Native media custody retained after delivery failure: %s", done.exception())
        task.add_done_callback(completed)
        return True

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
        if (self._is_blocked_for(peer, sender) or self._is_blocked_for(sender, peer)
                or self._store.is_cert_revoked(f"device:{sender}") or self._store.is_cert_revoked(f"device:{peer}")):
            return
        if row["forwarded"]:
            target = self._resolve_peer_gateway(peer)
            proof = await self._query_native_receipt(target, row) if target else None
            if proof and self._native_storage_writable():
                self._store.record_native_peer_progress(sender, row["message_id"], row["media_digest"], row["header_digest"],
                    header_committed=proof["header_committed"], committed=proof["committed"], declined=proof.get("declined", False))
            return
        if not row["event"]:
            return
        if row["transport"] == "chunks":
            await self._deliver_native_chunk_media(row)
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
        proof = await self._relay_native_media(target, row)
        if proof and self._native_storage_writable():
            if self._store.commit_native_media(peer, row["queue_id"], sender, row["message_id"], row["envelope_digest"], forwarded=True):
                self._store.record_native_peer_progress(sender, row["message_id"], row["media_digest"], row["header_digest"],
                    header_committed=proof["header_committed"], committed=proof["committed"], declined=proof.get("declined", False))

    async def _native_exchange(self, target, peer, payload, response_type):
        from .relay import sign_relay_envelope, verify_relay_envelope
        from .network import async_safe_request
        from datetime import datetime, timezone
        import secrets
        payload = {**payload, "from_webid": self._own_gateway_did(), "to_webid": peer,
                   "relay_sig_did": self._own_gateway_did(),
                   "relay_ts": datetime.now(timezone.utc).isoformat(), "relay_nonce": secrets.token_hex(16)}
        payload["signature"] = sign_relay_envelope(self.agent.identity_key, payload)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if len(body) > 131072:
            return None
        try:
            url = target.replace("wss://", "https://").replace("ws://", "http://").rstrip("/") + "/relay"
            code, _, raw = await async_safe_request("POST", url, body=body,
                headers={"Content-Type": "application/json"}, timeout=10, max_bytes=4096)
            proof = json.loads(raw)
            if (code != 200 or type(proof.get("v")) is not int or proof["v"] != 1
                    or proof.get("content_type") != response_type or proof.get("from_webid") != peer
                    or proof.get("relay_sig_did") != peer or proof.get("to_webid") != self._own_gateway_did()
                    or proof.get("request_digest") != hashlib.sha256(body).hexdigest()
                    or any(proof.get(key) != payload[key] for key in ("message_id", "media_digest", "header_digest", "operation", "request_id", "expected_pub", "manifest_digest", "seq", "after", "native_chunks_v") if key in payload)
                    or not verify_relay_envelope(proof)):
                return None
            return proof
        except Exception:
            return None

    def _native_response(self, data, response_type, **fields):
        from .relay import sign_relay_envelope
        from datetime import datetime, timezone
        import secrets
        proof = {"content_type": response_type, "v": 1,
                 "from_webid": self._own_gateway_did(), "to_webid": data["from_webid"],
                 **{key: data[key] for key in ("message_id", "media_digest", "header_digest", "operation", "request_id", "expected_pub", "manifest_digest", "seq", "after", "native_chunks_v") if key in data},
                 "request_digest": hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                 "relay_sig_did": self._own_gateway_did(),
                 "relay_ts": datetime.now(timezone.utc).isoformat(), "relay_nonce": secrets.token_hex(16), **fields}
        proof["signature"] = sign_relay_envelope(self.agent.identity_key, proof)
        return "200 OK", json.dumps(proof)

    @staticmethod
    def _native_progress_valid(proof):
        return (isinstance(proof, dict) and type(proof.get("header_committed")) is bool
                and type(proof.get("committed")) is bool and (not proof["committed"] or proof["header_committed"])
                and type(proof.get("declined", False)) is bool
                and (not proof.get("declined", False) or proof["header_committed"] and not proof["committed"]))

    async def _relay_native_media(self, target, row):
        event = row["event"]
        proof = await self._native_exchange(target, row["to_webid"], {
            "content_type": "native_inline", "message_id": row["message_id"],
            "media_digest": row["media_digest"], "header_digest": row["header_digest"],
            "payload": {key: event[key] for key in ("e2e", "e2e_v", "content", "nonce", "msg_num", "pn", "ratchet_pub", "file")},
        }, "native_media_custody")
        return proof if self._native_progress_valid(proof) else None

    async def _query_native_receipt(self, target, row):
        if not row["header_digest"]:
            return None  # old forwarded records need the exact saved payload, not an invented header
        proof = await self._native_exchange(target, row["to_webid"], {
            "content_type": "native_media_query", "operation": "receipt",
            "message_id": row["message_id"], "media_digest": row["media_digest"], "header_digest": row["header_digest"],
        }, "native_media_query_result")
        return proof if self._native_progress_valid(proof) else None

    async def _handle_native_inline_relay(self, data):
        # The HTTP router verifies the full envelope and requires signer==sender.
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
        try:
            declared_header = self._store._native_header_digest(data.get("payload"))
        except (KeyError, TypeError, ValueError):
            return "409 Conflict", '{"error":"native_media_header_mismatch"}'
        if data.get("header_digest") != declared_header:
            return "409 Conflict", '{"error":"native_media_header_mismatch"}'
        row = self._store.accept_native_media(sender, data.get("message_id"), owner, sender,
                                             data.get("media_digest"), data.get("payload"), incoming=True)
        if not row:
            return "503 Service Unavailable", '{"error":"native_media_not_accepted"}'
        if data.get("header_digest") != row["header_digest"]:
            return "409 Conflict", '{"error":"native_media_header_mismatch"}'
        return self._native_response(data, "native_media_custody", header_committed=row["header_committed"], committed=row["committed"], declined=row["declined"])

    def _native_ready(self, owner, expected_pub, *, chunks=False):
        pub = self._store.get_x25519_pub(owner) if self._store else None
        if not isinstance(pub, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", pub):
            return False, None
        sockets = self._device_sockets(owner, owner)
        capable = self._native_chunks_capable if chunks else self._native_capable
        supported = any(capable(ws) and (self._session_meta.get(ws) or {}).get("native_media_pub") == pub for ws in sockets)
        return supported and pub == expected_pub, pub if supported else None

    async def _handle_native_media_capability(self, ws, data):
        if not self._native_capable(ws) or not self._native_storage_writable():
            return
        request_id, expected_pub = data.get("request_id"), data.get("expected_pub")
        chunks = "native_chunks_v" in data
        if chunks and (type(data["native_chunks_v"]) is not int or data["native_chunks_v"] != 1 or not self._native_chunks_capable(ws)):
            return
        extra = {"native_chunks_v": 1} if chunks else {}
        if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
                or not isinstance(expected_pub, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", expected_pub)):
            return
        owner = self._client_webids[ws]
        thread = next((t for t in self._store.get_dm_threads(owner_webid=owner) if t["thread_id"] == data.get("cert_id")), None)
        if not thread or not str(thread["peer_webid"]).startswith("did:key:"):
            return
        peer, session = thread["peer_webid"], self._session_meta[ws].get("session_id")
        async def respond():
            supported, pub = False, None
            if not self._is_blocked_for(peer, owner) and not self._is_blocked_for(owner, peer):
                if self._sockets_for_exact(peer):
                    supported, pub = self._native_ready(peer, expected_pub, chunks=chunks)
                else:
                    target = self._resolve_peer_gateway(peer)
                    if target and self._is_owner_account(ws, owner):
                        proof = await self._native_exchange(target, peer, {"content_type": "native_media_query",
                            "operation": "capability", "request_id": request_id, "expected_pub": expected_pub, **extra}, "native_media_query_result")
                        if proof and proof.get("header_commit_supported") is True and type(proof.get("supported")) is bool:
                            pub = proof.get("peer_pub")
                            if pub is not None and (not isinstance(pub, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", pub)):
                                pub = None
                            supported = (proof["supported"] and pub == expected_pub
                                         and (not chunks or type(proof.get("native_chunks_v")) is int and proof["native_chunks_v"] == 1))
            live = self._session_meta.get(ws) or {}
            current_thread = next((t for t in self._store.get_dm_threads(owner_webid=owner) if t["thread_id"] == data.get("cert_id")), None)
            if (not self._native_storage_writable() or self._store.is_cert_revoked(f"device:{owner}")
                    or self._client_webids.get(ws) != owner or live.get("session_id") != session
                    or not self._native_capable(ws) or (chunks and not self._native_chunks_capable(ws))
                    or not current_thread or current_thread["peer_webid"] != peer):
                return
            await ws.send(json.dumps({"type": "native_media_capability_result", "v": 1,
                "request_id": request_id, "peer_webid": peer, "expected_pub": expected_pub,
                "supported": bool(supported), "peer_pub": pub, **extra}))
        if not self._schedule_native_task(("capability", id(ws), request_id), respond):
            await ws.send(json.dumps({"type": "native_media_capability_result", "v": 1,
                "request_id": request_id, "peer_webid": peer, "expected_pub": expected_pub,
                "supported": False, "peer_pub": None, **extra}))

    async def _handle_native_media_query_relay(self, data):
        if not self._native_storage_writable() or data.get("to_webid") != self._own_gateway_did():
            return "503 Service Unavailable", '{"error":"native_media_unavailable"}'
        sender = data.get("from_webid", "")
        if data.get("operation") == "capability":
            owner = self._store.get_relationship_owner(sender)
            expected_pub, request_id = data.get("expected_pub"), data.get("request_id")
            if (not isinstance(expected_pub, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", expected_pub)
                    or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)):
                return "400 Bad Request", '{"error":"invalid_native_query"}'
            if not owner or self._is_blocked_for(owner, sender) or self._store.is_revoked(sender):
                return "200 OK", '{"status":"received"}'
            chunks = "native_chunks_v" in data
            if chunks and (type(data["native_chunks_v"]) is not int or data["native_chunks_v"] != 1):
                return "400 Bad Request", '{"error":"invalid_native_query"}'
            ready, pub = self._native_ready(owner, expected_pub, chunks=chunks)
            return self._native_response(data, "native_media_query_result", header_commit_supported=True, supported=ready, peer_pub=pub)
        if data.get("operation") == "receipt":
            message, digest, header = data.get("message_id"), data.get("media_digest"), data.get("header_digest")
            if not all(isinstance(value, str) for value in (sender, message, digest, header)):
                return "400 Bad Request", '{"error":"invalid_native_query"}'
            row = self._store.get_native_media(sender, message)
            if (not row or not row["incoming"] or row["media_digest"] != digest or row["header_digest"] != header
                    or self._is_blocked_for(row["to_webid"], sender) or self._store.is_revoked(sender)):
                return "200 OK", '{"status":"received"}'
            return self._native_response(data, "native_media_query_result", header_committed=row["header_committed"], committed=row["committed"], declined=row["declined"])
        return "400 Bad Request", '{"error":"invalid_native_query"}'

    async def _handle_native_media_commit(self, ws, data, *, header_only=False, decline=False):
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
        row = self._store.get_native_media(sender, message)
        if (row and row['transport'] == 'chunks'
                and (not self._native_chunks_capable(ws)
                     or (self._session_meta.get(ws) or {}).get('native_media_pub') != row['recipient_pub'])):
            return
        if decline:
            if not row or row['transport'] != 'chunks':
                return
            fields = {"v": 1, "queue_id": data.get("queue_id"), "from_webid": sender, "message_id": message,
                      "envelope_digest": digest, "receive_id": receive_id}
            if self._store.decline_native_media(owner, data.get('queue_id'), sender, message, digest,
                    (self._session_meta.get(ws) or {}).get('native_media_pub')):
                await ws.send(json.dumps({"type": "native_media_declined", **fields}))
            elif (row['committed'] and row['to_webid'] == owner and type(data.get('queue_id')) is int
                    and row['queue_id'] == data['queue_id'] and row['envelope_digest'] == digest):
                await ws.send(json.dumps({"type": "native_media_decline_refused", "status": "committed", **fields}))
            return
        commit = self._store.commit_native_media_header if header_only else self._store.commit_native_media
        if commit(owner, data.get("queue_id"), sender, message, digest):
            fields = {"v": 1, "queue_id": data["queue_id"], "from_webid": sender, "message_id": message,
                      "envelope_digest": digest, "receive_id": receive_id}
            reply = ({"type": "native_media_header_committed", **fields} if header_only
                     else {"type": "native_media_committed", **fields})
            await ws.send(json.dumps(reply))

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
            if row and row["forwarded"]:
                self._schedule_native_media(row)
        for outgoing in (False, True):
            for row in self._store.pending_native_media(owner, outgoing=outgoing):
                if self._client_webids.get(ws) != owner or not self._native_capable(ws):
                    return
                self._schedule_native_media(row)
