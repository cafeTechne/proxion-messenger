"""R97: app-driven cloudflared tunnel manager + gateway auth-force invariant."""
from __future__ import annotations

import asyncio

import pytest

from proxion_messenger_core.tunnel import (
    TunnelManager, extract_tunnel_url, find_cloudflared,
)


# ── pure URL parsing ────────────────────────────────────────────────────────
def test_extract_tunnel_url_matches_banner():
    line = "2026-08-07 INF |  https://calm-forest-1234.trycloudflare.com  |"
    assert extract_tunnel_url(line) == "https://calm-forest-1234.trycloudflare.com"


def test_extract_tunnel_url_ignores_noise():
    assert extract_tunnel_url("INF Starting tunnel...") is None
    assert extract_tunnel_url("") is None
    assert extract_tunnel_url("https://example.com/not-a-tunnel") is None


# ── state machine with an injected fake process ─────────────────────────────
class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""   # EOF


class _FakeProc:
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        self.returncode = self.returncode or 0
        return self.returncode


def _mgr(lines):
    async def spawn(path, port):
        return _FakeProc(lines)
    return TunnelManager(cloudflared_path="/usr/bin/cloudflared", spawn=spawn)


@pytest.mark.asyncio
async def test_start_resolves_url_and_reports_running():
    mgr = _mgr([b"INF booting\n", b"INF https://abc-def-1.trycloudflare.com\n"])
    st = await mgr.start(8080)
    assert st["state"] == "running"
    assert st["url"] == "https://abc-def-1.trycloudflare.com"
    await mgr.stop()
    assert mgr.status()["state"] == "stopped"
    assert mgr.status()["url"] is None


@pytest.mark.asyncio
async def test_start_fails_when_process_exits_without_url():
    mgr = _mgr([b"INF booting\n"])   # then EOF, no URL
    st = await mgr.start(8080)
    assert st["state"] == "failed"
    assert st["error"]


@pytest.mark.asyncio
async def test_start_times_out():
    class _Hang:
        async def readline(self):
            await asyncio.sleep(10)
    class _HangProc:
        stdout = _Hang(); returncode = None
        def terminate(self): self.returncode = 0
        def kill(self): self.returncode = -9
        async def wait(self): return 0
    async def spawn(path, port):
        return _HangProc()
    mgr = TunnelManager(cloudflared_path="/usr/bin/cloudflared", spawn=spawn)
    st = await mgr.start(8080, timeout=0.05)
    assert st["state"] == "failed"
    assert "timed out" in st["error"]


def test_absent_when_binary_missing():
    mgr = TunnelManager(cloudflared_path=None, spawn=None)
    # If the host happens to have cloudflared, find_cloudflared() would populate
    # the path; this test only asserts the absent branch when there is no binary.
    if find_cloudflared() is None:
        assert mgr.status()["state"] == "absent"


# ── bundled cloudflared preference (R97/R98 turnkey) ────────────────────────
def _cf_name():
    import sys as _sys
    return "cloudflared.exe" if _sys.platform == "win32" else "cloudflared"


def test_find_cloudflared_prefers_meipass(tmp_path, monkeypatch):
    """The immutable bundled copy in sys._MEIPASS is used and nothing else is
    consulted, so a planted binary next to the executable cannot shadow it."""
    import sys
    import proxion_messenger_core.tunnel as tunnelmod
    name = _cf_name()

    meipass = tmp_path / "meipass"
    meipass.mkdir()
    bundled = meipass / name
    bundled.write_bytes(b"#!bundled cloudflared\n")

    # A planted binary sitting next to the executable must be ignored.
    exe_dir = tmp_path / "install"
    exe_dir.mkdir()
    (exe_dir / name).write_bytes(b"#!planted cloudflared\n")

    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "proxion-gateway"))
    monkeypatch.setattr(tunnelmod, "_pinned_sha256", lambda: None)

    assert tunnelmod.find_cloudflared() == str(bundled)


