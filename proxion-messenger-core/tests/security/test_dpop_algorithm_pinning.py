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


class TestDpopRequestBinding:
    """Optional htu/htm/ath binding: off by default, fail-closed when supplied."""

    def _bound_payload(self):
        import hashlib
        import base64
        now = int(time.time())
        token = "access-token-xyz"
        ath = base64.urlsafe_b64encode(hashlib.sha256(token.encode()).digest()).rstrip(b"=").decode()
        payload = {
            "iat": now, "exp": now + 60, "jti": "bind-jti-001",
            "htm": "POST", "htu": "https://pod.example/inbox", "ath": ath,
        }
        return payload, token

    def test_no_expectations_preserves_old_behavior(self):
        payload, _ = self._bound_payload()
        validate_dpop_claims(payload)  # no expected_* — should pass

    def test_matching_htu_htm_ath_passes(self):
        payload, token = self._bound_payload()
        validate_dpop_claims(
            payload,
            expected_htu="https://pod.example/inbox#frag",
            expected_htm="post",
            access_token=token,
        )  # should not raise

    def test_wrong_htm_rejected(self):
        payload, _ = self._bound_payload()
        with pytest.raises(ValueError, match="htm"):
            validate_dpop_claims(payload, expected_htm="GET")

    def test_wrong_htu_rejected(self):
        payload, _ = self._bound_payload()
        with pytest.raises(ValueError, match="htu"):
            validate_dpop_claims(payload, expected_htu="https://evil.example/inbox")

    def test_wrong_ath_rejected(self):
        payload, _ = self._bound_payload()
        with pytest.raises(ValueError, match="ath"):
            validate_dpop_claims(payload, access_token="different-token")

    def test_missing_htm_rejected_when_expected(self):
        payload, _ = self._bound_payload()
        del payload["htm"]
        with pytest.raises(ValueError, match="htm"):
            validate_dpop_claims(payload, expected_htm="POST")

    def test_missing_htu_rejected_when_expected(self):
        payload, _ = self._bound_payload()
        del payload["htu"]
        with pytest.raises(ValueError, match="htu"):
            validate_dpop_claims(payload, expected_htu="https://pod.example/inbox")

    def test_missing_ath_rejected_when_expected(self):
        payload, token = self._bound_payload()
        del payload["ath"]
        with pytest.raises(ValueError, match="ath"):
            validate_dpop_claims(payload, access_token=token)
