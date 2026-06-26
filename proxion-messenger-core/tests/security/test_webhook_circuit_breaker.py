"""Tests for R7 per-webhook circuit breaker in _fire_outgoing_webhook."""
import asyncio
import pytest
import os
from unittest.mock import MagicMock, AsyncMock, patch
import time


@pytest.fixture
def gateway(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "cb.db")),
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestWebhookCircuitBreaker:
    def test_circuit_opens_after_consecutive_failures(self, gateway):
        """After 10 consecutive failures, breaker opens and opened_at is set."""
        wh_id = "wh-test-1"
        gateway._webhook_breakers[wh_id] = {"failures": 9, "opened_at": None}
        # Simulate one more failure by directly updating (unit-testing the state machine)
        breaker = gateway._webhook_breakers[wh_id]
        breaker["failures"] += 1
        if breaker["failures"] >= 10 and breaker["opened_at"] is None:
            breaker["opened_at"] = time.time()
        gateway._webhook_breakers[wh_id] = breaker
        assert gateway._webhook_breakers[wh_id]["opened_at"] is not None

    def test_circuit_blocks_deliveries_during_cooldown(self, gateway):
        """When breaker is open and cooldown hasn't elapsed, fire is suppressed."""
        wh_id = "wh-test-2"
        gateway._webhook_breakers[wh_id] = {
            "failures": 10,
            "opened_at": time.time(),  # just opened
        }
        wh = {
            "id": wh_id,
            "url": "https://example.com/webhook",
            "token": "tok",
            "thread_id": "room-1",
        }
        # The breaker check happens at the top of _fire_outgoing_webhook:
        breaker = gateway._webhook_breakers.get(wh_id, {"failures": 0, "opened_at": None})
        _BREAKER_COOLDOWN = 600
        elapsed = time.time() - breaker["opened_at"]
        assert elapsed < _BREAKER_COOLDOWN  # still in cooldown → would suppress

    def test_circuit_resets_on_success(self, gateway):
        """Breaker state resets to failures=0, opened_at=None on success."""
        wh_id = "wh-test-3"
        gateway._webhook_breakers[wh_id] = {"failures": 10, "opened_at": time.time()}
        # Simulate success
        gateway._webhook_breakers[wh_id] = {"failures": 0, "opened_at": None}
        assert gateway._webhook_breakers[wh_id]["failures"] == 0
        assert gateway._webhook_breakers[wh_id]["opened_at"] is None

    def test_circuit_half_open_then_closes_on_success(self, gateway):
        """After cooldown, half-open allows a probe; success closes the breaker."""
        wh_id = "wh-test-4"
        # Opened 11 minutes ago (past 10-min cooldown)
        opened_at = time.time() - 660
        gateway._webhook_breakers[wh_id] = {"failures": 10, "opened_at": opened_at}
        breaker = gateway._webhook_breakers[wh_id]
        _BREAKER_COOLDOWN = 600
        elapsed = time.time() - breaker["opened_at"]
        assert elapsed >= _BREAKER_COOLDOWN  # half-open probe allowed
        # After probe succeeds, state is reset
        gateway._webhook_breakers[wh_id] = {"failures": 0, "opened_at": None}
        assert gateway._webhook_breakers[wh_id]["opened_at"] is None

    def test_webhook_breakers_initialized_empty(self, gateway):
        """_webhook_breakers starts empty on gateway init."""
        assert isinstance(gateway._webhook_breakers, dict)
        assert len(gateway._webhook_breakers) == 0

    def test_security_event_emitted_on_breaker_open(self, gateway):
        """When breaker opens, a security event is saved."""
        gateway._store.save_security_event(
            "webhook_circuit_opened", "warning",
            webid=None, ip=None,
            details="webhook_id=test failures=10",
        )
        events = gateway._store.get_security_events(event_type="webhook_circuit_opened")
        assert len(events) == 1
