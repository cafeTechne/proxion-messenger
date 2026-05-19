"""Tests for Solid-OIDC claim conformance validator (Round 14)."""
import time
import pytest

from proxion_messenger_core.solid_oidc_conformance import (
    validate_id_token_claims,
    OIDC_ISSUER_MISMATCH,
    OIDC_AUDIENCE_MISMATCH,
    OIDC_EXPIRED_TOKEN,
    OIDC_NONCE_MISMATCH,
    OIDC_MISSING_CLAIM,
    OIDC_CLOCK_SKEW,
)


def _valid_claims(**overrides):
    now = time.time()
    base = {
        "iss": "https://idp.example",
        "sub": "https://alice.pod.example/profile/card#me",
        "aud": "my-client-id",
        "exp": now + 3600,
        "iat": now - 5,
        "nonce": "abc123",
    }
    base.update(overrides)
    return base


class TestSolidOidcConformance:
    def test_valid_claims_pass(self):
        claims = _valid_claims()
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id")
        assert result["ok"] is True
        assert result["error_code"] == ""

    def test_rejects_issuer_mismatch(self):
        claims = _valid_claims()
        result = validate_id_token_claims(claims, "https://other.idp.example", "my-client-id")
        assert result["ok"] is False
        assert result["error_code"] == OIDC_ISSUER_MISMATCH

    def test_rejects_audience_mismatch(self):
        claims = _valid_claims()
        result = validate_id_token_claims(claims, "https://idp.example", "wrong-client")
        assert result["ok"] is False
        assert result["error_code"] == OIDC_AUDIENCE_MISMATCH

    def test_rejects_expired_token(self):
        claims = _valid_claims(exp=time.time() - 200)
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id", clock_skew_s=0)
        assert result["ok"] is False
        assert result["error_code"] == OIDC_EXPIRED_TOKEN

    def test_rejects_nonce_mismatch(self):
        claims = _valid_claims(nonce="wrong-nonce")
        result = validate_id_token_claims(
            claims, "https://idp.example", "my-client-id", expected_nonce="abc123"
        )
        assert result["ok"] is False
        assert result["error_code"] == OIDC_NONCE_MISMATCH

    def test_accepts_matching_nonce(self):
        claims = _valid_claims(nonce="abc123")
        result = validate_id_token_claims(
            claims, "https://idp.example", "my-client-id", expected_nonce="abc123"
        )
        assert result["ok"] is True

    def test_nonce_not_checked_when_not_provided(self):
        claims = _valid_claims()
        del claims["nonce"]
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id")
        assert result["ok"] is True

    def test_rejects_missing_required_claim(self):
        claims = _valid_claims()
        del claims["sub"]
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id")
        assert result["ok"] is False
        assert result["error_code"] == OIDC_MISSING_CLAIM

    def test_clock_skew_allows_slightly_expired(self):
        claims = _valid_claims(exp=time.time() - 30)
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id", clock_skew_s=60)
        assert result["ok"] is True

    def test_rejects_future_iat(self):
        claims = _valid_claims(iat=time.time() + 300)
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id", clock_skew_s=0)
        assert result["ok"] is False
        assert result["error_code"] == OIDC_CLOCK_SKEW

    def test_audience_list_accepted(self):
        claims = _valid_claims(aud=["my-client-id", "other-client"])
        result = validate_id_token_claims(claims, "https://idp.example", "my-client-id")
        assert result["ok"] is True
