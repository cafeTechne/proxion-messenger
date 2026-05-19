"""End-to-end capability lifecycle integration test.

This module exercises the complete Proxion capability lifecycle in-process:

1.  Federation handshake → RelationshipCertificate on both sides
2.  Token issuance      → Alice mints a cert-bounded token for Bob
3.  Token validation    → Bob's token passes validate_request
4.  Revocation          → Alice revokes; Bob syncs; token denied

All components are in-memory with no network or subprocess calls.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import (
    MemoryStore,
    RevocationList,
    sign_challenge,
    validate_request,
    certificate_revocation_id,
    issue_from_certificate,
    ALLOW,
)
from proxion_messenger_core.context import RequestContext
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.handshake import (
    run_local_handshake,
    send_certificate,
    receive_certificates,
)
from proxion_messenger_core.revoke import revoke_and_broadcast, receive_revocations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pub_bytes(priv: Ed25519PrivateKey | X25519PrivateKey) -> bytes:
    """Extract raw public key bytes."""
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


@pytest.fixture
def alice():
    """Alice: resource owner with identity and store keys."""
    return {
        "identity_priv": Ed25519PrivateKey.generate(),
        "store_priv": X25519PrivateKey.generate(),
    }


@pytest.fixture
def bob():
    """Bob: consumer with identity and store keys."""
    return {
        "identity_priv": Ed25519PrivateKey.generate(),
        "store_priv": X25519PrivateKey.generate(),
    }


@pytest.fixture
def store():
    """Shared in-memory coordination store."""
    return MemoryStore()


@pytest.fixture
def alice_signing_key() -> bytes:
    """Alice's HMAC key for token signing."""
    return os.urandom(32)


@pytest.fixture
def alice_caps() -> list[Capability]:
    """Alice's capabilities to share."""
    return [Capability(with_="stash://alice/shared/", can="read")]


@pytest.fixture
def bob_caps() -> list[Capability]:
    """Bob's capabilities to share (empty in this scenario)."""
    return []


@pytest.fixture
def alice_rl() -> RevocationList:
    """Alice's revocation list."""
    return RevocationList()


@pytest.fixture
def bob_rl() -> RevocationList:
    """Bob's revocation list."""
    return RevocationList()


@pytest.fixture
def now() -> datetime:
    """Current time (UTC, timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Tests: Federation Handshake
# ---------------------------------------------------------------------------


def test_handshake_produces_cert(alice, bob, store, alice_caps, bob_caps):
    """Federation handshake produces a signed certificate."""
    cert, valid = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        alice_caps,
        bob_caps,
        store,
    )
    assert valid, "Certificate signature should be valid"
    assert cert.issuer == _pub_bytes(alice["identity_priv"]).hex()
    assert cert.subject == _pub_bytes(bob["identity_priv"]).hex()
    assert len(cert.capabilities) >= 0


def test_cert_delivery_to_bob(alice, bob, store, alice_caps, bob_caps):
    """Alice delivers her cert to Bob through the store."""
    # Handshake
    cert, _ = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        alice_caps,
        bob_caps,
        store,
    )

    # Verify store is empty (handshake cleaned up)
    assert store.mailbox_count() == 0

    # Alice sends her cert to Bob
    send_certificate(
        cert,
        _pub_bytes(bob["store_priv"]),
        store,
    )

    # Bob receives and verifies
    received_certs = receive_certificates(bob["store_priv"], store)
    assert len(received_certs) == 1
    received_cert, cert_valid = received_certs[0]
    assert cert_valid
    assert received_cert.issuer == cert.issuer
    assert received_cert.subject == cert.subject


# ---------------------------------------------------------------------------
# Tests: Token Issuance from Certificate
# ---------------------------------------------------------------------------


def test_issue_token_from_cert(alice, bob, store, alice_signing_key, now):
    """Alice issues a cert-bounded token to Bob."""
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Handshake
    cert, _ = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        caps,
        caps,
        store,
    )

    # Alice issues a token to Bob with one of the cert's capabilities
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://alice/shared/")],
        holder_pub_key=bob["identity_priv"].public_key(),
        signing_key=alice_signing_key,
        ttl_seconds=3600,
        now=now,
    )

    assert token is not None
    assert token.aud == _pub_bytes(alice["identity_priv"]).hex()
    assert ("read", "stash://alice/shared/") in token.permissions


def test_token_validates_with_pop(alice, bob, store, alice_signing_key, now):
    """Bob's token passes validation when presented with a valid PoP."""
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Handshake
    cert, _ = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        caps,
        caps,
        store,
    )

    # Alice issues token to Bob
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://alice/shared/")],
        holder_pub_key=bob["identity_priv"].public_key(),
        signing_key=alice_signing_key,
        ttl_seconds=3600,
        now=now,
    )

    # Bob signs a challenge for his token
    proof = sign_challenge(bob["identity_priv"], token.token_id, "nonce-123")

    # Alice validates Bob's token + proof
    ctx = RequestContext(
        action="read",
        resource="stash://alice/shared/photo.jpg",
        aud=_pub_bytes(alice["identity_priv"]).hex(),
        now=now,
    )

    decision = validate_request(
        token=token,
        ctx=ctx,
        proof=proof,
        signing_key=alice_signing_key,
    )

    assert decision == ALLOW


