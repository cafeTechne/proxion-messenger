"""Round 23 security tests: passphrase via X-Proxion-Passphrase header for
/backup and /restore endpoints."""
import asyncio
import json
import socket
import pytest

pytest.importorskip("websockets")

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from gwharness import start_gateway as _serve_gw


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_gateway(tmp_path):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={},
                        config=cfg, read_state=ReadState())
    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.http_port, handle.ready


async def _http(http_port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(request)
    await writer.drain()
    resp = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    writer.close()
    return resp


# ── /backup ──────────────────────────────────────────────────────────────────


class TestBackupPassphrase:
    @pytest.mark.asyncio
    async def test_backup_accepts_passphrase_via_header(self, tmp_path):
        """GET /backup with X-Proxion-Passphrase header returns 200."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        resp = await _http(http_port,
            b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\n"
            b"X-Proxion-Passphrase: testpass\r\n\r\n"
        )
        assert b"200 OK" in resp, f"Expected 200, got: {resp[:200]!r}"

    @pytest.mark.asyncio
    async def test_backup_accepts_passphrase_via_query_string(self, tmp_path):
        """GET /backup?passphrase=... still works (backwards compat)."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        resp = await _http(http_port,
            b"GET /backup?passphrase=testpass HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"
        )
        assert b"200 OK" in resp, f"Expected 200, got: {resp[:200]!r}"

    @pytest.mark.asyncio
    async def test_backup_header_takes_precedence_over_query(self, tmp_path):
        """When both header and query are supplied, header passphrase is used."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        resp = await _http(http_port,
            b"GET /backup?passphrase=ignored HTTP/1.0\r\nHost: 127.0.0.1\r\n"
            b"X-Proxion-Passphrase: correct\r\n\r\n"
        )
        assert b"200 OK" in resp, f"Expected 200, got: {resp[:200]!r}"

    @pytest.mark.asyncio
    async def test_backup_empty_passphrase_returns_400(self, tmp_path):
        """GET /backup with no passphrase returns 400."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        resp = await _http(http_port,
            b"GET /backup HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"
        )
        assert b"400" in resp, f"Expected 400, got: {resp[:200]!r}"


# ── /restore ─────────────────────────────────────────────────────────────────


class TestRestorePassphrase:
    @pytest.mark.asyncio
    async def test_restore_empty_passphrase_returns_400(self, tmp_path):
        """POST /restore with no passphrase returns 400."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        body = b'{"dummy":"backup"}'
        resp = await _http(http_port,
            b"POST /restore HTTP/1.0\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        assert b"400" in resp, f"Expected 400, got: {resp[:200]!r}"

    @pytest.mark.asyncio
    async def test_restore_accepts_passphrase_via_query_string(self, tmp_path):
        """POST /restore?passphrase=... does not reject on missing passphrase."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        pp = b"testpass"
        blob = gw.agent.export_backup(pp)
        resp = await _http(http_port,
            b"POST /restore?passphrase=testpass HTTP/1.0\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(blob)).encode() + b"\r\n\r\n" + blob
        )
        assert b'"passphrase required"' not in resp, (
            f"Passphrase not read from query string: {resp[:300]!r}"
        )

    @pytest.mark.asyncio
    async def test_restore_accepts_passphrase_via_header(self, tmp_path):
        """POST /restore with X-Proxion-Passphrase header does not reject passphrase."""
        gw, http_port, ready = _start_gateway(tmp_path)
        assert ready.wait(timeout=5), "gateway failed to start"
        await asyncio.sleep(0.1)

        pp = b"testpass"
        blob = gw.agent.export_backup(pp)
        resp = await _http(http_port,
            b"POST /restore HTTP/1.0\r\nHost: 127.0.0.1\r\n"
            b"X-Proxion-Passphrase: testpass\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(blob)).encode() + b"\r\n\r\n" + blob
        )
        assert b'"passphrase required"' not in resp, (
            f"Passphrase not extracted from header: {resp[:300]!r}"
        )
