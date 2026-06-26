"""Tests for _check_room_enforcement — per-user rate limits and read-only access."""
from __future__ import annotations

import asyncio
import json
import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.room import RoomConfig, RoomMembership


OWNER_WEBID = "did:key:zOwner"
OTHER_WEBID = "did:key:zOther"


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


def _make_membership(owner_webid: str, read_only: bool = False, rate_limit: int | None = None) -> RoomMembership:
    config = RoomConfig(
        room_id="room-test",
        name="Test Room",
        owner_webid=owner_webid,
        pod_url="http://localhost:3001/",
        stash_root="stash://rooms/room-test/",
        created_at="2025-01-01T00:00:00Z",
        read_only=read_only,
        rate_limit=rate_limit,
    )
    membership = MagicMock(spec=RoomMembership)
    membership.room = config
    membership.cert = None
    return membership


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=GatewayConfig())


@pytest.mark.asyncio
async def test_read_only_blocks_non_owner(gateway):
    """Non-owner is blocked from posting in a read-only room."""
    ws = _mock_ws()
    gateway._client_webids[ws] = OTHER_WEBID
    membership = _make_membership(OWNER_WEBID, read_only=True)

    error = await gateway._check_room_enforcement(ws, "room-test", membership)

    assert error == "Room is read-only"


@pytest.mark.asyncio
async def test_read_only_allows_owner(gateway):
    """Room owner is allowed to post even when the room is read-only."""
    ws = _mock_ws()
    gateway._client_webids[ws] = OWNER_WEBID
    membership = _make_membership(OWNER_WEBID, read_only=True)

    error = await gateway._check_room_enforcement(ws, "room-test", membership)

    assert error is None


@pytest.mark.asyncio
async def test_rate_limit_is_per_user(gateway):
    """Rate limits are applied independently per user, not shared across the gateway."""
    ws_alice = _mock_ws()
    ws_bob = _mock_ws()
    gateway._client_webids[ws_alice] = "did:key:zAlice"
    gateway._client_webids[ws_bob] = "did:key:zBob"

    membership = _make_membership(OWNER_WEBID, rate_limit=60)

    # Alice's first message — should pass
    err_alice1 = await gateway._check_room_enforcement(ws_alice, "room-test", membership)
    assert err_alice1 is None, "Alice's first message should pass"

    # Alice's second message in the same window — should be rate-limited
    err_alice2 = await gateway._check_room_enforcement(ws_alice, "room-test", membership)
    assert err_alice2 is not None, "Alice's second message should be rate-limited"
    assert "Rate limit" in err_alice2

    # Bob's first message — must NOT inherit Alice's rate-limit bucket
    err_bob1 = await gateway._check_room_enforcement(ws_bob, "room-test", membership)
    assert err_bob1 is None, "Bob's first message must not be blocked by Alice's rate-limit bucket"


@pytest.mark.asyncio
async def test_no_enforcement_on_normal_room(gateway):
    """A room with neither read_only nor rate_limit imposes no restriction."""
    ws = _mock_ws()
    gateway._client_webids[ws] = OTHER_WEBID
    membership = _make_membership(OWNER_WEBID)

    error = await gateway._check_room_enforcement(ws, "room-test", membership)

    assert error is None
