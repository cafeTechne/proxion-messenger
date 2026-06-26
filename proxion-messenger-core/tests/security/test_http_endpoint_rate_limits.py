"""Round 4: Per-IP HTTP endpoint rate limiting."""
import asyncio
import json
import socket
import pytest
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _post(host, port, path, body=b"{}"):
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"POST {path} HTTP/1.0\r\nHost: {host}:{port}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    writer.write(req)
    await writer.drain()
    status = (await reader.readline()).decode().strip()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return status


@pytest.fixture
async def http_server(tmp_path):
    port = _free_port()
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port + 1000),
        read_state=ReadState(),
    )
    web_dir = str(tmp_path / "web")
    import os
    os.makedirs(web_dir, exist_ok=True)
    (tmp_path / "web" / "index.html").write_text("<html></html>")
    task = asyncio.create_task(gw._serve_http(web_dir, port))
    await asyncio.sleep(0.1)
    yield "127.0.0.1", port
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_relay_endpoint_rate_limited_per_ip(http_server):
    """POST /relay rate limited to 60/min per IP; 61st returns 429."""
    host, port = http_server
    # Exhaust the 60-request limit
    for _ in range(60):
        await _post(host, port, "/relay")
    status = await _post(host, port, "/relay")
    assert "429" in status, f"Expected 429, got {status}"


@pytest.mark.asyncio
async def test_invite_endpoints_rate_limited_per_ip(http_server):
    """POST /invite rate limited to 20/min per IP; 21st returns 429."""
    host, port = http_server
    invite_body = json.dumps({"@type": "FederationInvite"}).encode()
    for _ in range(20):
        await _post(host, port, "/invite", invite_body)
    status = await _post(host, port, "/invite", invite_body)
    assert "429" in status, f"Expected 429 after 21 requests, got {status}"


@pytest.mark.asyncio
async def test_backup_restore_import_rate_limited_per_ip(http_server):
    """POST /import rate limited to 5/min per IP; 6th returns 429."""
    host, port = http_server
    for _ in range(5):
        await _post(host, port, "/import")
    status = await _post(host, port, "/import")
    assert "429" in status, f"Expected 429 after 6 /import requests, got {status}"
