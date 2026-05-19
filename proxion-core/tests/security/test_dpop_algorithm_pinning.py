"""Tests for DPoP algorithm pinning (Round 6)."""
import pytest
from proxion_messenger_core.dpop import validate_dpop_claims
import time


class TestDpopAlgorithmPinning:
    def _base_payload(self):
        now = int(time.time())
        return {"iat": now, "exp": now + 60, "jti": "test-jti-001"}

    def test_accept_default_eddsa_ed25519_proof(self):
        payload = self._base_payload()
        header = {"alg": "EdDSA", "jwk": {"kty": "OKP", "crv": "Ed25519", "x": "abc"}}
        validate_dpop_claims(payload, header=header)  # should not raise

    def test_reject_non_eddsa_alg(self):
        payload = self._base_payload()
        header = {"alg": "RS256", "jwk": {"kty": "RSA", "crv": "Ed25519", "x": "abc"}}
        with pytest.raises(ValueError, match="unsupported_dpop_algorithm"):
            validate_dpop_claims(payload, header=header)

    def test_reject_non_ed25519_curve(self):
        payload = self._base_payload()
        header = {"alg": "EdDSA", "jwk": {"kty": "OKP", "crv": "Ed448", "x": "abc"}}
        with pytest.raises(ValueError, match="unsupported_dpop_algorithm"):
            validate_dpop_claims(payload, header=header)

    def test_reject_wrong_kty(self):
        payload = self._base_payload()
        header = {"alg": "EdDSA", "jwk": {"kty": "RSA", "crv": "Ed25519", "x": "abc"}}
        with pytest.raises(ValueError, match="unsupported_dpop_algorithm"):
            validate_dpop_claims(payload, header=header)

    def test_no_header_skips_alg_check(self):
        payload = self._base_payload()
        validate_dpop_claims(payload)  # no header — should pass
