"""The desktop app's sidecar contract, checked against the real run_gateway.py.

main.rs starts the gateway with a fixed environment and the webview (served from
the Tauri asset origin, not the gateway) dials it at fixed loopback URLs. These
tests launch run_gateway.py with exactly the environment main.rs passes (only
ports, data dir and home swapped for temp ones) and check the webview can
actually talk to it.
"""
from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

websockets = pytest.importorskip("websockets")

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "proxion-messenger-core" / "src"
MAIN_RS = REPO / "tauri-app" / "src-tauri" / "src" / "main.rs"
MAIN_JS = REPO / "web" / "main.js"
TAURI_ORIGIN = "https://tauri.localhost"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tauri_sidecar_env() -> dict:
    """The literal env.insert("KEY".into(), "VALUE".into()) pairs in main.rs."""
    src = MAIN_RS.read_text(encoding="utf-8")
    pairs = re.findall(r'env\.insert\(\s*"(\w+)"\.into\(\),\s*"([^"]*)"\.into\(\)\s*\)', src)
    assert pairs, "no sidecar env found in main.rs; regex likely stale"
    return dict(pairs)


class SidecarProcess:
    """run_gateway.py started the way the desktop app starts the sidecar."""

    def __init__(self, tmp: Path, *, http_port: int | None = None,
                 ws_port: int | None = None, extra_env: dict | None = None,
                 stdin=None):
        self.http_port = http_port or _free_port()
        self.ws_port = ws_port or _free_port()
        home = tmp / "home"
        home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(tauri_sidecar_env())
        env.update(
            PROXION_HTTP_PORT=str(self.http_port),
            PROXION_WS_PORT=str(self.ws_port),
            PROXION_DATA_DIR=str(tmp / "data"),
            HOME=str(home), USERPROFILE=str(home),
            # Skip the UPnP probe; the WS URL the page would be told is unchanged.
            PROXION_PUBLIC_URL=f"ws://127.0.0.1:{self.ws_port}",
            PROXION_CSS_URL="", PROXION_CSS_EMAIL="", PROXION_CSS_PASSWORD="",
            PROXION_SSL_CERT="", PROXION_SSL_KEY="",
            PROXION_SKIP_LEGACY_MIGRATION="1",
            PROXION_WEB_DIR=str(REPO / "web"),
            PYTHONPATH=str(SRC),
            PYTHONIOENCODING="utf-8",
        )
        env.update(extra_env or {})
        self.lines: list[str] = []
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "run_gateway.py")],
            cwd=str(tmp), env=env,
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.append(line)

    def output(self) -> str:
        return "".join(self.lines)

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "PROXION_GATEWAY_READY" in self.output():
                return
            if self.proc.poll() is not None:
                raise RuntimeError("sidecar exited before READY:\n" + self.output())
            time.sleep(0.1)
        raise RuntimeError("sidecar never printed READY:\n" + self.output())

    def wait_exit(self, timeout: float) -> int | None:
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)


@pytest.fixture
def sidecars(tmp_path):
    started: list[SidecarProcess] = []

    def _start(name: str = "a", **kw) -> SidecarProcess:
        sc = SidecarProcess(tmp_path / name, **kw)
        started.append(sc)
        return sc

    yield _start
    for sc in started:
        sc.stop()


# ── U1: the webview and the sidecar agree on plain loopback ws/http ──────────

def test_webview_default_ws_url_matches_sidecar_env():
    env = tauri_sidecar_env()
    src = MAIN_JS.read_text(encoding="utf-8")
    m = re.search(r'\|\|\s*"(wss?)://127\.0\.0\.1:(\d+)"', src)
    assert m, "default gateway URL not found in web/main.js"
    scheme, port = m.group(1), m.group(2)
    assert port == env["PROXION_WS_PORT"]
    assert scheme == "ws"
    # The webview cannot accept the gateway's self-signed cert, so the sidecar
    # must not switch itself to TLS.
    assert env.get("PROXION_AUTO_TLS") == "0"
    assert env["PROXION_HOST"] == "127.0.0.1"


def test_tauri_csp_allows_loopback_http_and_ws():
    import json
    conf = json.loads((REPO / "tauri-app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    csp = conf["tauri"]["security"]["csp"]
    connect = next(p.split()[1:] for p in csp.split(";") if p.split()[:1] == ["connect-src"])
    assert "ws://127.0.0.1:*" in connect
    assert "http://127.0.0.1:*" in connect


def test_webview_can_reach_sidecar_over_plain_ws_and_http(sidecars):
    sc = sidecars()
    sc.wait_ready()

    async def _handshake():
        async with websockets.connect(
            f"ws://127.0.0.1:{sc.ws_port}", origin=TAURI_ORIGIN, open_timeout=10,
        ) as ws:
            return ws.open if hasattr(ws, "open") else True

    assert asyncio.run(_handshake())
    req = urllib.request.Request(f"http://127.0.0.1:{sc.http_port}/health",
                                 headers={"Origin": TAURI_ORIGIN})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert r.headers.get("Access-Control-Allow-Origin") == TAURI_ORIGIN
