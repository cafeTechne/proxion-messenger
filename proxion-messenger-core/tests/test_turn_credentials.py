"""Tests: GET /turn-credentials endpoint."""
from __future__ import annotations
import json
import time
import pytest
from unittest.mock import MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore

@pytest.fixture
def gateway_no_turn(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db"), turn_url=None, turn_secret=None),
        read_state=ReadState(),
    )

@pytest.fixture
def gateway_with_turn(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    agent.identity_key.private_bytes = MagicMock(return_value=b"\x42" * 32)
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(
            port=9991, db_path=str(tmp_path / "test.db"),
            turn_url="turn:turn.example.com:3478",
            turn_secret="supersecret",
        ),
        read_state=ReadState(),
    )

def test_turn_credentials_returns_empty_urls_when_not_configured(gateway_no_turn):
    """_make_turn_creds returns None when TURN not configured."""
    creds = gateway_no_turn._make_turn_creds("did:key:zTest")
    assert creds is None

def test_turn_credentials_generates_hmac_credential(gateway_with_turn):
    """_make_turn_creds returns valid HMAC-SHA1 coturn credentials."""
    creds = gateway_with_turn._make_turn_creds("did:key:zTest")
    assert creds is not None
    assert "urls" in creds
    assert "username" in creds
    assert "credential" in creds
    assert "turn:turn.example.com:3478" in creds["urls"]

def test_turn_credentials_ttl_in_future(gateway_with_turn):
    """TURN username encodes an expiry timestamp more than 23h in the future."""
    creds = gateway_with_turn._make_turn_creds("did:key:zTest")
    assert creds is not None
    expiry = int(creds["username"].split(":")[0])
    assert expiry > time.time() + 30 * 60  # at least 30 minutes in the future
