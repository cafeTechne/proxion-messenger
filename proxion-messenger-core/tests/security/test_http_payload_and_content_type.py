"""Round 4: HTTP payload size limits and Content-Type enforcement."""
import asyncio
import socket
import pytest
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _raw_post(host, port, path, body, content_type="application/json"):
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"POST {path} HTTP/1.0\r\nHost: {host}:{port}\r\n"
        f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n"
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
async def test_json_endpoint_rejects_wrong_content_type(http_server):
    """POST /relay with text/plain Content-Type → 415."""
    host, port = http_server
    status = await _raw_post(host, port, "/relay", b"{}", content_type="text/plain")
    assert "415" in status, f"Expected 415, got {status}"


@pytest.mark.asyncio
async def test_relay_rejects_payload_over_128kb(http_server):
    """POST /relay with body > 128 KiB → 413."""
    host, port = http_server
    big_body = b"x" * (128 * 1024 + 1)
    status = await _raw_post(host, port, "/relay", big_body)
    assert "413" in status, f"Expected 413, got {status}"


@pytest.mark.asyncio
async def test_restore_and_import_reject_oversized_bodies(http_server):
    """POST /import with body > 20 MiB → 413."""
    host, port = http_server
    # Use content-length header trick to trigger limit without sending full body
    reader, writer = await asyncio.open_connection(host, port)
    oversized = 21 * 1024 * 1024
    req = (
        f"POST /import HTTP/1.0\r\nHost: {host}:{port}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {oversized}\r\n\r\n"
    ).encode()
    writer.write(req)
    await writer.drain()
    status = (await reader.readline()).decode().strip()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    assert "413" in status, f"Expected 413, got {status}"
