"""A reverse-proxied request is not the local owner.

Behind Caddy or nginx every request reaches the gateway from 127.0.0.1 with no
Origin, so the loopback fallback treated internet callers as the owner:
GET /backup?mode=recipient_key handed the identity to an attacker-chosen key, and
/restore, /export, /import, /message-edits shared the same gate. A loopback request
that carries proxy headers or a non-local Host is now remote, and a gateway with a
public hostname configured needs the provisioned token for these endpoints.
"""
import asyncio
import types

import pytest

pytest.importorskip("websockets")

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from gwharness import start_gateway as _serve_gw


from gwharness import free_port as _free_port


def _start_gateway(tmp_path, public_url=None):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=public_url or f"ws://127.0.0.1:{ws_port}",
        http_public_url=None,
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={},
                        config=cfg, read_state=ReadState())
    handle = _serve_gw(gw, ws_port, http_port)
    assert handle.ready.wait(timeout=5)
    return gw, handle.http_port


async def _http(http_port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(request)
    await writer.drain()
    resp = await asyncio.wait_for(reader.read(1 << 20), timeout=5.0)
    writer.close()
    return resp


def _status(resp: bytes) -> bytes:
    return resp.split(b"\r\n", 1)[0]


_PUB = "11" * 32
_PROXIED = b"Host: chat.example.com\r\nX-Forwarded-For: 203.0.113.9\r\n"

_SENSITIVE = [
    b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n",
    b"GET /backup HTTP/1.0\r\nX-Proxion-Passphrase: p\r\n",
    b"POST /restore HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: 0\r\n",
    b"GET /export HTTP/1.0\r\n",
    b"POST /import HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: 0\r\n",
    b"GET /message-edits?message_id=m1 HTTP/1.0\r\n",
    b"GET /contacts HTTP/1.0\r\n",
    b"GET /security-snapshot HTTP/1.0\r\n",
]


def _req(head: bytes, extra: bytes) -> bytes:
    return head + extra + b"\r\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("head", _SENSITIVE, ids=lambda h: h.split(b" ")[1][:20].decode())
async def test_forwarded_request_refused(tmp_path, head):
    _gw, http_port = _start_gateway(tmp_path)
    resp = await _http(http_port, _req(head, _PROXIED))
    assert b"403" in _status(resp), resp[:300]


@pytest.mark.asyncio
@pytest.mark.parametrize("hdr", [
    b"X-Forwarded-For: 203.0.113.9",
    b"X-Forwarded-Host: chat.example.com",
    b"X-Real-IP: 203.0.113.9",
    b"Forwarded: for=203.0.113.9",
    b"CF-Connecting-IP: 203.0.113.9",
])
async def test_each_proxy_header_marks_request_remote(tmp_path, hdr):
    _gw, http_port = _start_gateway(tmp_path)
    head = b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n"
    resp = await _http(http_port, _req(head, b"Host: 127.0.0.1\r\n" + hdr + b"\r\n"))
    assert b"403" in _status(resp), resp[:300]


@pytest.mark.asyncio
async def test_non_local_host_alone_marks_request_remote(tmp_path):
    _gw, http_port = _start_gateway(tmp_path)
    head = b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n"
    resp = await _http(http_port, _req(head, b"Host: chat.example.com\r\n"))
    assert b"403" in _status(resp), resp[:300]


@pytest.mark.asyncio
async def test_forged_local_origin_through_proxy_refused(tmp_path):
    _gw, http_port = _start_gateway(tmp_path)
    head = b"GET /message-edits?message_id=m1 HTTP/1.0\r\n"
    origin = b"Origin: http://127.0.0.1:" + str(http_port).encode() + b"\r\n"
    resp = await _http(http_port, _req(head, _PROXIED + origin))
    assert b"403" in _status(resp), resp[:300]


# ── plain loopback (local browser, desktop app) keeps working ─────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("host", [b"127.0.0.1", b"localhost", b"[::1]"])
async def test_plain_loopback_backup_still_allowed(tmp_path, host):
    _gw, http_port = _start_gateway(tmp_path)
    head = b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n"
    resp = await _http(http_port,
                       _req(head, b"Host: " + host + b":" + str(http_port).encode() + b"\r\n"))
    assert b"200" in _status(resp), resp[:300]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [b"/message-edits?message_id=m1", b"/export", b"/contacts"])
