"""Pod bridge prerequisites: the WebID-root check, SolidClient.request, the
no-redirect safe_get, and the owner-socket helper."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from proxion_messenger_core.podbridge import check_pod_root, pod_root_from_webid
from proxion_messenger_core.solid_client import SolidClient, SolidError


# ── WebID-derived root ───────────────────────────────────────────────────────

def test_root_from_css_webid():
    assert pod_root_from_webid("https://pod.example/alice/profile/card#me") == "https://pod.example/alice/"


def test_root_from_other_webid_falls_back_to_origin():
    assert pod_root_from_webid("https://alice.example/card#me") == "https://alice.example/"
    assert pod_root_from_webid("") is None
    assert pod_root_from_webid("ftp://x/y") is None


def test_root_check_accepts_matching_pod():
    assert check_pod_root("http://localhost:3000/a/profile/card#me", "http://localhost:3000/a/") == (True, "")
    assert check_pod_root("http://localhost:3000/a/profile/card#me", "http://localhost:3000/a") == (True, "")


def test_root_check_refuses_split_storage_and_missing_pod():
    # PodSpaces-style: the WebID lives on one host, storage on another.
    assert check_pod_root("https://id.example/alice/profile/card#me", "https://storage.example/alice/") == (False, "root_mismatch")
    assert check_pod_root("https://pod.example/alice/profile/card#me", "https://pod.example/bob/") == (False, "root_mismatch")
    assert check_pod_root(None, "https://pod.example/a/") == (False, "no_pod")
    assert check_pod_root("https://pod.example/a/profile/card#me", None) == (False, "no_pod")


# ── SolidClient.request ──────────────────────────────────────────────────────

class _Resolver:
    pod_base_url = "https://pod.example/alice/"

    def resolve(self, uri):
        return uri


def _client(handler, auth_headers=None):
    session = httpx.Client(transport=httpx.MockTransport(handler))
    return SolidClient(_Resolver(), session=session, auth_headers=auth_headers or {})


def test_request_returns_status_headers_body_and_filters_headers():
    def handler(req):
        assert req.method == "POST"
        assert req.content == b"{}"
        return httpx.Response(201, headers={
            "Location": "https://pod.example/alice/proxion/dm-inbox/x",
            "Set-Cookie": "secret=1", "ETag": '"e1"', "Link": "<.acl>; rel=\"acl\"",
        })
    status, headers, body = _client(handler).request(
        "POST", "https://pod.example/alice/proxion/dm-inbox/", b"{}", {"Content-Type": "application/json"})
    assert status == 201
    assert headers["location"].endswith("/x")
    assert headers["etag"] == '"e1"'
    assert "link" in headers
    assert "set-cookie" not in headers
    assert body == b""


def test_request_does_not_raise_on_404_and_never_follows_redirects():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.path.endswith("/moved"):
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        return httpx.Response(404)
    c = _client(handler)
    assert c.request("GET", "https://pod.example/alice/missing")[0] == 404
    assert c.request("GET", "https://pod.example/alice/moved")[0] == 302
    assert calls == ["https://pod.example/alice/missing", "https://pod.example/alice/moved"]


def test_request_caps_body():
    def handler(req):
        return httpx.Response(200, content=b"x" * 2000)
    with pytest.raises(SolidError):
        _client(handler).request("GET", "https://pod.example/alice/big", max_bytes=1000)


def test_request_rejects_bad_method_and_relative_url():
    c = _client(lambda req: httpx.Response(200))
    with pytest.raises(SolidError):
        c.request("TRACE", "https://pod.example/alice/")
    with pytest.raises(SolidError):
        c.request("GET", "stash://pod/x")


# ── safe_get(max_redirects=0) ────────────────────────────────────────────────

class _Redirector(BaseHTTPRequestHandler):
    def do_GET(self):   # noqa: N802
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@pytest.fixture
def redirect_server(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_safe_get_follows_by_default_and_refuses_with_max_redirects_zero(redirect_server):
    from proxion_messenger_core.network import NetworkError, safe_get
    assert safe_get(redirect_server + "/moved") == b"ok"
    with pytest.raises(NetworkError):
        safe_get(redirect_server + "/moved", max_redirects=0)
    assert safe_get(redirect_server + "/final", max_redirects=0) == b"ok"


@pytest.mark.asyncio
async def test_async_safe_get_refuses_redirect_with_zero(redirect_server):
    from proxion_messenger_core.network import NetworkError, async_safe_get
    with pytest.raises(NetworkError):
        await async_safe_get(redirect_server + "/moved", max_redirects=0)


# ── owner socket ─────────────────────────────────────────────────────────────

def _gw(auth_enforced, owner="did:key:zOwner", proxied=False, public=False):
    from proxion_messenger_core.gateway import ProxionGateway
    gw = SimpleNamespace(
        _client_webids={}, _session_meta={},
        _own_gateway_did=lambda: owner,
        _auth_enforced=lambda: auth_enforced,
        _ws_is_proxied=lambda ws: proxied,
        _public_deployment=lambda: public,
        _is_persisted_owner=lambda ws: False,
    )
    gw._owner_by_did_or_local = lambda ws: ProxionGateway._owner_by_did_or_local(gw, ws)
    return gw, ProxionGateway._is_pod_owner_socket


def test_owner_socket_requires_registration():
    gw, fn = _gw(False)
    ws = object()
    assert fn(gw, ws) is False   # loopback but not registered


def test_owner_socket_loopback_without_auth_and_never_when_proxied():
    gw, fn = _gw(False)
    ws = object()
    gw._client_webids[ws] = "did:key:zBrowser"
    gw._session_meta[ws] = {"ip_addr": "127.0.0.1"}
    assert fn(gw, ws) is True
    gw2, fn2 = _gw(False, proxied=True)
    gw2._client_webids[ws] = "did:key:zBrowser"
    gw2._session_meta[ws] = {"ip_addr": "127.0.0.1"}
    assert fn2(gw2, ws) is False
    gw3, fn3 = _gw(False)
    gw3._client_webids[ws] = "did:key:zBrowser"
    gw3._session_meta[ws] = {"ip_addr": "203.0.113.5"}
    assert fn3(gw3, ws) is False


def test_owner_socket_under_auth_needs_the_gateway_did():
    gw, fn = _gw(True)
    ws = object()
    gw._client_webids[ws] = "did:key:zBrowser"
    gw._session_meta[ws] = {"ip_addr": "127.0.0.1"}
    assert fn(gw, ws) is False
    gw._client_webids[ws] = "did:key:zOwner"
    assert fn(gw, ws) is True
