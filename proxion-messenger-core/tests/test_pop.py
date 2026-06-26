"""Tests for proxion_messenger_core.pop — Ed25519 Proof-of-Possession."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.pop import (
    fingerprint,
    fingerprint_from_key,
    make_challenge,
    sign_challenge,
    verify_pop,
)


@pytest.fixture
def priv():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def pub(priv):
    return priv.public_key()


@pytest.fixture
def pub_bytes(pub):
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_deterministic(pub_bytes):
    assert fingerprint(pub_bytes) == fingerprint(pub_bytes)


def test_fingerprint_from_key_matches(pub, pub_bytes):
    assert fingerprint_from_key(pub) == fingerprint(pub_bytes)


def test_different_keys_different_fingerprints():
    k1 = Ed25519PrivateKey.generate()
    k2 = Ed25519PrivateKey.generate()
    b1 = k1.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    b2 = k2.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert fingerprint(b1) != fingerprint(b2)


def test_fingerprint_is_base64url_string(pub_bytes):
    fp = fingerprint(pub_bytes)
    assert isinstance(fp, str)
    # base64url — no +, /, or = padding
    assert "+" not in fp
    assert "/" not in fp
    assert "=" not in fp


# ---------------------------------------------------------------------------
# make_challenge
# ---------------------------------------------------------------------------

def test_make_challenge_format():
    ch = make_challenge("tok123", "nonce456")
    assert ch == b"proxion-pop:tok123:nonce456"


def test_make_challenge_bytes():
    assert isinstance(make_challenge("a", "b"), bytes)


def test_make_challenge_encodes_utf8():
    ch = make_challenge("id", "nonce")
    assert ch.decode("utf-8") == "proxion-pop:id:nonce"


# ---------------------------------------------------------------------------
# sign_challenge / verify_pop — happy path
# ---------------------------------------------------------------------------

def test_verify_pop_valid(priv, pub_bytes):
    from proxion_messenger_core import issue_token
    import os
    from datetime import datetime, timedelta, timezone
    signing_key = os.urandom(32)
    now = datetime.now(timezone.utc)
    token = issue_token(
        permissions=[("read", "/data/")],
        exp=now + timedelta(hours=1),
        aud="test",
        caveats=[],
        holder_key_fingerprint=fingerprint(pub_bytes),
        signing_key=signing_key,
        now=now,
    )
    proof = sign_challenge(priv, token.token_id, "nonce-001")
    assert verify_pop(token, proof)


def test_verify_pop_fields(priv, pub_bytes):
    from proxion_messenger_core import issue_token
    import os
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    token = issue_token(
        permissions=[("read", "/")],
        exp=now + timedelta(hours=1),
        aud="test",
        caveats=[],
        holder_key_fingerprint=fingerprint(pub_bytes),
        signing_key=os.urandom(32),
        now=now,
    )
    proof = sign_challenge(priv, token.token_id, "my-nonce")
    assert proof.nonce == "my-nonce"
    assert len(proof.signature) == 64
    assert proof.public_key_bytes == pub_bytes


# ---------------------------------------------------------------------------
# verify_pop — rejection cases
# ---------------------------------------------------------------------------

def _make_token_for(pub_bytes, signing_key, now):
    from proxion_messenger_core import issue_token
    from datetime import timedelta
    return issue_token(
        permissions=[("read", "/data/")],
        exp=now + timedelta(hours=1),
        aud="test",
        caveats=[],
        holder_key_fingerprint=fingerprint(pub_bytes),
        signing_key=signing_key,
        now=now,
    )


def test_verify_pop_wrong_key_rejected():
    import os
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sk = os.urandom(32)
    holder = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    holder_bytes = holder.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    token = _make_token_for(holder_bytes, sk, now)
    # Attacker signs with their own key
    proof = sign_challenge(attacker, token.token_id, "nonce")
    assert not verify_pop(token, proof)


def test_verify_pop_wrong_nonce_rejected():
    """Challenge must match token_id + exact nonce — different nonce → different challenge."""
    import os
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sk = os.urandom(32)
    holder = Ed25519PrivateKey.generate()
    holder_bytes = holder.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    token = _make_token_for(holder_bytes, sk, now)
    proof = sign_challenge(holder, token.token_id, "nonce-A")
    # Swap nonce field after signing
    from proxion_messenger_core.pop import PopProof
    tampered = PopProof(
        public_key_bytes=proof.public_key_bytes,
        nonce="nonce-B",          # different nonce → different challenge → sig invalid
        signature=proof.signature,
    )
    assert not verify_pop(token, tampered)


def test_verify_pop_wrong_token_id_rejected():
    """Proof for one token must not be accepted for a different token."""
    import os
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sk = os.urandom(32)
    holder = Ed25519PrivateKey.generate()
    holder_bytes = holder.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    token_a = _make_token_for(holder_bytes, sk, now)
    token_b = _make_token_for(holder_bytes, sk, now)
    proof_a = sign_challenge(holder, token_a.token_id, "nonce")
    # Present token_a's proof for token_b
    assert not verify_pop(token_b, proof_a)


def test_verify_pop_fingerprint_mismatch_rejected():
    """If the proof's public key doesn't match the token's holder_key_fingerprint → reject."""
    import os
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    sk = os.urandom(32)
    alice = Ed25519PrivateKey.generate()
    bob = Ed25519PrivateKey.generate()
    alice_bytes = alice.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    # Token issued to Alice
    token = _make_token_for(alice_bytes, sk, now)
    # Bob constructs a valid self-proof, but his key doesn't match the token
    bob_bytes = bob.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    # Manually build a proof that would pass sig check if key matched
    from proxion_messenger_core.pop import make_challenge, PopProof
    challenge = make_challenge(token.token_id, "nonce")
    sig = bob.sign(challenge)
    proof = PopProof(public_key_bytes=bob_bytes, nonce="nonce", signature=sig)
    assert not verify_pop(token, proof)
