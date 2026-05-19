"""Tests for proxion_messenger_core.federation — message types with real Ed25519 signing."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.federation import (
    Capability,
    FederationInvite,
    InviteAcceptance,
    RelationshipCertificate,
)


@pytest.fixture
def priv():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def pub_hex(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _ed25519_verify(pubkey_hex, sig_bytes, message):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(sig_bytes, message)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------

def test_capability_to_dict():
    cap = Capability(with_="stash://alice/x", can="read", caveats={"quota": 100})
    d = cap.to_dict()
    assert d["with"] == "stash://alice/x"
    assert d["can"] == "read"
    assert d["caveats"] == {"quota": 100}


# ---------------------------------------------------------------------------
# FederationInvite
# ---------------------------------------------------------------------------

def test_invite_type_field(priv, pub_hex):
    invite = FederationInvite(
        issuer={"public_key": pub_hex},
        endpoint_hints=[],
        capabilities=[],
    )
    invite.sign(priv)
    assert invite.to_dict()["@type"] == "FederationInvite"


def test_invite_sign_and_verify(priv, pub_hex):
    invite = FederationInvite(
        issuer={"public_key": pub_hex},
        endpoint_hints=["relay://example.com"],
        capabilities=[Capability(with_="stash://alice/x", can="read")],
    )
    invite.sign(priv)
    assert invite.signature is not None
    assert invite.verify(_ed25519_verify)


def test_invite_verify_fails_wrong_key(priv, pub_hex):
    other = Ed25519PrivateKey.generate()
    other_hex = other.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    invite = FederationInvite(issuer={"public_key": pub_hex}, endpoint_hints=[], capabilities=[])
    invite.sign(priv)
    # Swap issuer key to someone else's — signature won't match
    invite.issuer = {"public_key": other_hex}
    assert not invite.verify(_ed25519_verify)


def test_invite_verify_fails_unsigned():
    invite = FederationInvite(issuer={"public_key": "aabb"}, endpoint_hints=[], capabilities=[])
    assert not invite.verify(_ed25519_verify)


def test_invite_has_challenge_marker(priv, pub_hex):
    invite = FederationInvite(issuer={"public_key": pub_hex}, endpoint_hints=[], capabilities=[])
    assert len(invite.challenge_marker) == 64   # 32 bytes hex


# ---------------------------------------------------------------------------
# InviteAcceptance
# ---------------------------------------------------------------------------

def test_acceptance_type_field(priv, pub_hex):
    acc = InviteAcceptance(
        invitation_id="inv-1",
        responder={"public_key": pub_hex},
        challenge_response="deadbeef" * 16,
    )
    acc.sign(priv)
    assert acc.to_dict()["@type"] == "InviteAcceptance"


def test_acceptance_sign_and_verify(priv, pub_hex):
    acc = InviteAcceptance(
        invitation_id="inv-1",
        responder={"public_key": pub_hex},
        challenge_response="deadbeef" * 16,
    )
    acc.sign(priv)
    assert acc.verify(_ed25519_verify)


def test_acceptance_challenge_verification(priv, pub_hex):
    """The challenge_response must be a valid signature over challenge_marker."""
    challenge_marker = "my-secret-marker"
    _CTX = b"proxion-handshake-v1:"
    challenge_sig = priv.sign(_CTX + challenge_marker.encode()).hex()
    acc = InviteAcceptance(
        invitation_id="inv-1",
        responder={"public_key": pub_hex},
        challenge_response=challenge_sig,
    )
    assert acc.verify_challenge(_ed25519_verify, challenge_marker)


def test_acceptance_wrong_challenge_rejected(priv, pub_hex):
    challenge_sig = priv.sign(b"real-marker").hex()
    acc = InviteAcceptance(
        invitation_id="inv-1",
        responder={"public_key": pub_hex},
        challenge_response=challenge_sig,
    )
    assert not acc.verify_challenge(_ed25519_verify, "wrong-marker")


# ---------------------------------------------------------------------------
# RelationshipCertificate
# ---------------------------------------------------------------------------

def test_cert_type_field(priv, pub_hex):
    cert = RelationshipCertificate(
        issuer=pub_hex, subject="bob", capabilities=[], wireguard={}
    )
    cert.sign(priv)
    assert cert.to_dict()["@type"] == "RelationshipCertificate"


def test_cert_sign_and_verify(priv, pub_hex):
    cert = RelationshipCertificate(
        issuer=pub_hex,
        subject="bob_pub_hex",
        capabilities=[Capability(with_="stash://alice/x", can="read")],
        wireguard={},
    )
    cert.sign(priv)
    assert cert.verify(_ed25519_verify)


def test_cert_verify_fails_wrong_issuer(priv, pub_hex):
    cert = RelationshipCertificate(issuer=pub_hex, subject="bob", capabilities=[], wireguard={})
    cert.sign(priv)
    cert.issuer = "0000" * 8   # tamper issuer after signing
    assert not cert.verify(_ed25519_verify)


def test_relationship_certificate_constructed_without_wireguard():
    """Assert RelationshipCertificate can be created without wireguard field."""
    cert = RelationshipCertificate(issuer="a", subject="b", capabilities=[])
    assert cert.wireguard == {}
