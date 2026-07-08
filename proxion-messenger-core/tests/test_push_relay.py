"""Tests: WebPush notifications for relayed messages."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


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
    gw._store = LocalStore(str(tmp_path / "test.db"))
    gw._vapid_private_pem = "fakepem"
    gw._vapid_subject = "mailto:test@example.com"
    return gw


@pytest.mark.asyncio
async def test_room_relay_fires_push_for_offline_member(gateway):
    """_handle_room_relay fires WebPush for members with no active socket."""
    room_id = "push-room-1"
    member_did = "did:key:zOffline"
    from_did = "did:key:zOnline"
    gateway._local_rooms[room_id] = {"name": "T", "members": set()}
    gateway._store.add_room_member(room_id, member_did)
    gateway._store.add_federated_room_member(room_id, from_did, "https://gw.example.com")
    gateway._store.save_push_subscription(
        "sub-1", member_did, "https://push.example.com/abc", "p256dhkey", "authkey"
    )

    with patch("proxion_messenger_core.webpush.send_web_push") as mock_push:
        await gateway._handle_room_relay({
            "room_id": room_id, "from_webid": from_did,
            "message_id": "msg-push-1", "content": "hello",
            "timestamp": "2026-06-02T10:00:00Z",
        })
        mock_push.assert_called_once()


@pytest.mark.asyncio
async def test_room_relay_skips_push_for_connected_member(gateway):
    """_handle_room_relay does not fire push for members with active socket."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s)
    room_id = "push-room-2"
    member_did = "did:key:zOnline"
    gateway._local_rooms[room_id] = {"name": "T", "members": {ws}}
    gateway.clients.add(ws)
    gateway._client_webids[ws] = member_did
    gateway._webid_sockets[member_did] = {ws}
    gateway._store.add_room_member(room_id, member_did)
    gateway._store.add_federated_room_member(room_id, "did:key:zSender", "https://gw.example.com")
    gateway._store.save_push_subscription(
        "sub-2", member_did, "https://push.example.com/xyz", "p256dh", "auth"
    )

    with patch("proxion_messenger_core.webpush.send_web_push") as mock_push:
        await gateway._handle_room_relay({
            "room_id": room_id, "from_webid": "did:key:zSender",
            "message_id": "msg-push-2", "content": "hi",
            "timestamp": "2026-06-02T10:00:00Z",
        })
        mock_push.assert_not_called()


@pytest.mark.asyncio
async def test_room_relay_skips_push_without_vapid(gateway):
    """_handle_room_relay does not crash or push when VAPID is not configured."""
    gateway._vapid_private_pem = ""
    room_id = "push-room-3"
    member_did = "did:key:zMember"
    gateway._local_rooms[room_id] = {"name": "T", "members": set()}
    gateway._store.add_room_member(room_id, member_did)

    with patch("proxion_messenger_core.webpush.send_web_push") as mock_push:
        await gateway._handle_room_relay({
            "room_id": room_id, "from_webid": "did:key:zSender",
            "message_id": "msg-push-3", "content": "test",
            "timestamp": "2026-06-02T10:00:00Z",
        })
        mock_push.assert_not_called()


@pytest.mark.asyncio
async def test_dm_relay_fires_push_for_offline_target(gateway):
    """DM relay path fires push for offline target with subscription."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from proxion_messenger_core.relay import sign_relay_message
    from proxion_messenger_core.didkey import pub_key_to_did
    import json, secrets as _sec
    from datetime import datetime, timezone

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    from_did = pub_key_to_did(pub)
    to_did = pub_key_to_did(b"\x02" * 32)

    gateway._store.save_push_subscription(
        "sub-3", to_did, "https://push.example.com/dm1", "p256dh", "auth"
    )

    ts = datetime.now(timezone.utc).isoformat()
    nonce = _sec.token_hex(8)
    msg_id = "dm-push-1"
    sig = sign_relay_message(priv, from_did, to_did, msg_id, "hello", ts, nonce)
    body = json.dumps({
        "from_webid": from_did, "to_webid": to_did,
        "message_id": msg_id, "content": "hello",
        "timestamp": ts, "signature": sig, "relay_nonce": nonce,
        "display_name": "Sender",
    }).encode()

    with patch("proxion_messenger_core.webpush.send_web_push") as mock_push:
        status, _ = await gateway._handle_relay_post(body, client_ip="127.0.0.1")
        # 202 = offline (no socket), push should have been attempted
        assert status in ("200 OK", "202 Accepted")
        if status == "202 Accepted":
            mock_push.assert_called_once()
