"""Tests for RelationshipCertificate policy enforcement (Round 5)."""
import pytest
import time
from proxion_messenger_core.federation import RelationshipCertificate, Capability


def _make_cert(**overrides):
    now = int(time.time())
    base = {
        "issuer": "aabbcc",
        "subject": "ddeeff",
        "capabilities": [Capability(with_="stash://test", can="crud/read")],
        "created_at": now,
        "expires_at": now + 86400,
        "certificate_id": "cert-1",
    }
    base.update(overrides)
    return RelationshipCertificate(**base)


class TestCertificatePolicyEnforcement:
    def test_accept_certificate_with_valid_policy_constraints(self):
        cert = _make_cert()
        cert.validate_policy()  # Should not raise

    def test_reject_certificate_with_created_after_expires(self):
        now = int(time.time())
        cert = _make_cert(created_at=now + 1000, expires_at=now)
        with pytest.raises(ValueError, match="invalid_certificate_policy"):
            cert.validate_policy()

    def test_reject_certificate_validity_over_365_days(self):
        now = int(time.time())
        cert = _make_cert(created_at=now, expires_at=now + 366 * 86400)
        with pytest.raises(ValueError, match="certificate_too_long_lived"):
            cert.validate_policy()

    def test_reject_empty_capabilities(self):
        cert = _make_cert(capabilities=[])
        with pytest.raises(ValueError, match="invalid_certificate_policy"):
            cert.validate_policy()

    def test_reject_expired_certificate(self):
        now = int(time.time())
        cert = _make_cert(created_at=now - 7200, expires_at=now - 3600)
        with pytest.raises(ValueError, match="certificate_expired"):
            cert.validate_policy()
