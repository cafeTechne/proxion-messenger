"""Tests for AgentState key rotation."""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.sealed import mailbox_id_for, seal, open_sealed

PASSPHRASE = b"rotation-test"


@pytest.fixture
def agent():
    return AgentState.generate()


# ---------------------------------------------------------------------------
# rotate_store_key
# ---------------------------------------------------------------------------

def test_rotate_store_key_returns_old_key(agent):
    old_key = agent.store_key
    returned = agent.rotate_store_key()
    assert returned is old_key


def test_rotate_store_key_changes_store_key(agent):
    old_pub = agent.store_pub_bytes
    agent.rotate_store_key()
    assert agent.store_pub_bytes != old_pub


def test_rotate_store_key_new_key_is_x25519(agent):
    agent.rotate_store_key()
    assert isinstance(agent.store_key, X25519PrivateKey)


def test_rotate_store_key_old_key_decrypts_old_messages(agent):
    """Messages sealed to the old key can still be decrypted after rotation."""
    old_pub = agent.store_pub_bytes
    envelope = seal(b"old message", old_pub)

    old_key = agent.rotate_store_key()
    plaintext = open_sealed(envelope, old_key)
    assert plaintext == b"old message"


def test_rotate_store_key_new_key_decrypts_new_messages(agent):
    """Messages sealed to the new key can be decrypted with the new key."""
    agent.rotate_store_key()
    new_pub = agent.store_pub_bytes
    envelope = seal(b"new message", new_pub)
    plaintext = open_sealed(envelope, agent.store_key)
    assert plaintext == b"new message"


def test_rotate_store_key_new_key_cannot_decrypt_old_messages(agent):
    """The new key must NOT be able to decrypt messages sealed to the old key."""
    from proxion_messenger_core.errors import CipherError
    old_pub = agent.store_pub_bytes
    envelope = seal(b"old message", old_pub)
    agent.rotate_store_key()
    with pytest.raises(CipherError):
        open_sealed(envelope, agent.store_key)


def test_rotate_store_key_changes_mailbox_id(agent):
    old_mailbox = mailbox_id_for(agent.store_pub_bytes)
    agent.rotate_store_key()
    new_mailbox = mailbox_id_for(agent.store_pub_bytes)
    assert old_mailbox != new_mailbox


def test_rotate_store_key_persisted(tmp_path, agent):
    agent.rotate_store_key()
    new_pub = agent.store_pub_bytes
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.store_pub_bytes == new_pub


# ---------------------------------------------------------------------------
# rotate_identity_key
# ---------------------------------------------------------------------------

def test_rotate_identity_key_returns_old_key(agent):
    old_key = agent.identity_key
    returned = agent.rotate_identity_key()
    assert returned is old_key


def test_rotate_identity_key_changes_identity_key(agent):
    old_pub = agent.identity_pub_bytes
    agent.rotate_identity_key()
    assert agent.identity_pub_bytes != old_pub


def test_rotate_identity_key_new_key_is_ed25519(agent):
    agent.rotate_identity_key()
    assert isinstance(agent.identity_key, Ed25519PrivateKey)


def test_rotate_identity_key_old_key_still_signs(agent):
    """Old key returned from rotate is still functional."""
    old_key = agent.rotate_identity_key()
    sig = old_key.sign(b"test message")
    assert len(sig) == 64


def test_rotate_identity_key_new_key_signs_differently(agent):
    old_key = agent.identity_key
    old_sig = old_key.sign(b"same message")
    agent.rotate_identity_key()
    new_sig = agent.identity_key.sign(b"same message")
    # Ed25519 is deterministic — different key → different signature
    assert old_sig != new_sig


def test_rotate_identity_key_persisted(tmp_path, agent):
    agent.rotate_identity_key()
    new_pub = agent.identity_pub_bytes
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert loaded.identity_pub_bytes == new_pub


def test_rotate_identity_key_existing_certs_preserved(tmp_path, agent):
    """Existing certificates are not affected by identity key rotation."""
    from proxion_messenger_core.federation import Capability, RelationshipCertificate
    cert = RelationshipCertificate("alice", "bob", [], {})
    agent.certificates.append(cert)
    agent.rotate_identity_key()
    p = tmp_path / "agent.json"
    agent.save(p, PASSPHRASE)
    loaded = AgentState.load(p, PASSPHRASE)
    assert len(loaded.certificates) == 1
    assert loaded.certificates[0].certificate_id == cert.certificate_id


# ---------------------------------------------------------------------------
# Multiple rotations
# ---------------------------------------------------------------------------

def test_multiple_store_rotations(agent):
    pubs = set()
    pubs.add(agent.store_pub_bytes)
    for _ in range(5):
        agent.rotate_store_key()
        pubs.add(agent.store_pub_bytes)
    assert len(pubs) == 6   # all distinct


def test_multiple_identity_rotations(agent):
    pubs = set()
    pubs.add(agent.identity_pub_bytes)
    for _ in range(5):
        agent.rotate_identity_key()
        pubs.add(agent.identity_pub_bytes)
    assert len(pubs) == 6


# ---------------------------------------------------------------------------
# Integration — token signed by old key still validates after rotation
# ---------------------------------------------------------------------------

def test_token_signed_by_old_identity_key_validates_after_rotation(agent):
    """
    Tokens are signed with an HMAC key (signing_key_bytes), not directly
    with the Ed25519 identity key.  If a resource server caches the HMAC key,
    tokens issued before rotation continue to validate.
    """
    import os
    from proxion_messenger_core import issue_token, sign_challenge, validate_request
    from proxion_messenger_core.context import RequestContext
    from proxion_messenger_core.pop import fingerprint_from_key

    # Issue a token with the current identity key as the holder
    sk = os.urandom(32)
    fp = fingerprint_from_key(agent.identity_pub)
    now = datetime.now(timezone.utc)
    token = issue_token(
        [("read", "/data/")],
        now + timedelta(hours=1),
        "svc",
        [],
        fp,
        sk,
        now=now,
    )

    # Rotate the identity key
    old_id_key = agent.rotate_identity_key()

    # Token was issued for the OLD identity key's fingerprint.
    # Proof must use the OLD key (since the token's holder_key_fingerprint is
    # derived from the old pubkey).
    proof = sign_challenge(old_id_key, token.token_id, "nonce-1")
    ctx = RequestContext(action="read", resource="/data/file.txt", aud="svc",
                        now=now, device_nonce="nonce-1")

    decision = validate_request(token, ctx, proof, sk)
    assert decision.allowed
