"""R94: per-destination-gateway reachability health.

Tracks, per peer gateway URL, the consecutive-failure streak and the start of
the current outage (down_since) so the sender's "queued, will retry" surface can
say how long a peer has been offline instead of a generic pending state.
"""
from __future__ import annotations

import time

import pytest

from proxion_messenger_core.local_store import LocalStore

GW = "https://peer.example.org:8443"


@pytest.fixture()
def store(tmp_path):
    return LocalStore(str(tmp_path / "health.db"))


def test_unknown_gateway_has_no_health(store):
    assert store.get_peer_gateway_health(GW) is None
    assert store.list_unhealthy_peer_gateways() == []


def test_first_failure_starts_outage(store):
    store.record_peer_gateway_failure(GW, "connect timeout")
    h = store.get_peer_gateway_health(GW)
    assert h["consecutive_failures"] == 1
    assert h["down_since"] is not None
    assert h["last_failure_at"] is not None
    assert h["last_error"] == "connect timeout"
    assert h["last_success_at"] is None


def test_repeated_failures_increment_but_hold_down_since(store):
    store.record_peer_gateway_failure(GW, "e1")
    first = store.get_peer_gateway_health(GW)["down_since"]
    time.sleep(0.01)
    store.record_peer_gateway_failure(GW, "e2")
    store.record_peer_gateway_failure(GW, "e3")
    h = store.get_peer_gateway_health(GW)
    assert h["consecutive_failures"] == 3
    # The outage clock is stamped once and does not reset on each failure.
    assert h["down_since"] == first
    assert h["last_error"] == "e3"


def test_success_clears_streak_and_outage(store):
    store.record_peer_gateway_failure(GW, "e1")
    store.record_peer_gateway_failure(GW, "e2")
    store.record_peer_gateway_success(GW)
    h = store.get_peer_gateway_health(GW)
    assert h["consecutive_failures"] == 0
    assert h["down_since"] is None
    assert h["last_success_at"] is not None


def test_failure_after_recovery_starts_a_new_outage(store):
    store.record_peer_gateway_failure(GW, "e1")
    down1 = store.get_peer_gateway_health(GW)["down_since"]
    store.record_peer_gateway_success(GW)
    time.sleep(0.01)
    store.record_peer_gateway_failure(GW, "e2")
    down2 = store.get_peer_gateway_health(GW)["down_since"]
    assert down2 is not None and down2 != down1
    assert store.get_peer_gateway_health(GW)["consecutive_failures"] == 1


def test_list_unhealthy_orders_by_failures_and_filters(store):
    a, b, c = "https://a", "https://b", "https://c"
    for _ in range(3):
        store.record_peer_gateway_failure(a, "x")
    store.record_peer_gateway_failure(b, "x")
    store.record_peer_gateway_success(c)  # healthy: 0 failures
    unhealthy = store.list_unhealthy_peer_gateways(min_failures=1)
    urls = [row["gateway_url"] for row in unhealthy]
    assert urls == [a, b]              # most failures first, c excluded
    assert store.list_unhealthy_peer_gateways(min_failures=2) == \
        [row for row in unhealthy if row["gateway_url"] == a]


def test_blank_url_is_a_noop(store):
    store.record_peer_gateway_failure("", "x")
    store.record_peer_gateway_success("")
    assert store.get_peer_gateway_health("") is None
    assert store.list_unhealthy_peer_gateways() == []
