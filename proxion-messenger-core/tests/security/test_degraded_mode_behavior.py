"""Tests for R7 get_degraded_mode_state owner-only command."""
import asyncio
import json
import os
import pytest
import time
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def gateway(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "dg.db")),
    )


def make_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("127.0.0.1", 9000)
    return ws


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDegradedModeBehavior:
    def test_get_degraded_mode_state_not_degraded_when_no_open_breakers(self, gateway):
        """Returns degraded=False when no webhook breakers are open."""
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
        ws = make_ws()
        gateway._client_webids[ws] = owner_did
        run(gateway._handle_get_degraded_mode_state(ws, {}))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        state = [c for c in calls if c.get("type") == "degraded_mode_state"]
        assert len(state) == 1
        assert state[0]["degraded"] is False
        assert state[0]["open_webhook_breakers"] == []

    def test_get_degraded_mode_state_degraded_when_breaker_open(self, gateway):
        """Returns degraded=True when at least one webhook breaker is open."""
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
        ws = make_ws()
        gateway._client_webids[ws] = owner_did
        gateway._webhook_breakers["wh-open"] = {"failures": 10, "opened_at": time.time()}
        run(gateway._handle_get_degraded_mode_state(ws, {}))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        state = [c for c in calls if c.get("type") == "degraded_mode_state"]
        assert state[0]["degraded"] is True
        assert "wh-open" in state[0]["open_webhook_breakers"]

    def test_get_degraded_mode_state_forbidden_for_non_owner(self, gateway):
        """Non-owner gets E_FORBIDDEN response."""
        ws = make_ws()
        gateway._client_webids[ws] = "did:key:z6MkNotOwner"
        run(gateway._handle_get_degraded_mode_state(ws, {}))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        errors = [c for c in calls if c.get("code") == "E_FORBIDDEN"]
        assert len(errors) == 1

    def test_breaker_details_include_failure_count(self, gateway):
        """Breaker details include failures count and open_seconds."""
        from proxion_messenger_core.didkey import pub_key_to_did
        owner_did = pub_key_to_did(gateway.agent.identity_pub_bytes)
        ws = make_ws()
        gateway._client_webids[ws] = owner_did
        opened = time.time() - 30
        gateway._webhook_breakers["wh-detail"] = {"failures": 15, "opened_at": opened}
        run(gateway._handle_get_degraded_mode_state(ws, {}))
        calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
        state = calls[-1]
        details = state.get("breaker_details", {}).get("wh-detail", {})
        assert details["failures"] == 15
        assert details["open_seconds"] is not None
        assert details["open_seconds"] >= 29
