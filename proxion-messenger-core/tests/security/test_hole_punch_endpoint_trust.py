"""Tests for STUN endpoint validation (validate_stun_endpoint)."""
import pytest

from proxion_messenger_core.stun_client import validate_stun_endpoint


def test_valid_public_ip_accepted():
    valid, reason = validate_stun_endpoint("203.0.113.42", 54321)
    assert valid is True
    assert reason == ""


def test_another_valid_public_ip():
    valid, reason = validate_stun_endpoint("8.8.8.8", 51820)
    assert valid is True


def test_loopback_rejected():
    valid, reason = validate_stun_endpoint("127.0.0.1", 5000)
    assert valid is False
    assert "loopback" in reason


def test_loopback_range_rejected():
    valid, reason = validate_stun_endpoint("127.255.255.255", 5000)
    assert valid is False


def test_multicast_rejected():
    valid, reason = validate_stun_endpoint("224.0.0.1", 5000)
    assert valid is False
    assert "multicast" in reason


def test_multicast_range_upper_rejected():
    valid, reason = validate_stun_endpoint("239.255.255.255", 5000)
    assert valid is False


def test_link_local_rejected():
    valid, reason = validate_stun_endpoint("169.254.1.1", 5000)
    assert valid is False
    assert "link-local" in reason


def test_unspecified_rejected():
    valid, reason = validate_stun_endpoint("0.0.0.0", 5000)
    assert valid is False
    assert "unspecified" in reason


def test_broadcast_rejected():
    valid, reason = validate_stun_endpoint("255.255.255.255", 5000)
    assert valid is False
    assert "broadcast" in reason


def test_port_zero_rejected():
    valid, reason = validate_stun_endpoint("203.0.113.1", 0)
    assert valid is False
    assert "port" in reason


def test_port_above_65535_rejected():
    valid, reason = validate_stun_endpoint("203.0.113.1", 65536)
    assert valid is False
    assert "port" in reason


def test_invalid_ip_string_rejected():
    valid, reason = validate_stun_endpoint("not-an-ip", 1234)
    assert valid is False
    assert "invalid IP" in reason


def test_port_1_accepted():
    valid, reason = validate_stun_endpoint("198.51.100.5", 1)
    assert valid is True


def test_port_65535_accepted():
    valid, reason = validate_stun_endpoint("198.51.100.5", 65535)
    assert valid is True
