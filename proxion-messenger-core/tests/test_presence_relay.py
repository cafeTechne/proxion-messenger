"""Tests: cross-gateway presence relay."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    return gw


def _seed_rel(gw, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": "cert-" + peer_did[-4:], "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=peer_did, owner_webid=owner)


@pytest.mark.asyncio
async def test_presence_relay_updates_local_cache(gateway):
    """_handle_presence_relay updates _user_presence cache (for a known peer)."""
    _seed_rel(gateway, "did:key:zAlice")
    status, _ = await gateway._handle_presence_relay({
        "from_webid": "did:key:zAlice",
        "status": "online",
        "status_message": "Working",
        "updated_at": "2026-05-24T10:00:00Z",
    })
    assert status.startswith("200")
    assert gateway._user_presence.get("did:key:zAlice", {}).get("status") == "online"


@pytest.mark.asyncio
async def test_presence_relay_delivers_to_connected_clients(gateway):
    """_handle_presence_relay broadcasts presence event to all connected sockets."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "did:key:zLocalUser"
    gateway._webid_sockets["did:key:zLocalUser"] = {ws}
    _seed_rel(gateway, "did:key:zBob", owner="did:key:zLocalUser")

    await gateway._handle_presence_relay({
        "from_webid": "did:key:zBob",
        "status": "away",
        "status_message": "",
        "updated_at": "2026-05-24T10:00:00Z",
    })

    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "presence"
    assert sent["status"] == "away"


@pytest.mark.asyncio
async def test_presence_relay_rejects_invalid_status(gateway):
    """_handle_presence_relay rejects unknown status values."""
    status, _ = await gateway._handle_presence_relay({
        "from_webid": "did:key:zEve",
        "status": "invisible",  # not a valid status
        "updated_at": "2026-05-24T10:00:00Z",
    })
    assert status.startswith("400")


@pytest.mark.asyncio
async def test_presence_relay_rejects_unknown_peer_spoof(gateway):
    """Presence for a webid we have NO relationship with is ignored (200, no
    reveal) — a peer gateway can't inject presence for arbitrary webids."""
    ws = AsyncMock(); ws.send = AsyncMock(); ws.__hash__ = lambda s: id(s)
    gateway.clients.add(ws)
    status, _ = await gateway._handle_presence_relay({
        "from_webid": "did:key:zStranger", "status": "online",
        "status_message": "", "updated_at": "2026-05-24T10:00:00Z",
    })
    assert status.startswith("200")
    assert "did:key:zStranger" not in gateway._user_presence
    ws.send.assert_not_called()
