"""Tests for MAX_FORWARD_DEPTH nesting protection in verify_forward()."""
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import (
    Message, compose, compose_forward, verify_forward,
    _forward_depth, MAX_FORWARD_DEPTH,
)
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert_priv(cert_id="cert-depth-test"):
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


def _build_chain(depth: int):
    """Build a forward chain of the given depth. Returns (outermost_msg, all_privkeys)."""
    cert, priv = _make_cert_priv()
    msg = compose(priv, cert, "original")
    privkeys = [priv]
    for i in range(depth):
        msg = compose_forward(priv, cert, msg)
        privkeys.append(priv)
    return msg, priv


class TestForwardDepthCheck:
    def test_non_forward_has_depth_zero(self):
        cert, priv = _make_cert_priv()
        msg = compose(priv, cert, "plain text")
        assert _forward_depth(msg) == 0

    def test_single_forward_has_depth_one(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        fwd = compose_forward(priv, cert, orig)
        assert _forward_depth(fwd) == 1

    def test_depth_two(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        fwd1 = compose_forward(priv, cert, orig)
        fwd2 = compose_forward(priv, cert, fwd1)
        assert _forward_depth(fwd2) == 2

    def test_depth_at_max_allowed(self):
        msg, priv = _build_chain(MAX_FORWARD_DEPTH)
        assert _forward_depth(msg) == MAX_FORWARD_DEPTH

    def test_depth_one_over_max(self):
        msg, priv = _build_chain(MAX_FORWARD_DEPTH + 1)
        assert _forward_depth(msg) > MAX_FORWARD_DEPTH

    def test_malformed_content_counts_as_depth_one(self):
        import dataclasses
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        fwd = dataclasses.replace(
            compose_forward(priv, cert, orig),
            content="not valid json",
        )
        assert _forward_depth(fwd) == 1


class TestVerifyForwardDepthRejection:
    def test_depth_within_limit_accepted(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        # Build chain at max depth
        msg = orig
        for _ in range(MAX_FORWARD_DEPTH):
            msg = compose_forward(priv, cert, msg)
        # The outermost forwarder is priv; the innermost original is also priv
        assert verify_forward(msg, _pub_bytes(priv), _pub_bytes(priv)) is True

    def test_depth_one_over_max_rejected(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        msg = orig
        for _ in range(MAX_FORWARD_DEPTH + 1):
            msg = compose_forward(priv, cert, msg)
        assert verify_forward(msg, _pub_bytes(priv), _pub_bytes(priv)) is False

    def test_depth_far_over_max_rejected(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        msg = orig
        for _ in range(MAX_FORWARD_DEPTH + 10):
            msg = compose_forward(priv, cert, msg)
        assert verify_forward(msg, _pub_bytes(priv), _pub_bytes(priv)) is False

    def test_flat_forward_accepted(self):
        cert_a, priv_a = _make_cert_priv("cert-a")
        cert_b, priv_b = _make_cert_priv("cert-b")
        orig = compose(priv_a, cert_a, "original content")
        fwd = compose_forward(priv_b, cert_b, orig)
        assert verify_forward(fwd, _pub_bytes(priv_b), _pub_bytes(priv_a)) is True

    def test_max_forward_depth_constant_is_five(self):
        assert MAX_FORWARD_DEPTH == 5
