"""Tests: UPnP module graceful degradation."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from proxion_messenger_core.upnp import try_upnp_map, remove_upnp_map


def test_try_upnp_returns_none_when_miniupnpc_missing():
    """Returns None gracefully when miniupnpc is not installed."""
    with patch.dict("sys.modules", {"miniupnpc": None}):
        result = try_upnp_map(8080)
    assert result is None


def test_try_upnp_returns_none_on_no_devices():
    """Returns None when UPnP discovery finds no IGD devices."""
    mock_upnp = MagicMock()
    mock_upnp.discover.return_value = 0
    mock_miniupnpc = MagicMock()
    mock_miniupnpc.UPnP.return_value = mock_upnp

    with patch.dict("sys.modules", {"miniupnpc": mock_miniupnpc}):
        result = try_upnp_map(8080)

    assert result is None


def test_try_upnp_returns_url_on_success():
    """Returns external URL string when UPnP mapping succeeds."""
    mock_upnp = MagicMock()
    mock_upnp.discover.return_value = 1
    mock_upnp.addportmapping.return_value = True
    mock_upnp.externalipaddress.return_value = "203.0.113.5"
    mock_miniupnpc = MagicMock()
    mock_miniupnpc.UPnP.return_value = mock_upnp

    with patch.dict("sys.modules", {"miniupnpc": mock_miniupnpc}):
        result = try_upnp_map(8080)

    assert result == "http://203.0.113.5:8080"


def test_remove_upnp_silently_succeeds_when_no_devices():
    """remove_upnp_map does not raise even when no IGD is found."""
    mock_upnp = MagicMock()
    mock_upnp.discover.return_value = 0
    mock_miniupnpc = MagicMock()
    mock_miniupnpc.UPnP.return_value = mock_upnp

    with patch.dict("sys.modules", {"miniupnpc": mock_miniupnpc}):
        remove_upnp_map(8080)  # must not raise
