"""Tests for certificate expiry enforcement in validate_request."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import fingerprint, issue_token, sign_challenge, validate_request
from proxion_messenger_core.context import RequestContext
from proxion_messenger_core.federation import Capability, RelationshipCertificate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cert(expires_offset_s: int = 3600) -> RelationshipCertificate:
    issuer_priv = Ed25519PrivateKey.generate()
    subject_priv = Ed25519PrivateKey.generate()
    cert = RelationshipCertificate(
        issuer=issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        subject=subject_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        capabilities=[Capability(with_="/data/", can="read")],
        wireguard={},
    )
    cert.expires_at = int(time.time()) + expires_offset_s
    return cert


def _make_token_env(now: datetime, ttl_s: int = 3600, perms=None):
    holder_priv = Ed25519PrivateKey.generate()
    holder_pub = holder_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sk = os.urandom(32)
    perms = perms or [("read", "/data/file.txt")]
    token = issue_token(
        permissions=perms,
        exp=now + timedelta(seconds=ttl_s),
        aud="svc",
        caveats=[],
        holder_key_fingerprint=fingerprint(holder_pub),
        signing_key=sk,
        now=now,
    )
    ctx = RequestContext(
        action=perms[0][0],
        resource=perms[0][1],
        aud="svc",
        now=now,
        device_nonce="n1",
    )
    proof = sign_challenge(holder_priv, token.token_id, "n1")
    return token, sk, ctx, proof


# ---------------------------------------------------------------------------
# validate_request without cert (existing behaviour unchanged)
# ---------------------------------------------------------------------------

def test_validate_no_cert_allowed():
    now = datetime.now(timezone.utc)
    token, sk, ctx, proof = _make_token_env(now)
    assert validate_request(token, ctx, proof, sk).allowed


def test_validate_no_cert_expired_token_denied():
    # Issue with now 10s in the past + 5s TTL → token expired by real-now
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    token, sk, _ctx, proof = _make_token_env(past, ttl_s=5)
    now = datetime.now(timezone.utc)
    ctx = RequestContext(action=_ctx.action, resource=_ctx.resource, aud=_ctx.aud,
                        now=now, device_nonce="n1")
    result = validate_request(token, ctx, proof, sk)
    assert not result.allowed
    assert result.reason == "expired"


# ---------------------------------------------------------------------------
# validate_request with a valid (not yet expired) cert
# ---------------------------------------------------------------------------

def test_valid_cert_still_allowed():
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=+3600)
    token, sk, ctx, proof = _make_token_env(now)
    result = validate_request(token, ctx, proof, sk, cert=cert)
    assert result.allowed


def test_valid_cert_does_not_change_existing_denial():
    """A fresh cert doesn't grant access if the token itself is expired."""
    cert = _make_cert(expires_offset_s=+3600)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    token, sk, _ctx, proof = _make_token_env(past, ttl_s=5)
    now = datetime.now(timezone.utc)
    ctx = RequestContext(action=_ctx.action, resource=_ctx.resource, aud=_ctx.aud,
                        now=now, device_nonce="n1")
    result = validate_request(token, ctx, proof, sk, cert=cert)
    assert not result.allowed
    assert result.reason == "expired"


# ---------------------------------------------------------------------------
# validate_request with an expired cert
# ---------------------------------------------------------------------------

def test_expired_cert_denies_valid_token():
    """Even a structurally valid token must be denied if its backing cert is expired."""
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=-1)  # expired 1 second ago
    token, sk, ctx, proof = _make_token_env(now)
    result = validate_request(token, ctx, proof, sk, cert=cert)
    assert not result.allowed
    assert result.reason == "cert_expired"


def test_expired_cert_reason_is_cert_expired():
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=-3600)  # expired 1 hour ago
    token, sk, ctx, proof = _make_token_env(now)
    result = validate_request(token, ctx, proof, sk, cert=cert)
    assert result.reason == "cert_expired"


def test_cert_expiry_checked_before_revocation():
    """cert_expired should be returned even when a revocation list is also present."""
    from proxion_messenger_core import RevocationList
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=-1)
    token, sk, ctx, proof = _make_token_env(now)
    rl = RevocationList()  # empty — no revocations
    result = validate_request(token, ctx, proof, sk, revocation_list=rl, cert=cert)
    assert not result.allowed
    assert result.reason == "cert_expired"


def test_cert_expiry_exact_boundary():
    """expires_at == now is expired (>= check)."""
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=0)
    # Set cert.expires_at to exactly now (as unix timestamp)
    cert.expires_at = int(now.timestamp())
    token, sk, ctx, proof = _make_token_env(now)
    result = validate_request(token, ctx, proof, sk, cert=cert)
    assert not result.allowed
    assert result.reason == "cert_expired"


def test_cert_one_second_future_allowed():
    """expires_at one second in the future is still valid."""
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=+1)
    token, sk, ctx, proof = _make_token_env(now)
    result = validate_request(token, ctx, proof, sk, cert=cert)
    assert result.allowed


# ---------------------------------------------------------------------------
# certtoken.issue_from_certificate — expiry enforcement at issuance
# ---------------------------------------------------------------------------

def test_issue_from_expired_cert_raises():
    from proxion_messenger_core.certtoken import issue_from_certificate, CertTokenError
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=-1)
    holder_priv = Ed25519PrivateKey.generate()
    sk = os.urandom(32)
    with pytest.raises(CertTokenError, match="expired"):
        issue_from_certificate(
            cert=cert,
            requested_permissions=[("read", "/data/")],
            holder_pub_key=holder_priv.public_key(),
            signing_key=sk,
            now=now,
        )


def test_issue_from_valid_cert_succeeds():
    from proxion_messenger_core.certtoken import issue_from_certificate
    now = datetime.now(timezone.utc)
    cert = _make_cert(expires_offset_s=+3600)
    holder_priv = Ed25519PrivateKey.generate()
    sk = os.urandom(32)
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "/data/")],
        holder_pub_key=holder_priv.public_key(),
        signing_key=sk,
        now=now,
    )
    assert token is not None
    assert token.exp <= datetime.fromtimestamp(cert.expires_at, tz=timezone.utc)
