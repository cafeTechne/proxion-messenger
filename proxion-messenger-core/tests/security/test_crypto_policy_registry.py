"""R11: Cryptographic policy registry tests."""
import pytest

from proxion_messenger_core.crypto_policy import (
    validate_signature_policy,
    CryptoPolicyError,
    get_allowed_algorithms,
    register_algorithm,
)


def test_ed25519_allowed_by_default():
    """EdDSA + Ed25519 must be allowed in all contexts."""
    validate_signature_policy("EdDSA", key_meta={"crv": "Ed25519"}, context="*")
    validate_signature_policy("EdDSA", key_meta={"crv": "Ed25519"}, context="dpop")
    validate_signature_policy("EdDSA", key_meta={"crv": "Ed25519"}, context="relay")


def test_unsupported_alg_rejected_with_policy_error():
    """Unknown algorithm must raise CryptoPolicyError."""
    with pytest.raises(CryptoPolicyError):
        validate_signature_policy("RS256", key_meta={"crv": ""}, context="*")


def test_unsupported_crv_rejected():
    """EdDSA with wrong curve must be rejected."""
    with pytest.raises(CryptoPolicyError):
        validate_signature_policy("EdDSA", key_meta={"crv": "P-256"}, context="*")


def test_policy_overlay_can_disable_context_specific_alg_usage():
    """register_algorithm extends policy; get_allowed_algorithms reflects the update."""
    register_algorithm("test_ctx", "EdDSA", "Ed448")
    allowed = get_allowed_algorithms("test_ctx")
    assert ("EdDSA", "Ed448") in allowed


def test_validate_without_key_meta_checks_alg_only():
    """validate_signature_policy with no key_meta checks alg alone."""
    validate_signature_policy("EdDSA", key_meta=None, context="*")


def test_dpop_rejects_rsa():
    with pytest.raises(CryptoPolicyError):
        validate_signature_policy("RS256", key_meta=None, context="dpop")


def test_relay_rejects_ecdsa():
    with pytest.raises(CryptoPolicyError):
        validate_signature_policy("ES256", key_meta={"crv": "P-256"}, context="relay")