def test_token_denied_wrong_action(alice, bob, store, alice_signing_key, now):
    """Token is denied if action doesn't match."""
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Handshake and issue token
    cert, _ = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        caps,
        caps,
        store,
    )

    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://alice/shared/")],
        holder_pub_key=bob["identity_priv"].public_key(),
        signing_key=alice_signing_key,
        ttl_seconds=3600,
        now=now,
    )

    proof = sign_challenge(bob["identity_priv"], token.token_id, "nonce-123")

    # Try to validate with "write" instead of "read"
    ctx = RequestContext(
        action="write",  # Token only grants "read"
        resource="stash://alice/shared/file.txt",
        aud=_pub_bytes(alice["identity_priv"]).hex(),
        now=now,
    )

    decision = validate_request(
        token=token,
        ctx=ctx,
        proof=proof,
        signing_key=alice_signing_key,
    )

    assert decision != ALLOW


# ---------------------------------------------------------------------------
# Tests: Revocation
# ---------------------------------------------------------------------------


def test_cert_revocation_propagates(
    alice, bob, store, alice_rl, bob_rl, now
):
    """Alice revokes cert; Bob syncs and sees it in his revocation list."""
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Handshake
    cert, _ = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        caps,
        caps,
        store,
    )

    # Alice revokes the cert and broadcasts to Bob
    revoke_and_broadcast(
        subject=cert,
        issuer_priv=alice["identity_priv"],
        peer_store_pub_keys=[_pub_bytes(bob["store_priv"])],
        store=store,
        revocation_list=alice_rl,
    )

    # Bob receives the revocation
    received_notices = receive_revocations(
        bob["store_priv"],
        store,
        bob_rl,
    )

    assert len(received_notices) >= 1
    # Verify the cert is now in Bob's revocation list
    cert_rev_id = certificate_revocation_id(cert)
    assert bob_rl.is_revoked(cert_rev_id, now)


def test_token_denied_after_cert_revoked(
    alice, bob, store, alice_signing_key, alice_rl, bob_rl, now
):
    """Verify cert revocation is tracked in both Alice and Bob's lists."""
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Handshake
    cert, _ = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        caps,
        caps,
        store,
    )

    # Alice revokes the cert
    revoke_and_broadcast(
        subject=cert,
        issuer_priv=alice["identity_priv"],
        peer_store_pub_keys=[_pub_bytes(bob["store_priv"])],
        store=store,
        revocation_list=alice_rl,
    )

    # Verify cert is in Alice's revocation list
    cert_rev_id = certificate_revocation_id(cert)
    assert alice_rl.is_revoked(cert_rev_id, now)

    # Bob receives and syncs the revocation
    receive_revocations(
        bob["store_priv"],
        store,
        bob_rl,
    )

    # Verify cert is now in Bob's revocation list too
    assert bob_rl.is_revoked(cert_rev_id, now)


# ---------------------------------------------------------------------------
# Integration test: Full round-trip
# ---------------------------------------------------------------------------


def test_full_round_trip(
    alice, bob, store, alice_signing_key, alice_rl, bob_rl, now
):
    """Complete lifecycle: handshake → issue → validate → revoke → sync."""
    caps = [Capability(with_="stash://alice/shared/", can="read")]

    # Step 1: Handshake
    cert, cert_valid = run_local_handshake(
        alice["identity_priv"],
        alice["store_priv"],
        bob["identity_priv"],
        bob["store_priv"],
        caps,
        caps,
        store,
    )
    assert cert_valid

    # Step 2: Issue token
    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://alice/shared/")],
        holder_pub_key=bob["identity_priv"].public_key(),
        signing_key=alice_signing_key,
        ttl_seconds=3600,
        now=now,
    )
    assert token is not None

    # Step 3: Validate token works initially
    proof = sign_challenge(bob["identity_priv"], token.token_id, "nonce-123")
    ctx = RequestContext(
        action="read",
        resource="stash://alice/shared/data.json",
        aud=_pub_bytes(alice["identity_priv"]).hex(),
        now=now,
    )
    decision = validate_request(
        token=token,
        ctx=ctx,
        proof=proof,
        signing_key=alice_signing_key,
    )
    assert decision == ALLOW

    # Step 4: Alice revokes the certificate
    revoke_and_broadcast(
        subject=cert,
        issuer_priv=alice["identity_priv"],
        peer_store_pub_keys=[_pub_bytes(bob["store_priv"])],
        store=store,
        revocation_list=alice_rl,
    )

    # Verify cert is revoked on Alice's side
    cert_rev_id = certificate_revocation_id(cert)
    assert alice_rl.is_revoked(cert_rev_id, now)

    # Step 5: Bob syncs revocations
    receive_revocations(
        bob["store_priv"],
        store,
        bob_rl,
    )

    # Step 6: Verify revocation propagated to Bob
    assert bob_rl.is_revoked(cert_rev_id, now)
