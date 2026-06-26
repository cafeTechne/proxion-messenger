"""Tests for B1 (pod reconnection watchdog) and B2 (broadcast_to_room)."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _make_gateway(**cfg_kwargs):
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    config = GatewayConfig(**cfg_kwargs)
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=config,
        read_state=ReadState(),
    )


# ── _pod_available flag ────────────────────────────────────────────────────────

def test_pod_available_starts_false():
    gw = _make_gateway()
    assert gw._pod_available is False


@pytest.mark.asyncio
async def test_setup_pod_connection_sets_available_on_success():
    gw = _make_gateway(
        css_url="https://pod.example.com",
        css_email="alice@example.com",
        css_password="secret",
    )

    def fake_connect(css_url, email, password):
        gw._pod_url = "https://pod.example.com/alice/"
        gw._pod_webid = "https://pod.example.com/alice/profile/card#me"
        return MagicMock(), gw._pod_url, gw._pod_webid

    with patch.object(gw, "_connect_css_sync", side_effect=fake_connect):
        await gw._setup_pod_connection()

    assert gw._pod_available is True


@pytest.mark.asyncio
async def test_setup_pod_connection_leaves_available_false_on_failure():
    gw = _make_gateway(
        css_url="https://pod.example.com",
        css_email="alice@example.com",
        css_password="bad",
    )
    with patch.object(gw, "_connect_css_sync", side_effect=Exception("refused")):
        await gw._setup_pod_connection()

    assert gw._pod_available is False


# ── _pod_health_check ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pod_health_check_returns_true_on_2xx():
    gw = _make_gateway()
    gw._pod_url = "https://pod.example.com/alice/"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("proxion_messenger_core.gateway.httpx.AsyncClient", return_value=mock_client):
        result = await gw._pod_health_check()

    assert result is True


@pytest.mark.asyncio
async def test_pod_health_check_returns_false_on_5xx():
    gw = _make_gateway()
    gw._pod_url = "https://pod.example.com/alice/"

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("proxion_messenger_core.gateway.httpx.AsyncClient", return_value=mock_client):
        result = await gw._pod_health_check()

    assert result is False


@pytest.mark.asyncio
async def test_pod_health_check_returns_false_on_connect_error():
    import httpx
    gw = _make_gateway()
    gw._pod_url = "https://pod.example.com/alice/"

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("proxion_messenger_core.gateway.httpx.AsyncClient", return_value=mock_client):
        result = await gw._pod_health_check()

    assert result is False


@pytest.mark.asyncio
async def test_pod_health_check_false_when_no_pod_url():
    gw = _make_gateway()
    result = await gw._pod_health_check()
    assert result is False


# ── _pod_watchdog broadcasts ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pod_watchdog_broadcasts_unavailable_when_pod_drops():
    """Watchdog detects pod going offline and broadcasts pod_status available=false."""
    gw = _make_gateway(
        css_url="https://pod.example.com",
        css_email="alice@example.com",
        css_password="secret",
    )
    gw._pod_url = "https://pod.example.com/alice/"
    gw._pod_available = True  # simulates pod was working

    broadcast_calls = []
    sleep_count = 0

    async def fake_broadcast(event):
        broadcast_calls.append(event)

    async def fake_sleep(_delay):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch.object(gw, "_pod_health_check", new_callable=AsyncMock, return_value=False), \
         patch.object(gw, "_setup_pod_connection", new_callable=AsyncMock), \
         patch.object(gw, "broadcast", side_effect=fake_broadcast), \
         patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gw._pod_watchdog()
        except asyncio.CancelledError:
            pass

    unavailable_events = [e for e in broadcast_calls if e.get("available") is False]
    assert len(unavailable_events) >= 1


@pytest.mark.asyncio
async def test_pod_watchdog_broadcasts_available_on_recovery():
    """Watchdog detects pod coming back and broadcasts pod_status available=true."""
    gw = _make_gateway(
        css_url="https://pod.example.com",
        css_email="alice@example.com",
        css_password="secret",
    )
    gw._pod_url = "https://pod.example.com/alice/"
    gw._pod_available = False  # simulates pod was already down

    broadcast_calls = []
    sleep_count = 0

    async def fake_broadcast(event):
        broadcast_calls.append(event)

    async def fake_sleep(_delay):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    async def fake_setup_pod():
        gw._pod_available = True

    with patch.object(gw, "_pod_health_check", new_callable=AsyncMock, return_value=False), \
         patch.object(gw, "_setup_pod_connection", side_effect=fake_setup_pod), \
         patch.object(gw, "broadcast", side_effect=fake_broadcast), \
         patch("asyncio.sleep", side_effect=fake_sleep):
        try:
            await gw._pod_watchdog()
        except asyncio.CancelledError:
            pass

    available_events = [e for e in broadcast_calls if e.get("available") is True]
    assert len(available_events) >= 1


@pytest.mark.asyncio
async def test_pod_watchdog_exits_immediately_when_not_configured():
    """Watchdog is a no-op when no pod is configured."""
    gw = _make_gateway()  # no css_url/email/password, no _pod_url
    broadcast_calls = []

    with patch.object(gw, "broadcast", side_effect=lambda e: broadcast_calls.append(e)):
        await gw._pod_watchdog()  # should return without sleeping

    assert broadcast_calls == []


# ── broadcast_to_room ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_to_room_sends_only_to_room_members():
    """broadcast_to_room delivers only to the target room's websockets."""
    gw = _make_gateway()

    ws_alice = MagicMock()
    ws_alice.send = AsyncMock()
    ws_bob = MagicMock()
    ws_bob.send = AsyncMock()
    ws_carol = MagicMock()
    ws_carol.send = AsyncMock()

    gw._local_rooms["room-A"] = {"members": {ws_alice, ws_bob}, "name": "A"}
    gw._local_rooms["room-B"] = {"members": {ws_carol}, "name": "B"}

    event = {"type": "test_event", "content": "hello"}
    await gw.broadcast_to_room("room-A", event)

    ws_alice.send.assert_awaited_once_with(json.dumps(event))
    ws_bob.send.assert_awaited_once_with(json.dumps(event))
    ws_carol.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_to_room_noop_for_unknown_room():
    """broadcast_to_room silently does nothing for a non-existent room."""
    gw = _make_gateway()
    ws = MagicMock()
    ws.send = AsyncMock()

    await gw.broadcast_to_room("nonexistent-room", {"type": "test"})

    ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_to_room_skips_broken_sockets():
    """broadcast_to_room tolerates send() raising and continues to other members."""
    gw = _make_gateway()

    ws_broken = MagicMock()
    ws_broken.send = AsyncMock(side_effect=Exception("connection closed"))
    ws_good = MagicMock()
    ws_good.send = AsyncMock()

    gw._local_rooms["room-X"] = {"members": {ws_broken, ws_good}, "name": "X"}

    event = {"type": "test"}
    await gw.broadcast_to_room("room-X", event)

    ws_good.send.assert_awaited_once()
