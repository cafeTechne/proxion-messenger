"""Sealed-sender envelope: hides the sender's identity from the relay gateway.

Wire format
-----------
    eph_pub (32 bytes)  || nonce (12 bytes) || ciphertext (variable)

where ``ciphertext`` is AES-256-GCM over a JSON payload:

    {
        "sender_cert": {
            "webid":          str,         # sender's WebID
            "identity_pub_b64": str,       # sender's raw Ed25519 pub, base64
            "timestamp":      float,       # Unix time of cert issuance
            "sig_b64":        str,         # Ed25519 sig over "webid||identity_pub||timestamp"
        },
        "message": <original message dict>
    }

Usage
-----
    sealed = seal(sender_cert, message_dict, recipient_x25519_pub_bytes)
    import base64
    sealed_b64 = base64.b64encode(sealed).decode()

    # On the recipient side:
    sender_cert, message = unseal(
        base64.b64decode(sealed_b64),
        recipient_x25519_priv_bytes,
    )
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _derive_key(dh_output: bytes, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from ECDH output via HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"ProxionSealedSenderV1",
    ).derive(dh_output)


def make_sender_cert(
    sender_webid: str,
    identity_key,  # Ed25519PrivateKey
) -> dict:
    """Build a self-signed sender certificate.

    Parameters
    ----------
    sender_webid:
        The sender's WebID URI.
    identity_key:
        Ed25519PrivateKey used to sign the certificate.

    Returns
    -------
    dict with keys: webid, identity_pub_b64, timestamp, sig_b64.
    """
    pub_bytes = identity_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    ts = time.time()
    msg = f"{sender_webid}|{_b64e(pub_bytes)}|{ts}".encode("utf-8")
    sig = identity_key.sign(msg)
    return {
        "webid": sender_webid,
        "identity_pub_b64": _b64e(pub_bytes),
        "timestamp": ts,
        "sig_b64": _b64e(sig),
    }


def verify_sender_cert(cert: dict) -> bool:
    """Verify the Ed25519 signature on a sender certificate.

    Returns True if the signature is valid, False otherwise.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub_bytes = _b64d(cert["identity_pub_b64"])
        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
        ts = cert["timestamp"]
        msg = f"{cert['webid']}|{cert['identity_pub_b64']}|{ts}".encode("utf-8")
        pub.verify(_b64d(cert["sig_b64"]), msg)
        return True
    except Exception:
        return False


def seal(
    sender_cert: dict,
    message: dict,
    recipient_x25519_pub_bytes: bytes,
) -> bytes:
    """Encrypt a message inside a sealed envelope.

    The resulting bytes reveal only the recipient's identity (via the outer
    addressing layer); the gateway cannot see the sender's WebID.

    Returns
    -------
    bytes: eph_pub(32) || nonce(12) || ciphertext
    """
    # Ephemeral X25519 keypair
    eph_priv = X25519PrivateKey.generate()
    eph_pub_bytes = eph_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    # ECDH
    recipient_pub = X25519PublicKey.from_public_bytes(recipient_x25519_pub_bytes)
    dh_out = eph_priv.exchange(recipient_pub)
    aes_key = _derive_key(dh_out, salt=eph_pub_bytes)

    # Encrypt
    nonce = os.urandom(12)
    inner = json.dumps({"sender_cert": sender_cert, "message": message}).encode("utf-8")
    ciphertext = AESGCM(aes_key).encrypt(nonce, inner, eph_pub_bytes)

    return eph_pub_bytes + nonce + ciphertext


def unseal(
    sealed_bytes: bytes,
    recipient_x25519_priv_bytes: bytes,
) -> tuple[dict, dict]:
    """Decrypt a sealed envelope.

    Parameters
    ----------
    sealed_bytes:
        The raw bytes produced by ``seal``.
    recipient_x25519_priv_bytes:
        Raw 32-byte X25519 private key of the recipient.

    Returns
    -------
    (sender_cert, message) — both dicts.

    Raises
    ------
    cryptography.exceptions.InvalidTag
        If authentication fails (wrong key, tampered ciphertext).
    ValueError
        If the sealed bytes are too short or malformed.
    """
    if len(sealed_bytes) < 44:
        raise ValueError("sealed_bytes too short")

    eph_pub_bytes = sealed_bytes[:32]
    nonce = sealed_bytes[32:44]
    ciphertext = sealed_bytes[44:]

    # ECDH
    recipient_priv = X25519PrivateKey.from_private_bytes(recipient_x25519_priv_bytes)
    eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)
    dh_out = recipient_priv.exchange(eph_pub)
    aes_key = _derive_key(dh_out, salt=eph_pub_bytes)

    plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, eph_pub_bytes)
    inner = json.loads(plaintext)
    return inner["sender_cert"], inner["message"]
