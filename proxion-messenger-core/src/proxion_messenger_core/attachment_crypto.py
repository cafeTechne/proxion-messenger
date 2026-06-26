"""Client-side file attachment encryption (AES-256-GCM).

Attachments are encrypted before upload; the per-file key travels inside the
already-E2E-encrypted message envelope so the server never sees it in plaintext.

Usage
-----
    enc = encrypt_attachment(file_bytes)
    # upload enc["ciphertext_b64"] to the server
    # embed attachment_key_payload(...) inside the E2E message
    # recipient: file_bytes = decrypt_attachment(**enc)
"""
from __future__ import annotations

import base64
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DEFAULT_TTL = 604800  # 7 days


def encrypt_attachment(file_bytes: bytes, ttl_seconds: int = _DEFAULT_TTL) -> dict:
    """Encrypt file_bytes with a fresh random AES-256-GCM key.

    Returns
    -------
    dict with keys:
        ciphertext_b64  str   — encrypted content (upload this to the server)
        key_b64         str   — 32-byte AES key (embed in E2E message, never send to server)
        nonce_b64       str   — 12-byte nonce
        size            int   — plaintext size in bytes
        expires_at      float — Unix timestamp after which the key is considered expired
    """
    key = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, file_bytes, b"")
    return {
        "ciphertext_b64": base64.b64encode(ciphertext).decode(),
        "key_b64": base64.b64encode(key).decode(),
        "nonce_b64": base64.b64encode(nonce).decode(),
        "size": len(file_bytes),
        "expires_at": time.time() + ttl_seconds,
    }


def is_attachment_key_expired(key_payload: dict) -> bool:
    """Return True if the attachment key's TTL has elapsed.

    A payload without an ``expires_at`` field is treated as not expired.
    """
    expires_at = key_payload.get("expires_at")
    if expires_at is None:
        return False
    return time.time() > float(expires_at)


def decrypt_attachment(ciphertext_b64: str, key_b64: str, nonce_b64: str) -> bytes:
    """Decrypt an attachment produced by encrypt_attachment.

    Raises
    ------
    cryptography.exceptions.InvalidTag
        If authentication fails (wrong key, corrupted ciphertext, or tampering).
    """
    key = base64.b64decode(key_b64)
    nonce = base64.b64decode(nonce_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    return AESGCM(key).decrypt(nonce, ciphertext, b"")


def attachment_key_payload(
    key_b64: str,
    nonce_b64: str,
    filename: str,
    mime_type: str,
) -> dict:
    """Bundle attachment key material for embedding in an E2E message payload.

    The returned dict is included inside the encrypted E2E envelope so the
    server never learns the key.
    """
    return {
        "type": "attachment_key",
        "key_b64": key_b64,
        "nonce_b64": nonce_b64,
        "filename": filename,
        "mime_type": mime_type,
    }


_REQUIRED_ENVELOPE_FIELDS = ("key_b64", "nonce_b64")


def validate_attachment_envelope(envelope: dict) -> tuple[bool, str]:
    """Validate that an attachment key payload has all required fields.

    Returns
    -------
    (valid, reason)
        valid is True if the envelope is well-formed; reason describes the failure.
    """
    if not isinstance(envelope, dict):
        return False, "envelope_not_a_dict"
    for field in _REQUIRED_ENVELOPE_FIELDS:
        if not envelope.get(field):
            return False, f"missing_required_field:{field}"
    if envelope.get("type") == "attachment_key":
        if not envelope.get("filename"):
            return False, "missing_required_field:filename"
    return True, "ok"
