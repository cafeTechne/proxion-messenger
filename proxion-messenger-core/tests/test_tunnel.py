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
def test_find_cloudflared_prefers_bundled(tmp_path, monkeypatch):
    """A bundled binary (e.g. PyInstaller _MEIPASS) is used before PATH so a
    shipped install is turnkey without a separate cloudflared install."""
    import proxion_messenger_core.tunnel as tunnelmod
    name = "cloudflared.exe" if __import__("sys").platform == "win32" else "cloudflared"
    bundled = tmp_path / name
    bundled.write_bytes(b"#!fake cloudflared\n")
    monkeypatch.setattr(tunnelmod, "_bundled_cloudflared_dirs", lambda: [str(tmp_path)])
    assert tunnelmod.find_cloudflared() == str(bundled)


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
    not be able to control it. tunnel_status stays open (benign)."""
    from proxion_messenger_core.security_policy import _OWNER_ONLY_COMMANDS
    assert "start_tunnel" in _OWNER_ONLY_COMMANDS
    assert "stop_tunnel" in _OWNER_ONLY_COMMANDS


@pytest.mark.asyncio
async def test_cold_stop_tunnel_does_not_wipe_configured_public_url():
    """R98: stop_tunnel with no active tunnel must not clobber a configured
    PROXION_PUBLIC_URL (or force_auth) with the init-default None/False."""
    from proxion_messenger_core.gateway import ProxionGateway
    gw = ProxionGateway.__new__(ProxionGateway)

    class _Cfg:
        public_url = "https://my.configured.example"
    gw.config = _Cfg()
    gw._tunnel = None
    gw._tunnel_prev_public_url = None          # never started a tunnel
    gw._force_auth = True                        # e.g. explicitly required
    gw._tunnel_prev_force_auth = False

    sent = []
    class _WS:
        async def send(self, m): sent.append(m)

    await gw._handle_stop_tunnel(_WS(), {})
    assert gw.config.public_url == "https://my.configured.example"  # untouched
    assert gw._force_auth is True                                    # untouched
    assert any("stopped" in m for m in sent)
