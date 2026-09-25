"""Async WebSocket client for the Proxion gateway.

A minimal, dependency-light client that speaks the same WebSocket protocol the
browser app uses: connect, receive the ``config`` event, ``register`` a
did:key identity (answering an ``auth_challenge`` by signing the nonce with the
Ed25519 identity key when the gateway requires auth), then issue commands and
await their typed responses.

This is the transport the MCP server (mcp_server.py) drives so an AI agent can
participate in Proxion as a first-class, signed identity. Going through the
gateway means the agent is subject to the very same consent, contact and
membership gating a human client is, with no separate code path to keep in sync.

Requests are serialized on a single connection (one command in flight at a
time); unsolicited pushes that arrive while awaiting a response are buffered
(bounded) so a later read can still see them.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections import deque

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .persist import AgentState
from .didkey import pub_key_to_did


class GatewayError(RuntimeError):
    """A gateway command returned an ``error`` event or auth failed."""


class GatewayClient:
    """A single authenticated client connection to a Proxion gateway.

    Parameters
    ----------
    url:
        WebSocket URL of the gateway, e.g. ``ws://127.0.0.1:8765``.
    agent:
        The :class:`AgentState` whose Ed25519 identity this client registers as.
    display_name:
        Human-readable name announced on register (truncated to 100 chars).
    """

    def __init__(self, url: str, agent: AgentState, display_name: str = "Proxion Agent"):
        self._url = url
        self._agent = agent
        self._display_name = display_name
        self._ws = None
        self._buf: deque = deque(maxlen=1000)   # unsolicited events awaiting a reader
        self._lock = asyncio.Lock()             # one command in flight at a time
        pub = agent.identity_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.did = pub_key_to_did(pub)

    # ── connection / handshake ────────────────────────────────────────────────
    async def connect(self, timeout: float = 10.0) -> None:
        """Open the socket and complete registration (signing the challenge)."""
        import websockets
        self._ws = await websockets.connect(self._url)

        # The gateway greets every client with a ``config`` event first.
        first = await self._raw_recv(timeout)
        if first.get("type") != "config":
            # Not fatal: buffer it and continue; some deployments may reorder.
            self._buf.append(first)

        reg = {"cmd": "register", "did": self.did, "display_name": self._display_name}
        if getattr(self._agent, "webid", None):
            reg["webid"] = self._agent.webid
        await self._raw_send(reg)

        resp = await self._await_type({"auth_challenge", "registered", "auth_failed"}, timeout)
        if resp.get("type") == "auth_challenge":
            resp = await self._answer_challenge(resp["nonce"], timeout)
        if resp.get("type") == "auth_failed":
            raise GatewayError(f"auth failed: {resp.get('reason', 'unknown')}")
        if resp.get("type") != "registered":
            raise GatewayError("registration did not complete")

    async def _answer_challenge(self, nonce: str, timeout: float) -> dict:
        sig = self._agent.identity_key.sign(nonce.encode())
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        await self._raw_send({"cmd": "auth_response", "nonce": nonce, "signature": sig_b64})
        return await self._await_type({"registered", "auth_failed"}, timeout)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            finally:
                self._ws = None

    # ── low-level send / receive ──────────────────────────────────────────────
    async def _raw_send(self, payload: dict) -> None:
        if self._ws is None:
            raise GatewayError("not connected")
        await self._ws.send(json.dumps(payload))

    async def _raw_recv(self, timeout: float) -> dict:
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def _await_type(self, types: set, timeout: float) -> dict:
        """Read fresh from the wire until an event whose ``type`` is in ``types``.

        Non-matching events are buffered (bounded), never consulted to satisfy
        this wait: the gateway pushes an unsolicited room snapshot on register,
        so a command response must come off the wire after the command is sent,
        not from a stale earlier push sitting in the buffer.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise GatewayError(f"timed out waiting for {sorted(types)!r}")
            ev = await self._raw_recv(remaining)
            if ev.get("type") in types:
                return ev
            if ev.get("type") == "error":
                raise GatewayError(ev.get("message") or ev.get("reason") or "gateway error")
            self._buf.append(ev)

    async def _request(self, cmd: str, expect: str, timeout: float = 8.0, **fields) -> dict:
        """Send a command and await the response of the expected type."""
        async with self._lock:
            await self._raw_send({"cmd": cmd, **fields})
            return await self._await_type({expect}, timeout)

    # ── high-level operations (mapped to MCP tools) ───────────────────────────
    async def whoami(self) -> dict:
        return {"did": self.did, "display_name": self._display_name,
                "webid": getattr(self._agent, "webid", None)}

    async def list_rooms(self) -> list:
        resp = await self._request("get_rooms", "rooms")
        return resp.get("rooms", [])

    async def list_dms(self) -> list:
        resp = await self._request("get_dms", "dms")
        return resp.get("dms", [])

    async def create_room(self, name: str) -> dict:
        resp = await self._request("chat_room_create", "room_created", name=name)
        return {"room_id": resp.get("room_id"), "code": resp.get("code")}

    async def join_room(self, code: str) -> dict:
        resp = await self._request("join_room", "room_joined", code=code)
        return {"room_id": resp.get("room_id"), "name": resp.get("name")}

    async def send_room(self, room_id: str, content: str) -> dict:
        resp = await self._request("send_room", "message", room_id=room_id, content=content)
        return {"message_id": resp.get("message_id"), "content": resp.get("content", content)}

    async def send_dm(self, target_did: str, content: str) -> dict:
        """Send a gateway-local DM to a contact's did:key. Subject to the same
        contact/consent gating the gateway enforces for any client."""
        resp = await self._request("local_dm", "message", target_webid=target_did, content=content)
        return {"message_id": resp.get("message_id"), "content": resp.get("content", content)}

    async def read_history(self, thread_id: str, limit: int = 50) -> list:
        resp = await self._request("get_local_history", "local_history",
                                   thread_id=thread_id, limit=limit)
        return resp.get("messages", [])

    async def search(self, query: str) -> list:
        resp = await self._request("search", "search_results", query=query)
        return resp.get("results", [])
