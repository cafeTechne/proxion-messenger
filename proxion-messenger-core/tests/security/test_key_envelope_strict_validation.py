"""Tests for R7 key_envelope strict validation, base64 length bounds, standardized error."""
import pytest
import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from proxion_messenger_core.key_envelope import (
    derive_wrap_key_scrypt,
    encrypt_key_bundle,
    decrypt_key_bundle,
)
from proxion_messenger_core.persist import PersistError


def make_wrap_key() -> bytearray:
    return bytearray(os.urandom(32))


class TestKeyEnvelopeStrictValidation:
    def test_decrypt_roundtrip(self):
        """Basic encrypt/decrypt roundtrip works."""
        key = make_wrap_key()
        key2 = bytearray(bytes(key))  # copy for decrypt
        bundle = {"identity_key": "abc123", "store_key": "xyz"}
        envelope = encrypt_key_bundle(bundle, key)
        result = decrypt_key_bundle(envelope, key2)
        assert result == bundle

    def test_unknown_envelope_keys_rejected_in_strict_mode(self):
        """In strict mode, unknown envelope fields raise PersistError."""
        key = make_wrap_key()
        key2 = bytearray(bytes(key))
        bundle = {"k": "v"}
        envelope = encrypt_key_bundle(bundle, key)
        envelope["evil_field"] = "injected"
        with pytest.raises(PersistError, match="invalid key envelope"):
            decrypt_key_bundle(envelope, key2, strict=True)

    def test_unknown_envelope_keys_allowed_in_non_strict_mode(self):
        """In non-strict mode, unknown fields are tolerated."""
        key = make_wrap_key()
        key2 = bytearray(bytes(key))
        bundle = {"k": "v"}
        envelope = encrypt_key_bundle(bundle, key)
        envelope["extra_field"] = "ok"
        # Should not raise — extra fields ignored
        result = decrypt_key_bundle(envelope, key2, strict=False)
        assert result == bundle

    def test_invalid_base64_length_rejected_pre_decrypt(self):
        """Oversized nonce_b64 is rejected before decryption attempt."""
        key = make_wrap_key()
        bundle = {"k": "v"}
        envelope = encrypt_key_bundle(bundle, bytearray(bytes(key)))
        # Replace nonce with something exceeding the max length
        envelope["nonce_b64"] = "A" * 1000
        key2 = make_wrap_key()
        with pytest.raises(PersistError, match="invalid key envelope"):
            decrypt_key_bundle(envelope, key2)

    def test_invalid_envelope_returns_standardized_error(self):
        """Any decryption failure raises PersistError('invalid key envelope')."""
        key = make_wrap_key()
        bad_envelope = {
            "scheme": "scrypt-aes256gcm-v1",
            "nonce_b64": base64.b64encode(os.urandom(12)).decode(),
            "ciphertext_b64": base64.b64encode(b"corrupted").decode(),
        }
        with pytest.raises(PersistError, match="invalid key envelope"):
            decrypt_key_bundle(bad_envelope, key)

    def test_wrong_key_raises_standardized_error(self):
        """Wrong decryption key raises PersistError('invalid key envelope')."""
        key = make_wrap_key()
        bundle = {"data": "secret"}
        envelope = encrypt_key_bundle(bundle, key)
        wrong_key = make_wrap_key()
        with pytest.raises(PersistError, match="invalid key envelope"):
            decrypt_key_bundle(envelope, wrong_key)

    def test_strict_mode_validates_all_three_allowed_keys(self):
        """strict=True accepts envelopes with exactly the expected keys."""
        key = make_wrap_key()
        key2 = bytearray(bytes(key))
        bundle = {"x": 1}
        envelope = encrypt_key_bundle(bundle, key)
        # Should have exactly {scheme, nonce_b64, ciphertext_b64}
        assert set(envelope.keys()) == {"scheme", "nonce_b64", "ciphertext_b64"}
        result = decrypt_key_bundle(envelope, key2, strict=True)
        assert result == bundle
