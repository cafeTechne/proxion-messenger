"""Tests for proxion_messenger_core.validator — validate_request with real PoP."""

import os
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import (
    RevocationList,
    fingerprint,
    issue_token,
    sign_challenge,
    validate_request,
)
from proxion_messenger_core.context import Caveat, RequestContext


def ip_allowlist(allowed: set) -> Caveat:
    return Caveat(id=f"ip:{','.join(sorted(allowed))}", predicate=lambda ctx: ctx.ip in allowed)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_env(perms=None, aud="svc", ttl=3600, caveats=None):
    holder_priv = Ed25519PrivateKey.generate()
    holder_pub  = holder_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sk   = os.urandom(32)
    now  = datetime.now(timezone.utc)
    exp  = now + timedelta(seconds=ttl)
    perms = perms or [("read", "/data/")]
    token = issue_token(
        permissions=perms,
        exp=exp,
        aud=aud,
        caveats=caveats or [],
        holder_key_fingerprint=fingerprint(holder_pub),
        signing_key=sk,
        now=now,
    )
    ctx = RequestContext(
        action=perms[0][0],
        resource=perms[0][1],
        aud=aud,
        now=now,
        device_nonce="req-1",
    )
    return token, holder_priv, sk, now, ctx


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_request_allowed():
    token, holder, sk, now, ctx = _make_env()
    proof = sign_challenge(holder, token.token_id, "req-1")
    assert validate_request(token, ctx, proof, sk).allowed


