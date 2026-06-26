"""Round 2: File upload anti-content-smuggling checks."""
import base64
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9964),
        read_state=ReadState(),
    )


def _ws(gw, webid="did:key:file-user2"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _room(gw, ws):
    room_id = "room-integrity-test"
    webid = gw._client_webids.get(ws, "did:key:file-user2")
    gw._local_rooms[room_id] = {
        "name": "Test", "code": "x" * 64, "members": {ws},
        "invite_url": "", "history_mode": "none", "messages": [],
        "creator_webid": webid,
    }
    return room_id


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.mark.asyncio
async def test_reject_zero_byte_file(gateway):
    """Empty file is rejected with invalid_file_encoding."""
    ws = _ws(gateway)
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "empty.txt",
        "mime_type": "text/plain",
        "data_b64": _b64(b""),
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid" in resp["message"]


@pytest.mark.asyncio
async def test_reject_text_mime_with_binary_nuls(gateway):
    """text/plain with >20% NUL bytes is rejected."""
    ws = _ws(gateway)
    # Build content with >20% NUL bytes
    data = b"hello" + b"\x00" * 30  # 35 bytes total, 30/35 = ~86% NUL
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "binary.txt",
        "mime_type": "text/plain",
        "data_b64": _b64(data),
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "file_type_not_allowed" in resp["message"]


@pytest.mark.asyncio
async def test_text_file_with_few_nuls_accepted(gateway):
    """text/plain with <20% NUL bytes is not rejected for NUL content."""
    ws = _ws(gateway)
    # <20% NUL bytes in plaintext — 1 out of 20 = 5%
    data = b"hello world !\x00 extra padding here"
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "oktext.txt",
        "mime_type": "text/plain",
        "data_b64": _b64(data),
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    nul_errors = [c for c in calls if "binary content" in c.get("message", "")]
    assert not nul_errors, f"Should not be rejected for NUL content: {calls}"


@pytest.mark.asyncio
async def test_reject_executable_magic_even_with_safe_mime(gateway):
    """ELF binary declared as image/png is rejected."""
    ws = _ws(gateway)
    elf_data = b"\x7fELF" + b"\x00" * 200
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "notreal.png",
        "mime_type": "image/png",
        "data_b64": _b64(elf_data),
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "file_type_not_allowed" in resp["message"]


@pytest.mark.asyncio
async def test_mime_normalization_strips_parameters(gateway):
    """mime_type with charset parameter is normalized before allowlist check."""
    ws = _ws(gateway)
    # PDF magic bytes — should be accepted even when declared as text/plain; charset=utf-8
    pdf_data = b"%PDF-1.4" + b"\x00" * 200
    room_id = _room(gateway, ws)
    await gateway._handle_send_file(ws, {
        "room_id": room_id,
        "filename": "doc.pdf",
        "mime_type": "TEXT/PLAIN; charset=utf-8",  # uppercase + parameter
        "data_b64": _b64(pdf_data),
    })
    calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    # Should not get file_type_not_allowed for MIME — PDF magic sniffed and accepted
    disallowed = [c for c in calls if "file_type_not_allowed" in c.get("message", "")]
    assert not disallowed, f"Should not block normalized PDF: {calls}"
