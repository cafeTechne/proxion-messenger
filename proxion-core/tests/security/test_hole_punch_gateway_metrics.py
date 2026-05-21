"""Tests that gateway metrics counters for hole punching are wired correctly."""
from unittest.mock import MagicMock

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = MagicMock(spec=AgentState)
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9999),
        read_state=ReadState(),
    )


def test_hole_punch_metrics_present(gateway):
    for key in ("hole_punch_attempts_total", "hole_punch_succeeded_total", "hole_punch_failed_total"):
        assert key in gateway._metrics, f"Missing metric: {key}"


def test_hole_punch_metrics_start_at_zero(gateway):
    assert gateway._metrics["hole_punch_attempts_total"] == 0
    assert gateway._metrics["hole_punch_succeeded_total"] == 0
    assert gateway._metrics["hole_punch_failed_total"] == 0


def test_all_wg_and_punch_metrics_present(gateway):
    expected = {
        "wg_peers_total",
        "wg_peers_direct",
        "wg_peers_relay",
        "relay_fallback_total",
        "relay_to_direct_recovery_total",
        "hole_punch_attempts_total",
        "hole_punch_succeeded_total",
        "hole_punch_failed_total",
    }
    for key in expected:
        assert key in gateway._metrics, f"Missing metric: {key}"
