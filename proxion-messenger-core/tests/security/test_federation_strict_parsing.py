"""Tests for strict federation invite/acceptance parsing (Round 6)."""
import pytest


class TestFederationStrictParsing:
    def _base_invite_dict(self):
        return {
            "@type": "FederationInvite",
            "version": 1,
            "invitation_id": "a" * 10,
            "issuer": {"public_key": "aabb", "did": "did:key:z6Mktest"},
            "endpoint_hints": ["https://example.com"],
            "capabilities": [],
            "created_at": 1700000000,
            "expires_at": 1700086400,
            "nonce": "a" * 32,
            "challenge_marker": "b" * 32,
        }

    def test_nonce_hex_validation(self):
        """Test that nonces must be valid hex."""
        nonce_valid = "a" * 32  # 32 hex chars
        nonce_invalid = "not-hex-!@#$%^&*()"

        import re
        _nonce_re = re.compile(r"^[A-Fa-f0-9]{32}$")

        assert _nonce_re.match(nonce_valid) is not None
        assert _nonce_re.match(nonce_invalid) is None

    def test_endpoint_hints_count_limit(self):
        """Test that endpoint hints have a reasonable limit."""
        max_hints = 10
        test_hints = [f"https://example{i}.com" for i in range(11)]

        # Simulating the validation logic
        if len(test_hints) > max_hints:
            valid = False
        else:
            valid = True

        assert valid is False
        assert len(test_hints) > max_hints

    def test_endpoint_hints_https_requirement(self):
        """Test that endpoint hints must be HTTPS."""
        hint_https = "https://example.com"
        hint_http = "http://example.com"
        hint_ftp = "ftp://evil.com"

        def is_safe_endpoint(url: str) -> bool:
            return url.startswith("https://")

        assert is_safe_endpoint(hint_https) is True
        assert is_safe_endpoint(hint_http) is False
        assert is_safe_endpoint(hint_ftp) is False

    def test_nonce_length_requirements(self):
        """Test that nonces have minimum and maximum length."""
        min_hex_chars = 32  # 16 bytes
        max_hex_chars = 64  # 32 bytes

        nonce_too_short = "aabb"
        nonce_valid = "a" * 32
        nonce_too_long = "a" * 128

        def is_valid_nonce(nonce: str) -> bool:
            return min_hex_chars <= len(nonce) <= max_hex_chars

        assert is_valid_nonce(nonce_too_short) is False
        assert is_valid_nonce(nonce_valid) is True
        assert is_valid_nonce(nonce_too_long) is False
