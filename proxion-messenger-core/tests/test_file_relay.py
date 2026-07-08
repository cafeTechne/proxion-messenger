"""Tests: file relay over HTTP /relay endpoint."""
from __future__ import annotations
import base64
import json
import pytest
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from unittest.mock import MagicMock, AsyncMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def sender_key():
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, pub_key_to_did(pub_bytes)


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


def _make_relay_body(sender_key, from_did: str, to_did: str, extra: dict | None = None) -> bytes:
    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "file-msg-001"
    content = "file"
    import secrets
    nonce = secrets.token_hex(8)
    sig = sign_relay_message(sender_key, from_did, to_did, msg_id, content, ts, nonce)
    payload = {
        "from_webid": from_did,
        "to_webid": to_did,
        "message_id": msg_id,
        "content": content,
        "timestamp": ts,
        "signature": sig,
        "relay_nonce": nonce,
        "display_name": "Sender",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload).encode()


def _small_file() -> dict:
    return {
        "filename": "test.txt",
        "mime_type": "text/plain",
        "size": 1100,
        "data_b64": base64.b64encode(b"hello world" * 100).decode(),
    }


@pytest.mark.asyncio
async def test_relay_with_file_is_accepted(gateway, sender_key, tmp_path):
    """POST /relay with a 'file' key is accepted (was previously rejected as 400)."""
    priv, from_did = sender_key

    # Register target as connected
    recipient_did = pub_key_to_did(b"\x02" * 32)
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    gateway.clients.add(ws)
    gateway._client_webids[ws] = recipient_did
    gateway._webid_sockets[recipient_did] = {ws}

    body = _make_relay_body(priv, from_did, recipient_did, {"file": _small_file()})
    status, response = await gateway._handle_relay_post(body, client_ip="127.0.0.1")

    assert not status.startswith("400") or "unsupported_relay_attachment" not in response, \
        f"File relay wrongly rejected: {status} {response}"


@pytest.mark.asyncio
async def test_relay_oversized_file_rejected(gateway, sender_key):
    """POST /relay with file.data_b64 > 128 KiB returns 413 before signature check."""
    priv, from_did = sender_key
    recipient_did = pub_key_to_did(b"\x03" * 32)

    huge_b64 = base64.b64encode(b"x" * 100000).decode()  # ~133 KiB base64
    body = _make_relay_body(priv, from_did, recipient_did, {
        "file": {"filename": "big.bin", "mime_type": "text/plain", "size": 100000, "data_b64": huge_b64}
    })
    status, _ = await gateway._handle_relay_post(body, client_ip="127.0.0.1")
    assert status.startswith("413"), f"Expected 413, got {status}"


@pytest.mark.asyncio
async def test_file_chunk_relay_to_gateway_identity_reaches_local_client(gateway):
    """Cross-gateway file transfer: a chunk addressed to THIS gateway's own identity
    (the recipient's Proxion address) must reach the local user's browser, which
    registered under its own client DID. Covered by the centralized _sockets_for
    one-gateway-per-user fallback (same fix as cross-gateway DM/voice)."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zBrowserClient"
    gateway._webid_sockets["did:key:zBrowserClient"] = {ws}

    gateway_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
    gateway._store.save_relationship(
        {"certificate_id": "cf-al", "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did="did:key:zAlice", owner_webid="")
    status, _ = await gateway._handle_file_relay({
        "content_type": "file_chunk",
        "to_webid": gateway_did,
        "from_webid": "did:key:zAlice",
        "file_id": "f1", "seq": 0, "data": "AAAA",
    })
    assert status == "200 OK"
    ws.send.assert_called_once()
    assert json.loads(ws.send.call_args[0][0])["type"] == "file_chunk"


@pytest.mark.asyncio
async def test_relay_file_forwarded_to_target_socket(gateway, sender_key):
    """File in relay payload is forwarded to the target's WebSocket."""
    priv, from_did = sender_key
    recipient_did = pub_key_to_did(b"\x04" * 32)

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    gateway.clients.add(ws)
    gateway._client_webids[ws] = recipient_did
    gateway._webid_sockets[recipient_did] = {ws}

    file_data = _small_file()
    body = _make_relay_body(priv, from_did, recipient_did, {"file": file_data})
    status, _ = await gateway._handle_relay_post(body, client_ip="127.0.0.1")

    assert status.startswith("200"), f"Expected 200, got {status}"
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert "file" in sent
    assert sent["file"]["filename"] == "test.txt"


@pytest.mark.asyncio
async def test_relay_unknown_key_still_rejected(gateway, sender_key):
    """POST /relay with unknown top-level field still returns 400."""
    priv, from_did = sender_key
    recipient_did = pub_key_to_did(b"\x05" * 32)

    body = _make_relay_body(priv, from_did, recipient_did, {"evil_field": "payload"})
    status, _ = await gateway._handle_relay_post(body, client_ip="127.0.0.1")
    assert status.startswith("400"), f"Expected 400, got {status}"