def test_find_cloudflared_rejects_unverified_sibling(tmp_path, monkeypatch):
    """A cloudflared next to the executable whose hash does not match the pin is
    refused (planting vector), and with nothing on PATH the result is None."""
    import sys
    import proxion_messenger_core.tunnel as tunnelmod
    name = _cf_name()

    exe_dir = tmp_path / "install"
    exe_dir.mkdir()
    (exe_dir / name).write_bytes(b"#!planted cloudflared\n")

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "proxion-gateway"))
    monkeypatch.setattr(tunnelmod, "_pinned_sha256", lambda: "deadbeef" * 8)
    monkeypatch.setattr(tunnelmod.shutil, "which", lambda _n: None)

    assert tunnelmod.find_cloudflared() is None


def test_find_cloudflared_accepts_verified_sibling(tmp_path, monkeypatch):
    """A cloudflared next to the executable is used when it matches the pin."""
    import sys
    import proxion_messenger_core.tunnel as tunnelmod
    name = _cf_name()

    exe_dir = tmp_path / "install"
    exe_dir.mkdir()
    cand = exe_dir / name
    payload = b"#!genuine cloudflared\n"
    cand.write_bytes(payload)
    good = tunnelmod._sha256_file(str(cand))

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "proxion-gateway"))
    monkeypatch.setattr(tunnelmod, "_pinned_sha256", lambda: good)
    monkeypatch.setattr(tunnelmod.shutil, "which", lambda _n: None)

    assert tunnelmod.find_cloudflared() == str(cand)


def test_cloudflared_asset_name_mapping():
    """Build-side asset mapping for each release triple (pure)."""
    import importlib.util
    from pathlib import Path
    bs_path = Path(__file__).resolve().parents[2] / "build_sidecar.py"
    spec = importlib.util.spec_from_file_location("build_sidecar", bs_path)
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    assert bs.cloudflared_asset_name("x86_64-pc-windows-msvc") == "cloudflared-windows-amd64.exe"
    assert bs.cloudflared_asset_name("aarch64-apple-darwin") == "cloudflared-darwin-arm64.tgz"
    assert bs.cloudflared_asset_name("x86_64-unknown-linux-gnu") == "cloudflared-linux-amd64"
    assert bs.cloudflared_asset_name("nonexistent-triple") is None


# ── gateway auth-force invariant ────────────────────────────────────────────
def test_force_auth_makes_auth_enforced_true(monkeypatch):
    from proxion_messenger_core.gateway import ProxionGateway
    gw = ProxionGateway.__new__(ProxionGateway)  # no full init needed
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)

    class _Cfg:
        host = "127.0.0.1"
    gw.config = _Cfg()
    gw._force_auth = False
    assert gw._auth_enforced() is False       # loopback default skips auth
    gw._force_auth = True
    assert gw._auth_enforced() is True         # a live tunnel forces it on


def test_tunnel_control_is_owner_only():
    """R98: opening/closing the public tunnel exposes/retracts the gateway, so a
    party reaching a live tunnel (who can register a self-claimed did:key) must
    not be able to control it. tunnel_status is owner-gated too (F7); the handler
    enforces owner/loopback for all three."""
    from proxion_messenger_core.security_policy import _OWNER_ONLY_COMMANDS
    assert "start_tunnel" in _OWNER_ONLY_COMMANDS
    assert "stop_tunnel" in _OWNER_ONLY_COMMANDS


# ── F7: handler-level owner gate for all three tunnel commands ──────────────
def _owner_gw(*, force_auth: bool, owner_registered: bool, ip: str = "127.0.0.1"):
    """A bare gateway wired just enough for _is_tunnel_owner and the handlers.

    owner_registered=True registers the caller WS under the gateway's own account
    DID (the owner); False registers a self-claimed non-owner DID (a peer)."""
    import json as _json
    from proxion_messenger_core.gateway import ProxionGateway
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did

    gw = ProxionGateway.__new__(ProxionGateway)
    gw.agent = AgentState.generate()
    owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)

    class _Cfg:
        host = "127.0.0.1"
        public_url = None
        http_port = 8080
    gw.config = _Cfg()
    gw._force_auth = force_auth
    gw._tunnel = None
    gw._tunnel_prev_public_url = None
    gw._tunnel_prev_force_auth = False

    class _WS:
        def __init__(self):
            self.sent = []
        async def send(self, m):
            self.sent.append(_json.loads(m))
    ws = _WS()
    caller_did = owner_did if owner_registered else (
        "did:key:z6MkpTHR8VNsBxYAAWHut2Geadd9jSwuBV8xRVjqxHt8vDgp")
    gw._client_webids = {ws: caller_did}
    gw._session_meta = {ws: {"ip_addr": ip}}
    return gw, ws, owner_did


