"""End-to-end encryption for Proxion messages using AES-256-GCM.

Messages are encrypted with a key derived from the shared capability certificate.
The pod operator cannot read encrypted messages.
"""

from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

if TYPE_CHECKING:
    from .federation import RelationshipCertificate


def derive_message_key(cert: RelationshipCertificate) -> bytes:
    """Derive a 32-byte AES-256-GCM key from a certificate.
    
    Uses HKDF-SHA256 with the certificate bytes as input material.
    
    Parameters
    ----------
    cert : RelationshipCertificate
        The certificate to derive from.
    
    Returns
    -------
    bytes
        32-byte AES-256-GCM key.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"proxion-message-key-v1",
    )
    # Use cert_bytes if it is real bytes (legacy / test path).
    # For real RelationshipCertificate objects (which have to_dict()) serialize
    # the canonical form instead.
    raw = getattr(cert, "cert_bytes", None)
    if isinstance(raw, bytes):
        material = raw
    else:
        material = json.dumps(cert.to_dict(), sort_keys=True).encode("utf-8")
    return hkdf.derive(material)


def encrypt_message(plaintext: str, key: bytes) -> str:
    """Encrypt a message using AES-256-GCM.
    
    Generates a random 12-byte nonce, encrypts the plaintext, and returns
    the result as a base64url string with an "enc1:" prefix.
    
    Parameters
    ----------
    plaintext : str
        The plaintext message to encrypt.
    key : bytes
        32-byte AES-256-GCM key.
    
    Returns
    -------
    str
        Encrypted message as "enc1:<base64url(nonce || ciphertext || tag)>".
    """
    nonce = os.urandom(12)
    cipher = AESGCM(key)
    
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext = cipher.encrypt(nonce, plaintext_bytes, None)
    
    # Combine nonce + ciphertext (tag is appended by AESGCM)
    combined = nonce + ciphertext
    
    # Base64url encode (no padding)
    encoded = base64.urlsafe_b64encode(combined).decode("ascii").rstrip("=")
    
    return f"enc1:{encoded}"


def decrypt_message(ciphertext: str, key: bytes) -> str:
    """Decrypt a message using AES-256-GCM.
    
    Checks for the "enc1:" prefix; if absent, returns the ciphertext as-is
    (backward compatibility with unencrypted messages).
    
    Parameters
    ----------
    ciphertext : str
        The encrypted message or plaintext.
    key : bytes
        32-byte AES-256-GCM key.
    
    Returns
    -------
    str
        Decrypted plaintext.
    
    Raises
    ------
    cryptography.hazmat.primitives.ciphers.InvalidTag
        If the authentication tag is invalid (wrong key or corrupted ciphertext).
    """
    if not ciphertext.startswith("enc1:"):
        # Not encrypted, return as-is
        return ciphertext
    
    # Strip prefix and decode from base64url
    encoded = ciphertext[5:]
    # Add padding if necessary
    padding = (4 - len(encoded) % 4) % 4
    encoded += "=" * padding
    
    try:
        combined = base64.urlsafe_b64decode(encoded)
    except Exception as e:
        raise ValueError(f"Invalid base64url encoding: {e}")
    
    # Split nonce and ciphertext (first 12 bytes are nonce, rest is ciphertext+tag)
    if len(combined) < 12:
        raise ValueError("Ciphertext too short")
    
    nonce = combined[:12]
    ciphertext_with_tag = combined[12:]
    
    cipher = AESGCM(key)
    plaintext_bytes = cipher.decrypt(nonce, ciphertext_with_tag, None)
    
    return plaintext_bytes.decode("utf-8")


def is_encrypted(content: str) -> bool:
    """Check if a message is encrypted.
    
    Parameters
    ----------
    content : str
        The message content to check.
    
    Returns
    -------
    bool
        True if the message starts with "enc1:", False otherwise.
    """
    return content.startswith("enc1:")
