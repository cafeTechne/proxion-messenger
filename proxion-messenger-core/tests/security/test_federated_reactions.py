"""Tests for signed federated reaction messages."""
import hashlib
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import (
    Message, compose, compose_reaction, check_hash_chain,
)
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert_priv(cert_id="cert-react-test"):
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    cert = RelationshipCertificate(
        certificate_id=cert_id,
        issuer=pub,
        subject=pub,
        capabilities=[],
    )
    return cert, priv


def _pub_bytes(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


class TestComposeReaction:
    def test_reaction_has_correct_type(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        assert react.message_type == "reaction"

    def test_reaction_content_is_emoji(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "❤️", orig.message_id)
        assert react.content == "❤️"

    def test_reaction_reply_to_id_is_target(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        assert react.reply_to_id == orig.message_id

    def test_reaction_signature_verifies(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "🎉", orig.message_id)
        assert react.verify(_pub_bytes(priv)) is True

    def test_wrong_key_does_not_verify(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "hello")
        react = compose_reaction(priv_a, cert_a, "👍", orig.message_id)
        assert react.verify(_pub_bytes(priv_b)) is False

    def test_reaction_chained_via_prev_hash(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "genesis")
        react = compose_reaction(priv, cert, "👍", orig.message_id, prev_msg=orig)
        expected = hashlib.sha256(orig.canonical_bytes()).hexdigest()
        assert react.prev_hash == expected

    def test_reaction_without_prev_hash_is_genesis(self):
        cert, priv = _make_cert_priv()
        react = compose_reaction(priv, cert, "👍", "some-msg-id")
        assert react.prev_hash == ""

    def test_chain_with_reactions_has_no_breaks(self):
        cert, priv = _make_cert_priv()
        m0 = compose(priv, cert, "text")
        r1 = compose_reaction(priv, cert, "👍", m0.message_id, prev_msg=m0)
        r2 = compose_reaction(priv, cert, "❤️", m0.message_id, prev_msg=r1)
        assert check_hash_chain([m0, r1, r2]) == []

    def test_reaction_roundtrip_from_dict(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "🔥", orig.message_id)
        recovered = Message.from_dict(react.to_dict())
        assert recovered.message_type == "reaction"
        assert recovered.content == "🔥"
        assert recovered.reply_to_id == orig.message_id
        assert recovered.verify(_pub_bytes(priv)) is True

    def test_different_authors_can_react_to_same_message(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "interesting post")
        r_a = compose_reaction(priv_a, cert_a, "👍", orig.message_id)
        r_b = compose_reaction(priv_b, cert_b, "👍", orig.message_id)
        assert r_a.verify(_pub_bytes(priv_a)) is True
        assert r_b.verify(_pub_bytes(priv_b)) is True
        assert r_a.from_pub_hex != r_b.from_pub_hex
