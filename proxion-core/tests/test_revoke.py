"""Tests for proxion_messenger_core.revoke — revocation propagation via the store."""

import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import (
    MemoryStore,
    RevocationList,
    fingerprint,
    issue_token,
    revoke_and_broadcast,
    receive_revocations,
    token_revocation_id,
    certificate_revocation_id,
)
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.revoke import (
    RevocationNotice,
    broadcast_revocation,
    create_certificate_revocation,
    create_token_revocation,
)
from proxion_messenger_core.sealed import mailbox_id_for


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sk():
    return os.urandom(32)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def issuer_priv():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def peer_store_priv():
    return X25519PrivateKey.generate()


@pytest.fixture
def peer_pub_bytes(peer_store_priv):
    return peer_store_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


@pytest.fixture
def token(sk, issuer_priv, now):
    holder = issuer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return issue_token(
        permissions=[("read", "/data/")],
        exp=now + timedelta(hours=1),
        aud="svc",
        caveats=[],
        holder_key_fingerprint=fingerprint(holder),
        signing_key=sk,
        now=now,
    )


@pytest.fixture
def cert():
    return RelationshipCertificate("alice", "bob", [], {})


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def rl():
    return RevocationList()


# ---------------------------------------------------------------------------
# create_token_revocation
# ---------------------------------------------------------------------------

def test_create_token_revocation_fields(token, issuer_priv, now):
    notice = create_token_revocation(token, issuer_priv, reason="test")
    assert notice.subject_type == "token"
    assert notice.subject_id == token.token_id
    assert notice.revocation_id == token_revocation_id(token)
    assert notice.not_after == int(token.exp.timestamp())
    assert notice.reason == "test"
    assert notice.signature is not None


def test_create_token_revocation_verifies(token, issuer_priv):
    notice = create_token_revocation(token, issuer_priv)
    assert notice.verify()


# ---------------------------------------------------------------------------
# create_certificate_revocation
# ---------------------------------------------------------------------------

def test_create_cert_revocation_fields(cert, issuer_priv):
    notice = create_certificate_revocation(cert, issuer_priv, reason="expired_rel")
    assert notice.subject_type == "certificate"
    assert notice.subject_id == cert.certificate_id
    assert notice.revocation_id == certificate_revocation_id(cert)
    assert notice.reason == "expired_rel"
    assert notice.verify()


# ---------------------------------------------------------------------------
# RevocationNotice.verify()
# ---------------------------------------------------------------------------

def test_notice_verify_fails_unsigned(token, issuer_priv):
    notice = RevocationNotice(
        subject_type="token",
        subject_id=token.token_id,
        revocation_id=token_revocation_id(token),
        not_after=int(token.exp.timestamp()),
    )
    assert not notice.verify()   # no signature


def test_notice_verify_fails_tampered_payload(token, issuer_priv):
    notice = create_token_revocation(token, issuer_priv)
    notice.reason = "tampered_after_signing"
    assert not notice.verify()


def test_notice_verify_fails_wrong_key(token, issuer_priv):
    notice = create_token_revocation(token, issuer_priv)
    # Re-sign with a different key
    other = Ed25519PrivateKey.generate()
    other_pub = other.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    notice.issuer_pub_key = other_pub   # mismatch: sig was made with issuer_priv
    assert not notice.verify()


# ---------------------------------------------------------------------------
# broadcast_revocation
# ---------------------------------------------------------------------------

def test_broadcast_posts_to_peer_mailbox(token, issuer_priv, peer_pub_bytes, store):
    notice = create_token_revocation(token, issuer_priv)
    msg_ids = broadcast_revocation(notice, [peer_pub_bytes], store)
    assert len(msg_ids) == 1
    assert store.peek(mailbox_id_for(peer_pub_bytes))["count"] == 1


def test_broadcast_to_multiple_peers(token, issuer_priv, store):
    peers = [
        X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        for _ in range(3)
    ]
    notice = create_token_revocation(token, issuer_priv)
    ids = broadcast_revocation(notice, peers, store)
    assert len(ids) == 3
    for p in peers:
        assert store.peek(mailbox_id_for(p))["count"] == 1


