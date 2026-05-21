"""Tests for WG overlay metrics in ProxionGateway._metrics."""
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


def test_metrics_include_overlay_counters(gateway):
    for key in ("wg_peers_total", "wg_peers_direct", "wg_peers_relay",
                "relay_fallback_total", "relay_to_direct_recovery_total"):
        assert key in gateway._metrics, f"Missing metric: {key}"


def test_wg_metrics_keys_present_in_gateway(gateway):
    assert "wg_peers_total" in gateway._metrics
    assert "wg_peers_relay" in gateway._metrics


def test_relay_fallback_counter_starts_at_zero(gateway):
    assert gateway._metrics["relay_fallback_total"] == 0
    assert gateway._metrics["relay_to_direct_recovery_total"] == 0
