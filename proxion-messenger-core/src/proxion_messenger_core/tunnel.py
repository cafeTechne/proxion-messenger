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
import re
import shutil
from typing import Awaitable, Callable, Optional

# cloudflared prints a line like:  https://calm-forest-1234.trycloudflare.com
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def extract_tunnel_url(line: str) -> Optional[str]:
    """Pull the quick-tunnel URL out of a cloudflared log line, or None."""
    if not line:
        return None
    m = _TUNNEL_URL_RE.search(line)
    return m.group(0) if m else None


def find_cloudflared() -> Optional[str]:
    """Absolute path to cloudflared on PATH, or None if not installed."""
    return shutil.which("cloudflared")


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
