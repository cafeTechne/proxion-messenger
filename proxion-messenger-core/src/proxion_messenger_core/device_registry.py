"""Per-device identity helpers.

Each device generates an Ed25519 keypair and proves ownership via an
attestation signature over (owner_webid, device_id, timestamp).
The gateway verifies the attestation before storing the device record.
"""
from __future__ import annotations

import base64
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)


def generate_device_key() -> dict:
    """Generate a fresh Ed25519 device keypair.

    Returns a dict with device_id, pub_b64, priv_b64, created_at.
    The caller must store priv_b64 securely and only share pub_b64.
    """
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return {
        "device_id": secrets.token_hex(16),
        "pub_b64": base64.b64encode(pub_bytes).decode(),
        "priv_b64": base64.b64encode(priv_bytes).decode(),
        "created_at": time.time(),
    }


def _attest_message(owner_webid: str, device_id: str, timestamp: float) -> bytes:
    """Canonical byte string to sign/verify for a device attestation."""
    parts = [owner_webid.encode(), device_id.encode(), str(int(timestamp)).encode()]
    return b"|".join(len(p).to_bytes(2, "big") + p for p in parts)


def sign_device_attestation(
    device_priv_bytes: bytes,
    owner_webid: str,
    device_id: str,
    timestamp: float,
) -> str:
    """Sign a device attestation proving device_id belongs to owner_webid.

    Returns base64-encoded Ed25519 signature.
    """
    priv = Ed25519PrivateKey.from_private_bytes(device_priv_bytes)
    msg = _attest_message(owner_webid, device_id, timestamp)
    return base64.b64encode(priv.sign(msg)).decode()


def verify_device_attestation(
    pub_b64: str,
    owner_webid: str,
    device_id: str,
    timestamp: float,
    sig_b64: str,
) -> bool:
    """Verify an attestation signature. Returns False on any failure."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        sig = base64.b64decode(sig_b64)
        msg = _attest_message(owner_webid, device_id, timestamp)
        pub.verify(sig, msg)
        return True
    except (InvalidSignature, Exception):
        return False
