"""Process lifecycle helpers for running the gateway as the desktop app's sidecar.

The desktop shell owns the gateway process. It must be able to stop it cleanly
(so cloudflared and the database are shut down properly) and the gateway must
not outlive the shell. The shell keeps the write end of the gateway's stdin
open: a ``shutdown`` line, or end-of-file when the shell exits for any reason
(quit, relaunch, update, crash), asks the gateway to stop. Bind failures are
reported as GatewayBindError so the launcher can say which port is taken.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import os
import socket
import threading
import time
from typing import Callable, Iterable, Optional

_log = logging.getLogger(__name__)

# Windows reports a taken port as WSAEADDRINUSE (10048), or WSAEACCES (10013)
# when the port sits in an excluded range.
_ADDR_IN_USE = {errno.EADDRINUSE, 10048, 10013}


class GatewayBindError(OSError):
    """The gateway could not listen on one of its ports."""

    def __init__(self, host: str, port: int, cause: Optional[BaseException] = None):
        self.host = host
        self.port = port
        self.cause = cause
        in_use = isinstance(cause, OSError) and cause.errno in _ADDR_IN_USE
        self.in_use = in_use or cause is None
        reason = "address already in use" if self.in_use else str(cause)
        super().__init__(f"cannot listen on {host}:{port} ({reason})")


def port_is_free(host: str, port: int) -> bool:
    """Whether a listening socket could bind host:port right now."""
    bind_host = "" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            # Match asyncio/websockets, which set SO_REUSEADDR on POSIX, so a
            # port in TIME_WAIT is not mistaken for a taken one. (On Windows the
            # flag would let us bind over a live listener, so it stays off.)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((bind_host, port))
        except OSError:
            return False
    return True


async def wait_for_free_ports(host: str, ports: Iterable[int], timeout: float) -> None:
    """Wait up to *timeout* seconds for every port to become bindable.

    Used after a relaunch or update, when the previous gateway may still be
    finishing its shutdown. Returns as soon as all are free; on timeout returns
    anyway and lets the real bind report the error.
    """
    ports = [p for p in ports if p]
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        busy = [p for p in ports if not port_is_free(host, p)]
        if not busy:
            return
        if time.monotonic() >= deadline:
            return
        _log.info("waiting for port(s) %s to be released", busy)
        await asyncio.sleep(0.25)


def watch_stdin_for_shutdown(stream, on_shutdown: Callable[[], None]) -> threading.Thread:
    """Call *on_shutdown* once when *stream* sends ``shutdown`` or reaches EOF.

    Runs in a daemon thread because a blocking read of a pipe is the only
    portable way to notice the parent going away (asyncio cannot add a pipe
    reader on the Windows selector loop).
    """
    def _run():
        try:
            while True:
                raw = stream.readline()
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                if not line:
                    _log.info("desktop app closed the gateway's stdin; shutting down")
                    break
                if line.strip().lower() == "shutdown":
                    _log.info("shutdown requested by the desktop app")
                    break
        except Exception as exc:  # stream closed under us
            _log.info("stdin watcher stopped: %s", exc)
        on_shutdown()

    t = threading.Thread(target=_run, name="proxion-stdin-watch", daemon=True)
    t.start()
    return t