@pytest.mark.asyncio
async def test_tunnel_status_refused_for_non_owner_when_auth_enforced():
    """A registered non-owner (e.g. a peer over the tunnel, where auth is forced
    on) may not read the tunnel state/URL."""
    gw, ws, _ = _owner_gw(force_auth=True, owner_registered=False)
    await gw._handle_tunnel_status(ws, {})
    assert ws.sent and ws.sent[-1]["type"] == "error"
    assert ws.sent[-1]["message"] == "gateway_owner_only"


@pytest.mark.asyncio
async def test_stop_tunnel_refused_for_non_owner_when_auth_enforced():
    gw, ws, _ = _owner_gw(force_auth=True, owner_registered=False)
    gw._tunnel = _mgr([b"x"])  # pretend one is up; must NOT be torn down
    await gw._handle_stop_tunnel(ws, {})
    assert ws.sent and ws.sent[-1]["type"] == "error"
    assert ws.sent[-1]["message"] == "gateway_owner_only"
    assert gw._tunnel is not None  # non-owner did not tear it down


@pytest.mark.asyncio
async def test_start_tunnel_refused_for_non_owner_when_auth_enforced():
    gw, ws, _ = _owner_gw(force_auth=True, owner_registered=False)
    await gw._handle_start_tunnel(ws, {})
    assert ws.sent and ws.sent[-1]["type"] == "error"
    assert ws.sent[-1]["message"] == "gateway_owner_only"


@pytest.mark.asyncio
async def test_tunnel_status_allowed_for_owner():
    gw, ws, _ = _owner_gw(force_auth=True, owner_registered=True)
    await gw._handle_tunnel_status(ws, {})
    assert ws.sent and ws.sent[-1]["type"] == "tunnel_status"


