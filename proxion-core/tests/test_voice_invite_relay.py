"""Tests: voice invite attempts relay before pod fallback."""
from __future__ import annotations
import asyncio
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
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    store = MagicMock()
    store.get_relationship_by_did = MagicMock(return_value={"certificate_id": "cert-ok"})
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )
    gw._store = store
    return gw


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    ws.__eq__ = lambda s, o: s is o
    return ws


@pytest.mark.asyncio
async def test_voice_invite_relay_attempted_when_gateway_known(gateway):
    """_handle_voice_invite tries relay when peer gateway URL is cached."""
    caller_ws = _ws()
    target_webid = "did:key:zCallee"
    caller_webid = "did:key:zCaller"
    gateway._client_webids[caller_ws] = caller_webid
    gateway._peer_gateway_urls[target_webid] = "https://callee.example.com"

    with patch.object(gateway, "_relay_voice_signal", new_callable=AsyncMock, return_value=True) as mock_relay:
        await gateway._handle_voice_invite(caller_ws, {
            "session_id": "sess-inv-1",
            "sdp_offer": "v=0\r\n...",
            "target_webid": target_webid,
        })

    mock_relay.assert_called_once()
    args = mock_relay.call_args[0]
    assert args[0] == target_webid
    assert args[1] == "offer"
    assert args[2]["session_id"] == "sess-inv-1"


@pytest.mark.asyncio
async def test_voice_invite_pod_fallback_when_relay_fails(gateway):
    """_handle_voice_invite falls back to pod when relay returns False."""
    caller_ws = _ws()
    target_webid = "did:key:zCallee2"
    caller_webid = "did:key:zCaller2"
    gateway._client_webids[caller_ws] = caller_webid
    gateway._peer_gateway_urls[target_webid] = "https://callee2.example.com"

    with patch.object(gateway, "_relay_voice_signal", new_callable=AsyncMock, return_value=False) as mock_relay:
        await gateway._handle_voice_invite(caller_ws, {
            "session_id": "sess-inv-2",
            "sdp_offer": "v=0\r\n...",
            "target_webid": target_webid,
        })

    mock_relay.assert_called_once()


@pytest.mark.asyncio
async def test_voice_invite_relay_skips_pod_when_relay_succeeds(gateway):
    """_handle_voice_invite skips pod write when relay returns True."""
    caller_ws = _ws()
    target_webid = "did:key:zCallee3"
    caller_webid = "did:key:zCaller3"
    gateway._client_webids[caller_ws] = caller_webid
    gateway._peer_gateway_urls[target_webid] = "https://callee3.example.com"

    async def fake_relay(webid, signal_type, data):
        return True

    with patch.object(gateway, "_relay_voice_signal", side_effect=fake_relay):
        with patch("proxion_messenger_core.voice.signal_voice_invite") as mock_pod:
            await gateway._handle_voice_invite(caller_ws, {
                "session_id": "sess-inv-3",
                "sdp_offer": "v=0\r\n...",
                "target_webid": target_webid,
            })
            mock_pod.assert_not_called()