def test_allows_sub_path_of_permitted_prefix():
    token, holder, sk, now, _ = _make_env(perms=[("read", "/data/")])
    ctx = RequestContext("read", "/data/photos/img.jpg", "svc", now, device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    assert validate_request(token, ctx, proof, sk).allowed


def test_allows_wildcard_root():
    token, holder, sk, now, _ = _make_env(perms=[("read", "/")])
    ctx = RequestContext("read", "/anything/at/all", "svc", now, device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    assert validate_request(token, ctx, proof, sk).allowed


def test_allows_exact_resource():
    token, holder, sk, now, _ = _make_env(perms=[("read", "/exact")])
    ctx = RequestContext("read", "/exact", "svc", now, device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    assert validate_request(token, ctx, proof, sk).allowed


# ---------------------------------------------------------------------------
# Denial cases
# ---------------------------------------------------------------------------

def test_expired_token_denied():
    token, holder, sk, now, _ = _make_env(ttl=1)
    ctx = RequestContext("read", "/data/", "svc", now + timedelta(seconds=10), device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk)
    assert not d.allowed and d.reason == "expired"


def test_audience_mismatch_denied():
    token, holder, sk, now, _ = _make_env(aud="svc-A")
    ctx = RequestContext("read", "/data/", "svc-B", now, device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk)
    assert not d.allowed and d.reason == "audience_mismatch"


def test_wrong_action_denied():
    token, holder, sk, now, _ = _make_env(perms=[("read", "/data/")])
    ctx = RequestContext("write", "/data/", "svc", now, device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk)
    assert not d.allowed and d.reason == "permission_missing"


def test_resource_outside_prefix_denied():
    token, holder, sk, now, _ = _make_env(perms=[("read", "/data/")])
    ctx = RequestContext("read", "/other/file", "svc", now, device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    assert not validate_request(token, ctx, proof, sk).allowed


def test_wrong_holder_key_denied():
    token, _, sk, now, ctx = _make_env()
    proof = sign_challenge(Ed25519PrivateKey.generate(), token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk)
    assert not d.allowed and d.reason == "invalid_proof"


def test_none_proof_denied():
    token, _, sk, now, ctx = _make_env()
    assert not validate_request(token, ctx, None, sk).allowed


def test_dict_proof_denied():
    """Old dict-style proofs must no longer be accepted."""
    token, _, sk, now, ctx = _make_env()
    assert not validate_request(token, ctx, {"holder_key_fingerprint": "fp1"}, sk).allowed


def test_tampered_signature_denied():
    token, holder, sk, now, ctx = _make_env()
    proof = sign_challenge(holder, token.token_id, "req-1")
    bad = replace(token, signature="AAAAAAAAAAAAAAAAAAAAAA")
    assert not validate_request(bad, ctx, proof, sk).allowed


def test_caveat_failure_denied():
    token, holder, sk, now, _ = _make_env(caveats=[ip_allowlist({"127.0.0.1"})])
    ctx = RequestContext("read", "/data/", "svc", now, ip="10.0.0.1", device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk)
    assert not d.allowed and d.reason == "caveat_failed"


def test_revoked_token_denied():
    token, holder, sk, now, ctx = _make_env()
    rl = RevocationList()
    rl.revoke(token, now)
    proof = sign_challenge(holder, token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk, revocation_list=rl)
    assert not d.allowed and d.reason == "revoked"


def test_revocation_expiry_restores_access():
    token, holder, sk, now, _ = _make_env()
    rl = RevocationList()
    rl.revoke(token, now, ttl_seconds=1)
    ctx = RequestContext("read", "/data/", "svc", now + timedelta(seconds=2), device_nonce="req-1")
    proof = sign_challenge(holder, token.token_id, "req-1")
    assert validate_request(token, ctx, proof, sk, revocation_list=rl).allowed


# ---------------------------------------------------------------------------
# Custom proof verifier
# ---------------------------------------------------------------------------

def test_custom_proof_verifier_allow():
    token, _, sk, now, ctx = _make_env()
    d = validate_request(token, ctx, object(), sk, proof_verifier=lambda t, c, p: True)
    assert d.allowed


def test_custom_proof_verifier_deny():
    token, holder, sk, now, ctx = _make_env()
    proof = sign_challenge(holder, token.token_id, "req-1")
    d = validate_request(token, ctx, proof, sk, proof_verifier=lambda t, c, p: False)
    assert not d.allowed


# ---------------------------------------------------------------------------
# Task 2 — delegation_cert support in validate_request
# ---------------------------------------------------------------------------

def _make_delegation_env():
    """Build alice → root cert → delegation cert → token setup."""
    from proxion_messenger_core.certtoken import delegate_cert, issue_from_certificate
    from proxion_messenger_core.federation import Capability, RelationshipCertificate
    from proxion_messenger_core import run_local_handshake, MemoryStore

    alice_id = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    alice_store = X25519PrivateKey.generate()
    bob_id = Ed25519PrivateKey.generate()
    bob_store = X25519PrivateKey.generate()

    store = MemoryStore()
    caps = [Capability(with_="stash://alice/shared/bob/", can="read")]
    root_cert, valid = run_local_handshake(alice_id, alice_store, bob_id, bob_store, caps, caps, store)
    assert valid

    device = Ed25519PrivateKey.generate()
    device_pub = device.public_key()
    delegation_cert = delegate_cert(root_cert, device_pub, alice_id)

    sk = os.urandom(32)
    now = datetime.now(timezone.utc)
    token = issue_from_certificate(
        cert=delegation_cert,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=device_pub,
        signing_key=sk,
        now=now,
    )
    ctx = RequestContext(
        action="read",
        resource="stash://alice/shared/bob/file.txt",
        aud=root_cert.issuer,
        now=now,
        device_nonce="req-d",
    )
    return token, ctx, sk, now, device, delegation_cert, root_cert, alice_id


def test_validate_with_delegation_cert_happy_path():
    from proxion_messenger_core.pop import sign_challenge
    token, ctx, sk, now, device, delegation_cert, root_cert, alice_id = _make_delegation_env()
    proof = sign_challenge(device, token.token_id, "req-d")
    d = validate_request(token, ctx, proof, sk, delegation_cert=delegation_cert)
    assert d.allowed, f"Expected ALLOW, got: {d.reason}"


def test_validate_delegation_cert_expired_denied():
    from proxion_messenger_core.pop import sign_challenge
    from proxion_messenger_core.federation import RelationshipCertificate
    token, ctx, sk, now, device, delegation_cert, root_cert, alice_id = _make_delegation_env()
    # Create an expired delegation cert
    expired_dcert = RelationshipCertificate(
        issuer=delegation_cert.issuer,
        subject=delegation_cert.subject,
        capabilities=delegation_cert.capabilities,
        wireguard={},
        expires_at=int(now.timestamp()) - 1,  # already expired
    )
    expired_dcert.sign(alice_id)
    proof = sign_challenge(device, token.token_id, "req-d")
    d = validate_request(token, ctx, proof, sk, delegation_cert=expired_dcert)
    assert not d.allowed
    assert d.reason == "delegation_cert_expired"


def test_validate_delegation_cert_subject_mismatch_denied():
    from proxion_messenger_core.pop import sign_challenge
    token, ctx, sk, now, device, delegation_cert, root_cert, alice_id = _make_delegation_env()
    # Sign with a different key — subject won't match delegation cert
    wrong_device = Ed25519PrivateKey.generate()
    proof = sign_challenge(wrong_device, token.token_id, "req-d")
    d = validate_request(token, ctx, proof, sk, delegation_cert=delegation_cert)
    assert not d.allowed
    assert d.reason == "delegation_cert_subject_mismatch"


def test_validate_delegation_cert_scope_exceeded_denied():
    from proxion_messenger_core.pop import sign_challenge
    from proxion_messenger_core.certtoken import delegate_cert, issue_from_certificate
    from proxion_messenger_core.federation import Capability, RelationshipCertificate
    from proxion_messenger_core import run_local_handshake, MemoryStore

    alice_id = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    alice_store = X25519PrivateKey.generate()
    bob_id = Ed25519PrivateKey.generate()
    bob_store = X25519PrivateKey.generate()
    store = MemoryStore()
    caps = [Capability(with_="stash://alice/shared/bob/", can="read")]
    root_cert, valid = run_local_handshake(alice_id, alice_store, bob_id, bob_store, caps, caps, store)
    assert valid

    device = Ed25519PrivateKey.generate()
    # Make delegation cert with narrow caps (photos/ only)
    narrow_dcert = RelationshipCertificate(
        issuer=root_cert.issuer,
        subject=device.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        capabilities=[Capability(with_="stash://alice/shared/bob/photos/", can="read")],
        wireguard={},
        expires_at=root_cert.expires_at,
    )
    narrow_dcert.sign(alice_id)

    sk = os.urandom(32)
    now = datetime.now(timezone.utc)
    # Token covers the broad root cert scope
    token = issue_from_certificate(
        cert=root_cert,
        requested_permissions=[("read", "stash://alice/shared/bob/")],
        holder_pub_key=device.public_key(),
        signing_key=sk,
        now=now,
    )
    ctx = RequestContext(
        action="read",
        resource="stash://alice/shared/bob/file.txt",
        aud=root_cert.issuer,
        now=now,
        device_nonce="req-d",
    )
    proof = sign_challenge(device, token.token_id, "req-d")
    d = validate_request(token, ctx, proof, sk, delegation_cert=narrow_dcert)
    assert not d.allowed
    assert d.reason == "delegation_cert_scope_exceeded"

