"""Round 1: gateway_url validation in _handle_register (anti-poisoning tests)."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9977), read_state=ReadState(),
    )
    return gw


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    return ws


async def _do_register(gw, ws, gateway_url: str, monkeypatch=None) -> None:
    """Run _handle_register with the given gateway_url."""
    import os
    old = os.environ.get("PROXION_REQUIRE_AUTH")
    os.environ["PROXION_REQUIRE_AUTH"] = "0"
    try:
        await gw._handle_register(ws, {
            "did": "did:key:z6Mktest",
            "gateway_url": gateway_url,
        })
    finally:
        if old is None:
            os.environ.pop("PROXION_REQUIRE_AUTH", None)
        else:
            os.environ["PROXION_REQUIRE_AUTH"] = old


@pytest.mark.asyncio
async def test_register_rejects_gateway_url_with_userinfo(gateway):
    """gateway_url with embedded credentials must be silently rejected."""
    ws = _ws()
    await _do_register(gateway, ws, "wss://user:pass@evil.example.com/ws")
    # The URL must not be cached
    assert "did:key:z6Mktest" not in gateway._peer_gateway_urls or \
           "user:pass" not in gateway._peer_gateway_urls.get("did:key:z6Mktest", "")


@pytest.mark.asyncio
async def test_register_rejects_http_gateway_url(gateway):
    """gateway_url with http:// scheme (not ws/wss) must be silently rejected."""
    ws = _ws()
    await _do_register(gateway, ws, "http://legit.example.com")
    gw_url = gateway._peer_gateway_urls.get("did:key:z6Mktest", "")
    assert not gw_url.startswith("http://")


@pytest.mark.asyncio
async def test_register_rejects_overlong_gateway_url(gateway):
    """gateway_url longer than 512 chars must be silently rejected."""
    ws = _ws()
    long_url = "wss://example.com/" + "a" * 500
    await _do_register(gateway, ws, long_url)
    gw_url = gateway._peer_gateway_urls.get("did:key:z6Mktest", "")
    assert len(gw_url) <= 512


@pytest.mark.asyncio
async def test_register_accepts_valid_wss_gateway_url(monkeypatch, gateway):
    """A well-formed wss:// gateway_url with PROXION_ALLOW_PRIVATE_RELAY=1 is accepted."""
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    ws = _ws()
    await _do_register(gateway, ws, "wss://127.0.0.1:9090/ws")
    gw_url = gateway._peer_gateway_urls.get("did:key:z6Mktest", "")
    assert gw_url == "wss://127.0.0.1:9090/ws"


@pytest.mark.asyncio
async def test_register_rejects_private_gateway_url_without_flag(gateway):
    """Without PROXION_ALLOW_PRIVATE_RELAY=1, private-IP gateway URLs are rejected."""
    import os
    os.environ.pop("PROXION_ALLOW_PRIVATE_RELAY", None)
    ws = _ws()
    await _do_register(gateway, ws, "wss://192.168.1.1:9090/ws")
    gw_url = gateway._peer_gateway_urls.get("did:key:z6Mktest", "")
    assert "192.168" not in gw_url
