"""Tests: SVG file upload is blocked (RCE hardening S1)."""
from __future__ import annotations
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


@pytest.mark.asyncio
async def test_svg_upload_rejected(gateway):
    """SVG file uploads are rejected with file_type_not_allowed."""
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway.clients.add(ws)

    svg_content = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    svg_b64 = base64.b64encode(svg_content).decode()

    await gateway._handle_send_file(ws, {
        "cert_id": "cert-test",
        "filename": "evil.svg",
        "data_b64": svg_b64,
        "mime_type": "image/svg+xml",
    })

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "error"
    assert "not_allowed" in sent["message"]


@pytest.mark.asyncio
async def test_png_upload_not_blocked_by_svg_removal(gateway):
    """PNG uploads still work after removing SVG from the allowlist."""
    ws = _ws()
    gateway._client_webids[ws] = "did:key:zAlice"
    gateway.clients.add(ws)

    # Minimal valid PNG (1×1 pixel)
    png_bytes = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    png_b64 = base64.b64encode(png_bytes).decode()

    await gateway._handle_send_file(ws, {
        "cert_id": "cert-test",
        "filename": "photo.png",
        "data_b64": png_b64,
        "mime_type": "image/png",
    })

    # Should NOT get a file_type_not_allowed error
    if ws.send.called:
        for call in ws.send.call_args_list:
            sent = json.loads(call[0][0])
            assert "not_allowed" not in sent.get("message", ""), \
                f"PNG was incorrectly rejected: {sent}"
