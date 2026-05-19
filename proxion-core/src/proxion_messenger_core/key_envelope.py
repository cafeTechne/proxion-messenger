"""Scrypt + AES-256-GCM key-bundle encryption helpers for AgentState persistence."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def derive_wrap_key_scrypt(passphrase: bytes, salt: bytes) -> bytearray:
    """Derive a 32-byte wrap key from *passphrase* using scrypt (n=32768, r=8, p=1).

    Returns a bytearray so callers can zeroize after use.
    maxmem is set to 64 MiB to satisfy OpenSSL's default memory limit check.
    """
    raw = hashlib.scrypt(
        passphrase, salt=salt, n=32768, r=8, p=1, dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return bytearray(raw)


def encrypt_key_bundle(bundle: dict, wrap_key: bytes | bytearray) -> dict:
    """Encrypt *bundle* (JSON-serialisable dict) with AES-256-GCM.

    Returns a dict with ``scheme``, ``nonce_b64``, and ``ciphertext_b64`` fields
    suitable for embedding in the agent state file.  The *wrap_key* buffer is
    zeroized on exit (best-effort).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key = bytes(wrap_key)
    nonce = os.urandom(12)
    plaintext = bytearray(json.dumps(bundle, separators=(",", ":")).encode("utf-8"))
    try:
        ciphertext = AESGCM(_key).encrypt(nonce, bytes(plaintext), None)
        return {
            "scheme": "scrypt-aes256gcm-v1",
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        }
    finally:
        # Best-effort zeroization of plaintext buffer
        for i in range(len(plaintext)):
            plaintext[i] = 0
        if isinstance(wrap_key, bytearray):
            for i in range(len(wrap_key)):
                wrap_key[i] = 0


_ENVELOPE_ALLOWED_KEYS = frozenset({"scheme", "nonce_b64", "ciphertext_b64"})
_NONCE_B64_MAX = 32   # 12 bytes → 16 base64 chars, with some headroom
_CIPHERTEXT_B64_MAX = 1_048_576  # 1 MB


def decrypt_key_bundle(envelope: dict, wrap_key: bytes | bytearray, strict: bool = False) -> dict:
    """Decrypt an envelope produced by :func:`encrypt_key_bundle`.

    When *strict* is True, unknown envelope keys are rejected.
    Always validates base64 length bounds before attempting decryption.
    Maps any failure to ``PersistError("invalid key envelope")`` to avoid
    leaking oracle information via distinct error messages.  The *wrap_key*
    buffer is zeroized on exit (best-effort).
    """
    from .persist import PersistError
    plaintext_buf: bytearray | None = None
    try:
        if strict:
            unknown = set(envelope.keys()) - _ENVELOPE_ALLOWED_KEYS
            if unknown:
                raise PersistError("invalid key envelope")
        nonce_b64 = envelope.get("nonce_b64", "")
        ciphertext_b64 = envelope.get("ciphertext_b64", "")
        if not isinstance(nonce_b64, str) or len(nonce_b64) > _NONCE_B64_MAX:
            raise PersistError("invalid key envelope")
        if not isinstance(ciphertext_b64, str) or len(ciphertext_b64) > _CIPHERTEXT_B64_MAX:
            raise PersistError("invalid key envelope")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        _key = bytes(wrap_key)
        nonce = base64.b64decode(nonce_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
        plaintext_bytes = AESGCM(_key).decrypt(nonce, ciphertext, None)
        plaintext_buf = bytearray(plaintext_bytes)
        result = json.loads(bytes(plaintext_buf))
        return result
    except PersistError:
        raise
    except Exception:
        raise PersistError("invalid key envelope")
    finally:
        if plaintext_buf is not None:
            for i in range(len(plaintext_buf)):
                plaintext_buf[i] = 0
        if isinstance(wrap_key, bytearray):
            for i in range(len(wrap_key)):
                wrap_key[i] = 0
