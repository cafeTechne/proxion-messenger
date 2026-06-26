"""Tests: verify_contact marks as verified; safety numbers computed correctly."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=GatewayConfig(port=9990, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    return ws


@pytest.mark.asyncio
async def test_verify_contact_saves_to_sqlite(gateway):
    """verify_contact stores the verification in SQLite."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    peer_webid = "https://bob.pod/profile/card#me"
    safety_numbers = "1234 5678 9012 3456"

    await gateway.process_command(ws, {
        "cmd": "verify_contact",
        "peer_webid": peer_webid,
        "safety_numbers": safety_numbers,
    })

    result = gateway._store.get_contact_verification(peer_webid)
    assert result is not None
    assert result["safety_numbers"] == safety_numbers


@pytest.mark.asyncio
async def test_verify_contact_sends_verified_event(gateway):
    """verify_contact sends contact_verified event back to client."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    webid = "https://alice.pod/profile/card#me"
    gateway._client_webids[ws] = webid
    gateway._display_names[ws] = "Alice"

    await gateway.process_command(ws, {
        "cmd": "verify_contact",
        "peer_webid": "https://charlie.pod/profile/card#me",
        "safety_numbers": "0000 1111",
    })
    ws.send.assert_called()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "contact_verified"


@pytest.mark.asyncio
async def test_verify_contact_requires_auth(gateway):
    """verify_contact is rejected if client is not registered."""
    ws = _mock_ws()
    gateway.clients.add(ws)
    # Not registered — no webid in _client_webids

    await gateway.process_command(ws, {
        "cmd": "verify_contact",
        "peer_webid": "https://eve.pod/profile/card#me",
        "safety_numbers": "9999 8888",
    })
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("type") == "error"
