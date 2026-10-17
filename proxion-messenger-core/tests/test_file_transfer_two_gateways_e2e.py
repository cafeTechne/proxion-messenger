"""Large chunked files cross two real gateways over the real /relay path.

Two gateways, one friend on each, real WebSocket clients that speak the same
protocol as web/filetransfer.js: offer, accept, chunks paced by file_chunk_ack,
file_complete, then the receiver's file_received. A 2 MB file and a max-size
(25 MB) file, back to back, must arrive byte for byte and be confirmed.

Before the fix the receiving gateway read only 64 KiB of each /relay body, so
every ~88 KB chunk was truncated and dropped; chunks were also capped at 60 per
minute per IP and per 10 s per socket, while a 25 MB file is ~400 chunks.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os

import pytest

pytest.importorskip("websockets")
import websockets

from gwharness import free_port, start_gateway

CHUNK_BYTES = 64 * 1024      # web/filetransfer.js CHUNK_BYTES
WINDOW = 8                   # web/filetransfer.js CHUNK_WINDOW
MAX_FILE_BYTES = 25 * 1024 * 1024


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")


def _gateway(tmp_path, name):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        http_public_url=f"http://127.0.0.1:{http_port}",
                        db_path=str(tmp_path / f"{name}.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    handle = start_gateway(gw, ws_port, http_port)
    return gw, handle


def _did(gw):
    from proxion_messenger_core.didkey import pub_key_to_did
    return pub_key_to_did(gw.agent.identity_pub_bytes)


def _on_loop(handle, fn):
    asyncio.run_coroutine_threadsafe(_call(fn), handle.loop).result(timeout=5)


async def _call(fn):
    fn()


class Client:
    """A browser stand-in: one socket, events sorted into per-type queues."""

    def __init__(self, ws):
        self.ws = ws
        self.queues: dict[str, asyncio.Queue] = {}
        self._reader = asyncio.create_task(self._read())

    def q(self, type_):
        return self.queues.setdefault(type_, asyncio.Queue())

    async def _read(self):
        async for raw in self.ws:
            ev = json.loads(raw)
            self.q(ev.get("type", "")).put_nowait(ev)

    async def send(self, cmd, **payload):
        await self.ws.send(json.dumps({"cmd": cmd, **payload}))

    async def next(self, type_, timeout=30.0):
        return await asyncio.wait_for(self.q(type_).get(), timeout)


async def _connect(handle, did):
    ws = await websockets.connect(f"ws://127.0.0.1:{handle.ws_port}", max_size=8 * 1024 * 1024)
    c = Client(ws)
    await c.send("register", did=did, display_name=did[-6:])
    await c.next("registered", timeout=10)
    return c


async def _send_file(sender, receiver, to_webid, file_id, data: bytes):
    total = -(-len(data) // CHUNK_BYTES)
    await sender.send("file_offer", to_webid=to_webid, file_id=file_id, filename="blob.pdf",
                      mime_type="application/pdf", size_bytes=len(data), total_chunks=total)
    offer = await receiver.next("file_offer")
    assert offer["file_id"] == file_id
    reply_to = offer["from_webid"]
    await receiver.send("file_accept", to_webid=reply_to, file_id=file_id)
    await sender.next("file_accept")

    got: dict[int, str] = {}

    async def _receive():
        while True:
            ev = await receiver.next("file_chunk")
            got[ev["seq"]] = ev["data"]
            if len(got) == total:
                return

    recv_task = asyncio.create_task(_receive())
    acked = 0
    for seq in range(total):
        while seq - acked >= WINDOW:
            ack = await sender.next("file_chunk_ack")
            assert ack["file_id"] == file_id
            acked += 1
        chunk = data[seq * CHUNK_BYTES:(seq + 1) * CHUNK_BYTES]
        await sender.send("file_chunk", to_webid=to_webid, file_id=file_id, seq=seq,
                          data=base64.b64encode(chunk).decode())
    while acked < total:
        await sender.next("file_chunk_ack")
        acked += 1
    await asyncio.wait_for(recv_task, 30)
    await sender.send("file_complete", to_webid=to_webid, file_id=file_id)
    await receiver.next("file_complete")

    rebuilt = b"".join(base64.b64decode(got[i]) for i in range(total))
    assert hashlib.sha256(rebuilt).digest() == hashlib.sha256(data).digest()
    await receiver.send("file_received", to_webid=reply_to, file_id=file_id)
    done = await sender.next("file_received")
    assert done["file_id"] == file_id
    assert sender.q("file_send_failed").empty()


@pytest.mark.asyncio
async def test_large_files_cross_two_gateways(tmp_path):
    gw_a, h_a = _gateway(tmp_path, "a")
    gw_b, h_b = _gateway(tmp_path, "b")
    ga, gb = _did(gw_a), _did(gw_b)
    alice, bob = "did:key:z6MkAlice" + "a" * 30, "did:key:z6MkBob" + "b" * 32

    def _rel(gw, cert, peer, owner, url):
        def _go():
            gw._store.save_relationship(
                {"certificate_id": cert, "subject": "ab" * 32, "created_at": 0,
                 "expires_at": 2**31 - 1}, peer_did=peer, owner_webid=owner)
            gw._peer_gateway_urls[peer] = url
        return _go

    _on_loop(h_a, _rel(gw_a, "cert-a", gb, alice, f"http://127.0.0.1:{h_b.http_port}"))
    _on_loop(h_b, _rel(gw_b, "cert-b", ga, bob, f"http://127.0.0.1:{h_a.http_port}"))

    a = await _connect(h_a, alice)
    b = await _connect(h_b, bob)
    try:
        two_mb = os.urandom(2 * 1024 * 1024)
        await asyncio.wait_for(_send_file(a, b, gb, "f2mb", two_mb), 60)
        biggest = b"%PDF" + os.urandom(MAX_FILE_BYTES - 4)
        await asyncio.wait_for(_send_file(a, b, gb, "fmax", biggest), 180)
    finally:
        await a.ws.close()
        await b.ws.close()


@pytest.mark.asyncio
async def test_failed_relay_is_reported_to_the_sender(tmp_path, monkeypatch):
    """A chunk that cannot reach the peer gateway fails the transfer loudly
    instead of being dropped while the sender carries on."""
    import proxion_messenger_core._gateway_files as gf
    monkeypatch.setattr(gf, "FILE_RELAY_RETRY_DELAYS", (0.01, 0.01))
    gw_a, h_a = _gateway(tmp_path, "a")
    peer = "did:key:z6MkGone" + "g" * 31
    alice = "did:key:z6MkAlice" + "a" * 30

    def _go():
        gw_a._store.save_relationship(
            {"certificate_id": "cert-a", "subject": "ab" * 32, "created_at": 0,
             "expires_at": 2**31 - 1}, peer_did=peer, owner_webid=alice)
        gw_a._peer_gateway_urls[peer] = f"http://127.0.0.1:{free_port()}"   # nothing listening
    _on_loop(h_a, _go)
    a = await _connect(h_a, alice)
    try:
        await a.send("file_chunk", to_webid=peer, file_id="fx", seq=0,
                     data=base64.b64encode(b"%PDF-1.7 hello").decode())
        failed = await a.next("file_send_failed", timeout=15)
        assert failed["file_id"] == "fx"
        assert a.q("file_chunk_ack").empty()
    finally:
        await a.ws.close()


def test_relayed_chunks_have_their_own_budget(tmp_path):
    """File chunks do not spend the 60/min relay budget DMs use, and are capped
    in bytes per signing gateway instead."""
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core import _gateway_http as gh

    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")))
    chunk = "A" * (88 * 1024)
    allowed = gh.FILE_RELAY_BYTES_PER_MIN // len(chunk)
    assert allowed * 64 * 1024 >= 2 * MAX_FILE_BYTES, "budget must fit two max-size files a minute"
    for _ in range(allowed):
        assert gw._file_relay_bytes_ok("did:key:zPeer", chunk, 1000.0)
    assert not gw._file_relay_bytes_ok("did:key:zPeer", chunk, 1001.0)
    assert gw._file_relay_bytes_ok("did:key:zOther", chunk, 1001.0)      # per gateway
    assert gw._file_relay_bytes_ok("did:key:zPeer", chunk, 1061.0)       # window slides
    assert gh._relay_body_is_file_chunk(json.dumps({"content_type": "file_chunk"}).encode())
    assert not gh._relay_body_is_file_chunk(b'{"content_type": "dm"}')
    assert not gh._relay_body_is_file_chunk(b"not json")
