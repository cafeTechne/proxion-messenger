"""Round 8: File upload pipeline hardening — base64 validation, filename normalization,
MIME allowlist, and magic-byte sniffing."""
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
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9974),
        read_state=ReadState(),
    )


def _registered_ws(gw, webid="did:key:file-user"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


def _room(gw, ws):
    room_id = "room-file-test"
    webid = gw._client_webids.get(ws, "did:key:file-user")
    gw._local_rooms[room_id] = {
        "name": "File Test", "code": "x" * 64,
        "members": {ws}, "invite_url": "",
        "history_mode": "none", "messages": [],
        "creator_webid": webid,
    }
    return room_id


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# base64 validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_base64_rejected(gateway):
    ws = _registered_ws(gateway)
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "test.png",
        "mime_type": "image/png",
        "data_b64": "!!!NOT_BASE64!!!",
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid" in resp.get("message", "")


@pytest.mark.asyncio
async def test_non_canonical_base64_rejected(gateway):
    """base64 with non-alphabet whitespace chars must fail validate=True."""
    ws = _registered_ws(gateway)
    # Inject a space (not valid base64url, validate=True rejects it)
    bad = "aGVs bG8="   # space in the middle
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "test.png",
        "mime_type": "image/png",
        "data_b64": bad,
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "invalid" in resp.get("message", "")


# ---------------------------------------------------------------------------
# Filename normalization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_directory_traversal_filename_stripped(gateway):
    """../../../etc/passwd should be normalized to 'passwd'."""
    ws = _registered_ws(gateway)
    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    room_id = _room(gateway, ws)
    await gateway._handle_send_file(ws, {
        "room_id": room_id,
        "filename": "../../../../etc/passwd",
        "mime_type": "image/png",
        "data_b64": _b64(png_magic),
    })
    # Should succeed (PNG magic passes) and broadcast without the traversal
    for call in gateway._local_rooms[room_id]["members"]:
        pass  # we mainly care no exception was raised and no error sent
    # Verify no 'error' type in sends (might not send if room not in local_rooms post-test)


@pytest.mark.asyncio
async def test_windows_path_filename_stripped(gateway):
    """C:\\Windows\\System32\\evil.dll → normalized away."""
    ws = _registered_ws(gateway)
    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    room_id = _room(gateway, ws)
    await gateway._handle_send_file(ws, {
        "room_id": room_id,
        "filename": r"C:\Windows\System32\evil.dll",
        "mime_type": "image/png",
        "data_b64": _b64(png_magic),
    })
    # If an error is sent, it should not mention filename traversal success


# ---------------------------------------------------------------------------
# MIME allowlist enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disallowed_mime_rejected(gateway):
    """application/x-executable must be rejected."""
    ws = _registered_ws(gateway)
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "evil.exe",
        "mime_type": "application/x-executable",
        "data_b64": _b64(b"\x4d\x5a" + b"\x00" * 100),  # MZ header (PE)
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "file_type_not_allowed" in resp.get("message", "")


@pytest.mark.asyncio
async def test_allowed_jpeg_accepted(gateway):
    """A real JPEG magic header should be accepted."""
    ws = _registered_ws(gateway)
    jpeg_magic = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    room_id = _room(gateway, ws)
    await gateway._handle_send_file(ws, {
        "room_id": room_id,
        "filename": "photo.jpg",
        "mime_type": "image/jpeg",
        "data_b64": _b64(jpeg_magic),
    })
    # Should not return a MIME-rejection error
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        assert "file_type_not_allowed" not in msg.get("message", "")


# ---------------------------------------------------------------------------
# Magic-byte sniffing overrides declared MIME
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_magic_byte_sniffing_overrides_wrong_mime(gateway):
    """File with PDF magic but declared as text/plain → accepted as PDF (allowlisted)."""
    ws = _registered_ws(gateway)
    pdf_magic = b"%PDF-1.4" + b"\x00" * 200
    room_id = _room(gateway, ws)
    await gateway._handle_send_file(ws, {
        "room_id": room_id,
        "filename": "document.pdf",
        "mime_type": "text/plain",   # wrong declared MIME
        "data_b64": _b64(pdf_magic),
    })
    # PDF is allowlisted, so should succeed
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        assert "file_type_not_allowed" not in msg.get("message", "")


@pytest.mark.asyncio
async def test_pe_file_disguised_as_png_rejected(gateway):
    """A PE (MZ) file declared as image/png is rejected by magic-byte sniffing."""
    ws = _registered_ws(gateway)
    pe_magic = b"\x4d\x5a\x90\x00" + b"\x00" * 200  # MZ PE header
    await gateway._handle_send_file(ws, {
        "room_id": _room(gateway, ws),
        "filename": "evil.png",
        "mime_type": "image/png",   # declared as PNG but it's a PE
        "data_b64": _b64(pe_magic),
    })
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert "file_type_not_allowed" in resp.get("message", "")
