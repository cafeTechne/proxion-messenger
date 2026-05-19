"""Round 2: Relay identity consistency and field format validation."""
import json
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.readstate import ReadState
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=9961, db_path=str(tmp_path / "test.db")),
        read_state=ReadState(),
    )


def _signed_payload(agent, to_webid="did:key:zbob", content="hello", **overrides):
    from_webid = pub_key_to_did(agent.identity_pub_bytes)
    message_id = "msg-abc123"
    ts = datetime.now(timezone.utc).isoformat()
    nonce = "deadbeef"
    sig = sign_relay_message(agent.identity_key, from_webid, to_webid, message_id, content, ts, nonce)
    payload = {
        "from_webid": from_webid,
        "to_webid": to_webid,
        "message_id": message_id,
        "content": content,
        "timestamp": ts,
        "relay_nonce": nonce,
        "signature": sig,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_relay_rejects_sender_from_mismatch(tmp_path):
    """sender_webid != from_webid → 401 sender_identity_mismatch."""
    gw = _make_gateway(tmp_path)
    p = _signed_payload(gw.agent)
    p["sender_webid"] = "did:key:zdifferent"
    body = json.dumps(p).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("401")
    assert "sender_identity_mismatch" in resp


@pytest.mark.asyncio
async def test_relay_accepts_matching_sender_from(tmp_path):
    """sender_webid == from_webid → passes identity check."""
    gw = _make_gateway(tmp_path)
    p = _signed_payload(gw.agent)
    p["sender_webid"] = p["from_webid"]
    body = json.dumps(p).encode()
    status, resp = await gw._handle_relay_post(body)
    # Should not get a 401 for this reason (may get 400 for signature if no socket, that's ok)
    assert not (status.startswith("401") and "sender_identity_mismatch" in resp)


@pytest.mark.asyncio
async def test_relay_rejects_bad_nonce_format(tmp_path):
    """relay_nonce with non-hex chars → 400 invalid_relay_fields."""
    gw = _make_gateway(tmp_path)
    p = _signed_payload(gw.agent)
    p["relay_nonce"] = "not-hex-nonce!"
    body = json.dumps(p).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("400")
    assert "invalid_relay_fields" in resp


@pytest.mark.asyncio
async def test_relay_rejects_bad_message_id_format(tmp_path):
    """message_id with spaces → 400 invalid_relay_fields."""
    gw = _make_gateway(tmp_path)
    p = _signed_payload(gw.agent)
    p["message_id"] = "bad id with spaces"
    body = json.dumps(p).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("400")
    assert "invalid_relay_fields" in resp


@pytest.mark.asyncio
async def test_relay_rejects_timestamp_without_timezone(tmp_path):
    """ISO8601 timestamp without tz info → 400 invalid_relay_fields."""
    gw = _make_gateway(tmp_path)
    p = _signed_payload(gw.agent)
    p["timestamp"] = "2025-01-01T12:00:00"  # no tzinfo
    body = json.dumps(p).encode()
    status, resp = await gw._handle_relay_post(body)
    assert status.startswith("400")
    assert "invalid_relay_fields" in resp
