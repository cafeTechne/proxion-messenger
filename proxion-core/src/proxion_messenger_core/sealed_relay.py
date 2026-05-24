"""Gateway-to-gateway sealed relay encryption.

Wraps the relay payload so `from_webid` is hidden from HTTP intermediaries.
The receiving gateway decrypts with its X25519 private key; the sender is
revealed only to the receiving gateway.

Wire format (sealed_payload field value):
    base64url( ephemeral_pub_32 || nonce_12 || chacha20poly1305(json_payload) )
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

_INFO = b"proxion-relay-seal-v1"


def seal_relay_payload(payload: dict, peer_x25519_pub_b64: str) -> str:
    """Encrypt *payload* for the peer gateway identified by *peer_x25519_pub_b64*.

    Returns a base64url string (no padding) suitable for the ``sealed_payload`` field.
    """
    # Pad if necessary before decoding
    pad = "=" * (-len(peer_x25519_pub_b64) % 4)
    peer_pub_bytes = base64.urlsafe_b64decode(peer_x25519_pub_b64 + pad)
    peer_pub = X25519PublicKey.from_public_bytes(peer_pub_bytes)

    ephemeral_priv = X25519PrivateKey.generate()
    ephemeral_pub_bytes = ephemeral_priv.public_key().public_bytes_raw()

    shared = ephemeral_priv.exchange(peer_pub)
    key = HKDF(SHA256(), 32, salt=None, info=_INFO).derive(shared)

    nonce = os.urandom(12)
    plaintext = json.dumps(payload).encode()
    chacha = ChaCha20Poly1305(key)
    ct = chacha.encrypt(nonce, plaintext, None)

    raw = ephemeral_pub_bytes + nonce + ct
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unseal_relay_payload(sealed: str, own_x25519_priv_bytes: bytes) -> dict:
    """Decrypt a sealed relay payload using the gateway's X25519 private key.

    Raises ``ValueError`` on decryption failure.
    """
    try:
        pad = "=" * (-len(sealed) % 4)
        raw = base64.urlsafe_b64decode(sealed + pad)
        if len(raw) < 44:
            raise ValueError("sealed payload too short")
        ephemeral_pub_bytes = raw[:32]
        nonce = raw[32:44]
        ct = raw[44:]

        own_priv = X25519PrivateKey.from_private_bytes(own_x25519_priv_bytes)
        ephemeral_pub = X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)
        shared = own_priv.exchange(ephemeral_pub)
        key = HKDF(SHA256(), 32, salt=None, info=_INFO).derive(shared)

        chacha = ChaCha20Poly1305(key)
        plaintext = chacha.decrypt(nonce, ct, None)
        return json.loads(plaintext)
    except Exception as exc:
        raise ValueError(f"unseal failed: {exc}") from exc
