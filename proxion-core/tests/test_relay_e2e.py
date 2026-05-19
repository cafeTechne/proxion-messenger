"""End-to-end test: two in-process gateways exchange a relay DM.

Both gateways run in background threads (their own event loops), replicating
production deployment. Gateway A sends a DM to Gateway B via HTTP /relay.
"""
import asyncio
import json
import os
import socket
import threading
import time

import pytest

pytest.importorskip("websockets")
import websockets


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(agent, ws_port: int, http_port: int, db_path: str):
    """Start a ProxionGateway in a background daemon thread with its own event loop.

    Returns (thread, stop_event, ready_event).
    """
    import asyncio as _asyncio
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    cfg = GatewayConfig(
        host="127.0.0.1",
        port=ws_port,
        http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=db_path,
    )
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())

    ready = threading.Event()
    loop = _asyncio.new_event_loop()

    def _run():
        _asyncio.set_event_loop(loop)

        async def _serve():
            async with websockets.serve(gw.handle_client, "127.0.0.1", ws_port):
                # Start the HTTP server (relay endpoint)
                http_task = _asyncio.create_task(gw._serve_http(
                    web_dir=None,      # no static files needed for tests
                    http_port=http_port,
                ))
                ready.set()
                # Run until cancelled
                try:
                    await _asyncio.Event().wait()
                except _asyncio.CancelledError:
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


async def _drain(ws, timeout=0.1):
    """Consume queued messages, ignore timeout."""
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            break


@pytest.mark.asyncio
async def test_relay_delivers_dm(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    ws_a, ws_b = _free_port(), _free_port()
    http_a, http_b = _free_port(), _free_port()

    agent_a = AgentState.generate()
    agent_b = AgentState.generate()

    gw_a, t_a, loop_a, ready_a = _start_gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, t_b, loop_b, ready_b = _start_gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))

    assert ready_a.wait(timeout=5), "Gateway A failed to start"
    assert ready_b.wait(timeout=5), "Gateway B failed to start"
    await asyncio.sleep(0.2)  # let event loops settle

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)

    async with websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b:

        await conn_a.send(json.dumps({"cmd": "register", "did": did_a}))
        await conn_b.send(json.dumps({"cmd": "register", "did": did_b}))
        await asyncio.sleep(0.15)

        await _drain(conn_a)
        await _drain(conn_b)

        # A sends DM targeting B's HTTP base URL
        await conn_a.send(json.dumps({
            "cmd": "local_dm",
            "target_webid": did_b,
            "target_gateway_url": f"http://127.0.0.1:{http_b}",
            "content": "hello from A",
            "thread_id": "test-thread-01",
        }))

        # B should receive the relayed message within 3 seconds
        delivered = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_b.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "message" and msg.get("content") == "hello from A":
                    delivered = msg
                    break
            except asyncio.TimeoutError:
                continue

    assert delivered is not None, "Gateway B did not receive the relayed message within 3s"
    assert delivered["from_webid"] == did_a
    assert delivered["content"] == "hello from A"


@pytest.mark.asyncio
async def test_send_dm_relay_fallback(tmp_path):
    """send_dm falls back to relay when cert is in SQLite but no pod client.

    Simulates the post-syncFromPod state: the browser sends `send_dm` with a
    cert_id that lives in the gateway's SQLite but not in dm_clients (because
    no pod is connected).  The gateway should detect this and relay via HTTP.
    """
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    ws_a, ws_b = _free_port(), _free_port()
    http_a, http_b = _free_port(), _free_port()

    agent_a = AgentState.generate()
    agent_b = AgentState.generate()

    gw_a, t_a, loop_a, ready_a = _start_gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, t_b, loop_b, ready_b = _start_gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))

    assert ready_a.wait(timeout=5), "Gateway A failed to start"
    assert ready_b.wait(timeout=5), "Gateway B failed to start"
    await asyncio.sleep(0.2)

    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)

    # Pre-populate A's store: a relationship cert whose peer is B,
    # with B's gateway URL recorded.
    cert_id = "cert-test-fallback-01"
    gw_a._store.save_relationship(
        {"certificate_id": cert_id, "issuer": agent_a.identity_pub_bytes.hex(),
         "subject": agent_b.identity_pub_bytes.hex(), "signature": "dummy"},
        peer_did=did_b,
    )
    gw_a._record_peer_gateway(did_b, f"http://127.0.0.1:{http_b}")

    async with websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b:

        await conn_a.send(json.dumps({"cmd": "register", "did": did_a}))
        await conn_b.send(json.dumps({"cmd": "register", "did": did_b}))
        await asyncio.sleep(0.15)
        await _drain(conn_a)
        await _drain(conn_b)

        # A sends send_dm with cert_id — no dm_clients entry, should relay
        await conn_a.send(json.dumps({
            "cmd": "send_dm",
            "cert_id": cert_id,
            "content": "relay fallback works",
        }))

        delivered = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_b.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "message" and msg.get("content") == "relay fallback works":
                    delivered = msg
                    break
            except asyncio.TimeoutError:
                continue

    assert delivered is not None, "send_dm relay fallback did not deliver within 3s"
    assert delivered["content"] == "relay fallback works"