def test_broadcast_unsigned_raises(token, rl):
    store = MemoryStore()
    notice = RevocationNotice(subject_type="token", subject_id=token.token_id,
                              revocation_id="x", not_after=0)
    with pytest.raises(ValueError, match="signed"):
        broadcast_revocation(notice, [b"\x00" * 32], store)


# ---------------------------------------------------------------------------
# receive_revocations
# ---------------------------------------------------------------------------

def test_receive_applies_to_revocation_list(token, issuer_priv, peer_store_priv, peer_pub_bytes, store, rl, now):
    notice = create_token_revocation(token, issuer_priv)
    broadcast_revocation(notice, [peer_pub_bytes], store)
    applied = receive_revocations(peer_store_priv, store, rl)
    assert len(applied) == 1
    assert rl.is_revoked(token, now)


def test_receive_clears_mailbox(token, issuer_priv, peer_store_priv, peer_pub_bytes, store, rl):
    notice = create_token_revocation(token, issuer_priv)
    broadcast_revocation(notice, [peer_pub_bytes], store)
    receive_revocations(peer_store_priv, store, rl)
    assert store.peek(mailbox_id_for(peer_pub_bytes))["count"] == 0


def test_receive_discards_tampered_notice(token, issuer_priv, peer_store_priv, peer_pub_bytes, store, rl, now):
    notice = create_token_revocation(token, issuer_priv)
    notice.reason = "tampered_after_sign"   # invalidates signature
    broadcast_revocation(notice, [peer_pub_bytes], store)
    applied = receive_revocations(peer_store_priv, store, rl)
    assert applied == []
    assert not rl.is_revoked(token, now)


def test_receive_ignores_other_message_types(token, issuer_priv, peer_store_priv, peer_pub_bytes, store, rl):
    """FederationInvite messages must survive receive_revocations."""
    from proxion_messenger_core.sealed import seal_json
    store.put(
        mailbox_id_for(peer_pub_bytes),
        seal_json({"@type": "FederationInvite", "invitation_id": "x"}, peer_pub_bytes),
    )
    notice = create_token_revocation(token, issuer_priv)
    broadcast_revocation(notice, [peer_pub_bytes], store)
    receive_revocations(peer_store_priv, store, rl)
    # Invite should remain
    assert store.peek(mailbox_id_for(peer_pub_bytes))["count"] == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_idempotency_via_seen_ids(token, issuer_priv, peer_store_priv, peer_pub_bytes, store, rl):
    notice = create_token_revocation(token, issuer_priv)
    broadcast_revocation(notice, [peer_pub_bytes], store)
    broadcast_revocation(notice, [peer_pub_bytes], store)   # duplicate
    seen = set()
    first = receive_revocations(peer_store_priv, store, rl, seen_notice_ids=seen)
    second = receive_revocations(peer_store_priv, store, rl, seen_notice_ids=seen)
    assert len(first) == 1
    assert len(second) == 0   # duplicate skipped


# ---------------------------------------------------------------------------
# revoke_and_broadcast
# ---------------------------------------------------------------------------

def test_revoke_and_broadcast_applies_locally(token, issuer_priv, peer_pub_bytes, store, rl, now):
    revoke_and_broadcast(token, issuer_priv, [peer_pub_bytes], store, rl)
    assert rl.is_revoked(token, now)


def test_revoke_and_broadcast_posts_to_peer(token, issuer_priv, peer_pub_bytes, store, rl):
    revoke_and_broadcast(token, issuer_priv, [peer_pub_bytes], store, rl)
    assert store.peek(mailbox_id_for(peer_pub_bytes))["count"] == 1


def test_revoke_and_broadcast_cert(cert, issuer_priv, peer_pub_bytes, store, rl):
    notice = revoke_and_broadcast(cert, issuer_priv, [peer_pub_bytes], store, rl)
    assert notice.subject_type == "certificate"


def test_revoke_and_broadcast_wrong_type_raises(store, rl):
    issuer = Ed25519PrivateKey.generate()
    with pytest.raises(TypeError):
        revoke_and_broadcast("not_a_token_or_cert", issuer, [], store, rl)
