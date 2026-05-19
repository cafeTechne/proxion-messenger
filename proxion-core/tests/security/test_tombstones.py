"""Tests for apply_deletions() tombstone authorization and chain integrity."""
import hashlib
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import Message, compose, apply_deletions, check_hash_chain
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert_priv():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    cert = RelationshipCertificate(
        certificate_id="cert-tombstone-test",
        issuer=pub,
        subject=pub,
        capabilities=[],
    )
    return cert, priv


class TestApplyDeletions:
    def test_author_can_delete_own_message(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "delete me")
        tombstone = compose(priv, cert, "", prev_msg=orig, message_type="delete", reply_to_id=orig.message_id)
        result = apply_deletions([orig, tombstone])
        assert all(m.message_id != orig.message_id for m in result)

    def test_tombstone_itself_removed_from_output(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "msg")
        tombstone = compose(priv, cert, "", prev_msg=orig, message_type="delete", reply_to_id=orig.message_id)
        result = apply_deletions([orig, tombstone])
        assert all(m.message_type != "delete" for m in result)

    def test_cross_author_deletion_rejected(self):
        cert_a, priv_a = _make_cert_priv()
        cert_b, priv_b = _make_cert_priv()
        orig = compose(priv_a, cert_a, "alice's message")
        # Bob tries to delete Alice's message
        tombstone = compose(priv_b, cert_b, "", message_type="delete", reply_to_id=orig.message_id)
        result = apply_deletions([orig, tombstone])
        # Original must survive; tombstone dropped
        assert any(m.message_id == orig.message_id for m in result)

    def test_other_messages_preserved(self):
        cert, priv = _make_cert_priv()
        m0 = compose(priv, cert, "keep me")
        m1 = compose(priv, cert, "delete me", prev_msg=m0)
        tombstone = compose(priv, cert, "", prev_msg=m1, message_type="delete", reply_to_id=m1.message_id)
        result = apply_deletions([m0, m1, tombstone])
        assert len(result) == 1
        assert result[0].message_id == m0.message_id

    def test_empty_list_returns_empty(self):
        assert apply_deletions([]) == []

    def test_no_deletions_returns_originals(self):
        cert, priv = _make_cert_priv()
        msgs = [compose(priv, cert, f"msg {i}") for i in range(3)]
        assert apply_deletions(msgs) == msgs

    def test_tombstone_without_reply_to_id_ignored(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "message")
        import dataclasses
        orphan = dataclasses.replace(
            compose(priv, cert, "", message_type="delete"),
            reply_to_id=None,
        )
        result = apply_deletions([orig, orphan])
        assert any(m.message_id == orig.message_id for m in result)

    def test_hash_chain_valid_with_tombstone(self):
        cert, priv = _make_cert_priv()
        m0 = compose(priv, cert, "genesis")
        m1 = compose(priv, cert, "message", prev_msg=m0)
        tombstone = compose(priv, cert, "", prev_msg=m1, message_type="delete", reply_to_id=m1.message_id)
        breaks = check_hash_chain([m0, m1, tombstone])
        assert breaks == []

    def test_apply_deletions_then_chain_still_valid(self):
        cert, priv = _make_cert_priv()
        m0 = compose(priv, cert, "stay")
        m1 = compose(priv, cert, "go", prev_msg=m0)
        tombstone = compose(priv, cert, "", prev_msg=m1, message_type="delete", reply_to_id=m1.message_id)
        remaining = apply_deletions([m0, m1, tombstone])
        # Single genesis block — no breaks
        assert check_hash_chain(remaining) == []

    def test_multiple_deletions(self):
        cert, priv = _make_cert_priv()
        m0 = compose(priv, cert, "keep")
        m1 = compose(priv, cert, "del-1", prev_msg=m0)
        t1 = compose(priv, cert, "", prev_msg=m1, message_type="delete", reply_to_id=m1.message_id)
        m2 = compose(priv, cert, "del-2", prev_msg=t1)
        t2 = compose(priv, cert, "", prev_msg=m2, message_type="delete", reply_to_id=m2.message_id)
        result = apply_deletions([m0, m1, t1, m2, t2])
        assert len(result) == 1
        assert result[0].message_id == m0.message_id
