"""Tests: chunked large-file transfer forwarding (R39)."""
from __future__ import annotations
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.file_transfer import chunk_count, CHUNK_SIZE, TIER1_MAX_BYTES, FileOffer
from proxion_messenger_core._gateway_files import MAX_FILE_BYTES, MAX_CHUNK_B64_LEN


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


# ── file_transfer helpers ──

def test_chunk_count_boundaries():
    assert chunk_count(0) == 0
    assert chunk_count(1) == 1
    assert chunk_count(CHUNK_SIZE) == 1
    assert chunk_count(CHUNK_SIZE + 1) == 2
    assert chunk_count(CHUNK_SIZE * 3) == 3


def test_file_offer_tier():
    small = FileOffer.new("a.jpg", "image/jpeg", 1024)
    big = FileOffer.new("b.bin", "application/octet-stream", TIER1_MAX_BYTES + 1)
    assert small.tier() == 1
    assert big.tier() == 2


# ── offer validation ──

@pytest.mark.asyncio
async def test_file_offer_rejects_oversized(gateway):
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    await gateway._handle_file_offer(ws, {
        "to_webid": "did:key:zBob", "file_id": "f1",
        "filename": "huge.bin", "size_bytes": MAX_FILE_BYTES + 1, "total_chunks": 999,
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error"
    assert sent["message"] == "file_too_large"


# ── local forwarding ──

@pytest.mark.asyncio
async def test_file_offer_forwarded_to_local_recipient(gateway):
    sender = _ws(); recipient = _ws()
    gateway._client_webids[sender] = "did:key:zAlice"
    gateway._client_webids[recipient] = "did:key:zBob"
    gateway._webid_sockets["did:key:zBob"] = {recipient}
    gateway.clients.add(sender); gateway.clients.add(recipient)

    await gateway._handle_file_offer(sender, {
        "to_webid": "did:key:zBob", "file_id": "f2",
        "filename": "photo.jpg", "mime_type": "image/jpeg",
        "size_bytes": 200000, "total_chunks": 4,
    })

    recipient.send.assert_called_once()
    sent = json.loads(recipient.send.call_args[0][0])
    assert sent["type"] == "file_offer"
    assert sent["file_id"] == "f2"
    assert sent["from_webid"] == "did:key:zAlice"
    assert sent["total_chunks"] == 4


@pytest.mark.asyncio
async def test_file_chunk_forwarded_to_local_recipient(gateway):
    sender = _ws(); recipient = _ws()
    gateway._client_webids[sender] = "did:key:zAlice"
    gateway._client_webids[recipient] = "did:key:zBob"
    gateway._webid_sockets["did:key:zBob"] = {recipient}
    gateway.clients.add(sender); gateway.clients.add(recipient)

    chunk_b64 = base64.b64encode(b"x" * CHUNK_SIZE).decode()
    await gateway._handle_file_chunk(sender, {
        "to_webid": "did:key:zBob", "file_id": "f3", "seq": 0, "data": chunk_b64,
    })

    recipient.send.assert_called_once()
    sent = json.loads(recipient.send.call_args[0][0])
    assert sent["type"] == "file_chunk"
    assert sent["seq"] == 0
    assert sent["data"] == chunk_b64


@pytest.mark.asyncio
async def test_file_chunk_rejects_oversized_chunk(gateway):
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    huge = "a" * (MAX_CHUNK_B64_LEN + 1)
    await gateway._handle_file_chunk(ws, {
        "to_webid": "did:key:zBob", "file_id": "f4", "seq": 0, "data": huge,
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error"
    assert sent["message"] == "chunk_too_large"


# ── cross-gateway relay ──

@pytest.mark.asyncio
async def test_file_offer_relays_when_recipient_remote(gateway):
    sender = _ws()
    gateway._client_webids[sender] = "did:key:zAlice"
    gateway.clients.add(sender)
    gateway._peer_gateway_urls["did:key:zBob"] = "https://bob.example.com"

    tasks = []
    with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c) or MagicMock()):
        await gateway._handle_file_offer(sender, {
            "to_webid": "did:key:zBob", "file_id": "f5",
            "filename": "doc.pdf", "size_bytes": 300000, "total_chunks": 5,
        })
    assert len(tasks) == 1  # relayed to Bob's gateway


@pytest.mark.asyncio
async def test_file_relay_inbound_delivers_to_local(gateway):
    recipient = _ws()
    gateway._client_webids[recipient] = "did:key:zBob"
    gateway._webid_sockets["did:key:zBob"] = {recipient}
    gateway.clients.add(recipient)

    status, _ = await gateway._handle_file_relay({
        "content_type": "file_chunk", "to_webid": "did:key:zBob",
        "from_webid": "did:key:zAlice", "file_id": "f6", "seq": 2,
        "data": base64.b64encode(b"y" * 1000).decode(),
    })
    assert status.startswith("200")
    sent = json.loads(recipient.send.call_args[0][0])
    assert sent["type"] == "file_chunk"
    assert sent["seq"] == 2


@pytest.mark.asyncio
async def test_file_relay_offline_returns_202(gateway):
    status, body = await gateway._handle_file_relay({
        "content_type": "file_offer", "to_webid": "did:key:zOffline",
        "from_webid": "did:key:zAlice", "file_id": "f7",
    })
    assert status.startswith("202")
    assert "offline" in body
