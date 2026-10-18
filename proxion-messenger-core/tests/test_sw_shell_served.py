"""Every file the service worker precaches must be served by the gateway.

The worker's install step caches web/sw.js SHELL. clientid.jsonld is in that
list, but the static server had no content type for .jsonld and answered 404,
so off localhost (where the worker actually caches) the install failed and the
app never got an offline shell or push over the tunnel.
"""
import re
import socket
from pathlib import Path

import pytest

pytest.importorskip("websockets")

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState

from gwharness import free_port, start_gateway

WEB = Path(__file__).resolve().parents[2] / "web"


def _shell_entries():
    src = (WEB / "sw.js").read_text(encoding="utf-8")
    block = re.search(r"const SHELL = \[(.*?)\];", src, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def _get(port, path, host):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {k.strip().lower(): v.strip() for k, v in
               (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
    return status, headers, body


@pytest.fixture
def gw_port(tmp_path):
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=cfg, read_state=ReadState())
    start_gateway(gw, ws_port, http_port, web_dir=str(WEB))
    return http_port


def test_clientid_jsonld_served_as_json_ld_off_localhost(gw_port):
    status, headers, body = _get(gw_port, "/clientid.jsonld", "proxion.example.com")
    assert status == 200
    assert headers["content-type"].startswith("application/ld+json")
    assert b'"client_id"' in body


def test_every_sw_shell_entry_is_served(gw_port):
    entries = _shell_entries()
    assert "clientid.jsonld" in entries and len(entries) > 20
    missing = []
    for entry in entries:
        path = "/" if entry == "./" else "/" + entry
        status, _, _ = _get(gw_port, path, "proxion.example.com")
        if status != 200:
            missing.append(f"{entry} -> {status}")
    assert not missing, missing
