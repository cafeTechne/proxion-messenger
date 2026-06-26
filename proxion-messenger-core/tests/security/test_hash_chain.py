"""Tests for Merkle hash chain integrity in messaging.py."""
import hashlib
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import Message, compose, check_hash_chain
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert() -> RelationshipCertificate:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    cert = RelationshipCertificate(
        certificate_id="cert-hash-chain-test",
        issuer=pub,
        subject=pub,
        capabilities=[],
    )
    return cert, priv


def _genesis(cert, priv) -> Message:
    return compose(priv, cert, "genesis message")


def _chained(cert, priv, prev: Message) -> Message:
    return compose(priv, cert, f"after {prev.message_id}", prev_msg=prev)


# ---------------------------------------------------------------------------
# check_hash_chain unit tests
# ---------------------------------------------------------------------------

class TestCheckHashChain:
    def test_empty_list_no_breaks(self):
        assert check_hash_chain([]) == []

    def test_single_genesis_no_breaks(self):
        cert, priv = _make_cert()
        msgs = [_genesis(cert, priv)]
        assert check_hash_chain(msgs) == []

    def test_valid_chain_no_breaks(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        m2 = _chained(cert, priv, m1)
        assert check_hash_chain([m0, m1, m2]) == []

    def test_tampered_prev_hash_detected(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        # Manually corrupt m1's prev_hash
        import dataclasses
        m1_bad = dataclasses.replace(m1, prev_hash="deadbeef" * 8)
        breaks = check_hash_chain([m0, m1_bad])
        assert 1 in breaks

    def test_missing_middle_message_detected(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        m2 = _chained(cert, priv, m1)
        # Skip m1 — m2 now chains to m1 which is absent
        breaks = check_hash_chain([m0, m2])
        assert 1 in breaks

    def test_all_genesis_no_breaks(self):
        # Multiple messages with prev_hash="" treated as independent genesis blocks
        cert, priv = _make_cert()
        msgs = [_genesis(cert, priv) for _ in range(5)]
        assert check_hash_chain(msgs) == []

    def test_chain_after_legacy_genesis_valid(self):
        cert, priv = _make_cert()
        legacy = _genesis(cert, priv)  # prev_hash=""
        chained = _chained(cert, priv, legacy)
        assert check_hash_chain([legacy, chained]) == []

    def test_multiple_breaks_reported(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        m2 = _chained(cert, priv, m1)
        m3 = _chained(cert, priv, m2)
        import dataclasses
        m1_bad = dataclasses.replace(m1, prev_hash="aaa" * 21 + "a")
        m3_bad = dataclasses.replace(m3, prev_hash="bbb" * 21 + "b")
        breaks = check_hash_chain([m0, m1_bad, m2, m3_bad])
        assert 1 in breaks
        assert 3 in breaks


# ---------------------------------------------------------------------------
# prev_hash in canonical_bytes and compose
# ---------------------------------------------------------------------------

class TestPrevHashInCanonical:
    def test_genesis_prev_hash_is_empty(self):
        cert, priv = _make_cert()
        msg = _genesis(cert, priv)
        assert msg.prev_hash == ""

    def test_chained_prev_hash_matches_sha256_of_prev(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        expected = hashlib.sha256(m0.canonical_bytes()).hexdigest()
        assert m1.prev_hash == expected

    def test_prev_hash_in_canonical_bytes(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        canon = m1.canonical_bytes().decode()
        assert m1.prev_hash in canon

    def test_to_dict_includes_prev_hash_when_set(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        d = m1.to_dict()
        assert "prev_hash" in d
        assert d["prev_hash"] == m1.prev_hash

    def test_to_dict_omits_prev_hash_when_empty(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        d = m0.to_dict()
        assert "prev_hash" not in d

    def test_from_dict_roundtrip_preserves_prev_hash(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        m1 = _chained(cert, priv, m0)
        recovered = Message.from_dict(m1.to_dict())
        assert recovered.prev_hash == m1.prev_hash

    def test_from_dict_missing_prev_hash_defaults_empty(self):
        cert, priv = _make_cert()
        m0 = _genesis(cert, priv)
        d = m0.to_dict()
        d.pop("prev_hash", None)
        recovered = Message.from_dict(d)
        assert recovered.prev_hash == ""


# ---------------------------------------------------------------------------
# Backward compat: legacy messages (no prev_hash) still verify
# ---------------------------------------------------------------------------

class TestLegacySignatureVerification:
    def test_legacy_message_verifies_with_fallback(self):
        cert, priv = _make_cert()
        pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        # Simulate a pre-Round-14 message: canonical_bytes WITHOUT prev_hash
        import json, secrets, dataclasses
        raw_payload = {
            "message_id": secrets.token_urlsafe(16),
            "cert_id": cert.certificate_id,
            "from_pub_hex": pub_bytes.hex(),
            "content": "old message",
            "timestamp": 1_000_000,
            "reply_to_id": None,
            "message_type": "text",
        }
        legacy_canonical = json.dumps(raw_payload, sort_keys=True).encode("utf-8")
        sig = priv.sign(legacy_canonical).hex()
        legacy_msg = Message(
            message_id=raw_payload["message_id"],
            cert_id=raw_payload["cert_id"],
            from_pub_hex=raw_payload["from_pub_hex"],
            content=raw_payload["content"],
            timestamp=raw_payload["timestamp"],
            signature=sig,
            prev_hash="",  # absent in old messages
        )
        assert legacy_msg.verify(pub_bytes) is True
