"""Unit tests for account device certificates (multi-device delegation, slice 1)."""
from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.device_cert import (
    MAX_TTL_DAYS,
    issue_device_cert,
    verify_device_cert,
)
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv: Ed25519PrivateKey) -> str:
    return pub_key_to_did(priv.public_key().public_bytes_raw())


@pytest.fixture
def account():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def device():
    return Ed25519PrivateKey.generate()


def test_issue_then_verify_roundtrip(account, device):
    device_did = _did(device)
    cert = issue_device_cert(account, device_did)
    assert cert["account_did"] == _did(account)
    assert cert["device_did"] == device_did
    got = verify_device_cert(cert, expected_device_did=device_did)
    assert got == _did(account)


def test_verify_binds_to_expected_device(account, device):
    """A cert for device A must not verify when B claims control of its own DID."""
    other = Ed25519PrivateKey.generate()
    cert = issue_device_cert(account, _did(device))
    # The connection proved control of `other`, not `device` — reject.
    assert verify_device_cert(cert, expected_device_did=_did(other)) is None


def test_verify_binds_to_expected_account(account, device):
    stranger = Ed25519PrivateKey.generate()
    cert = issue_device_cert(account, _did(device))
    assert verify_device_cert(cert, expected_account_did=_did(stranger)) is None
    assert verify_device_cert(cert, expected_account_did=_did(account)) == _did(account)


def test_forged_signature_rejected(account, device):
    cert = issue_device_cert(account, _did(device))
    cert["signature"] = base64.b64encode(b"\x00" * 64).decode()
    assert verify_device_cert(cert) is None


def test_tampered_field_rejected(account, device):
    """Changing a signed field after issuance invalidates the signature."""
    victim = Ed25519PrivateKey.generate()
    cert = issue_device_cert(account, _did(device))
    cert["device_did"] = _did(victim)  # attacker swaps in their own device
    assert verify_device_cert(cert) is None


def test_expired_cert_rejected(account, device):
    past = time.time() - 10 * 86400
    cert = issue_device_cert(account, _did(device), ttl_days=1, now=past)
    assert verify_device_cert(cert) is None


def test_not_yet_expired_cert_accepted_at_issue_time(account, device):
    t = time.time()
    cert = issue_device_cert(account, _did(device), ttl_days=1, now=t)
    assert verify_device_cert(cert, now=t + 3600) == _did(account)


def test_ttl_bounds_enforced(account, device):
    with pytest.raises(ValueError):
        issue_device_cert(account, _did(device), ttl_days=0)
    with pytest.raises(ValueError):
        issue_device_cert(account, _did(device), ttl_days=MAX_TTL_DAYS + 1)


def test_device_did_must_be_did_key(account):
    with pytest.raises(ValueError):
        issue_device_cert(account, "https://not-a-did/card#me")


def test_malformed_cert_inputs_return_none(account, device):
    assert verify_device_cert(None) is None
    assert verify_device_cert({}) is None
    assert verify_device_cert({"account_did": "x", "device_did": "y", "signature": "z"}) is None
    # non-did:key account
    cert = issue_device_cert(account, _did(device))
    cert["account_did"] = "https://evil/card#me"
    assert verify_device_cert(cert) is None


def test_manually_forged_expiry_extension_rejected(account, device):
    """Extending expires_at past the signed value breaks the signature."""
    cert = issue_device_cert(account, _did(device), ttl_days=1)
    cert["expires_at"] = cert["issued_at"] + MAX_TTL_DAYS * 86400
    assert verify_device_cert(cert) is None
