"""Tests for verifiable forward messages — dual-signature verification."""
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import (
    Message, compose, compose_forward, verify_forward,
)
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert_priv(cert_id="cert-fwd-test"):
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


class TestComposeForward:
    def test_forward_has_correct_type(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original message")
        fwd = compose_forward(priv, cert, orig)
        assert fwd.message_type == "forward"

    def test_forward_content_embeds_original(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        fwd = compose_forward(priv, cert, orig)
        nested = json.loads(fwd.content)
        assert "original" in nested
        assert nested["original"]["message_id"] == orig.message_id

    def test_forward_preserves_original_signature(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        fwd = compose_forward(priv, cert, orig)
        nested = json.loads(fwd.content)
        recovered = Message.from_dict(nested["original"])
        assert recovered.signature == orig.signature

    def test_forwarder_outer_signature_valid(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "alice's message")
        fwd = compose_forward(priv_b, cert_b, orig)
        assert fwd.verify(_pub_bytes(priv_b)) is True

    def test_forward_chained_via_prev_hash(self):
        import hashlib
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "first message")
        fwd = compose_forward(priv, cert, orig, prev_msg=orig)
        expected = hashlib.sha256(orig.canonical_bytes()).hexdigest()
        assert fwd.prev_hash == expected


class TestVerifyForward:
    def test_valid_forward_verifies(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "original content")
        fwd = compose_forward(priv_b, cert_b, orig)
        assert verify_forward(fwd, _pub_bytes(priv_b), _pub_bytes(priv_a)) is True

    def test_wrong_forwarder_key_fails(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        cert_c, priv_c = _make_cert_priv("cert-c")
        orig = compose(priv_a, cert_a, "original")
        fwd = compose_forward(priv_b, cert_b, orig)
        assert verify_forward(fwd, _pub_bytes(priv_c), _pub_bytes(priv_a)) is False

    def test_wrong_original_author_key_fails(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        cert_c, priv_c = _make_cert_priv("cert-c")
        orig = compose(priv_a, cert_a, "original")
        fwd = compose_forward(priv_b, cert_b, orig)
        # Wrong key for original author
        assert verify_forward(fwd, _pub_bytes(priv_b), _pub_bytes(priv_c)) is False

    def test_tampered_original_content_fails(self):
        import dataclasses
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "genuine content")
        fwd = compose_forward(priv_b, cert_b, orig)
        # Tamper the embedded original
        nested = json.loads(fwd.content)
        nested["original"]["content"] = "TAMPERED"
        tampered_content = json.dumps({"original": nested["original"]}, separators=(",", ":"))
        fwd_tampered = dataclasses.replace(fwd, content=tampered_content, signature="")
        # Re-sign the tampered outer — original signature inside is still intact
        # but since from_pub_hex mismatch means verify_forward checks orig.verify
        # against the REAL original which was signed over "genuine content"
        # The inner signature was made by priv_a over the original content,
        # so recovering it and verifying against priv_a with tampered content fails.
        assert verify_forward(fwd_tampered, _pub_bytes(priv_b), _pub_bytes(priv_a)) is False

    def test_malformed_content_fails(self):
        import dataclasses
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "msg")
        fwd = compose_forward(priv, cert, orig)
        bad = dataclasses.replace(fwd, content="not valid json", signature="")
        assert verify_forward(bad, _pub_bytes(priv), _pub_bytes(priv)) is False

    def test_forward_roundtrip_from_dict(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "hello world")
        fwd = compose_forward(priv_b, cert_b, orig)
        recovered = Message.from_dict(fwd.to_dict())
        assert verify_forward(recovered, _pub_bytes(priv_b), _pub_bytes(priv_a)) is True

    def test_self_forward_valid(self):
        """The original author can also be the forwarder."""
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "my own message")
        fwd = compose_forward(priv, cert, orig)
        assert verify_forward(fwd, _pub_bytes(priv), _pub_bytes(priv)) is True
