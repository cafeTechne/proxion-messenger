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


def _run(coro):
    """Run *coro* on a private loop, leaving the thread's current loop alone
    (asyncio.run clears it, which breaks later tests that call get_event_loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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
            # Held open like the desktop app does; closing it means "app gone".
            stdin=stdin if stdin is not None else subprocess.PIPE,
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
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except OSError:
                pass


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

    assert _run(_handshake())
    req = urllib.request.Request(f"http://127.0.0.1:{sc.http_port}/health",
                                 headers={"Origin": TAURI_ORIGIN})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert r.headers.get("Access-Control-Allow-Origin") == TAURI_ORIGIN


# ── U4: a taken port fails loudly instead of "READY" then exit 0 ─────────────

def _hold_port(port: int) -> socket.socket:
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


def _log_text(tmp: Path) -> str:
    logs = tmp / "data" / "logs"
    return "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in logs.glob("*") if p.is_file()) if logs.exists() else ""


@pytest.mark.parametrize("which", ["http", "ws"])
def test_busy_port_exits_nonzero_without_ready(sidecars, tmp_path, which):
    port = _free_port()
    holder = _hold_port(port)
    try:
        kw = {"http_port": port} if which == "http" else {"ws_port": port}
        sc = sidecars(which, extra_env={"PROXION_BIND_WAIT": "0.5"}, **kw)
        code = sc.wait_exit(60)
        out = sc.output()
        assert code is not None, "sidecar kept running with its port taken:\n" + out
        assert code != 0, out
        assert "PROXION_GATEWAY_READY" not in out
        assert f"PROXION_GATEWAY_ERROR port_in_use {port}" in out
        assert f"127.0.0.1:{port}" in _log_text(tmp_path / which)
    finally:
        holder.close()


def test_bind_wait_rides_out_a_previous_gateway_shutting_down(sidecars):
    port = _free_port()
    holder = _hold_port(port)
    sc = sidecars(http_port=port, extra_env={"PROXION_BIND_WAIT": "20"})
    time.sleep(2.0)
    assert "PROXION_GATEWAY_READY" not in sc.output()
    holder.close()
    sc.wait_ready()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
        assert r.status == 200


def test_ready_means_both_ports_accept(sidecars):
    sc = sidecars()
    sc.wait_ready()
    for port in (sc.http_port, sc.ws_port):
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass


# ── U3: the sidecar never outlives the desktop app ───────────────────────────

def test_sidecar_env_asks_for_stdin_shutdown_and_bind_wait():
    env = tauri_sidecar_env()
    assert env.get("PROXION_SHUTDOWN_ON_STDIN_EOF") == "1"
    assert float(env.get("PROXION_BIND_WAIT", "0")) > 0


def test_app_exit_closing_stdin_stops_the_sidecar(sidecars, tmp_path):
    sc = sidecars()
    sc.wait_ready()
    sc.proc.stdin.close()      # what the OS does when the desktop app exits
    code = sc.wait_exit(30)
    assert code == 0, sc.output()
    assert "stdin" in _log_text(tmp_path / "a")


def test_shutdown_line_stops_the_sidecar(sidecars):
    sc = sidecars()
    sc.wait_ready()
    sc.proc.stdin.write("shutdown\n")
    sc.proc.stdin.flush()
    assert sc.wait_exit(30) == 0, sc.output()


def test_without_the_flag_a_closed_stdin_is_ignored(sidecars):
    # Running the gateway by hand (stdin at /dev/null, a service, nohup) must
    # not make it exit.
    sc = sidecars(extra_env={"PROXION_SHUTDOWN_ON_STDIN_EOF": ""}, stdin=subprocess.DEVNULL)
    sc.wait_ready()
    assert sc.wait_exit(3) is None


def test_gateway_exit_stops_the_tunnel(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    class FakeTunnel:
        stopped = False

        async def stop(self):
            FakeTunnel.stopped = True

    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", port=_free_port(),
                                             db_path=str(tmp_path / "gw.db")),
                        read_state=ReadState())
    gw._tunnel = FakeTunnel()
    _run(gw._stop_tunnel_for_exit())
    assert FakeTunnel.stopped and gw._tunnel is None
    src = (SRC / "proxion_messenger_core" / "gateway.py").read_text(encoding="utf-8")
    run_body = src[src.index("    async def run(self):"):]
    assert "await self._stop_tunnel_for_exit()" in run_body


def test_tunnel_backstop_kills_cloudflared_at_exit():
    from proxion_messenger_core.tunnel import TunnelManager

    async def spawn(path, port):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys,time; print('https://fake-x.trycloudflare.com', flush=True); time.sleep(120)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    async def scenario():
        mgr = TunnelManager(cloudflared_path="cloudflared", spawn=spawn)
        try:
            st = await mgr.start(8080, timeout=20)
            assert st["state"] == "running", st
            assert mgr._atexit_registered
            proc = mgr._proc
            mgr._kill_at_exit()          # what atexit runs if stop() was skipped
            await asyncio.wait_for(proc.wait(), 10)
            assert proc.returncode is not None
        finally:
            await mgr.stop()

    _run(scenario())


def test_desktop_shell_stops_the_gateway_on_every_exit_path():
    src = MAIN_RS.read_text(encoding="utf-8")
    body = src[:src.index("#[cfg(test)]")]
    assert "RunEvent::Exit" in body
    assert r'child.write(b"shutdown\n")' in body
    # The only hard kill is the timeout fallback inside shutdown_gateway.
    assert body.count("child.kill()") == 1
    fn = body[body.index("fn shutdown_gateway"):]
    assert "child.kill()" in fn[:fn.index("\n}\n")]
    for path in ("fn quit_app", '"quit" =>', "async fn stop_gateway"):
        seg = body[body.index(path):]
        assert "shutdown_gateway(" in seg[:400], path


# ── Direct runs keep every file in the data dir ───────────────────────────────

def test_direct_run_keeps_tls_and_stash_in_data_dir(sidecars, tmp_path):
    """A plain `python run_gateway.py` (auto-TLS on) writes its cert, key and
    invite stash under PROXION_DATA_DIR, not the shared ~/.proxion, and the
    banner advertises the https URL it actually serves."""
    sc = sidecars("direct", extra_env={"PROXION_AUTO_TLS": "1",
                                       "PROXION_PUBLIC_URL": "",
                                       "PROXION_HOST": "127.0.0.1"})
    sc.wait_ready()
    data = tmp_path / "direct" / "data"
    home = tmp_path / "direct" / "home"
    assert (data / "tls" / "cert.pem").is_file()
    assert (data / "tls" / "key.pem").is_file()
    assert (data / "stash").is_dir()
    assert not (home / ".proxion" / "tls").exists()
    assert not (home / ".proxion" / "stash").exists()
    deadline = time.monotonic() + 5
    while "Web UI" not in sc.output() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert f"Web UI  : https://127.0.0.1:{sc.http_port}" in sc.output()


def test_frozen_default_data_dir_is_not_the_pyinstaller_temp_dir(tmp_path):
    """A onefile executable unpacks into _MEIPASS, which is deleted on exit.
    The default data dir must survive that."""
    meipass = tmp_path / "_MEI12345"
    home = tmp_path / "home"
    meipass.mkdir()
    home.mkdir()
    code = (
        "import sys; sys.frozen = True; sys._MEIPASS = sys.argv[1];"
        "sys.path.insert(0, sys.argv[2]); import run_gateway;"
        "print(run_gateway._data_dir())"
    )
    env = {k: v for k, v in os.environ.items() if k != "PROXION_DATA_DIR"}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(SRC))
    out = subprocess.run([sys.executable, "-c", code, str(meipass), str(REPO)],
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    data_dir = Path(out.stdout.strip().splitlines()[-1])
    assert meipass not in data_dir.parents and data_dir != meipass
    assert data_dir == home / ".proxion" / "data"


def test_mcp_client_connects_to_a_direct_run_gateway(sidecars, tmp_path):
    """The MCP server's GatewayClient reaches `python run_gateway.py` over
    wss:// by trusting the gateway's own self-signed cert, with verification on."""
    from proxion_messenger_core.gateway_client import GatewayClient
    from proxion_messenger_core.persist import AgentState
    sc = sidecars("mcp", extra_env={"PROXION_AUTO_TLS": "1",
                                    "PROXION_PUBLIC_URL": "",
                                    "PROXION_HOST": "127.0.0.1"})
    sc.wait_ready()
    cert = tmp_path / "mcp" / "data" / "tls" / "cert.pem"

    async def _go():
        c = GatewayClient(f"wss://127.0.0.1:{sc.ws_port}", AgentState.generate(),
                          display_name="Agent", cafile=str(cert))
        await c.connect()
        try:
            return await c.whoami()
        finally:
            await c.close()

    who = _run(_go())
    assert who["did"].startswith("did:key:")


def test_mcp_server_finds_the_gateway_cert(tmp_path, monkeypatch):
    from proxion_messenger_core import mcp_server
    monkeypatch.delenv("PROXION_GATEWAY_CAFILE", raising=False)
    monkeypatch.setenv("PROXION_DATA_DIR", str(tmp_path))
    assert mcp_server._gateway_cafile() is None
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "cert.pem").write_text("x")
    assert mcp_server._gateway_cafile() == str(tmp_path / "tls" / "cert.pem")
    monkeypatch.setenv("PROXION_GATEWAY_CAFILE", "/elsewhere.pem")
    assert mcp_server._gateway_cafile() == "/elsewhere.pem"
