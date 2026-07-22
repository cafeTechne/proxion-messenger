"""Shared harness for tests that run a real ProxionGateway in a background thread.

Historically every such test module carried its own copy of ``_start_gateway``.
Those copies leaked: each call started a daemon thread with its own event loop
and two listening sockets, and nothing ever shut them down. A full-suite run
accumulated one live gateway per call site (43 of them; measured at 42 daemon
threads and 798 open handles after only nine modules). That pressure pushed
timing-sensitive socket tests past their deadlines on slow or loaded machines,
which surfaced as rare, non-reproducible failures in whichever socket-dependent
test happened to run when pressure peaked, rather than as a consistent failure
in one place.

The copies also hid startup failures. ``except Exception: ready.set()`` signalled
READY on failure, so ``assert ready.wait(...), "gateway failed to start"`` could
never fail; the test then made HTTP calls against a gateway that was never
listening and failed later with a confusing connection error.

This harness fixes both:

* startup errors are captured and re-raised at the call site,
* "ready" means the HTTP port actually accepts a connection,
* every gateway is registered for automatic shutdown by the autouse fixture in
  ``conftest.py``, so nothing outlives the test that created it.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import websockets

# Gateways started during the current test. Drained by the autouse
# _shutdown_test_gateways fixture in conftest.py.
_REGISTRY: list["GatewayHandle"] = []

# Budget for a gateway to come up. Deliberately generous: the wait polls and
# returns the moment the server is serving, so this costs nothing on the happy
# path and only buys slack when the machine is loaded. A full suite run puts
# thousands of tests through this, and a 5s budget was tight enough that
# startup occasionally lost the race on a busy Windows box.
STARTUP_TIMEOUT = 15.0


def free_port() -> int:
    """Return a currently-free localhost TCP port.

    Inherently racy (the port is released before the caller binds it), so
    serve_gateway retries on collision rather than trusting this outright.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_accepts(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class GatewayHandle:
    """A running gateway plus the machinery needed to shut it down."""

    def __init__(self, gw, ws_port: int, http_port: int):
        self.gw = gw
        self.ws_port = ws_port
        self.http_port = http_port
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the gateway and join its thread. Safe to call more than once."""
        loop, stop_event = self.loop, self._stop_event
        if loop is not None and stop_event is not None:
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass  # loop already closed
        if self.thread is not None:
            self.thread.join(timeout=timeout)


def serve_gateway(gw, ws_port: int, http_port: int, *, serve_http: bool = True,
                  wait_http: bool = True, timeout: float = STARTUP_TIMEOUT) -> GatewayHandle:
    """Run ``gw`` in a background thread and wait until it is actually serving.

    Raises RuntimeError if the gateway fails to start or never accepts a
    connection, instead of handing back a dead gateway.
    """
    handle = GatewayHandle(gw, ws_port, http_port)
    loop = asyncio.new_event_loop()
    handle.loop = loop

    def _run():
        asyncio.set_event_loop(loop)

        async def _serve():
            stop = asyncio.Event()
            handle._stop_event = stop
            async with websockets.serve(gw.handle_client, "127.0.0.1", ws_port):
                http_task = None
                if serve_http:
                    http_task = asyncio.create_task(gw._serve_http(None, http_port))
                handle.ready.set()
                try:
                    await stop.wait()
                finally:
                    if http_task is not None:
                        http_task.cancel()

        try:
            loop.run_until_complete(_serve())
        except BaseException as exc:          # noqa: BLE001 - recorded, re-raised by caller
            handle.error = exc
            handle.ready.set()                # unblock the waiter; it checks .error
        finally:
            try:
                loop.close()
            except Exception:
                pass

    handle.thread = threading.Thread(target=_run, daemon=True)
    _REGISTRY.append(handle)
    handle.thread.start()

    if not handle.ready.wait(timeout=timeout):
        handle.stop()
        raise RuntimeError(f"gateway did not signal ready within {timeout}s")
    if handle.error is not None:
        handle.stop()
        raise RuntimeError(f"gateway failed to start: {handle.error!r}") from handle.error

    # ready fires when _serve_http is *scheduled*, not bound. Poll until the port
    # really accepts, so callers never race a not-yet-listening server.
    if serve_http and wait_http:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if handle.error is not None:
                handle.stop()
                raise RuntimeError(f"gateway failed to start: {handle.error!r}")
            if _port_accepts(http_port):
                break
            time.sleep(0.02)
        else:
            handle.stop()
            raise RuntimeError(
                f"gateway HTTP port {http_port} never accepted a connection "
                f"within {timeout}s"
            )
    return handle


# Public name used by the test modules. Deliberately NOT retrying on a different
# port: the gateway was constructed with these ports in its GatewayConfig
# (public_url, config.port), so serving on a different pair would leave the
# config silently inconsistent with reality. A rare free_port() collision should
# surface as a clear error instead.
start_gateway = serve_gateway


def shutdown_all(timeout: float = 5.0) -> int:
    """Stop every gateway started since the last drain. Returns how many."""
    stopped = 0
    while _REGISTRY:
        handle = _REGISTRY.pop()
        try:
            handle.stop(timeout=timeout)
            stopped += 1
        except Exception:
            pass
    return stopped
