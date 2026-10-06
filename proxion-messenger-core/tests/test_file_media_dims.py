"""Optional width/height on file attachments: validated, bounded, never required."""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core._gateway_files import (
    MAX_MEDIA_DIM, media_dims, sanitize_file_dims,
)
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

from proxion_messenger_core.relay import sign_relay_message


def _make_relay_body(sender_key, from_did, to_did, extra):
    from datetime import datetime, timezone
    import secrets
    ts = datetime.now(timezone.utc).isoformat()
    nonce = secrets.token_hex(8)
    sig = sign_relay_message(sender_key, from_did, to_did, "file-dims-001", "file", ts, nonce)
    payload = {
        "from_webid": from_did, "to_webid": to_did, "message_id": "file-dims-001",
        "content": "file", "timestamp": ts, "signature": sig, "relay_nonce": nonce,
        "display_name": "Sender", **extra,
    }
    return json.dumps(payload).encode()


def _small_file():
    return {"filename": "test.txt", "mime_type": "text/plain", "size": 11,
            "data_b64": base64.b64encode(b"hello world").decode()}


def test_media_dims_accepts_valid_pair():
    assert media_dims({"width": 640, "height": 480}) == {"width": 640, "height": 480}
    assert media_dims({"width": 1, "height": MAX_MEDIA_DIM}) == {"width": 1, "height": MAX_MEDIA_DIM}


@pytest.mark.parametrize("w,h", [
    (0, 10), (10, 0), (-5, 10), (MAX_MEDIA_DIM + 1, 10), (10.5, 10), ("640", 480),
    (True, 10), (None, 10), (640, None), (10 ** 12, 10),
])
def test_media_dims_drops_invalid(w, h):
    assert media_dims({"width": w, "height": h}) == {}


def test_media_dims_missing_or_not_dict():
    assert media_dims({}) == {}
    assert media_dims(None) == {}
    assert media_dims("x") == {}


def test_sanitize_file_dims_keeps_other_fields():
    f = {"filename": "a.png", "width": 9e9, "height": 2}
    out = sanitize_file_dims(f)
    assert out == {"filename": "a.png"}
    ok = sanitize_file_dims({"filename": "a.png", "width": 3, "height": 2})
    assert ok == {"filename": "a.png", "width": 3, "height": 2}


@pytest.fixture
def gateway():
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9975), read_state=ReadState(),
    )


def _member(gw, webid="did:key:dims-user"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    gw._local_rooms["room-dims"] = {
        "name": "Dims", "code": "x" * 64, "members": {ws}, "invite_url": "",
        "history_mode": "none", "messages": [], "creator_webid": webid,
    }
    return ws


def _echoed_file(ws):
    files = [json.loads(c[0][0]).get("file") for c in ws.send.call_args_list]
    files = [f for f in files if f]
    assert files
    return files[-1]


_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 100).decode()


@pytest.mark.asyncio
async def test_send_file_echoes_valid_dims(gateway):
    ws = _member(gateway)
    await gateway._handle_send_file(ws, {
        "room_id": "room-dims", "filename": "p.jpg", "mime_type": "image/jpeg",
        "data_b64": _JPEG, "width": 800, "height": 600,
    })
    f = _echoed_file(ws)
    assert f["width"] == 800 and f["height"] == 600


@pytest.mark.asyncio
async def test_send_file_without_dims_unchanged(gateway):
    ws = _member(gateway)
    await gateway._handle_send_file(ws, {
        "room_id": "room-dims", "filename": "p.jpg", "mime_type": "image/jpeg",
        "data_b64": _JPEG,
    })
    f = _echoed_file(ws)
    assert "width" not in f and "height" not in f


@pytest.mark.asyncio
async def test_send_file_drops_bogus_dims(gateway):
    ws = _member(gateway)
    await gateway._handle_send_file(ws, {
        "room_id": "room-dims", "filename": "p.jpg", "mime_type": "image/jpeg",
        "data_b64": _JPEG, "width": "<script>", "height": 10 ** 9,
    })
    f = _echoed_file(ws)
    assert "width" not in f and "height" not in f


@pytest.fixture
def relay_gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9992, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "t.db"))
    return gw


async def _relay_file(gw, file_obj):
    priv = Ed25519PrivateKey.generate()
    from_did = pub_key_to_did(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    to_did = pub_key_to_did(b"\x07" * 32)
    ws = AsyncMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = to_did
    gw._webid_sockets[to_did] = {ws}
    body = _make_relay_body(priv, from_did, to_did, {"file": file_obj})
    status, _ = await gw._handle_relay_post(body, client_ip="127.0.0.1")
    assert status.startswith("200")
    return json.loads(ws.send.call_args[0][0])["file"]


@pytest.mark.asyncio
async def test_relayed_file_keeps_valid_dims(relay_gateway):
    f = await _relay_file(relay_gateway, {**_small_file(), "width": 320, "height": 200})
    assert f["width"] == 320 and f["height"] == 200


@pytest.mark.asyncio
async def test_relayed_file_strips_invalid_dims(relay_gateway):
    f = await _relay_file(relay_gateway, {**_small_file(), "width": -1, "height": "big"})
    assert "width" not in f and "height" not in f
    assert f["filename"] == "test.txt"
