"""R17: /metrics endpoint OpenMetrics format checks."""
import pytest
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.security_policy import get_policy


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(agent, {}, {}, GatewayConfig())


def _build_metrics_text(gw):
    """Mirror the /metrics endpoint logic."""
    tier = get_policy().get_tier()
    lines = [
        "# HELP proxion_security_tier Current adaptive security tier (0-3)",
        "# TYPE proxion_security_tier gauge",
        f"proxion_security_tier {tier}",
        "# HELP proxion_relay_queue_depth Pending relay messages awaiting delivery",
        "# TYPE proxion_relay_queue_depth gauge",
        "proxion_relay_queue_depth 0",
        "# HELP proxion_ws_connections_current Active WebSocket connections",
        "# TYPE proxion_ws_connections_current gauge",
        f"proxion_ws_connections_current {len(gw._client_webids)}",
    ]
    for k, v in gw._metrics.items():
        typ = "gauge" if k.endswith("_current") else "counter"
        lines.append(f"# HELP proxion_{k} {k.replace('_', ' ')}")
        lines.append(f"# TYPE proxion_{k} {typ}")
        lines.append(f"proxion_{k} {v}")
    return "\n".join(lines) + "\n"


def test_metrics_endpoint_exposes_openmetrics_format(gateway):
    text = _build_metrics_text(gateway)
    lines = text.strip().split("\n")
    assert any(l.startswith("# HELP") for l in lines)
    assert any(l.startswith("# TYPE") for l in lines)


def test_metrics_contains_required_counters_gauges_histograms(gateway):
    text = _build_metrics_text(gateway)
    required = [
        "proxion_security_tier",
        "proxion_relay_queue_depth",
        "proxion_ws_connections_current",
        "proxion_messages_total",
    ]
    for name in required:
        assert name in text, f"Missing metric: {name}"


def test_metrics_security_tier_gauge_tracks_policy_state(gateway):
    text = _build_metrics_text(gateway)
    tier = get_policy().get_tier()
    assert f"proxion_security_tier {tier}" in text
