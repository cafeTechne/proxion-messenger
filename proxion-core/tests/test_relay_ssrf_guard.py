"""Tests: origin_gateway_url SSRF guard in relay post handler (S2)."""
from __future__ import annotations
import json
import secrets
import pytest
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from unittest.mock import MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def sender_key():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, pub_key_to_did(pub)


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
    return gw


def _make_body(sender_priv, from_did: str, to_did: str, origin_gateway: str) -> bytes:
    ts = datetime.now(timezone.utc).isoformat()
    msg_id = f"ssrf-test-{secrets.token_hex(4)}"
    nonce = secrets.token_hex(8)
    sig = sign_relay_message(sender_priv, from_did, to_did, msg_id, "hi", ts, nonce)
    return json.dumps({
        "from_webid": from_did,
        "to_webid": to_did,
        "message_id": msg_id,
        "content": "hi",
        "timestamp": ts,
        "signature": sig,
        "relay_nonce": nonce,
        "display_name": "Sender",
        "origin_gateway_url": origin_gateway,
    }).encode()


@pytest.mark.asyncio
async def test_private_ip_origin_not_stored(gateway, sender_key):
    """Private IP in origin_gateway_url is not recorded as peer gateway."""
    priv, from_did = sender_key
    to_did = pub_key_to_did(b"\x02" * 32)
    body = _make_body(priv, from_did, to_did, "http://10.0.0.1/relay")

    await gateway._handle_relay_post(body, client_ip="127.0.0.1")

    assert from_did not in gateway._peer_gateway_urls


@pytest.mark.asyncio
async def test_loopback_origin_not_stored(gateway, sender_key):
    """Loopback address in origin_gateway_url is not recorded as peer gateway."""
    priv, from_did = sender_key
    to_did = pub_key_to_did(b"\x03" * 32)
    body = _make_body(priv, from_did, to_did, "http://127.0.0.1:8080/relay")

    await gateway._handle_relay_post(body, client_ip="127.0.0.1")

    assert from_did not in gateway._peer_gateway_urls


@pytest.mark.asyncio
async def test_valid_public_origin_stored(gateway, sender_key):
    """Valid public URL in origin_gateway_url is stored as peer gateway."""
    priv, from_did = sender_key
    to_did = pub_key_to_did(b"\x04" * 32)
    # Use a public domain — _validate_relay_target checks the IP
    # We mock the validation to return True for any https:// URL that isn't private
    from unittest.mock import patch
    body = _make_body(priv, from_did, to_did, "https://alice.example.com")

    with patch("proxion_messenger_core.gateway._is_safe_gateway_url", return_value=True):
        await gateway._handle_relay_post(body, client_ip="127.0.0.1")

    assert gateway._peer_gateway_urls.get(from_did) == "https://alice.example.com"
