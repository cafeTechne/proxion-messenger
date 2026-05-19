"""Round 3: Security events table and owner-only API."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "events.db"))


@pytest.fixture
def gw(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9885, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def test_security_event_saved_and_retrieved(store):
    """save_security_event persists and get_security_events retrieves it."""
    store.save_security_event("auth_lockout", "warning", ip="1.2.3.4", details="5 failures")
    events = store.get_security_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "auth_lockout"
    assert events[0]["severity"] == "warning"
    assert events[0]["ip"] == "1.2.3.4"


def test_get_security_events_filters_by_type(store):
    """get_security_events with event_type filters correctly."""
    store.save_security_event("auth_lockout", "warning")
    store.save_security_event("rate_limit", "info")
    store.save_security_event("auth_lockout", "warning")
    lockouts = store.get_security_events(event_type="auth_lockout")
    assert len(lockouts) == 2
    others = store.get_security_events(event_type="rate_limit")
    assert len(others) == 1


@pytest.mark.asyncio
async def test_get_security_events_owner_only(gw):
    """Non-owner gets E_FORBIDDEN when calling get_security_events."""
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = "did:key:not-the-owner"

    await gw._handle_get_security_events(ws, {})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "error"
    assert resp.get("code") == "E_FORBIDDEN" or "owner" in resp.get("message", "").lower()


@pytest.mark.asyncio
async def test_get_security_events_filters_and_limits(gw):
    """get_security_events respects event_type filter and limit."""
    from proxion_messenger_core.didkey import pub_key_to_did
    owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = owner_did

    # Write some events
    for i in range(5):
        gw._store.save_security_event("auth_lockout", "warning")
    for i in range(3):
        gw._store.save_security_event("rate_limit", "info")

    await gw._handle_get_security_events(ws, {"event_type": "auth_lockout", "limit": 3})
    resp = json.loads(ws.send.call_args[0][0])
    assert resp["type"] == "security_events"
    assert len(resp["events"]) == 3
    for e in resp["events"]:
        assert e["event_type"] == "auth_lockout"
