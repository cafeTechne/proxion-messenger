"""E2E test helpers for WebSocket session management."""

import asyncio
import json
from typing import Optional, Any


class WsSession:
    """Thin wrapper around a live websocket connection for E2E tests."""

    def __init__(self, ws, display_name: str, did: str, webid: str):
        self.ws = ws
        self.display_name = display_name
        self.did = did
        self.webid = webid
        self._buf: list[dict] = []  # received but not yet consumed events

    async def send(self, **kwargs) -> None:
        """Send a command dict."""
        await self.ws.send(json.dumps(kwargs))

    async def recv_any(self, timeout: float = 3.0) -> dict:
        """Receive next event (from buffer first, then wire)."""
        if self._buf:
            return self._buf.pop(0)
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def recv_type(self, type_: str, timeout: float = 5.0) -> dict:
        """Wait for an event of a specific type, buffering others."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for event type={type_!r}")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            event = json.loads(raw)
            if event.get("type") == type_:
                return event
            self._buf.append(event)

    async def recv_types(self, *types: str, timeout: float = 5.0) -> dict:
        """Wait for any of the given types."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for event types={types!r}")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            event = json.loads(raw)
            if event.get("type") in types:
                return event
            self._buf.append(event)

    async def drain(self, timeout: float = 0.1) -> list[dict]:
        """Collect all pending events (for assertions after an action)."""
        events = list(self._buf)
        self._buf.clear()
        try:
            while True:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
                events.append(json.loads(raw))
        except (asyncio.TimeoutError, Exception):
            pass
        return events


async def connect_and_register(
    url: str,
    display_name: str,
    agent_state,
) -> WsSession:
    """
    Connect to the gateway, authenticate, and register a user.

    Parameters
    ----------
    url : str
        WebSocket URL (e.g., "ws://127.0.0.1:7474")
    display_name : str
        Display name for the user
    agent_state : AgentState
        The agent's state containing identity key and public key

    Returns
    -------
    WsSession
        An authenticated and registered session
    """
    import websockets
    import base64
    from proxion_messenger_core.didkey import pub_key_to_did
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat
    )

    ws = await websockets.connect(url)

    # Receive and discard the initial config event
    config_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
    config = json.loads(config_raw)
    assert config.get("type") == "config", f"Expected config, got {config.get('type')}"

    # Get the DID from the agent's public key
    pub_bytes = agent_state.identity_pub.public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    did = pub_key_to_did(pub_bytes)

    # Register (this will trigger auth_challenge if required)
    await ws.send(json.dumps({"cmd": "register", "did": did, "display_name": display_name}))

    # Wait for either auth_challenge or registered
    # The gateway may send multiple events, so we need to find the registered one
    registered = None
    auth_challenge = None
    deadline = asyncio.get_event_loop().time() + 5.0

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for registration to complete")

        resp_raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        resp = json.loads(resp_raw)

        if resp.get("type") == "auth_challenge":
            auth_challenge = resp
            break
        elif resp.get("type") == "registered":
            registered = resp
            break

    if auth_challenge:
        # Need to sign the nonce
        nonce = auth_challenge["nonce"]
        sig = agent_state.identity_key.sign(nonce.encode())
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

        # Send auth_response
        await ws.send(json.dumps({
            "cmd": "auth_response",
            "nonce": nonce,
            "signature": sig_b64,
        }))

        # Now wait for registered
        deadline = asyncio.get_event_loop().time() + 5.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for registered after auth_response")
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            resp = json.loads(resp_raw)
            if resp.get("type") == "registered":
                registered = resp
                break

    if not registered:
        raise RuntimeError("Failed to complete registration")

    return WsSession(ws, display_name, did, did)
