"""Tests for RelationshipCertificate policy enforcement (Round 5)."""
import pytest
import time
from proxion_messenger_core.federation import (
    RelationshipCertificate,
    Capability,
    MAX_CERT_VALIDITY_SECONDS,
    clamp_cert_expiry,
)
from proxion_messenger_core.local_store import LocalStore


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


# ---------------------------------------------------------------------------
# F7 — max-validity clamp at ingest (save_relationship)
# ---------------------------------------------------------------------------

def test_max_cert_validity_is_365_days():
    assert MAX_CERT_VALIDITY_SECONDS == 365 * 86400


def test_clamp_cert_expiry_caps_over_long():
    created = 1_000_000
    over = created + 999 * 86400
    assert clamp_cert_expiry(created, over) == created + MAX_CERT_VALIDITY_SECONDS


def test_clamp_cert_expiry_leaves_normal_untouched():
    created = 1_000_000
    normal = created + 30 * 86400
    assert clamp_cert_expiry(created, normal) == normal


def test_save_relationship_clamps_far_future_expiry(tmp_path):
    store = LocalStore(str(tmp_path / "t.db"))
    now = int(time.time())
    # A cert claiming expiry in the year 9999 must be clamped to <= created + 365d.
    far_future = now + 500 * 365 * 86400
    store.save_relationship(
        {
            "certificate_id": "cert-far",
            "subject": "aa" * 32,
            "created_at": now,
            "expires_at": far_future,
        },
        peer_did="did:key:zFar",
        owner_webid="",
    )
    row = store.get_relationship_by_cert_id("cert-far")
    assert row is not None
    with store._conn() as conn:
        stored = conn.execute(
            "SELECT expires_at FROM relationships WHERE certificate_id=?",
            ("cert-far",),
        ).fetchone()["expires_at"]
    assert stored <= now + MAX_CERT_VALIDITY_SECONDS
    assert stored == now + MAX_CERT_VALIDITY_SECONDS


def test_save_relationship_keeps_normal_expiry(tmp_path):
    store = LocalStore(str(tmp_path / "t.db"))
    now = int(time.time())
    normal = now + 30 * 86400
    store.save_relationship(
        {
            "certificate_id": "cert-ok",
            "subject": "bb" * 32,
            "created_at": now,
            "expires_at": normal,
        },
        peer_did="did:key:zOk",
        owner_webid="",
    )
    with store._conn() as conn:
        stored = conn.execute(
            "SELECT expires_at FROM relationships WHERE certificate_id=?",
            ("cert-ok",),
        ).fetchone()["expires_at"]
    assert stored == normal


def test_save_relationship_expired_still_rejected_at_use(tmp_path):
    store = LocalStore(str(tmp_path / "t.db"))
    now = int(time.time())
    store.save_relationship(
        {
            "certificate_id": "cert-exp",
            "subject": "cc" * 32,
            "created_at": now - 7200,
            "expires_at": now - 3600,
        },
        peer_did="did:key:zExp",
        owner_webid="",
    )
    # The clamp never resurrects an expired cert: the at-use filters still exclude it.
    assert store.get_relationship_by_cert_id("cert-exp") is None
    assert store.get_relationship_by_did("did:key:zExp") is None
