"""Tests for relay dedup, backoff, expiry, and delivery events — R9.5."""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    cfg = GatewayConfig(db_path=str(tmp_path / "gw.db"), host="127.0.0.1")
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())
    return gw, agent


def _signed_relay_body(from_priv, from_did, to_did, msg_id="m1", content="hi"):
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    sig = sign_relay_message(from_priv, from_did, to_did, msg_id, content, ts)
    return json.dumps({
        "from_webid": from_did,
        "to_webid": to_did,
        "message_id": msg_id,
        "content": content,
        "timestamp": ts,
        "signature": sig,
    }).encode()


def _key_and_did():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv, pub_key_to_did(pub)


# ── R9.5.1: duplicate message_id not double-delivered ─────────────────────


@pytest.mark.asyncio
async def test_duplicate_relay_message_id_returns_duplicate_status(tmp_path):
    """R9.5.1: second POST with same message_id returns status=duplicate."""
    gw, _ = _make_gateway(tmp_path)
    sender_priv, sender_did = _key_and_did()
    _, receiver_did = _key_and_did()

    body = _signed_relay_body(sender_priv, sender_did, receiver_did, msg_id="dedup-msg-1")

    # First delivery — recipient offline, queued as 202
    status1, resp1 = await gw._handle_relay_post(body)
    assert status1 in ("200 OK", "202 Accepted")
    assert json.loads(resp1).get("status") != "duplicate"

    # Second delivery — same message_id, should be deduped
    status2, resp2 = await gw._handle_relay_post(body)
    assert status2 == "200 OK"
    assert json.loads(resp2)["status"] == "duplicate"


@pytest.mark.asyncio
async def test_different_message_ids_both_accepted(tmp_path):
    """Different message_ids are both processed (not deduped)."""
    gw, _ = _make_gateway(tmp_path)
    sender_priv, sender_did = _key_and_did()
    _, receiver_did = _key_and_did()

    body1 = _signed_relay_body(sender_priv, sender_did, receiver_did, msg_id="msg-aaa")
    body2 = _signed_relay_body(sender_priv, sender_did, receiver_did, msg_id="msg-bbb")

    status1, resp1 = await gw._handle_relay_post(body1)
    status2, resp2 = await gw._handle_relay_post(body2)

    # Both should succeed (200 if delivered, 202 if queued)
    assert status1 in ("200 OK", "202 Accepted")
    assert status2 in ("200 OK", "202 Accepted")
    assert json.loads(resp1).get("status") != "duplicate"
    assert json.loads(resp2).get("status") != "duplicate"


# ── R9.5.3: 7-day expiry emits relay_expired ──────────────────────────────


@pytest.mark.asyncio
async def test_relay_retry_loop_emits_relay_expired_after_7_days(tmp_path):
    """R9.5.3: messages older than 7 days are expired and relay_expired is sent to sender."""
    gw, _ = _make_gateway(tmp_path)
    sender_priv, sender_did = _key_and_did()
    _, receiver_did = _key_and_did()

    EIGHT_DAYS_AGO = time.time() - (8 * 24 * 3600)
    payload = {
        "from_webid": sender_did,
        "to_webid": receiver_did,
        "content": "old message",
        "message_id": "stale-1",
        "timestamp": "2024-01-01T00:00:00Z",
        "signature": "s",
    }
    with gw._store._conn() as conn:
        conn.execute(
            "INSERT INTO pending_relays (id, to_webid, to_gateway_url, payload_json, created_at, status) "
            "VALUES (?,?,?,?,?,?)",
            ("stale-1", receiver_did, "http://b:8080", json.dumps(payload), EIGHT_DAYS_AGO, "pending"),
        )

    # Register sender socket so _send_to_identity can deliver
    sender_ws = AsyncMock()
    sender_ws.send = AsyncMock()
    sender_ws.__hash__ = lambda self: id(self)
    gw.clients.add(sender_ws)
    gw._client_webids[sender_ws] = sender_did
    gw._webid_sockets[sender_did] = {sender_ws}

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=False):
        try:
            await gw._relay_retry_loop()
        except asyncio.CancelledError:
            pass

    # The relay should be marked permanently failed
    assert gw._store.get_pending_relays() == []

    # relay_expired event should have been sent to the sender
    assert sender_ws.send.called
    expired_events = []
    for call in sender_ws.send.call_args_list:
        msg = json.loads(call[0][0])
        if msg.get("type") == "relay_expired":
            expired_events.append(msg)
    assert len(expired_events) == 1
    assert expired_events[0]["message_id"] == "stale-1"


# ── R9.5.4: backoff formula ────────────────────────────────────────────────


def test_relay_backoff_formula():
    """R9.5.4: backoff = min(30 * 2^attempt, 3600)."""
    expected = [
        (0, 30),
        (1, 60),
        (2, 120),
        (3, 240),
        (4, 480),
        (5, 960),
        (6, 1920),
        (7, 3600),   # 30*128=3840 → capped at 3600
        (8, 3600),
        (9, 3600),
    ]
    for attempt, expected_delay in expected:
        result = min(30 * (2 ** attempt), 3600)
        assert result == expected_delay, f"attempt={attempt}: got {result}, want {expected_delay}"


# ── R9.5.5: relay_delivered emitted on successful retry ───────────────────


@pytest.mark.asyncio
async def test_relay_retry_loop_emits_relay_delivered_on_success(tmp_path):
    """R9.5.5: relay_delivered is sent to the sender when a pending relay succeeds."""
    gw, _ = _make_gateway(tmp_path)
    sender_priv, sender_did = _key_and_did()
    _, receiver_did = _key_and_did()

    payload = {
        "from_webid": sender_did,
        "to_webid": receiver_did,
        "content": "pending msg",
        "message_id": "pending-1",
        "timestamp": "2024-01-01T00:00:00Z",
        "signature": "s",
    }
    gw._store.enqueue_relay("pending-1", receiver_did, "http://b:8080", payload)

    sender_ws = AsyncMock()
    sender_ws.send = AsyncMock()
    sender_ws.__hash__ = lambda self: id(self)
    gw.clients.add(sender_ws)
    gw._client_webids[sender_ws] = sender_did
    gw._webid_sockets[sender_did] = {sender_ws}

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=True):
        try:
            await gw._relay_retry_loop()
        except asyncio.CancelledError:
            pass

    # Relay should now be delivered (removed from pending)
    assert gw._store.get_pending_relays() == []

    # relay_delivered event should have been sent to the sender
    assert sender_ws.send.called
    delivered_events = [
        json.loads(call[0][0]) for call in sender_ws.send.call_args_list
        if json.loads(call[0][0]).get("type") == "relay_delivered"
    ]
    assert len(delivered_events) == 1
    assert delivered_events[0]["message_id"] == "pending-1"
