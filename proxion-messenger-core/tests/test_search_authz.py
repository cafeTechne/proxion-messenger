"""Search must not leak messages from threads the caller isn't in.

Regression: search_messages ignores the member-thread scope when a thread_id
filter is given, so passing thread_id for any room/DM returned its messages.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _last(ws, type_):
    for c in reversed(ws.send.call_args_list):
        m = json.loads(c[0][0])
        if m.get("type") == type_:
            return m
    return None


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "search.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


@pytest.mark.asyncio
async def test_thread_filter_for_foreign_thread_returns_empty(gateway, noauth_env):
    alice = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, alice)
    # A room Alice is NOT a member of, containing a secret message.
    gateway._store.save_message(
        "m1", "secret-room", "room", "did:key:zBob", "Bob",
        "topsecret battle plans", "2026-01-01T00:00:00+00:00",
    )

    ws.send.reset_mock()
    await gateway.process_command(ws, {
        "cmd": "search", "query": "topsecret", "thread_id": "secret-room",
    })
    res = _last(ws, "search_results")
    assert res is not None
    assert res["results"] == [], "must not leak a non-member thread's messages"


@pytest.mark.asyncio
async def test_search_still_finds_own_thread(gateway, noauth_env):
    alice = _did(Ed25519PrivateKey.generate())
    ws = _mock_ws()
    await _register(gateway, ws, alice)
    gateway._store.save_dm_thread("alice-dm", _did(Ed25519PrivateKey.generate()), "Carol", owner_webid=alice)
    gateway._store.save_message(
        "m2", "alice-dm", "dm", alice, "Alice",
        "findme needle here", "2026-01-01T00:00:00+00:00",
    )

    ws.send.reset_mock()
    await gateway.process_command(ws, {"cmd": "search", "query": "findme", "thread_id": "alice-dm"})
    res = _last(ws, "search_results")
    assert res is not None and len(res["results"]) >= 1
