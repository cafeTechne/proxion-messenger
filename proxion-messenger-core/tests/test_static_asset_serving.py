"""Static web assets: compressed, revalidated with an ETag, and on a kept-alive connection.

Every asset used to go out uncompressed with Cache-Control: no-store on a fresh
TCP connection per file, so each load of the ~110-file shell moved ~3.5 MB and
a reload moved it all again. Assets other than index.html are now no-cache with
an ETag (still revalidated on every use, so a service-worker update sees new
files at once), gzip-encoded when the client accepts it, and served on a
keep-alive connection. index.html stays no-store: it carries per-request
injections.
"""
import gzip
import http.client
import os

import pytest

pytest.importorskip("websockets")
from gwharness import free_port, start_gateway

from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

_JS = ("// asset\n" + "export const x = 'hello world';\n" * 200)


@pytest.fixture
def served(tmp_path, monkeypatch):
    for k in ("PROXION_CSS_URL", "PROXION_CSS_EMAIL", "PROXION_CSS_PASSWORD"):
        monkeypatch.setenv(k, "")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text(
        "<html><head><title>t</title></head><body>" + "<p>hi</p>" * 300 + "</body></html>")
    (web / "main.js").write_bytes(_JS.encode())
    (web / "sw.js").write_text("// sw\n" + "self.x = 1;\n" * 200)
    ws_port, http_port = free_port(), free_port()
    gw = ProxionGateway(AgentState.generate(), {}, {}, GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=str(tmp_path / "g.db")), ReadState())
    start_gateway(gw, ws_port, http_port, web_dir=str(web))
    return web, http_port


def _get(conn, path, **headers):
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    return r, r.read()


def test_js_is_gzipped_and_revalidated_not_no_store(served):
    _, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    r, body = _get(c, "/main.js", **{"Accept-Encoding": "gzip"})
    assert r.status == 200
    assert r.getheader("Content-Encoding") == "gzip"
    assert gzip.decompress(body).decode() == _JS
    assert len(body) < len(_JS) // 4
    assert r.getheader("Cache-Control") == "no-cache"
    assert r.getheader("ETag")
    c.close()


def test_plain_client_gets_identity_body(served):
    _, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    r, body = _get(c, "/main.js")
    assert r.getheader("Content-Encoding") is None
    assert body.decode() == _JS
    c.close()


def test_etag_revalidation_returns_304_and_tracks_edits(served):
    web, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    r, _ = _get(c, "/main.js", **{"Accept-Encoding": "gzip"})
    etag = r.getheader("ETag")
    r2, body2 = _get(c, "/main.js", **{"Accept-Encoding": "gzip", "If-None-Match": etag})
    assert r2.status == 304 and body2 == b""
    # A changed file must never be answered with 304 (service-worker updates).
    (web / "main.js").write_bytes((_JS + "// v2\n").encode())
    st = os.stat(web / "main.js")
    os.utime(web / "main.js", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    r3, body3 = _get(c, "/main.js", **{"If-None-Match": etag})
    assert r3.status == 200 and body3.decode().endswith("// v2\n")
    assert r3.getheader("ETag") != etag
    c.close()


def test_index_and_sw_stay_revalidated(served):
    _, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    r, body = _get(c, "/", **{"Accept-Encoding": "gzip"})
    assert r.status == 200
    assert r.getheader("Cache-Control") == "no-store"
    assert r.getheader("ETag") is None
    assert b"<title>t</title>" in gzip.decompress(body)
    r2, _ = _get(c, "/sw.js")
    assert r2.getheader("Cache-Control") == "no-cache"
    c.close()


def test_static_requests_share_one_connection(served):
    _, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    _get(c, "/main.js")
    sock = c.sock
    assert sock is not None, "server closed the connection after a static GET"
    for path in ("/sw.js", "/", "/main.js"):
        r, _ = _get(c, path)
        assert r.status == 200
        assert c.sock is sock
    c.close()


def test_connection_close_is_honored(served):
    _, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    r, _ = _get(c, "/main.js", Connection="close")
    assert r.getheader("Connection", "").lower() == "close"
    assert c.sock is None
    c.close()
