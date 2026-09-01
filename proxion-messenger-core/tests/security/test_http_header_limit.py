"""HTTP request header flood protection: too many header lines return 431."""
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


@pytest.fixture
async def http_server(tmp_path):
    port = _free_port()
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port + 1000),
        read_state=ReadState(),
    )
    import os
    web_dir = str(tmp_path / "web")
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
async def test_too_many_header_lines_returns_431(http_server):
    """A request with >100 header lines returns 431 and does not hang."""
    host, port = http_server
    reader, writer = await asyncio.open_connection(host, port)
    req = f"GET / HTTP/1.0\r\nHost: {host}:{port}\r\n".encode()
    req += b"".join(f"X-Pad-{i}: v\r\n".encode() for i in range(200))
    req += b"\r\n"
    writer.write(req)
    await writer.drain()
    status = (await asyncio.wait_for(reader.readline(), timeout=5.0)).decode().strip()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    assert "431" in status, f"Expected 431, got {status!r}"


@pytest.mark.asyncio
async def test_normal_header_count_ok(http_server):
    """A request with a handful of headers is served normally (not 431)."""
    host, port = http_server
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"GET / HTTP/1.0\r\nHost: {host}:{port}\r\n"
        f"User-Agent: test\r\nAccept: */*\r\n\r\n"
    ).encode()
    writer.write(req)
    await writer.drain()
    status = (await asyncio.wait_for(reader.readline(), timeout=5.0)).decode().strip()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    assert "431" not in status, f"Unexpected 431 for a normal request: {status!r}"
