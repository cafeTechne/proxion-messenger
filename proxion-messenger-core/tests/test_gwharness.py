"""Tests for the shared gateway test harness (tests/gwharness.py).

The harness replaced twelve copy-pasted `_start_gateway` helpers whose
`except Exception: ready.set()` signalled READY on failure. That made
`assert ready.wait(...), "gateway failed to start"` unfalsifiable and handed
dead gateways to tests, which then failed later with confusing connection
errors. These tests lock in the corrected behaviour.
"""
from __future__ import annotations

import socket
import threading

import pytest

pytest.importorskip("websockets")

import gwharness


class _ExplodingGateway:
    """Stands in for a gateway whose server cannot start."""

    async def handle_client(self, *a, **kw):  # pragma: no cover - never reached
        raise AssertionError("should not be called")

    async def _serve_http(self, web_dir, http_port):  # pragma: no cover
        raise AssertionError("should not be called")


def test_startup_failure_raises_instead_of_returning_dead_gateway():
    """A port already in use must surface as an error, not a silent dead gateway."""
    # Occupy a port so websockets.serve cannot bind it.
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken_port = blocker.getsockname()[1]
    try:
        with pytest.raises(RuntimeError, match="failed to start"):
            gwharness.serve_gateway(
                _ExplodingGateway(), taken_port, gwharness.free_port(),
                serve_http=False, timeout=3.0,
            )
    finally:
        blocker.close()
        gwharness.shutdown_all()


def test_shutdown_all_stops_threads_and_frees_the_port(tmp_path):
    """A started gateway is stopped by shutdown_all, releasing its port."""
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState

    ws_port, http_port = gwharness.free_port(), gwharness.free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={},
                        room_memberships={}, config=cfg, read_state=ReadState())

    before = threading.active_count()
    handle = gwharness.serve_gateway(gw, ws_port, http_port)
    assert handle.thread is not None and handle.thread.is_alive()
    assert gwharness._port_accepts(http_port), "harness returned before the port was up"

    assert gwharness.shutdown_all() >= 1
    handle.thread.join(timeout=5)
    assert not handle.thread.is_alive(), "gateway thread outlived shutdown_all"
    assert threading.active_count() <= before, "thread leaked past shutdown"


def test_registry_is_drained_between_tests():
    """The autouse conftest fixture leaves no gateways registered."""
    assert gwharness._REGISTRY == []