@pytest.mark.asyncio
async def test_tunnel_status_allowed_on_loopback_dev_non_owner_did(monkeypatch):
    """Loopback single-user dev: auth not enforced, the browser registers its own
    session DID (not the account DID), and it must still read tunnel state."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    gw, ws, _ = _owner_gw(force_auth=False, owner_registered=False, ip="127.0.0.1")
    await gw._handle_tunnel_status(ws, {})
    assert ws.sent and ws.sent[-1]["type"] == "tunnel_status"


@pytest.mark.asyncio
async def test_cold_stop_tunnel_does_not_wipe_configured_public_url():
    """R98: stop_tunnel with no active tunnel must not clobber a configured
    PROXION_PUBLIC_URL (or force_auth) with the init-default None/False."""
    gw, ws, _ = _owner_gw(force_auth=True, owner_registered=True)
    gw.config.public_url = "https://my.configured.example"
    gw._tunnel = None
    gw._tunnel_prev_public_url = None          # never started a tunnel
    gw._tunnel_prev_force_auth = False

    await gw._handle_stop_tunnel(ws, {})
    assert gw.config.public_url == "https://my.configured.example"  # untouched
    assert gw._force_auth is True                                    # untouched
    assert any(m.get("state") == "stopped" for m in ws.sent)


# ── F3: concurrent-start race + failed-start invariant ──────────────────────
@pytest.mark.asyncio
async def test_concurrent_start_rejects_second_no_second_cloudflared(monkeypatch):
    """A second start_tunnel arriving while the first is still 'starting' must be
    rejected: no second cloudflared, no state corruption. Simulated with a slow
    TunnelManager.start held open on an event."""
    import proxion_messenger_core.tunnel as tunnelmod
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    gw, ws1, owner_did = _owner_gw(force_auth=False, owner_registered=True)
    ws2 = type(ws1)()
    gw._client_webids[ws2] = owner_did
    gw._session_meta[ws2] = {"ip_addr": "127.0.0.1"}

    starts = {"count": 0}
    release = asyncio.Event()

    class _SlowMgr:
        def __init__(self):
            self._state = "stopped"; self._url = None; self._error = None
        def status(self):
            return {"state": self._state, "url": self._url, "error": self._error}
        async def start(self, port, timeout=30.0):
            starts["count"] += 1
            self._state = "starting"
            await release.wait()
            self._state = "running"
            self._url = "https://slow-1.trycloudflare.com"
            return self.status()
        async def stop(self):
            self._state = "stopped"; self._url = None

    monkeypatch.setattr(tunnelmod, "TunnelManager", _SlowMgr)
    monkeypatch.setattr(tunnelmod, "find_cloudflared", lambda: "/usr/bin/cloudflared")
    monkeypatch.setattr(gw, "_ws_public_url", lambda: "wss://slow-1.trycloudflare.com")
    monkeypatch.setattr(gw, "_proxion_address", lambda: "addr")

    task_a = asyncio.create_task(gw._handle_start_tunnel(ws1, {}))
    # Let A reach the 'starting' state (owns _tunnel, suspended inside start()).
    for _ in range(100):
        await asyncio.sleep(0)
        if gw._tunnel is not None and gw._tunnel.status()["state"] == "starting":
            break
    assert gw._tunnel is not None and gw._tunnel.status()["state"] == "starting"

    # B starts concurrently — rejected by the 'starting' re-entrancy check.
    await gw._handle_start_tunnel(ws2, {})
    assert starts["count"] == 1                       # no second cloudflared
    assert ws2.sent[-1]["type"] == "tunnel_status"
    assert ws2.sent[-1]["state"] == "starting"

    release.set()
    await task_a
    assert gw._tunnel.status()["state"] == "running"
    assert gw._force_auth is True                     # auth stays forced on
    assert starts["count"] == 1                       # still only one
    assert ws1.sent[-1]["type"] == "tunnel_ready"


@pytest.mark.asyncio
async def test_failed_start_never_leaves_tunnel_active_with_auth_off(monkeypatch):
    """A failed start rolls _force_auth back and clears _tunnel, so there is never
    a live tunnel while auth is off (the F3 exposed-without-auth class)."""
    import proxion_messenger_core.tunnel as tunnelmod
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    gw, ws, _ = _owner_gw(force_auth=False, owner_registered=True)

    class _FailMgr:
        def __init__(self):
            self._state = "stopped"; self._url = None; self._error = None
            self.stopped = False
        def status(self):
            return {"state": self._state, "url": self._url, "error": self._error}
        async def start(self, port, timeout=30.0):
            self._state = "failed"; self._error = "boom"
            return self.status()
        async def stop(self):
            self.stopped = True; self._state = "stopped"

    monkeypatch.setattr(tunnelmod, "TunnelManager", _FailMgr)
    monkeypatch.setattr(tunnelmod, "find_cloudflared", lambda: "/usr/bin/cloudflared")

    await gw._handle_start_tunnel(ws, {})
    assert gw._tunnel is None                          # cleared
    assert gw._force_auth is False                     # restored to prior
    # The invariant: never a live tunnel while auth is off.
    assert not (gw._tunnel is not None and gw._force_auth is False)
    assert ws.sent[-1]["type"] == "tunnel_status"
    assert ws.sent[-1]["state"] == "failed"


@pytest.mark.asyncio
async def test_solid_webhook_is_rate_limited(tmp_path):
    """F13: /solid-webhook/{token} caps per-IP requests like /webhook/ does."""
    import socket as _sock
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    from gwharness import start_gateway as _serve_gw

    def _free():
        with _sock.socket() as s:
            s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

    ws_port, http_port = _free(), _free()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / "gw.db"))
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={},
                        room_memberships={}, config=cfg, read_state=ReadState())
    handle = _serve_gw(gw, ws_port, http_port)
    assert handle.ready.wait(timeout=5)

    async def _post():
        reader, writer = await asyncio.open_connection("127.0.0.1", handle.http_port)
        writer.write(b"POST /solid-webhook/sometoken HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                     b"Content-Length: 0\r\n\r\n")
        await writer.drain()
        resp = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        writer.close()
        return resp

    saw_429 = False
    for _ in range(70):                     # webhook limit is 60/min
        resp = await _post()
        if b"429" in resp:
            saw_429 = True
            break
    assert saw_429, "solid-webhook was not rate limited after >60 requests"
