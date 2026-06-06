"""Tests: GET /connectivity endpoint."""
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


@pytest.fixture
def gateway_no_public(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db"),
                             public_url=None, upnp_mapped=False),
        read_state=ReadState(),
    )


@pytest.fixture
def gateway_upnp(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9991, db_path=str(tmp_path / "test.db"),
                             public_url="http://203.0.113.5:8080", upnp_mapped=True),
        read_state=ReadState(),
    )


def test_connectivity_fields_without_public_url(gateway_no_public):
    """GatewayConfig reflects no public URL and no UPnP."""
    gw = gateway_no_public
    assert gw.config.public_url is None
    assert gw.config.upnp_mapped is False
    # The fields used by /connectivity endpoint
    assert bool(gw.config.public_url) is False
    assert gw.config.upnp_mapped is False


def test_connectivity_fields_with_upnp(gateway_upnp):
    """GatewayConfig reflects UPnP-mapped public URL."""
    gw = gateway_upnp
    assert gw.config.public_url == "http://203.0.113.5:8080"
    assert gw.config.upnp_mapped is True
    assert bool(gw.config.public_url) is True