@pytest.mark.asyncio
async def test_full_invite_accept_dm_flow(tmp_path):
    """Full federation flow: invite → accept → DM relayed to requester.

    Bob sends a friend request to Alice.  Alice accepts.  Alice immediately
    sends a DM to Bob via the relay.  Verifies that both sides learn each
    other's gateway URL through the handshake so no manual URL wiring is needed.
    """
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    import httpx

    ws_a, ws_b = _free_port(), _free_port()
    http_a, http_b = _free_port(), _free_port()

    agent_a = AgentState.generate()
    agent_b = AgentState.generate()

    # Gateway A = Alice, Gateway B = Bob
    gw_a, t_a, loop_a, ready_a = _start_gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, t_b, loop_b, ready_b = _start_gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))

    assert ready_a.wait(timeout=5), "Gateway A failed to start"
    assert ready_b.wait(timeout=5), "Gateway B failed to start"
    await asyncio.sleep(0.2)

    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)
    http_url_a = f"http://127.0.0.1:{http_a}"
    http_url_b = f"http://127.0.0.1:{http_b}"
    # Allow http:// endpoint hints for local E2E test gateways
    import os as _os_e2e; _os_e2e.environ["PROXION_ALLOW_INSECURE_FEDERATION"] = "1"

    async with websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b:

        await conn_a.send(json.dumps({"cmd": "register", "did": did_a}))
        await conn_b.send(json.dumps({"cmd": "register", "did": did_b}))
        await asyncio.sleep(0.15)
        await _drain(conn_a)
        await _drain(conn_b)

        # ── Step 1: Bob sends a friend request to Alice via her HTTP invite endpoint ──
        # (Normally `send_friend_request` does this; here we do it directly so we
        #  control the endpoint_hints that carry Bob's callback URL back to Alice.)
        import asyncio as _aio
        from proxion_messenger_core import handshake
        from proxion_messenger_core.federation import Capability
        invite = handshake.create_invite(
            agent_b.identity_key,
            agent_b.store_pub_bytes,
            [Capability(with_="stash://dm/", can="crud/write")],
            endpoint_hints=[http_url_b],   # Bob's HTTP URL so Alice can call back
        )
        # Mimic what _handle_send_friend_request does: save in sender's (Bob's) store
        # so _handle_invite_accept_post can validate the invitation_id exists.
        if gw_b._store:
            gw_b._store.save_pending_invite(invite.to_dict(), did_a)
        resp = httpx.post(f"{http_url_a}/invite", json=invite.to_dict(), timeout=5)
        assert resp.status_code == 200, f"/invite rejected: {resp.text}"

        # Alice's browser sees friend_request_received
        alice_req = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_a.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "friend_request_received":
                    alice_req = msg
                    break
            except asyncio.TimeoutError:
                continue
        assert alice_req is not None, "Alice did not receive friend_request_received"

        # ── Step 2: Alice accepts ──
        await conn_a.send(json.dumps({
            "cmd": "accept_friend_request",
            "invitation_id": invite.invitation_id,
        }))

        # Alice should get friend_request_accepted with a cert
        alice_accepted = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_a.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "friend_request_accepted":
                    alice_accepted = msg
                    break
            except asyncio.TimeoutError:
                continue
        assert alice_accepted is not None, "Alice did not receive friend_request_accepted"
        alice_cert = alice_accepted["certificate"]

        # Bob should get contact_added (via /invite/accept callback)
        bob_added = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_b.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "contact_added":
                    bob_added = msg
                    break
            except asyncio.TimeoutError:
                continue
        assert bob_added is not None, "Bob did not receive contact_added"

        await _drain(conn_a)
        await _drain(conn_b)

        # ── Step 3: Alice sends a DM to Bob via cert (relay fallback path) ──
        await conn_a.send(json.dumps({
            "cmd": "send_dm",
            "cert_id": alice_cert["certificate_id"],
            "content": "hello bob from alice",
        }))

        bob_msg = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_b.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "message" and msg.get("content") == "hello bob from alice":
                    bob_msg = msg
                    break
            except asyncio.TimeoutError:
                continue

    assert bob_msg is not None, "Bob did not receive Alice's DM within 3s"
    assert bob_msg["content"] == "hello bob from alice"
    # thread_id must be the cert_id (not the raw DID) so the browser routes correctly
    bob_cert_id = bob_added["certificate"]["certificate_id"]
    assert bob_msg.get("thread_id") == bob_cert_id, (
        f"thread_id mismatch: got {bob_msg.get('thread_id')!r}, want {bob_cert_id!r}"
    )

    # ── Step 4: Bob replies to Alice — bidirectional relay ──
    async with websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a2, \
               websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b2:

        await conn_a2.send(json.dumps({"cmd": "register", "did": did_a}))
        await conn_b2.send(json.dumps({"cmd": "register", "did": did_b}))
        await asyncio.sleep(0.15)
        await _drain(conn_a2)
        await _drain(conn_b2)

        await conn_b2.send(json.dumps({
            "cmd": "send_dm",
            "cert_id": bob_cert_id,
            "content": "hi alice from bob",
        }))

        alice_reply = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(conn_a2.recv(), timeout=0.3)
                msg = json.loads(raw)
                if msg.get("type") == "message" and msg.get("content") == "hi alice from bob":
                    alice_reply = msg
                    break
            except asyncio.TimeoutError:
                continue

    assert alice_reply is not None, "Alice did not receive Bob's reply within 3s"
    # Alice's thread_id must also be the cert_id (from her cert — same UUID, roles swapped)
    alice_cert_id = alice_cert["certificate_id"]
    assert alice_reply.get("thread_id") == alice_cert_id, (
        f"Alice thread_id mismatch: got {alice_reply.get('thread_id')!r}, want {alice_cert_id!r}"
    )
