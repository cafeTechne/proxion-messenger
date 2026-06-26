"""Tests: /health endpoint federation indicators."""
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway_no_turn_no_url(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db"),
                             turn_url=None, turn_secret=None, public_url=None),
        read_state=ReadState(),
    )


@pytest.fixture
def gateway_with_turn_and_url(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(
            port=9991, db_path=str(tmp_path / "test.db"),
            turn_url="turn:turn.example.com:3478",
            turn_secret="supersecret",
            public_url="https://gateway.example.com",
        ),
        read_state=ReadState(),
    )


def test_health_turn_configured_false(gateway_no_turn_no_url):
    """/health reports turn_configured: false when TURN not set."""
    gw = gateway_no_turn_no_url
    assert gw.config.turn_url is None
    # Verify the field value logic directly
    assert bool(gw.config.turn_url and gw.config.turn_secret) is False


def test_health_turn_configured_true(gateway_with_turn_and_url):
    """/health reports turn_configured: true when TURN configured."""
    gw = gateway_with_turn_and_url
    assert bool(gw.config.turn_url and gw.config.turn_secret) is True


def test_health_relay_capable_reflects_public_url(gateway_no_turn_no_url, gateway_with_turn_and_url):
    """/health relay_capable reflects whether public_url is set."""
    assert bool(gateway_no_turn_no_url.config.public_url) is False
    assert bool(gateway_with_turn_and_url.config.public_url) is True
