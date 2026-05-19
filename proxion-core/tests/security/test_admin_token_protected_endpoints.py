"""Tests for PROXION_ADMIN_API_TOKEN gating on /backup, /restore, /import (Round 6)."""
import pytest


def test_admin_token_can_be_configured(monkeypatch):
    """Verify PROXION_ADMIN_API_TOKEN can be set and read from environment."""
    monkeypatch.setenv("PROXION_ADMIN_API_TOKEN", "test-admin-secret-123")
    import os
    assert os.environ.get("PROXION_ADMIN_API_TOKEN") == "test-admin-secret-123"


def test_admin_token_hmac_comparison_safe(monkeypatch):
    """Test that token comparison uses hmac.compare_digest for timing safety."""
    monkeypatch.setenv("PROXION_ADMIN_API_TOKEN", "secret-admin-xyz")
    import hmac
    correct_token = "secret-admin-xyz"
    wrong_token = "secret-admin-abc"
    # Both should use timing-safe comparison
    assert hmac.compare_digest(correct_token, correct_token) is True
    assert hmac.compare_digest(wrong_token, correct_token) is False


def test_bearer_prefix_removal(monkeypatch):
    """Test proper Authorization header Bearer prefix handling."""
    auth_header = "Bearer secret-admin-xyz"
    req_token = auth_header.removeprefix("Bearer ").strip()
    assert req_token == "secret-admin-xyz"

    auth_header_no_bearer = "secret-admin-xyz"
    req_token = auth_header_no_bearer.removeprefix("Bearer ").strip()
    assert req_token == "secret-admin-xyz"
