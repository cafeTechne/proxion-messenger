"""Tests for strict key-type validation in verify_relay_message (Round 6)."""
import pytest
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.relay import verify_relay_message, sign_relay_message


def _make_identity():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    did = pub_key_to_did(pub)
    return priv, did


class TestRelayKeyTypeValidation:
    def test_relay_valid_key_path_unchanged(self):
        priv, did = _make_identity()
        ts = datetime.now(timezone.utc).isoformat()
        sig = sign_relay_message(priv, did, "did:key:z6MkTarget", "msg-v", "hello", ts)
        assert verify_relay_message(did, "did:key:z6MkTarget", "msg-v", "hello", ts, sig) is True

    def test_relay_rejects_malformed_did_multicodec(self):
        """A DID that decodes to non-32 bytes should be rejected."""
        # Craft a DID that doesn't produce 32 bytes
        result = verify_relay_message(
            "did:key:z6Mk",  # Too short — will fail multicodec decode
            "did:key:z6MkTarget",
            "msg-bad",
            "content",
            datetime.now(timezone.utc).isoformat(),
            "invalidsig",
        )
        assert result is False

    def test_verify_returns_false_not_raises_on_bad_did(self):
        """Bad DID should return False, not raise an exception."""
        result = verify_relay_message(
            "not-a-did-at-all",
            "did:key:z6MkTarget",
            "msg-err",
            "hello",
            datetime.now(timezone.utc).isoformat(),
            "badsig",
        )
        assert result is False
