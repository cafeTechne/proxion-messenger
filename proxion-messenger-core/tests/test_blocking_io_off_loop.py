"""Blocking network calls (web push, pod writes) do not freeze the event loop.

send_web_push blocks for up to 10 s per subscription and a pod write blocks for
as long as the pod takes. Both ran inline on the event loop in the inbound relay
handlers, the pod-backed DM/room send paths and the outbox flush, stalling every
connected client meanwhile. Each test makes the blocking call sleep and checks
that a ticker on the loop keeps running.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import proxion_messenger_core._gateway_dm as _dm_mod
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

BLOCK = 0.8      # how long the fake blocking call takes
MAX_GAP = 0.4    # the loop must never stall this long


@pytest.fixture
def gw(tmp_path):
    g = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                       config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "b.db")),
                       read_state=ReadState())
    g._vapid_private_pem = "fakepem"
    g._vapid_subject = "mailto:t@example.com"
    return g


async def _max_loop_gap(coro):
    """Run *coro* while a ticker measures the longest event-loop stall."""
    gaps = []
    done = asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.02)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    t = asyncio.ensure_future(ticker())
    await asyncio.sleep(0.05)
    try:
        result = await coro
        await asyncio.sleep(BLOCK + 0.2)   # cover background work started by coro
    finally:
        done.set()
        await t
    return result, max(gaps)


async def _pushes_done():
    tasks = list(getattr(_dm_mod, "_PUSH_TASKS", ()))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _slow(*_a, **_k):
    time.sleep(BLOCK)
    return True


@pytest.mark.asyncio
async def test_room_relay_push_runs_off_loop(gw):
    room_id = "push-room"
    gw._local_rooms[room_id] = {"name": "T", "members": set()}
    gw._store.add_room_member(room_id, "did:key:zOffline")
    gw._store.add_federated_room_member(room_id, "did:key:zSender", "https://gw.example.com")
    gw._store.save_push_subscription("s1", "did:key:zOffline",
                                     "https://push.example.com/a", "p256", "auth")
    with patch("proxion_messenger_core.webpush.send_web_push", side_effect=_slow) as push:
        (status, _), gap = await _max_loop_gap(gw._handle_room_relay({
            "room_id": room_id, "from_webid": "did:key:zSender",
            "message_id": "m1", "content": "hi", "timestamp": "2026-06-02T10:00:00Z",
        }))
        await _pushes_done()
    assert push.call_count == 1
    assert gap < MAX_GAP, f"event loop stalled {gap:.2f}s during the push"
    assert status.startswith("200"), status


@pytest.mark.asyncio
async def test_dm_relay_push_runs_off_loop(gw):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from datetime import datetime, timezone
    import json
    import secrets
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.relay import sign_relay_message

    priv = Ed25519PrivateKey.generate()
    from_did = pub_key_to_did(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    to_did = pub_key_to_did(b"\x02" * 32)
    gw._store.save_push_subscription("s3", to_did, "https://push.example.com/d", "p", "a")
    ts = datetime.now(timezone.utc).isoformat()
    nonce = secrets.token_hex(8)
    sig = sign_relay_message(priv, from_did, to_did, "dm-1", "hello", ts, nonce)
    body = json.dumps({"from_webid": from_did, "to_webid": to_did, "message_id": "dm-1",
                       "content": "hello", "timestamp": ts, "signature": sig,
                       "relay_nonce": nonce, "display_name": "S"}).encode()
    with patch("proxion_messenger_core.webpush.send_web_push", side_effect=_slow) as push:
        (status, _), gap = await _max_loop_gap(
            gw._handle_relay_post(body, client_ip="127.0.0.1"))
        await _pushes_done()
    assert status == "202 Accepted", status
    assert push.call_count == 1
    assert gap < MAX_GAP, f"event loop stalled {gap:.2f}s during the push"


@pytest.mark.asyncio
async def test_pod_dm_send_runs_off_loop(gw):
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    gw._client_webids[ws] = "did:key:zMe"
    cert = MagicMock()
    cert.subject = "ab" * 32
    gw.dm_clients["cert-1"] = (cert, MagicMock())
    with patch("proxion_messenger_core.messaging.compose", return_value=MagicMock()), \
         patch("proxion_messenger_core.messaging.send", side_effect=_slow) as send, \
         patch.object(gw, "_is_blocked_for", return_value=False):
        _, gap = await _max_loop_gap(gw._handle_send_dm(
            ws, {"cert_id": "cert-1", "content": "hi", "encrypt": False}))
    assert send.call_count == 1
    assert gap < MAX_GAP, f"event loop stalled {gap:.2f}s during the pod write"


@pytest.mark.asyncio
async def test_outbox_flush_runs_off_loop(gw):
    with patch.object(gw, "_flush_outbox_once", side_effect=_slow) as flush:
        holder = {}

        async def start():
            # Started once the ticker is running, so the first pass is measured.
            holder["task"] = asyncio.ensure_future(gw.flush_loop())

        try:
            _, gap = await _max_loop_gap(start())
        finally:
            gw._stop_event.set()
            task = holder.get("task")
            if task is not None:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
    assert flush.call_count == 1
    assert gap < MAX_GAP, f"event loop stalled {gap:.2f}s during the outbox flush"
