"""Tests for Layer 1 — public deployment config (env vars, SSL config, public_url)."""
import os
import pytest
from unittest.mock import MagicMock, patch

from proxion_messenger_core.gateway import GatewayConfig


def test_config_host_default_is_all_interfaces():
    """Default host should bind to all interfaces for public deployment."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PROXION_HOST", None)
        cfg = GatewayConfig()
        assert cfg.host == "0.0.0.0"


def test_config_host_from_env():
    with patch.dict(os.environ, {"PROXION_HOST": "192.168.1.1"}):
        cfg = GatewayConfig()
        assert cfg.host == "192.168.1.1"


def test_config_port_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PROXION_PORT", None)
        cfg = GatewayConfig()
        assert cfg.port == 7474


def test_config_port_from_env():
    with patch.dict(os.environ, {"PROXION_PORT": "8765"}):
        cfg = GatewayConfig()
        assert cfg.port == 8765


def test_config_ssl_defaults_to_none():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PROXION_SSL_CERT", None)
        os.environ.pop("PROXION_SSL_KEY", None)
        cfg = GatewayConfig()
        assert cfg.ssl_certfile is None
        assert cfg.ssl_keyfile is None


def test_config_ssl_from_env():
    with patch.dict(os.environ, {
        "PROXION_SSL_CERT": "/etc/certs/fullchain.pem",
        "PROXION_SSL_KEY": "/etc/certs/privkey.pem",
    }):
        cfg = GatewayConfig()
        assert cfg.ssl_certfile == "/etc/certs/fullchain.pem"
        assert cfg.ssl_keyfile == "/etc/certs/privkey.pem"


def test_config_public_url_from_env():
    with patch.dict(os.environ, {"PROXION_PUBLIC_URL": "wss://chat.example.com"}):
        cfg = GatewayConfig()
        assert cfg.public_url == "wss://chat.example.com"


def test_config_css_credentials_from_env():
    with patch.dict(os.environ, {
        "PROXION_CSS_URL": "https://pod.example.com",
        "PROXION_CSS_EMAIL": "alice@example.com",
        "PROXION_CSS_PASSWORD": "secret123",
    }):
        cfg = GatewayConfig()
        assert cfg.css_url == "https://pod.example.com"
        assert cfg.css_email == "alice@example.com"
        assert cfg.css_password == "secret123"


def test_ws_public_url_uses_public_url_if_set():
    from unittest.mock import MagicMock
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    from proxion_messenger_core.gateway import ProxionGateway
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(port=7474, public_url="wss://chat.example.com"),
        read_state=ReadState(),
    )
    assert gw._ws_public_url() == "wss://chat.example.com"


def _gw_with(**cfg):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    from proxion_messenger_core.gateway import ProxionGateway
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    return ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(**cfg), read_state=ReadState(),
    )


def test_ws_public_url_upgrades_ws_to_wss_when_gateway_terminates_tls():
    # A ws:// public_url is unusable when the gateway serves the UI over https
    # (the secure page can't open an insecure socket — mixed content). Upgrade it.
    gw = _gw_with(public_url="ws://localhost:7474",
                  ssl_certfile="/x/cert.pem", ssl_keyfile="/x/key.pem")
    assert gw._ws_public_url() == "wss://localhost:7474"


def test_ws_public_url_leaves_ws_untouched_without_gateway_tls():
    # Reverse-proxy / plain-local: gateway has no TLS of its own, leave ws:// alone.
    gw = _gw_with(public_url="ws://localhost:7474",
                  ssl_certfile=None, ssl_keyfile=None)
    assert gw._ws_public_url() == "ws://localhost:7474"


def test_ws_public_url_leaves_explicit_wss_untouched():
    gw = _gw_with(public_url="wss://chat.example.com",
                  ssl_certfile=None, ssl_keyfile=None)
    assert gw._ws_public_url() == "wss://chat.example.com"


def test_ws_public_url_falls_back_to_ws_scheme():
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    from proxion_messenger_core.gateway import ProxionGateway
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(host="0.0.0.0", port=7474),
        read_state=ReadState(),
    )
    url = gw._ws_public_url()
    assert url.startswith("ws://")
    assert "7474" in url


def test_make_ssl_context_returns_none_without_certs():
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x00" * 32
    from proxion_messenger_core.gateway import ProxionGateway
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(),
        read_state=ReadState(),
    )
    assert gw._make_ssl_context() is None
