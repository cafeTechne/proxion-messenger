"""Round 2: HTTP security policy headers on gateway responses."""
import asyncio
import json
import socket
import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_get(host: str, port: int, path: str) -> dict:
    """Make a raw HTTP GET and return parsed header dict + status line."""
    reader, writer = await asyncio.open_connection(host, port)
    request = f"GET {path} HTTP/1.0\r\nHost: {host}:{port}\r\n\r\n"
    writer.write(request.encode())
    await writer.drain()
    headers = {}
    status_line = (await reader.readline()).decode().strip()
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=3.0)
        if line.strip() == b"":
            break
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return {"status": status_line, "headers": headers}


async def _http_post(host: str, port: int, path: str, body: bytes = b"{}") -> dict:
    """Make a raw HTTP POST and return parsed header dict + status line."""
    reader, writer = await asyncio.open_connection(host, port)
    request = (
        f"POST {path} HTTP/1.0\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    writer.write(request.encode() + body)
    await writer.drain()
    headers = {}
    status_line = (await reader.readline()).decode().strip()
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=3.0)
        if line.strip() == b"":
            break
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return {"status": status_line, "headers": headers}


@pytest.fixture
async def http_server(tmp_path):
    """Start the gateway HTTP server on a free port; yield (host, port)."""
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
    # Write a minimal index.html so static serving doesn't 404
    (tmp_path / "web" / "index.html").write_text("<html></html>")

    server_task = asyncio.create_task(gw._serve_http(web_dir, port))
    # Give the server a moment to bind
    await asyncio.sleep(0.1)
    yield "127.0.0.1", port
    server_task.cancel()
    try:
        await server_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_permissions_policy_header_present(http_server):
    """GET /health returns Permissions-Policy header."""
    host, port = http_server
    resp = await _http_get(host, port, "/health")
    hdrs = resp["headers"]
    assert "permissions-policy" in hdrs, f"Missing Permissions-Policy. Headers: {hdrs}"
    policy = hdrs["permissions-policy"]
    assert "microphone" in policy
    assert "camera" in policy
    assert "geolocation" in policy


@pytest.mark.asyncio
async def test_dynamic_endpoints_use_no_store(http_server):
    """/health and POST /relay both return Cache-Control: no-store."""
    host, port = http_server

    health = await _http_get(host, port, "/health")
    cc = health["headers"].get("cache-control", "")
    assert "no-store" in cc, f"/health missing no-store. Cache-Control: {cc!r}"

    relay_payload = json.dumps({
        "from_webid": "did:key:tester",
        "to_webid": "did:key:nobody",
        "message_id": "test-msg-001",
        "content": "hello",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "relay_nonce": "abcdef1234567890",
    }).encode()
    relay = await _http_post(host, port, "/relay", relay_payload)
    cc2 = relay["headers"].get("cache-control", "")
    assert "no-store" in cc2, f"/relay missing no-store. Cache-Control: {cc2!r}"


@pytest.mark.asyncio
async def test_corp_header_present(http_server):
    """/health returns Cross-Origin-Resource-Policy: same-origin."""
    host, port = http_server
    resp = await _http_get(host, port, "/health")
    hdrs = resp["headers"]
    assert "cross-origin-resource-policy" in hdrs, (
        f"Missing Cross-Origin-Resource-Policy. Headers: {hdrs}"
    )
    assert hdrs["cross-origin-resource-policy"] == "same-origin"


@pytest.mark.asyncio
async def test_coop_header_present(http_server):
    """/health returns Cross-Origin-Opener-Policy: same-origin."""
    host, port = http_server
    resp = await _http_get(host, port, "/health")
    hdrs = resp["headers"]
    assert "cross-origin-opener-policy" in hdrs, (
        f"Missing Cross-Origin-Opener-Policy. Headers: {hdrs}"
    )
    assert hdrs["cross-origin-opener-policy"] == "same-origin"
