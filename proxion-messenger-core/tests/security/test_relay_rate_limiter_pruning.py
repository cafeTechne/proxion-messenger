"""The public relay rate-limit map must not grow without bound.

/relay and /relay/receipt key their rate-limit buckets by source IP via
setdefault. The deques self-trim old timestamps, but an emptied deque keeps its
dict slot, so the map previously grew by one entry per distinct source IP and
was never reclaimed — unbounded memory driven entirely by remote input.
"""
from collections import deque

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gw(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9896, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )


def test_idle_buckets_are_pruned_once_the_map_is_large(gw):
    now = 10_000.0
    # Simulate many one-shot peers whose buckets have aged out.
    for i in range(gw._RELAY_RL_PRUNE_AT + 50):
        gw._relay_rate_limiter[f"10.0.{i // 256}.{i % 256}"] = deque(
            [now - gw._RELAY_RL_WINDOW - 1]
        )
    # One actively-sending peer must be kept.
    gw._relay_rate_limiter["203.0.113.9"] = deque([now])

    gw._prune_relay_rate_limiter(now)

    assert "203.0.113.9" in gw._relay_rate_limiter, "active bucket must survive"
    assert len(gw._relay_rate_limiter) == 1, "idle buckets should be reclaimed"


def test_prune_is_a_noop_while_the_map_is_small(gw):
    """Avoid scanning the map on every single request."""
    now = 10_000.0
    gw._relay_rate_limiter["10.0.0.1"] = deque([now - 999])
    gw._prune_relay_rate_limiter(now)
    assert "10.0.0.1" in gw._relay_rate_limiter


def test_empty_bucket_is_reclaimed(gw):
    now = 10_000.0
    for i in range(gw._RELAY_RL_PRUNE_AT):
        gw._relay_rate_limiter[f"172.16.{i // 256}.{i % 256}"] = deque()
    gw._prune_relay_rate_limiter(now)
    assert gw._relay_rate_limiter == {}
