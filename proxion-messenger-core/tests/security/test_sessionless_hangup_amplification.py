"""A voice_hangup with no local session is relayed to the peer's gateway by webid.
Each repeat used to be one more outbound relay POST (up to the whole 300/10s
signaling budget), and made-up session ids were relayed just the same."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core import _gateway_voice
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState

PEER = "did:key:z6MkPeerGateway"


@pytest.fixture(autouse=True)
def _clear():
    getattr(_gateway_voice, "_sessionless_hangups", {}).clear()
    yield
    getattr(_gateway_voice, "_sessionless_hangups", {}).clear()


def _setup(tmp_path):
    gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "s.db")))
    gw._store.save_relationship({"certificate_id": "cert-P", "subject": "cc" * 32,
                                 "created_at": 0, "expires_at": 2**31 - 1},
                                peer_did=PEER, owner_webid="did:key:z6MkMe")
    ws = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    gw.clients.add(ws)
    gw._client_webids[ws] = "did:key:z6MkMe"
    gw._webid_sockets["did:key:z6MkMe"] = {ws}
    gw._relay_voice_signal = AsyncMock(return_value=True)
    return gw, ws


@pytest.mark.asyncio
async def test_repeated_sessionless_hangup_relays_once(tmp_path):
    gw, ws = _setup(tmp_path)
    for _ in range(20):
        await gw._handle_voice_hangup(ws, {"session_id": "s1", "target_webid": PEER})
    await asyncio.sleep(0)
    assert gw._relay_voice_signal.call_count == 1


@pytest.mark.asyncio
async def test_sessionless_hangups_are_capped_per_sender(tmp_path):
    gw, ws = _setup(tmp_path)
    for i in range(50):
        await gw._handle_voice_hangup(ws, {"session_id": f"s{i}", "target_webid": PEER})
    await asyncio.sleep(0)
    assert gw._relay_voice_signal.call_count == getattr(_gateway_voice, "_SESSIONLESS_HANGUP_BURST", 10)


def test_signal_budget_is_bounded():
    # A call needs a few dozen ICE candidates; 300 per 10s per socket was a
    # relay amplifier for cross-gateway signaling.
    assert ProxionGateway._SIGNAL_RATE_LIMIT <= 150
