"""Tests for sig_key_version in relay signing/verification (Round 5)."""
import pytest
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.relay import sign_relay_message, verify_relay_message


def _make_identity():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    did = pub_key_to_did(pub)
    return priv, did


class TestRelaySigKeyVersion:
    def test_sign_without_sig_key_version_still_verifies(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-1", "hello", ts)
        assert verify_relay_message(did, "did:key:z6MkTarget", "msg-1", "hello", ts, sig)

    def test_sign_with_sig_key_version_verifies_with_same_version(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-2", "hello", ts, sig_key_version=1)
        assert verify_relay_message(did, "did:key:z6MkTarget", "msg-2", "hello", ts, sig, sig_key_version=1)

    def test_verify_relay_accepts_missing_sig_key_version_for_compat(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        # Signed without version
        sig = sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-3", "hello", ts)
        # Verify without version — must pass (backward compat)
        assert verify_relay_message(did, "did:key:z6MkTarget", "msg-3", "hello", ts, sig)

    def test_verify_relay_rejects_invalid_sig_key_version_type(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-4", "hello", ts)
        # Passing a negative as sig_key_version to verify should return False
        assert verify_relay_message(did, "did:key:z6MkTarget", "msg-4", "hello", ts, sig, sig_key_version=-1) is False

    def test_sign_rejects_invalid_sig_key_version(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        with pytest.raises(ValueError):
            sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-5", "hello", ts, sig_key_version=0)

    def test_sign_relay_includes_sig_key_version_in_canonical(self):
        """Version mismatch between sign and verify should fail signature check."""
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-6", "hello", ts, sig_key_version=1)
        # Verifying with version=2 should fail — different canonical string
        assert verify_relay_message(did, "did:key:z6MkTarget", "msg-6", "hello", ts, sig, sig_key_version=2) is False
