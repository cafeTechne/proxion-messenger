"""App-driven public tunnel (R97).

Gives a phone browser a real HTTPS/WSS endpoint to the home gateway by running a
cloudflared "quick tunnel" (no account, ephemeral ``*.trycloudflare.com`` URL).
Detection-only: if cloudflared is not installed we report that rather than
bundling or downloading it (30 MB per platform, and a supply-chain/consent
question). The caller (gateway) is responsible for forcing auth on while a tunnel
is active, since exposing the gateway publicly must not rely on the
loopback-skips-auth default.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional

_log = logging.getLogger(__name__)

# cloudflared prints a line like:  https://calm-forest-1234.trycloudflare.com
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# Rust target triples for the host, mirroring build_sidecar.TRIPLE_MAP. Only
# needed to look up the pin at runtime; the authoritative copy lives in the
# build script.
_TRIPLE_MAP: dict[tuple[str, str], str] = {
    ("Windows", "AMD64"):  "x86_64-pc-windows-msvc",
    ("Windows", "ARM64"):  "aarch64-pc-windows-msvc",
    ("Darwin",  "x86_64"): "x86_64-apple-darwin",
    ("Darwin",  "arm64"):  "aarch64-apple-darwin",
    ("Linux",   "x86_64"): "x86_64-unknown-linux-gnu",
    ("Linux",   "aarch64"): "aarch64-unknown-linux-gnu",
}


def extract_tunnel_url(line: str) -> Optional[str]:
    """Pull the quick-tunnel URL out of a cloudflared log line, or None."""
    if not line:
        return None
    m = _TUNNEL_URL_RE.search(line)
    return m.group(0) if m else None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pinned_sha256() -> Optional[str]:
    """SHA-256 a fallback cloudflared must match on this host to be trusted.

    Sourced from ``cloudflared.lock`` — the same pin build_sidecar.py verifies
    against when bundling, so there is a single source of truth rather than a
    duplicated literal. The lock records the hash of the release *asset*: for
    Windows and Linux that asset is the raw binary, so it can be compared to a
    binary on disk. The macOS asset is a ``.tgz`` whose hash is not the extracted
    binary's, so those triples are treated as unpinned (returns None) instead of
    verified against the wrong hash. Also returns None when the lock is not on
    disk (frozen/installed layouts) or holds no usable pin for this host.
    """
    triple = _TRIPLE_MAP.get((platform.system(), platform.machine()))
    if not triple or "darwin" in triple:
        return None
    lock = Path(__file__).resolve().parents[3] / "cloudflared.lock"
    try:
        data = json.loads(lock.read_text())
    except Exception:
        return None
    pin = str(data.get("sha256", {}).get(triple, "")).strip().lower()
    return pin or None


def find_cloudflared() -> Optional[str]:
    """Path to cloudflared, preferring the immutable bundled copy (R97/R98).

    A PyInstaller onefile build extracts the verified-at-build-time binary to
    ``sys._MEIPASS``; when that copy is present it is used and nothing else is
    consulted, so a binary planted in a user-writable location cannot shadow it.
    The remaining locations are fallbacks only for builds that did not bundle
    cloudflared: a copy sitting next to the executable is user-writable (the
    planting vector), so it is accepted only when it matches the pinned release
    hash; a copy on PATH is one the user installed themselves, outside the
    sidecar directory, so it is used but logged as unverified.
    """
    name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"

    # 1. Immutable bundled copy wins outright.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = os.path.join(meipass, name)
        if os.path.isfile(cand):
            return cand

    expected = _pinned_sha256()

    # 2. A copy next to the executable is user-writable; trust it only when it
    #    matches the pinned release hash.
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        exe_dir = None
    if exe_dir:
        cand = os.path.join(exe_dir, name)
        if os.path.isfile(cand):
            if expected and _sha256_file(cand) == expected:
                return cand
            _log.warning(
                "ignoring cloudflared next to the executable (unverified): %s",
                cand,
            )

    # 3. PATH: a cloudflared the user installed themselves.
    which = shutil.which("cloudflared")
    if which:
        if expected and _sha256_file(which) == expected:
            return which
        _log.warning("using unverified cloudflared from PATH: %s", which)
        return which
    return None


class TunnelManager:
    """Lifecycle for a single cloudflared quick tunnel.

    States: ``absent`` (no binary), ``stopped``, ``starting``, ``running`` (has a
    URL), ``failed`` (with an error). ``spawn`` is injectable so tests can drive
    the state machine without a real cloudflared.
    """

    def __init__(
        self,
        cloudflared_path: Optional[str] = None,
        spawn: Optional[Callable[[str, int], Awaitable[asyncio.subprocess.Process]]] = None,
    ):
        self._path = cloudflared_path or find_cloudflared()
        self._spawn = spawn or self._default_spawn
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._url: Optional[str] = None
        self._error: Optional[str] = None
        self._state = "stopped" if self._path else "absent"

    def status(self) -> dict:
        return {"state": self._state, "url": self._url, "error": self._error}

    async def _default_spawn(self, path: str, http_port: int) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            path, "tunnel", "--url", f"http://127.0.0.1:{http_port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def _read_until_url(self) -> str:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                raise RuntimeError("cloudflared exited before providing a url")
            url = extract_tunnel_url(raw.decode("utf-8", "replace"))
            if url:
                return url

    async def start(self, http_port: int, timeout: float = 30.0) -> dict:
        """Start the tunnel and resolve once it reports a URL. Idempotent when
        already running; returns the status dict either way."""
        if not self._path:
            self._state = "absent"
            return self.status()
        if self._state == "running":
            return self.status()
        self._state = "starting"
        self._url = None
        self._error = None
        try:
            self._proc = await self._spawn(self._path, http_port)
            url = await asyncio.wait_for(self._read_until_url(), timeout)
        except asyncio.TimeoutError:
            self._error = "timed out waiting for the tunnel URL"
            self._state = "failed"
            await self.stop()
            self._state = "failed"
            return self.status()
        except Exception as exc:  # spawn failed / process died
            self._error = str(exc)
            self._state = "failed"
            await self.stop()
            self._state = "failed"
            return self.status()
        self._url = url
        self._state = "running"
        return self.status()

    async def stop(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        self._proc = None
        self._url = None
        if self._state != "absent":
            self._state = "stopped"
