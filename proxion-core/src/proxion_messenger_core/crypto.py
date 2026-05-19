"""AES-256-GCM encryption for Proxion resource envelopes."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import CipherError

_NONCE_SIZE = 12   # 96-bit nonce — NIST SP 800-38D recommendation
_KEY_SIZE = 32     # AES-256


def _b64enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64dec(s: str) -> bytes:
    padding = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


class Cipher:
    """AES-256-GCM encryption wrapper for Proxion resource envelopes.

    Each encrypt() call generates a fresh random nonce, so the same
    key can safely encrypt many messages without nonce reuse.

    Envelope format (JSON-serialisable dict):
        {
            "@type":      "EncryptedResource",
            "alg":        "AES-256-GCM",
            "nonce":      <base64url, 12 bytes>,
            "ciphertext": <base64url, plaintext + 16-byte GCM tag>,
        }
    """

    ALG = "AES-256-GCM"

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_SIZE:
            raise CipherError(
                f"key must be exactly {_KEY_SIZE} bytes for AES-256, got {len(key)}"
            )
        self._aesgcm = AESGCM(key)

    def encrypt(self, data: Any) -> dict:
        """Encrypt *data* (any JSON-serialisable value) and return an envelope."""
        plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(_NONCE_SIZE)
        # AESGCM.encrypt appends the 16-byte authentication tag automatically.
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
        return {
            "@type": "EncryptedResource",
            "alg": self.ALG,
            "nonce": _b64enc(nonce),
            "ciphertext": _b64enc(ciphertext),
        }

    def _decode_envelope(self, envelope: dict) -> tuple[bytes, bytes]:
        """Parse and validate an envelope; return (nonce, ciphertext) bytes."""
        if envelope.get("alg") != self.ALG:
            raise CipherError(f"unsupported alg: {envelope.get('alg')!r}")
        try:
            nonce = _b64dec(envelope["nonce"])
            ciphertext = _b64dec(envelope["ciphertext"])
        except (KeyError, ValueError) as exc:
            raise CipherError(f"malformed envelope: {exc}") from exc
        if len(nonce) != _NONCE_SIZE:
            raise CipherError(f"nonce must be {_NONCE_SIZE} bytes, got {len(nonce)}")
        return nonce, ciphertext

    def decrypt(self, envelope: dict) -> Any:
        """Decrypt an envelope produced by :meth:`encrypt`.

        Raises :class:`~proxion_messenger_core.errors.CipherError` if the alg is
        unsupported, the envelope is malformed, or authentication fails
        (wrong key or tampered ciphertext).
        """
        nonce, ciphertext = self._decode_envelope(envelope)
        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise CipherError(
                "decryption failed — wrong key or tampered ciphertext"
            ) from exc
        return json.loads(plaintext)
