"""Tests: _discover_peer_gateway fetches .well-known/proxion."""
from __future__ import annotations
import json
from contextlib import ExitStack
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.network import NetworkError

@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "test.db"))
    return gw

def _valid_well_known(did="did:key:z6MkABCD"):
    return json.dumps({
        "did": did,
        "gateway_http_url": "https://bob.example.com",
        "display_name": "Bob",
        "x25519_pub": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "fingerprint": "AA:BB",
    }).encode()


def _patch_fetch(well_known_bytes):
    """Route _discover_peer_gateway's fetch through a mocked SSRF-safe GET and
    treat the discovered gateway URL as safe (no real DNS in unit tests)."""
    stack = ExitStack()
    stack.enter_context(patch(
        "proxion_messenger_core.network.async_safe_get",
        new=AsyncMock(return_value=well_known_bytes)))
    stack.enter_context(patch(
        "proxion_messenger_core.relay._validate_relay_target", return_value=True))
    return stack

@pytest.mark.asyncio
async def test_discover_peer_gateway_valid(gateway):
    """Valid .well-known response caches gateway URL and x25519 pub."""
    with _patch_fetch(_valid_well_known("did:key:z6MkABCD")):
        result = await gateway._discover_peer_gateway("did:key:z6MkABCD@https://bob.example.com")

    assert result is not None
    assert result["did"] == "did:key:z6MkABCD"
    assert gateway._resolve_peer_gateway("did:key:z6MkABCD") is not None

@pytest.mark.asyncio
async def test_discover_peer_gateway_fingerprint_mismatch(gateway):
    """DID in address must match DID in response; mismatch returns None."""
    with _patch_fetch(_valid_well_known("did:key:z6MkOTHER")):
        result = await gateway._discover_peer_gateway("did:key:z6MkABCD@https://bob.example.com")

    assert result is None

@pytest.mark.asyncio
async def test_discover_peer_gateway_timeout(gateway):
    """Network timeout returns None without raising."""
    with patch("proxion_messenger_core.network.async_safe_get",
               new=AsyncMock(side_effect=NetworkError("timeout"))):
        result = await gateway._discover_peer_gateway("did:key:z6MkABCD@https://bob.example.com")
    assert result is None

@pytest.mark.asyncio
async def test_discover_peer_gateway_caches_x25519(gateway):
    """x25519_pub from .well-known is stored in x25519_pubs table."""
    with _patch_fetch(_valid_well_known("did:key:z6MkABCD")):
        await gateway._discover_peer_gateway("did:key:z6MkABCD@https://bob.example.com")

    stored = gateway._store.get_x25519_pub("did:key:z6MkABCD")
    assert stored is not None

@pytest.mark.asyncio
async def test_discover_peer_command_sends_peer_discovered(gateway):
    """discover_peer WebSocket command returns peer_discovered on success."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    gateway.clients.add(ws)
    gateway._client_webids[ws] = "https://alice.pod/profile/card#me"

    with _patch_fetch(_valid_well_known("did:key:z6MkABCD")):
        await gateway.process_command(ws, {"cmd": "discover_peer", "address": "did:key:z6MkABCD@https://bob.example.com"})

    ws.send.assert_called()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "peer_discovered"
    assert sent["did"] == "did:key:z6MkABCD"
