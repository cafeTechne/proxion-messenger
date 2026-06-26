"""Tests: /.well-known/proxion nat_warning field."""
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway_no_public_url(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db"), public_url=None),
        read_state=ReadState(),
    )


@pytest.fixture
def gateway_with_public_url(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(
            port=9991, db_path=str(tmp_path / "test.db"),
            public_url="https://gateway.example.com",
        ),
        read_state=ReadState(),
    )


def test_nat_warning_present_without_public_url(gateway_no_public_url):
    """.well-known/proxion includes nat_warning:true when public_url is None."""
    gw = gateway_no_public_url
    discovery = gw._build_discovery_data()
    assert discovery.get("nat_warning") is True


def test_nat_warning_absent_with_public_url(gateway_with_public_url):
    """.well-known/proxion omits nat_warning when public_url is set."""
    gw = gateway_with_public_url
    discovery = gw._build_discovery_data()
    assert "nat_warning" not in discovery