async def test_plain_loopback_reads_still_allowed(tmp_path, path):
    _gw, http_port = _start_gateway(tmp_path)
    resp = await _http(http_port, b"GET " + path + b" HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    assert b"200" in _status(resp), resp[:300]


@pytest.mark.asyncio
async def test_desktop_webview_origin_still_allowed(tmp_path):
    _gw, http_port = _start_gateway(tmp_path)
    resp = await _http(http_port,
                       b"GET /message-edits?message_id=m1 HTTP/1.0\r\nHost: 127.0.0.1:"
                       + str(http_port).encode() + b"\r\nOrigin: tauri://localhost\r\n\r\n")
    assert b"200" in _status(resp), resp[:300]


@pytest.mark.asyncio
async def test_proxied_request_with_token_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_ADMIN_API_TOKEN", "secret-admin-token")
    _gw, http_port = _start_gateway(tmp_path)
    head = b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n"
    resp = await _http(http_port,
                       _req(head, _PROXIED + b"Authorization: Bearer secret-admin-token\r\n"))
    assert b"200" in _status(resp), resp[:300]


# ── a public hostname means loopback can be a proxy that hides its headers ─────

@pytest.mark.asyncio
async def test_public_url_requires_token_even_from_plain_loopback(tmp_path, monkeypatch):
    monkeypatch.delenv("PROXION_UPNP_MAPPED", raising=False)
    _gw, http_port = _start_gateway(tmp_path, public_url="wss://chat.example.com")
    head = b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n"
    resp = await _http(http_port, _req(head, b"Host: 127.0.0.1\r\n"))
    assert b"403" in _status(resp), resp[:300]


@pytest.mark.asyncio
async def test_public_url_with_token_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("PROXION_UPNP_MAPPED", raising=False)
    monkeypatch.setenv("PROXION_API_TOKEN", "legacy-token")
    _gw, http_port = _start_gateway(tmp_path, public_url="wss://chat.example.com")
    head = b"GET /backup?mode=recipient_key&recipient_pubkey=" + _PUB.encode() + b" HTTP/1.0\r\n"
    resp = await _http(http_port,
                       _req(head, b"Host: 127.0.0.1\r\nAuthorization: Bearer legacy-token\r\n"))
    assert b"200" in _status(resp), resp[:300]


def _bare_gw(public_url=None, host="127.0.0.1"):
    gw = ProxionGateway.__new__(ProxionGateway)
    gw.config = GatewayConfig(host=host, port=1, http_port=2, public_url=public_url,
                              http_public_url=None)
    gw._tunnel = None
    return gw


def test_public_deployment_classification(monkeypatch):
    monkeypatch.delenv("PROXION_UPNP_MAPPED", raising=False)
    assert _bare_gw("wss://chat.example.com")._public_deployment()
    assert _bare_gw("wss://93.184.216.34:7474")._public_deployment()
    assert not _bare_gw(None)._public_deployment()
    assert not _bare_gw("ws://127.0.0.1:7474")._public_deployment()
    assert not _bare_gw("ws://192.168.1.5:7474")._public_deployment()
    monkeypatch.setenv("PROXION_UPNP_MAPPED", "1")
    assert not _bare_gw("wss://93.184.216.34:7474")._public_deployment()


# ── WebSocket side: a proxied socket is never the loopback owner ──────────────

def test_ws_proxied_socket_is_not_tunnel_owner(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw = _bare_gw()
    gw._client_webids = {}
    gw._session_meta = {}

    class _Sock:
        def __init__(self, headers):
            self.request = types.SimpleNamespace(headers=headers)

    def _ws(headers):
        ws = _Sock(headers)
        gw._session_meta[ws] = {"ip_addr": "127.0.0.1"}
        return ws

    local = _ws({"host": "127.0.0.1:7474"})
    proxied = _ws({"host": "chat.example.com", "x-forwarded-for": "203.0.113.9"})
    assert gw._is_tunnel_owner(local)
    assert not gw._is_tunnel_owner(proxied)


@pytest.mark.asyncio
async def test_message_edits_refused_over_tunnel_without_token(tmp_path):
    # Even a request that looks fully local is refused while the tunnel is live.
    gw, http_port = _start_gateway(tmp_path)
    gw._tunnel = types.SimpleNamespace(status=lambda: {"state": "running"})
    resp = await _http(http_port,
                       b"GET /message-edits?message_id=m1 HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    assert b"403" in _status(resp), resp[:300]
