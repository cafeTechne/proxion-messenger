"""E2E test: R10.3.3 — Alice sends relay DM, Bob marks it read, Alice gets read_receipt.

Uses two in-process gateways (same pattern as test_relay_e2e.py).
"""
import asyncio
import json
import socket
import threading
import time

import pytest

pytest.importorskip("websockets")
import websockets

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(agent, ws_port: int, http_port: int, db_path: str):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path,
    )
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())

    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(loop)

        async def _serve():
            async with websockets.serve(gw.handle_client, "127.0.0.1", ws_port):
                http_task = asyncio.create_task(gw._serve_http(None, http_port))
                ready.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    http_task.cancel()

        try:
            loop.run_until_complete(_serve())
        except Exception:
            ready.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return gw, t, loop, ready


@pytest.fixture(autouse=True)
def allow_private_relay(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")


async def _drain(ws, timeout=0.15):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            break


@pytest.mark.asyncio
async def test_relay_read_receipt_e2e(tmp_path):
    """R10.3.3: Alice relay-DMs Bob → Bob marks read → Alice receives read_receipt event."""
    import httpx

    ws_a, ws_b = _free_port(), _free_port()
    http_a, http_b = _free_port(), _free_port()

    agent_a = AgentState.generate()
    agent_b = AgentState.generate()

    gw_a, _, _, ready_a = _start_gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, _, _, ready_b = _start_gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))

    assert ready_a.wait(timeout=5), "Gateway A failed to start"
    assert ready_b.wait(timeout=5), "Gateway B failed to start"
    await asyncio.sleep(0.2)

    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)

    async with (
        websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a,
        websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b,
    ):
        await conn_a.send(json.dumps({"cmd": "register", "did": did_a}))
        await conn_b.send(json.dumps({"cmd": "register", "did": did_b}))
        await asyncio.sleep(0.15)
        await _drain(conn_a)
        await _drain(conn_b)

        # ── Step 1: Alice sends a relay DM to Bob ──
        from proxion_messenger_core.relay import sign_relay_message
        from cryptography.hazmat.primitives import serialization
        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.now(_tz.utc).isoformat()
        msg_id = "e2e-receipt-msg-1"
        content = "hello bob, mark me read"
        sig = sign_relay_message(agent_a.identity_key, did_a, did_b, msg_id, content, ts)

        resp = httpx.post(f"http://127.0.0.1:{http_b}/relay", json={
            "from_webid": did_a,
            "to_webid": did_b,
            "message_id": msg_id,
            "content": content,
            "timestamp": ts,
            "signature": sig,
            "origin_gateway_url": f"http://127.0.0.1:{http_a}",
        }, timeout=5)
        assert resp.status_code == 200, f"Relay failed: {resp.text}"

        # Bob receives the message
        bob_msg = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_b.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "message" and msg.get("message_id") == msg_id:
                    bob_msg = msg
                    break
            except asyncio.TimeoutError:
                continue
        assert bob_msg is not None, "Bob did not receive the relay DM"

        # ── Step 2: Bob marks the message read ──
        thread_id = bob_msg.get("thread_id") or did_a
        await conn_b.send(json.dumps({
            "cmd": "mark_read",
            "thread_id": thread_id,
            "message_id": msg_id,
        }))

        # ── Step 3: Alice receives a read_receipt event ──
        receipt = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_a.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("type") == "read_receipt" and msg.get("message_id") == msg_id:
                    receipt = msg
                    break
            except asyncio.TimeoutError:
                continue

    assert receipt is not None, "Alice did not receive read_receipt from Bob"
    assert receipt["message_id"] == msg_id
