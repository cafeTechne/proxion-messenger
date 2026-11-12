"""Pod bridge: lets the desktop client speak the browser build's pod protocol.

The browser build delivers DMs, presence and call/join signals through drop
boxes in each user's Solid pod (web/podio.js). The desktop client runs the same
protocol code, but it has no Solid session: the gateway holds the pod
credentials. These owner-only commands give the client a narrow, policy-scoped
fetch over those credentials (podbridge.classify):

    pod_bridge_info                     -> pod_bridge {ready, webid, pod_root, auth, reason, consumer}
    pod_fetch {req_id, method, url, headers, body}
                                        -> pod_fetch_result {req_id, status, headers, body[, error]}
    pod_watch {url, fast} / pod_unwatch -> pod_changed {url}
    pod_thread {peer_webid, display_name}  lists a WebID thread with get_dms
    pod_room_register {room_id, owner_webid, req_id?} -> pod_room_registered {room_id, container, ok, reason, req_id?}
    pod_room_unregister {container}

A room on someone else's pod (a browser-build user's Long Chat) is the one
place the pod credentials leave our own pod: after an approved join the client
registers the room's container, and only then does pod_fetch make DPoP-signed
GET/HEAD/PATCH requests on that container's chat files, plus GET/HEAD on its
signed pin index. Registered rooms also permit bounded JSON chunk POSTs and
GET/HEAD on asset resources; chunk writes cannot overwrite an existing resource.
A bearer-token pod
(JSS/NSS) never sends its token off its own pod; those rooms are refused.

The gateway carries ciphertext only and never decides who a drop is from: the
client verifies every envelope (webdm.js createSenderVerifier). The gateway
does refuse a blocked sender's drop (it deletes it unread) and keeps the drop
boxes for one consuming device, the one whose key signed proxion/identity/signer.json.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Optional

from . import podbridge as _pbp

logger = logging.getLogger(__name__)


class PodBridgeMixin:

    POD_BRIDGE_COMMANDS = frozenset({"pod_bridge_info", "pod_fetch", "pod_watch", "pod_unwatch", "pod_thread",
                                     "pod_room_register", "pod_room_unregister"})
    # Owner-only by policy (security_policy) and remapped for the loopback owner
    # (gateway.process_command), like the tunnel commands.
    POD_BRIDGE_OWNER_COMMANDS = frozenset({"pod_fetch", "pod_watch", "pod_unwatch", "pod_thread",
                                           "pod_room_register", "pod_room_unregister"})
    _POD_BRIDGE_RATE_LIMIT = 240          # per socket per 10 s (one drain is a few dozen requests)
    _POD_WATCH_MAX = 120                  # watched URLs per socket
    _POD_WATCH_OWN_S = 5.0                # own drop boxes while the client is open
    _POD_WATCH_FAST_S = 1.0               # ... while a call is ringing
    _POD_WATCH_FOREIGN_S = 30.0           # a contact's presence
    _POD_CLOSED_POLL_S = 60.0             # own boxes while no consuming device is connected
    _POD_REQUEST_TIMEOUT = 10.0

    # ── state ────────────────────────────────────────────────────────────────

    def _pb(self) -> dict:
        st = getattr(self, "_pod_bridge_state", None)
        if st is None:
            st = self._pod_bridge_state = {
                "watches": {},          # url -> {"sockets": {ws: fast}, "own": bool, "sig": str|None, "next": float}
                "consumer": None,       # signer did named by our signer.json
                "consumer_for": None,   # the webid that consumer was read for
                "consumer_identity": None,  # the socket identity that published it (push target)
                "boxes_for": None,      # webid the drop boxes were ensured for
                "seen": {},             # box url -> set(member urls), closed-app push baseline
                "closed_next": 0.0,
                "rate": {},             # ws -> [count, window_start]
                "rooms": {},            # joined room container -> room id (credentials allowed)
                "rooms_for": None,      # the webid those rooms were registered for
                "room_nonces": {},      # origin -> last DPoP-Nonce a room's server asked for
            }
        return st

    def _pb_rooms(self) -> dict:
        """Joined-room containers registered for the connected pod's WebID."""
        st = self._pb()
        webid = getattr(self, "_pod_webid", None)
        if st["rooms_for"] != webid:
            st["rooms"], st["rooms_for"], st["room_nonces"] = {}, webid, {}
        return st["rooms"]

    def _pb_forget_socket(self, websocket) -> None:
        st = getattr(self, "_pod_bridge_state", None)
        if not st:
            return
        st["rate"].pop(websocket, None)
        for url in list(st["watches"]):
            w = st["watches"][url]
            w["sockets"].pop(websocket, None)
            if not w["sockets"]:
                st["watches"].pop(url, None)

    def _check_pod_bridge_rate(self, websocket) -> bool:
        now = time.monotonic()
        rate = self._pb()["rate"]
        entry = rate.get(websocket)
        if entry is None or now - entry[1] >= 10.0:
            rate[websocket] = [1, now]
            return True
        if entry[0] >= self._POD_BRIDGE_RATE_LIMIT:
            return False
        entry[0] += 1
        return True

    def _pb_signing_did(self, websocket) -> str:
        """The did:key this connection signs with (its own device key, also when
        it registered under an account through a delegation cert)."""
        return (self._session_device_did.get(websocket)
                or self._session_signing_did.get(websocket)
                or (self._client_webids.get(websocket, "") if str(self._client_webids.get(websocket, "")).startswith("did:key:") else ""))

    def _pod_bridge_status(self) -> dict:
        """{ready, webid, pod_root, auth, reason} for the connected pod."""
        webid = getattr(self, "_pod_webid", None)
        pod_url = getattr(self, "_pod_url", None)
        client = self._pod_client() if webid else None
        if not client:
            return {"ready": False, "webid": None, "pod_root": None, "auth": None, "reason": "no_pod"}
        ok, reason = _pbp.check_pod_root(webid, pod_url)
        root = _pbp.pod_root_from_webid(webid)
        from .css_auth import DpopSolidClient
        auth = "dpop" if isinstance(client, DpopSolidClient) else "bearer"
        return {"ready": ok, "webid": webid, "pod_root": root if ok else None,
                "auth": auth, "reason": reason}

    async def _pb_request_own(self, method: str, url: str, body: Optional[bytes] = None,
                              headers: Optional[dict] = None, max_bytes: int = _pbp.MAX_SMALL_RESPONSE):
        client = self._pod_client()
        if client is None:
            raise RuntimeError("no pod")
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(loop.run_in_executor(
            None, lambda: client.request(method, url, body, headers or {}, max_bytes=max_bytes)),
            timeout=self._POD_REQUEST_TIMEOUT + 5)

    async def _pb_request_room(self, method: str, url: str, body: Optional[bytes] = None,
                               headers: Optional[dict] = None, max_bytes: int = _pbp.MAX_SMALL_RESPONSE):
        """One DPoP-authenticated request to a joined room on another pod.

        The proof is bound to this method and URL and the token to our DPoP key,
        so the room's server cannot replay either elsewhere. The host is resolved
        and pinned like any foreign request and redirects are never followed."""
        from .css_auth import DpopSolidClient
        from .dpop import make_dpop_proof_es256, _extract_dpop_nonce
        from .network import async_safe_request
        from urllib.parse import urlsplit
        client = self._pod_client()
        if not isinstance(client, DpopSolidClient):
            raise PermissionError("bearer")
        creds = client._credentials
        loop = asyncio.get_event_loop()
        token = await loop.run_in_executor(None, creds.get_token)
        nonces = self._pb()["room_nonces"]
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        for attempt in range(2):
            proof = make_dpop_proof_es256(creds._dpop_ec_key, method, url,
                                          nonce=nonces.get(origin), access_token=token)
            req_headers = {**(headers or {}), "Authorization": f"DPoP {token}", "DPoP": proof,
                           "User-Agent": "Proxion/1.0"}
            code, rh, rbody = await async_safe_request(
                method, url, body=body, headers=req_headers,
                timeout=self._POD_REQUEST_TIMEOUT, max_bytes=max_bytes)
            if code == 401 and attempt == 0:
                nonce = rh.get("dpop-nonce") or _extract_dpop_nonce(rh.get("www-authenticate", ""))
                if nonce and nonce != nonces.get(origin):
                    nonces[origin] = nonce
                    continue
            return code, rh, rbody
        return 401, {}, b""

    async def _pb_consumer(self, refresh: bool = False) -> Optional[str]:
        """The signer did our pod's signer.json names: the one device that consumes
        the drop boxes. Authorization uses a fresh read; hints may use the cache."""
        st = self._pb()
        webid = getattr(self, "_pod_webid", None)
        if not refresh and st["consumer_for"] == webid and st["consumer"]:
            return st["consumer"]
        status = self._pod_bridge_status()
        if not status["ready"]:
            return None
        try:
            code, _h, raw = await self._pb_request_own("GET", status["pod_root"] + "proxion/identity/signer.json")
            signer = None
            if code == 200:
                doc = json.loads(raw.decode("utf-8", "replace") or "null")
                if isinstance(doc, dict) and isinstance(doc.get("signer"), str):
                    signer = doc["signer"]
            current = self._pod_bridge_status()
            if getattr(self, "_pod_webid", None) != webid or current.get("pod_root") != status["pod_root"]:
                return None
            if signer != st["consumer"]:
                st["consumer_identity"] = None
            st["consumer"], st["consumer_for"] = signer, webid
            return signer
        except Exception as exc:
            logger.debug("pod bridge: signer read failed: %s", exc)
            return None  # Never authorize from a stale signer after a failed refresh.

    # ── commands ─────────────────────────────────────────────────────────────

    async def _handle_pod_bridge_info(self, websocket, data: dict) -> None:
        if not self._is_pod_owner_socket(websocket):
            await websocket.send(json.dumps({"type": "pod_bridge", "ready": False, "reason": "not_owner"}))
            return
        status = self._pod_bridge_status()
        consumer = await self._pb_consumer(refresh=True) if status["ready"] else None
        status["consumer"] = bool(consumer) and consumer == self._pb_signing_did(websocket)
        await websocket.send(json.dumps({"type": "pod_bridge", **status}))
        if status["ready"]:
            asyncio.ensure_future(self._pb_ensure_boxes())

    async def _pod_fetch_reply(self, websocket, req_id: str, status: int, headers=None,
                               body: bytes = b"", error: str = "") -> None:
        msg = {"type": "pod_fetch_result", "req_id": req_id, "status": status,
               "headers": headers or {}, "body": body.decode("utf-8", "replace") if body else ""}
        if error:
            msg["error"] = error
        await websocket.send(json.dumps(msg))

    def _pb_validate_body(self, websocket, decision, method: str, body: bytes) -> str:
        """Content checks for writes the bridge makes on the owner's behalf.
        Returns a refusal reason, or ''."""
        if method not in ("PUT", "POST") or decision.kind in ("acl", "marker", "joined", "room_index", "typeindex"):
            return ""
        if decision.kind == "dm_assets":
            return "" if not body else "bad_body"
        if decision.kind == "dm_asset_acl":
            container = decision.url.rsplit(".", 1)[0]
            # The Link advertises the model; an ACP server can use an .acl URL.
            grants = (_pbp.dm_asset_acl_turtle(container, getattr(self, "_pod_webid", ""), model).encode("utf-8")
                      for model in ("wac", "acp"))
            return "" if body in grants else "bad_body"
        if decision.kind == "box" and decision.own and method == "PUT":
            return ""   # container creation, empty body
        try:
            doc = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            return "bad_body"
        if not isinstance(doc, dict):
            return "bad_body"
        if decision.kind in ("room_container", "dm_asset"):
            import base64
            import re
            if (type(doc.get("v")) is not int or doc["v"] != 1 or not isinstance(doc.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", doc["id"])
                    or type(doc.get("index")) is not int or not 0 <= doc["index"] < (65 if decision.kind == "dm_asset" else 64)
                    or not isinstance(doc.get("data"), str) or len(doc["data"]) > 43692):
                return "bad_body"
            try:
                if len(base64.b64decode(doc["data"], validate=True)) > 32768:
                    return "bad_body"
            except ValueError:
                return "bad_body"
            if decision.kind == "dm_asset":
                if (set(doc) != {"v", "id", "index", "data"}
                        or not decision.url.endswith(f"/asset-{doc['id']}-{doc['index']}.json")
                        or base64.b64encode(base64.b64decode(doc["data"])).decode("ascii") != doc["data"]):
                    return "bad_body"
        if decision.kind == "identity" and decision.url.endswith("/signer.json"):
            # The signer names THIS device's key and no account: the gateway's own
            # did must never be published as an account a browser key speaks for.
            if doc.get("signer") != self._pb_signing_did(websocket) or doc.get("account_did") is not None:
                return "bad_signer"
        elif decision.kind == "identity":
            if not isinstance(doc.get("pub"), str) or len(doc["pub"]) > 128:
                return "bad_body"
        elif decision.kind == "presence":
            if not isinstance(doc.get("status"), str) or len(doc["status"]) > 32:
                return "bad_body"
        elif decision.kind == "box":
            # A drop into any box (a peer's, or our own) speaks as our WebID. A
            # room owner's answer to a join request names itself as owner_webid.
            sender = doc.get("from_webid")
            if sender is None and decision.box == "join" and doc.get("kind") in ("join_approved", "join_declined"):
                sender = doc.get("owner_webid")
            if sender != getattr(self, "_pod_webid", None):
                return "bad_sender"
        elif decision.kind == "room_pins":
            if doc.get("owner") != getattr(self, "_pod_webid", None) or doc.get("index") != decision.url or doc.get("v") != 1:
                return "bad_body"
        elif decision.kind == "descriptor":
            # Our room's descriptor names us as owner and the room its path names.
            if doc.get("room_id") != decision.room or doc.get("owner") != getattr(self, "_pod_webid", None):
                return "bad_body"
        return ""

    async def _handle_pod_fetch(self, websocket, data: dict) -> None:
        req_id = data.get("req_id")
        if not isinstance(req_id, str) or not req_id or len(req_id) > 64:
            return
        if not self._is_pod_owner_socket(websocket):
            await self._pod_fetch_reply(websocket, req_id, 403, error="not_owner")
            return
        if not self._check_pod_bridge_rate(websocket):
            await self._pod_fetch_reply(websocket, req_id, 429, error="rate_limited")
            return
        status = self._pod_bridge_status()
        if not status["ready"]:
            await self._pod_fetch_reply(websocket, req_id, 503, error=status["reason"] or "not_ready")
            return
        method = str(data.get("method") or "GET").upper()
        url = data.get("url")
        decision = _pbp.classify(method, url, status["pod_root"], self._pb_rooms())
        if not decision.allow:
            await self._pod_fetch_reply(websocket, req_id, 403, error=decision.reason)
            return
        if decision.auth and status["auth"] != "dpop":
            await self._pod_fetch_reply(websocket, req_id, 403, error="bearer")
            return
        raw_body = data.get("body")
        if raw_body is not None and not isinstance(raw_body, str):
            await self._pod_fetch_reply(websocket, req_id, 400, error="bad_body")
            return
        body = (raw_body or "").encode("utf-8")
        if len(body) > _pbp.MAX_REQUEST_BODY:
            await self._pod_fetch_reply(websocket, req_id, 413, error="too_large")
            return
        if method in ("GET", "HEAD", "DELETE"):
            body = b""
        reason = self._pb_validate_body(websocket, decision, method, body)
        if reason:
            await self._pod_fetch_reply(websocket, req_id, 403, error=reason)
            return
        headers = _pbp.filter_request_headers(data.get("headers"), decision, method)
        if decision.kind == "dm_asset" and method == "DELETE":
            import re
            if not re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]{0,256}"', headers.get("If-Match", "")):
                await self._pod_fetch_reply(websocket, req_id, 400, error="asset_version_required")
                return
        if decision.kind in ("dm_assets", "dm_asset") and method == "PUT" and headers.get("If-None-Match") != "*":
            await self._pod_fetch_reply(websocket, req_id, 400, error="immutable_asset")
            return
        if decision.kind == "room_container" and method == "POST":
            chunk = json.loads(body)
            if headers.get("Slug") != f"asset-{chunk['id']}-{chunk['index']}.json":
                await self._pod_fetch_reply(websocket, req_id, 400, error="bad_slug")
                return
        # One consuming device: only the device that signed our signer.json reads
        # and clears the drop boxes. Creating a box or dropping into it is open to
        # any owner connection.
        if decision.own and decision.kind in ("box", "drop") and method in ("GET", "HEAD", "DELETE"):
            if (await self._pb_consumer(refresh=True)) != self._pb_signing_did(websocket):
                await self._pod_fetch_reply(websocket, req_id, 409, error="not_consumer")
                return
        try:
            if decision.own:
                code, rh, rbody = await self._pb_request_own(method, url, body or None, headers, decision.max_bytes)
            elif decision.auth:
                code, rh, rbody = await self._pb_request_room(method, url, body or None, headers, decision.max_bytes)
            else:
                from .network import async_safe_request
                code, rh, rbody = await async_safe_request(
                    method, url, body=body or None, headers=headers,
                    timeout=self._POD_REQUEST_TIMEOUT, max_bytes=decision.max_bytes)
        except Exception as exc:
            msg = str(exc)
            await self._pod_fetch_reply(websocket, req_id, 502,
                                        error="too_large" if "too large" in msg or "exceeds" in msg else "upstream")
            return
        rh = {k: v for k, v in (rh or {}).items() if k in _pb_response_headers()}
        # A blocked sender's drop is removed unread, before the client sees it.
        if decision.own and decision.kind == "drop" and method == "GET" and code == 200:
            if self._pb_drop_is_blocked(websocket, rbody):
                try:
                    await self._pb_request_own("DELETE", url)
                except Exception:
                    pass
                await self._pod_fetch_reply(websocket, req_id, 404, error="blocked")
                return
        if decision.own and decision.kind == "identity" and method == "PUT" and url.endswith("/signer.json") and 200 <= code < 300:
            st = self._pb()
            prev = st["consumer"]
            st["consumer"], st["consumer_for"] = self._pb_signing_did(websocket), getattr(self, "_pod_webid", None)
            st["consumer_identity"] = self._client_webids.get(websocket, "") or None
            if prev and prev != st["consumer"]:
                rh = {**rh, "x-proxion-previous-signer": "1"}
        await self._pod_fetch_reply(websocket, req_id, code, rh, rbody)

    def _pb_drop_is_blocked(self, websocket, raw: bytes) -> bool:
        try:
            doc = json.loads(raw.decode("utf-8", "replace") or "null")
        except Exception:
            return False
        sender = doc.get("from_webid") if isinstance(doc, dict) else None
        owner = self._client_webids.get(websocket, "")
        return bool(isinstance(sender, str) and sender and owner and self._is_blocked_for(owner, sender))

    async def _handle_pod_watch(self, websocket, data: dict) -> None:
        url = data.get("url")
        if not self._is_pod_owner_socket(websocket):
            return
        status = self._pod_bridge_status()
        if not status["ready"]:
            await websocket.send(json.dumps({"type": "pod_watch_refused", "url": url, "reason": status["reason"]}))
            return
        decision = _pbp.classify("GET", url, status["pod_root"], self._pb_rooms())
        room_resource = decision.allow and decision.kind in ("room_day", "room_pins", "room_asset")
        if room_resource and decision.auth and status["auth"] != "dpop":
            await websocket.send(json.dumps({"type": "pod_watch_refused", "url": url, "reason": "bearer"}))
            return
        allowed = decision.allow and ((decision.own and decision.kind == "box") or room_resource
                                      or (not decision.own and decision.kind == "presence"))
        if allowed and decision.own and not room_resource and (await self._pb_consumer(refresh=True)) != self._pb_signing_did(websocket):
            await websocket.send(json.dumps({"type": "pod_watch_refused", "url": url, "reason": "not_consumer"}))
            return
        if not allowed:
            await websocket.send(json.dumps({"type": "pod_watch_refused", "url": url, "reason": decision.reason or "path_not_allowed"}))
            return
        st = self._pb()
        mine = sum(1 for w in st["watches"].values() if websocket in w["sockets"])
        w = st["watches"].get(url)
        if (w is None or websocket not in w["sockets"]) and mine >= self._POD_WATCH_MAX:
            await websocket.send(json.dumps({"type": "pod_watch_refused", "url": url, "reason": "too_many"}))
            return
        if w is None:
            w = st["watches"][url] = {"sockets": {}, "own": decision.own, "auth": decision.auth,
                                      "room": room_resource, "sig": None, "next": 0.0}
        w["sockets"][websocket] = bool(data.get("fast"))
        w["next"] = 0.0

    async def _handle_pod_unwatch(self, websocket, data: dict) -> None:
        st = self._pb()
        w = st["watches"].get(data.get("url"))
        if w:
            w["sockets"].pop(websocket, None)
            if not w["sockets"]:
                st["watches"].pop(data.get("url"), None)

    async def _handle_pod_thread(self, websocket, data: dict) -> None:
        """Keep a WebID DM thread in this account's DM list (get_dms / local_dms),
        so it survives a reload like any other DM."""
        if not self._is_pod_owner_socket(websocket) or not self._store:
            return
        peer = data.get("peer_webid")
        if not _pbp.is_dm_peer_webid(peer):
            await websocket.send(json.dumps({"type": "error", "code": "E_SCHEMA", "message": "invalid_webid"}))
            return
        name = str(data.get("display_name") or "")[:100] or None
        owner = self._client_webids.get(websocket, "")
        self._store.save_dm_thread(peer, peer, name, owner_webid=owner)

    async def _pb_join_on_record(self, root: str, room_id: str, owner_webid: str, container: str) -> bool:
        """Whether our own pod shows we joined *room_id*: the marker the client
        writes when it asks to join (cleared once approved), or the joined-room
        list it writes after the approval."""
        try:
            accept = {"Accept": "application/json, application/ld+json;q=0.9"}
            code, _h, raw = await self._pb_request_own(
                "GET", root + _pbp.pending_join_path(room_id, owner_webid), None, accept)
            if code == 200:
                doc = json.loads(raw.decode("utf-8", "replace") or "null")
                # Legacy markers may be returned as expanded JSON-LD. Match the
                # room and owner on one node, never across unrelated subjects.
                nodes = doc if isinstance(doc, list) else (
                    doc.get("@graph", [doc]) if isinstance(doc, dict) else [])

                def value(node, key):
                    if not isinstance(node, dict):
                        return None
                    v = node.get("px:" + key, node.get("https://proxion.dev/vocab/v1#" + key))
                    if isinstance(v, list):
                        if len(v) != 1:
                            return None
                        v = v[0]
                    return v.get("@value") if isinstance(v, dict) else v

                if any(value(n, "roomId") == room_id and value(n, "ownerWebId") == owner_webid
                       for n in nodes):
                    return True
            code, _h, raw = await self._pb_request_own("GET", root + "proxion/joined-rooms.json", None,
                                                       {"Accept": "application/json"}, _pbp.MAX_LISTING_RESPONSE)
            # Once a canonical list exists it is authoritative. A stale legacy
            # list must never reauthorize a room removed from the JSON record.
            if code == 404:
                code, _h, raw = await self._pb_request_own("GET", root + "proxion/joined-rooms.jsonld", None,
                                                           accept, _pbp.MAX_LISTING_RESPONSE)
            if code == 200:
                doc = json.loads(raw.decode("utf-8", "replace") or "null")
                for r in (doc.get("px:rooms") if isinstance(doc, dict) else None) or []:
                    if (isinstance(r, dict) and r.get("roomId") == room_id and r.get("owner") == owner_webid
                            and r.get("container") == container):
                        return True
        except Exception as exc:
            logger.debug("pod bridge: join record read failed: %s", exc)
        return False

    async def _handle_pod_room_register(self, websocket, data: dict) -> None:
        """Let pod_fetch use our credentials on a joined room's Long Chat.

        Only the room's own container on its owner's WebID-derived pod, only
        when our pod records the join, only over DPoP, and only on a host that
        passes the SSRF check."""
        room_id = data.get("room_id")
        owner = data.get("owner_webid")
        request_id = data.get("req_id")

        async def reply(ok: bool, reason: str = "", container: str = "") -> None:
            await websocket.send(json.dumps({"type": "pod_room_registered", "room_id": room_id,
                                             "container": container, "ok": ok, "reason": reason,
                                             **({"req_id": request_id} if isinstance(request_id, str) and len(request_id) <= 64 else {})}))
        if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 64):
            await reply(False, "bad_request")
            return
        if not self._is_pod_owner_socket(websocket):
            await reply(False, "not_owner")
            return
        status = self._pod_bridge_status()
        if not status["ready"]:
            await reply(False, status["reason"] or "not_ready")
            return
        if status["auth"] != "dpop":
            await reply(False, "bearer")
            return
        container = _pbp.joined_room_container(owner, room_id)
        if not container:
            await reply(False, "bad_room")
            return
        if container.startswith(status["pod_root"]):
            await reply(False, "own_pod", container)
            return
        rooms = self._pb_rooms()
        client = self._pod_client()

        def current():
            now = self._pod_bridge_status()
            return (self._is_pod_owner_socket(websocket) and now["ready"]
                    and now["webid"] == status["webid"] and now["pod_root"] == status["pod_root"]
                    and self._pod_client() is client and self._pb_rooms() is rooms)

        if container not in rooms and len(rooms) >= _pbp.MAX_JOINED_ROOMS:
            await reply(False, "too_many")
            return
        from .network import _resolve_safe_ip
        loop = asyncio.get_event_loop()
        if await loop.run_in_executor(None, _resolve_safe_ip, container) is None:
            await reply(False, "unsafe_host")
            return
        if not current():
            await reply(False, "account_changed")
            return
        if not await self._pb_join_on_record(status["pod_root"], room_id, owner, container):
            await reply(False, "not_joined")
            return
        if not current():
            await reply(False, "account_changed")
            return
        rooms[container] = room_id
        await reply(True, "", container)

    async def _handle_pod_room_unregister(self, websocket, data: dict) -> None:
        if not self._is_pod_owner_socket(websocket):
            return
        self._pb_rooms().pop(data.get("container"), None)

    # ── background: boxes, watches, closed-app push ──────────────────────────

    async def _pb_ensure_boxes(self) -> None:
        """Create our three drop boxes with a public-Append ACL once per pod, so
        peers can drop to us before the desktop client has ever opened."""
        st = self._pb()
        status = self._pod_bridge_status()
        if not status["ready"] or st["boxes_for"] == status["webid"]:
            return
        st["boxes_for"] = status["webid"]
        root = status["pod_root"]
        for box in _pbp.BOXES:
            url = f"{root}proxion/{box}-inbox/"
            try:
                code, _h, _b = await self._pb_request_own("PUT", url, b"", {
                    "Content-Type": "text/turtle", "Link": _pb_container_link()})
                if code not in (200, 201, 204, 205, 409, 405):
                    st["boxes_for"] = None
                    continue
                code, h, _b = await self._pb_request_own("HEAD", url)
                if not 200 <= code < 300:
                    st["boxes_for"] = None
                    continue
                acl_url, model = _pb_acl_from_link(h.get("link", ""), url)
                if not acl_url.startswith(url):
                    st["boxes_for"] = None
                    continue
                code, _h, _b = await self._pb_request_own(
                    "PUT", acl_url, _pbp.inbox_acl_turtle(url, status["webid"], model).encode(),
                    {"Content-Type": "text/turtle"})
                if not (200 <= code < 300):
                    st["boxes_for"] = None
            except Exception as exc:
                logger.debug("pod bridge: ensure %s failed: %s", url, exc)
                st["boxes_for"] = None

    async def _pb_signature(self, url: str, own: bool, auth: bool = False) -> Optional[str]:
        try:
            accept = "application/json" if url.endswith("/pins.json") else "text/turtle"
            if own:
                code, h, body = await self._pb_request_own("GET", url, None, {"Accept": accept},
                                                          _pbp.MAX_LISTING_RESPONSE)
            elif auth:
                code, h, body = await self._pb_request_room("GET", url, None, {"Accept": accept},
                                                           _pbp.MAX_LISTING_RESPONSE)
            else:
                from .network import async_safe_request
                code, h, body = await async_safe_request("GET", url, timeout=self._POD_REQUEST_TIMEOUT,
                                                         max_bytes=_pbp.MAX_SMALL_RESPONSE)
        except Exception:
            return None
        if code == 404:
            return "absent"
        if not (200 <= code < 300):
            return None
        return hashlib.sha256(body + (h.get("etag", "") + h.get("last-modified", "")).encode()).hexdigest()

    async def _pb_poll_watches(self, now: float) -> None:
        st = self._pb()
        consumer_checked, consumer = False, None
        for url, w in list(st["watches"].items()):
            if not w["sockets"]:
                st["watches"].pop(url, None)
                continue
            if now < w["next"]:
                continue
            if w["own"] and not w.get("room"):
                if not consumer_checked:
                    consumer = await self._pb_consumer(refresh=True)
                    consumer_checked = True
                status = self._pod_bridge_status()
                decision = _pbp.classify("GET", url, status.get("pod_root") or "", self._pb_rooms())
                current_box = status["ready"] and decision.allow and decision.own and decision.kind == "box"
                for ws in list(w["sockets"]):
                    if not current_box or not self._is_pod_owner_socket(ws) or self._pb_signing_did(ws) != consumer:
                        w["sockets"].pop(ws, None)
                        try:
                            await ws.send(json.dumps({"type": "pod_watch_refused", "url": url, "reason": "not_consumer"}))
                        except Exception:
                            pass
                if not w["sockets"]:
                    st["watches"].pop(url, None)
                    continue
            auth = w.get("auth", False)
            if auth and not any(url.startswith(c) for c in self._pb_rooms()):
                st["watches"].pop(url, None)   # the room was left (or the pod switched)
                continue
            if w.get("room"):
                interval = self._POD_WATCH_OWN_S
            elif w["own"]:
                interval = self._POD_WATCH_FAST_S if any(w["sockets"].values()) else self._POD_WATCH_OWN_S
            else:
                interval = self._POD_WATCH_FOREIGN_S
            w["next"] = now + interval
            sig = await self._pb_signature(url, w["own"], auth)
            if sig is None:
                continue
            prev, w["sig"] = w["sig"], sig
            if prev is not None and prev != sig:
                msg = json.dumps({"type": "pod_changed", "url": url})
                for ws in list(w["sockets"]):
                    try:
                        await ws.send(msg)
                    except Exception:
                        w["sockets"].pop(ws, None)

    def _pb_consumer_online(self) -> bool:
        st = self._pb()
        ident = st.get("consumer_identity") or st.get("consumer")
        return bool(ident and self._webid_sockets.get(ident))

    async def _pb_closed_app_check(self) -> None:
        """While no consuming device is connected, look at our DM and call boxes
        and send a content-free push when a new drop from an unblocked sender
        lands: "missed call" for a call invite (this look runs once a minute, so
        the ring is over by then), else "new message". A blocked sender's drop is
        deleted unread."""
        st = self._pb()
        status = self._pod_bridge_status()
        consumer = await self._pb_consumer(refresh=True)
        if not status["ready"] or not consumer:
            return
        if self._pb_consumer_online():
            st["seen"].clear()
            return  # Recheck the current signer before deciding a device is online.
        ident = st.get("consumer_identity") or consumer
        notices = {"dm": [], "missed_call": []}
        for box in ("dm", "call"):
            url = f"{status['pod_root']}proxion/{box}-inbox/"
            try:
                code, _h, listing = await self._pb_request_own("GET", url, None, {"Accept": "text/turtle"},
                                                              _pbp.MAX_LISTING_RESPONSE)
            except Exception:
                continue
            if code != 200:
                continue
            members = [m for m in _pbp.container_members(listing, url) if m.startswith(url)]
            seen = st["seen"].get(url)
            if seen is None:
                st["seen"][url] = set(members)
                continue   # first look sets the baseline
            examined = st["seen"][url] = seen.intersection(members)
            for m in [m for m in members if m not in examined][:20]:
                try:
                    code, _h, raw = await self._pb_request_own("GET", m)
                except Exception:
                    continue
                if code != 200:
                    if code == 404:
                        examined.add(m)
                    continue
                try:
                    doc = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    examined.add(m)
                    continue
                examined.add(m)
                sender = doc.get("from_webid") if isinstance(doc, dict) else None
                if isinstance(sender, str) and self._is_blocked_for(ident, sender):
                    try:
                        await self._pb_request_own("DELETE", m)
                    except Exception:
                        pass
                    continue
                if box == "call" and isinstance(doc, dict) and doc.get("type") == "voice_invite":
                    notices["missed_call"].append((examined, m))
                    examined.discard(m)
                elif box == "dm":
                    notices["dm"].append((examined, m))
                    examined.discard(m)
        if notices["missed_call"]:
            if self._pb_push(ident, "missed_call"):
                for examined, member in notices["missed_call"]:
                    examined.add(member)
        elif notices["dm"]:
            if self._pb_push(ident):
                for examined, member in notices["dm"]:
                    examined.add(member)

    def _pb_push(self, identity: str, kind: str = "dm") -> bool:
        """A content-free 'new message' (or 'missed_call') push to *identity*'s
        devices, rate-limited with the inbox push (no sender, no content, no thread)."""
        if not identity or not self._store:
            return False
        subs = self._store.get_push_subscriptions(identity)
        vpk = getattr(self, "_vapid_private_pem", None)
        vsub = getattr(self, "_vapid_subject", None)
        if not (subs and vpk and vsub):
            return False
        last = self._last_inbox_push.get("pod:" + identity, 0.0)
        now = time.time()
        if now - last < self._INBOX_PUSH_MIN_INTERVAL:
            return False
        self._last_inbox_push["pod:" + identity] = now
        payload = {"type": "missed_call" if kind == "missed_call" else "dm", "thread_id": ""}
        asyncio.ensure_future(self._push_offline_fanout(subs, payload, vpk, vsub))
        return True

    async def _pod_bridge_loop(self) -> None:
        """Watches, the one-time drop-box setup, and closed-app push."""
        while True:
            await asyncio.sleep(1.0)
            if not self._pod_bridge_status()["ready"]:
                continue
            await self._pb_ensure_boxes()
            now = time.monotonic()
            await self._pb_poll_watches(now)
            st = self._pb()
            if now >= st["closed_next"]:
                st["closed_next"] = now + self._POD_CLOSED_POLL_S
                await self._pb_closed_app_check()


def _pb_response_headers() -> frozenset:
    from .solid_client import SolidClient
    return SolidClient.RESPONSE_HEADERS


def _pb_container_link() -> str:
    return '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"'


def _pb_acl_from_link(link: str, resource_url: str) -> tuple[str, str]:
    """(acl url, 'wac' | 'acp') from a Link header, else the .acl convention."""
    import re
    from urllib.parse import urljoin
    acl = None
    acp = False
    for m in re.finditer(r'<([^>]*)>\s*;\s*rel="?([^";,]+)"?', link or ""):
        uri, rel = m.group(1), m.group(2)
        if rel == "http://www.w3.org/ns/solid/acp#accessControl":
            acl, acp = urljoin(resource_url, uri), True
        elif rel == "acl" and acl is None:
            acl = urljoin(resource_url, uri)
    if not acl:
        return resource_url + ".acl", "wac"
    if acp or acl.endswith(".acr"):
        return acl, "acp"
    return acl, "wac"
