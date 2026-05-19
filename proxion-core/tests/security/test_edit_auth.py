"""Tests for apply_edits() author-authorization enforcement."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core.messaging import Message, compose, apply_edits
from proxion_messenger_core.federation import RelationshipCertificate


def _make_cert_priv():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    cert = RelationshipCertificate(
        certificate_id="cert-edit-auth-test",
        issuer=pub,
        subject=pub,
        capabilities=[],
    )
    return cert, priv


def _pub_hex(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


class TestApplyEditsAuthorship:
    def test_same_author_edit_applied(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "hello")
        edit = compose(priv, cert, "hello edited", message_type="edit", reply_to_id=orig.message_id)
        result = apply_edits([orig, edit])
        assert len(result) == 1
        assert result[0].content == "hello edited"

    def test_cross_author_edit_rejected(self):
        cert_a, priv_a = _make_cert_priv()
        cert_b, priv_b = _make_cert_priv()
        orig = compose(priv_a, cert_a, "alice's message")
        # Bob tries to edit Alice's message
        edit = compose(priv_b, cert_b, "bob's edit attempt", message_type="edit", reply_to_id=orig.message_id)
        result = apply_edits([orig, edit])
        assert len(result) == 1
        assert result[0].content == "alice's message"

    def test_edit_without_reply_to_id_ignored(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "original")
        # Malformed edit: no reply_to_id
        import dataclasses
        orphan_edit = dataclasses.replace(
            compose(priv, cert, "orphan edit", message_type="edit"),
            reply_to_id=None,
        )
        result = apply_edits([orig, orphan_edit])
        assert result == [orig]

    def test_latest_same_author_edit_wins(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "v1")
        edit1 = compose(priv, cert, "v2", message_type="edit", reply_to_id=orig.message_id)
        import time; time.sleep(0.01)
        import dataclasses
        edit2 = dataclasses.replace(
            compose(priv, cert, "v3", message_type="edit", reply_to_id=orig.message_id),
            timestamp=edit1.timestamp + 1,
        )
        result = apply_edits([orig, edit1, edit2])
        assert result[0].content == "v3"

    def test_mixed_author_edits_only_owner_applied(self):
        cert_a, priv_a = _make_cert_priv()
        cert_b, priv_b = _make_cert_priv()
        orig = compose(priv_a, cert_a, "original")
        good_edit = compose(priv_a, cert_a, "authorized edit", message_type="edit", reply_to_id=orig.message_id)
        bad_edit = compose(priv_b, cert_b, "unauthorized edit", message_type="edit", reply_to_id=orig.message_id)
        result = apply_edits([orig, good_edit, bad_edit])
        assert len(result) == 1
        assert result[0].content == "authorized edit"

    def test_edit_of_nonexistent_message_dropped(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "real message")
        dangling_edit = compose(priv, cert, "edit of ghost", message_type="edit", reply_to_id="nonexistent-id")
        result = apply_edits([orig, dangling_edit])
        assert len(result) == 1
        assert result[0].message_id == orig.message_id

    def test_empty_list_returns_empty(self):
        assert apply_edits([]) == []

    def test_no_edits_returns_originals(self):
        cert, priv = _make_cert_priv()
        msgs = [compose(priv, cert, f"msg {i}") for i in range(3)]
        assert apply_edits(msgs) == msgs

    def test_edit_messages_filtered_from_output(self):
        cert, priv = _make_cert_priv()
        orig = compose(priv, cert, "text")
        edit = compose(priv, cert, "edited text", message_type="edit", reply_to_id=orig.message_id)
        result = apply_edits([orig, edit])
        assert all(m.message_type != "edit" for m in result)
