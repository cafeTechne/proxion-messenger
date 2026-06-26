"""Round 1: HTTP security headers present on all key responses."""
import asyncio
import json
import os
import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_get(port: int, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal async HTTP/1.1 GET, returns (status_line, headers_dict, body)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        req = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        writer.write(req.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    finally:
        writer.close()
    # Parse
    header_part, _, body = raw.partition(b"\r\n\r\n")
    lines = header_part.decode(errors="replace").split("\r\n")
    status = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


def _assert_sec_headers(headers: dict[str, str], path: str) -> None:
    assert "x-content-type-options" in headers, \
        f"X-Content-Type-Options missing from {path} response"
    assert headers["x-content-type-options"].lower() == "nosniff"
    assert "referrer-policy" in headers, \
        f"Referrer-Policy missing from {path} response"
    assert "content-security-policy" in headers, \
        f"Content-Security-Policy missing from {path} response"
    assert "cross-origin-opener-policy" in headers, \
        f"Cross-Origin-Opener-Policy missing from {path} response"


@pytest.fixture
async def http_server(tmp_path):
    agent = AgentState.generate()
    port = _free_port()
    web_dir = str(tmp_path / "web")
    os.makedirs(web_dir, exist_ok=True)
    (tmp_path / "web" / "index.html").write_text("<html><head></head><body>hi</body></html>")
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=port + 1, http_port=port, web_dir=web_dir),
        read_state=ReadState(),
    )
    task = asyncio.create_task(gw._serve_http(web_dir, port))
    await asyncio.sleep(0.15)
    yield port
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_health_endpoint_sets_security_headers(http_server):
    port = http_server
    _, headers, _ = await _http_get(port, "/health")
    _assert_sec_headers(headers, "/health")


@pytest.mark.asyncio
async def test_static_response_sets_csp_and_nosniff(http_server):
    port = http_server
    _, headers, _ = await _http_get(port, "/index.html")
    _assert_sec_headers(headers, "/index.html")
    # CSP must contain expected directives
    csp = headers.get("content-security-policy", "")
    assert "default-src" in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.asyncio
async def test_headers_present_on_error_responses(http_server):
    port = http_server
    _, headers, _ = await _http_get(port, "/this-does-not-exist.xyz")
    _assert_sec_headers(headers, "/404-path")
