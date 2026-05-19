"""Tests for compose_unreaction() and apply_unreactions()."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import (
    compose, compose_reaction, compose_unreaction, apply_unreactions,
)
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert_priv(cert_id="cert-unreact-test"):
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


class TestComposeUnreaction:
    def test_unreaction_has_correct_type(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        assert unreact.message_type == "unreaction"

    def test_unreaction_reply_to_id_is_reaction(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        assert unreact.reply_to_id == react.message_id

    def test_unreaction_signature_verifies(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        assert unreact.verify(_pub_bytes(priv)) is True

    def test_unreaction_content_is_empty(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "msg")
        react = compose_reaction(priv, cert, "❤️", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        assert unreact.content == ""


class TestApplyUnreactions:
    def test_reaction_removed_after_unreaction(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "post")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        result = apply_unreactions([orig, react, unreact])
        ids = {m.message_id for m in result}
        assert react.message_id not in ids

    def test_unreaction_itself_removed_from_output(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "post")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        result = apply_unreactions([orig, react, unreact])
        assert all(m.message_type != "unreaction" for m in result)

    def test_cross_author_unreaction_rejected(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "post")
        react = compose_reaction(priv_a, cert_a, "👍", orig.message_id)
        # Bob tries to un-react Alice's reaction
        unreact = compose_unreaction(priv_b, cert_b, react.message_id)
        result = apply_unreactions([orig, react, unreact])
        # Alice's reaction must survive
        assert any(m.message_id == react.message_id for m in result)

    def test_unreaction_targets_non_reaction_ignored(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "plain message")
        # Unreaction targeting a non-reaction message should be ignored
        unreact = compose_unreaction(priv, cert, orig.message_id)
        result = apply_unreactions([orig, unreact])
        # orig preserved; unreact dropped
        assert any(m.message_id == orig.message_id for m in result)

    def test_other_messages_preserved(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "post")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        unreact = compose_unreaction(priv, cert, react.message_id)
        result = apply_unreactions([orig, react, unreact])
        assert any(m.message_id == orig.message_id for m in result)

    def test_empty_list_returns_empty(self):
        assert apply_unreactions([]) == []

    def test_no_unreactions_returns_all(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "post")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        result = apply_unreactions([orig, react])
        assert len(result) == 2

    def test_unreaction_without_reply_to_id_ignored(self):
        import dataclasses
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "msg")
        react = compose_reaction(priv, cert, "👍", orig.message_id)
        orphan = dataclasses.replace(
            compose_unreaction(priv, cert, react.message_id),
            reply_to_id=None,
        )
        result = apply_unreactions([orig, react, orphan])
        # react should survive since orphan has no reply_to_id
        assert any(m.message_id == react.message_id for m in result)

    def test_multiple_unreactions_same_reactor(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "post")
        r1 = compose_reaction(priv, cert, "👍", orig.message_id)
        r2 = compose_reaction(priv, cert, "❤️", orig.message_id)
        u1 = compose_unreaction(priv, cert, r1.message_id)
        result = apply_unreactions([orig, r1, r2, u1])
        ids = {m.message_id for m in result}
        assert r1.message_id not in ids  # r1 removed
        assert r2.message_id in ids       # r2 preserved
