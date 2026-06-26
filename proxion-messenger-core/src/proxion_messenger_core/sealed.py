"""Sealed-box encryption for the Proxion Coordination Store.

A *sealed envelope* is a message encrypted for a specific recipient using
their X25519 public key.  The sender generates an ephemeral keypair for each
message, so:

* The sender's identity is not revealed in the ciphertext.
* Each message has a unique key — compromise of one message reveals nothing
  about others.
* The store operator sees only opaque bytes; no plaintext ever touches the
  store.

Construction (ECIES / X25519 + HKDF-SHA256 + AES-256-GCM)
-----------------------------------------------------------
To **seal** a message for *recipient*:

1. Generate a fresh ephemeral X25519 keypair ``(eph_priv, eph_pub)``.
2. Compute ECDH: ``shared = X25519(eph_priv, recipient_pub)``.
3. Derive a 32-byte AES key: ``k = HKDF-SHA256(ikm=shared,
   info="proxion-sealed-v1" || eph_pub_bytes || recipient_pub_bytes)``.
4. Encrypt: ``ct = AES-256-GCM(k, nonce, plaintext)``.
5. Output: :class:`SealedEnvelope` carrying ``(eph_pub, nonce, ct)``.

To **open** an envelope as the *recipient*:

1. Extract ``eph_pub`` from the envelope.
2. Compute ECDH: ``shared = X25519(recipient_priv, eph_pub)``.
3. Re-derive the key (same HKDF call, deterministic).
4. Decrypt and authenticate the ciphertext.

Mailbox addressing
------------------
Agents are addressed by an opaque *mailbox ID* derived from their X25519
public key via :func:`mailbox_id_for`.  The store never learns the underlying
public key from the mailbox ID alone; senders compute it from the public key
they already have.

Key types
---------
Agents have **two separate keypairs**:

* ``identity_key``  — Ed25519 (signing, PoP, federation — see :mod:`pop`)
* ``store_key``     — X25519  (receiving sealed messages — this module)

Keeping them separate avoids the complexity of Ed25519→Curve25519 conversion
and provides clean cryptographic separation of concerns.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .errors import CipherError

_NONCE_SIZE = 12
_KEY_SIZE = 32
_HKDF_INFO_PREFIX = b"proxion-sealed-v1:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64dec(s: str) -> bytes:
    padding = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def _pub_raw(key: X25519PublicKey) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _derive_key(shared_secret: bytes, eph_pub_bytes: bytes, recipient_pub_bytes: bytes) -> bytes:
    """HKDF-SHA256: binds the key to the specific ephemeral/recipient pair."""
    info = _HKDF_INFO_PREFIX + eph_pub_bytes + recipient_pub_bytes
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=None,
        info=info,
    ).derive(shared_secret)


# ---------------------------------------------------------------------------
# Mailbox addressing
# ---------------------------------------------------------------------------

def mailbox_id_for(store_pubkey_bytes: bytes) -> str:
    """Derive the opaque mailbox ID for a recipient's X25519 public key.

    The mailbox ID is the hex-encoded SHA-256 of
    ``"proxion-mailbox:" + pubkey_bytes`` — deterministic for a given key,
    but not reversible to the public key by the store operator alone.
    """
    digest = hashlib.sha256(b"proxion-mailbox:" + store_pubkey_bytes).digest()
    return digest.hex()


# ---------------------------------------------------------------------------
# SealedEnvelope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SealedEnvelope:
    """An encrypted message readable only by the intended recipient.

    Attributes
    ----------
    ephemeral_pub:
        Raw 32-byte X25519 ephemeral public key used for this message.
    nonce:
        12-byte AES-GCM nonce.
    ciphertext:
        Encrypted payload including the 16-byte GCM authentication tag.
    """

    ephemeral_pub: bytes
    nonce: bytes
    ciphertext: bytes

    def to_dict(self) -> dict:
        return {
            "@type": "SealedEnvelope",
            "ephemeral_pub": _b64enc(self.ephemeral_pub),
            "nonce": _b64enc(self.nonce),
            "ciphertext": _b64enc(self.ciphertext),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SealedEnvelope":
        try:
            return cls(
                ephemeral_pub=_b64dec(d["ephemeral_pub"]),
                nonce=_b64dec(d["nonce"]),
                ciphertext=_b64dec(d["ciphertext"]),
            )
        except (KeyError, ValueError) as exc:
            raise CipherError(f"malformed SealedEnvelope: {exc}") from exc

    @property
    def byte_size(self) -> int:
        """Approximate wire size for quota accounting."""
        return len(self.ephemeral_pub) + len(self.nonce) + len(self.ciphertext)


# ---------------------------------------------------------------------------
# Seal / Open
# ---------------------------------------------------------------------------

def seal(plaintext: bytes, recipient_pub_bytes: bytes) -> SealedEnvelope:
    """Encrypt *plaintext* for a recipient identified by their X25519 public key.

    The sender requires no keypair — each call generates a fresh ephemeral key.

    Parameters
    ----------
    plaintext:
        Raw bytes to encrypt (caller is responsible for serialisation).
    recipient_pub_bytes:
        The recipient's 32-byte raw X25519 public key.

    Returns
    -------
    SealedEnvelope
        An opaque envelope the store can forward without reading.
    """
    if len(recipient_pub_bytes) != 32:
        raise CipherError(
            f"recipient public key must be 32 bytes, got {len(recipient_pub_bytes)}"
        )

    eph_priv = X25519PrivateKey.generate()
    eph_pub = eph_priv.public_key()
    eph_pub_bytes = _pub_raw(eph_pub)

    try:
        recipient_pub = X25519PublicKey.from_public_bytes(recipient_pub_bytes)
    except Exception as exc:
        raise CipherError(f"invalid recipient public key: {exc}") from exc

    shared = eph_priv.exchange(recipient_pub)
    key = _derive_key(shared, eph_pub_bytes, recipient_pub_bytes)

    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)

    return SealedEnvelope(ephemeral_pub=eph_pub_bytes, nonce=nonce, ciphertext=ct)


def seal_json(data: Any, recipient_pub_bytes: bytes) -> SealedEnvelope:
    """JSON-serialise *data* then seal it.  Convenience wrapper around :func:`seal`."""
    return seal(json.dumps(data, separators=(",", ":")).encode("utf-8"), recipient_pub_bytes)


def open_sealed(envelope: SealedEnvelope, recipient_priv: X25519PrivateKey) -> bytes:
    """Decrypt a :class:`SealedEnvelope` using the recipient's private key.

    Raises :class:`~proxion_messenger_core.errors.CipherError` on authentication failure
    (wrong key, tampered ciphertext, or malformed envelope).
    """
    recipient_pub_bytes = _pub_raw(recipient_priv.public_key())

    if len(envelope.ephemeral_pub) != 32:
        raise CipherError("invalid ephemeral_pub length")
    if len(envelope.nonce) != _NONCE_SIZE:
        raise CipherError("invalid nonce length")

    try:
        eph_pub = X25519PublicKey.from_public_bytes(envelope.ephemeral_pub)
    except Exception as exc:
        raise CipherError(f"invalid ephemeral_pub: {exc}") from exc

    shared = recipient_priv.exchange(eph_pub)
    key = _derive_key(shared, envelope.ephemeral_pub, recipient_pub_bytes)

    try:
        return AESGCM(key).decrypt(envelope.nonce, envelope.ciphertext, None)
    except Exception as exc:
        raise CipherError(
            "decryption failed — wrong key or tampered ciphertext"
        ) from exc


def open_sealed_json(envelope: SealedEnvelope, recipient_priv: X25519PrivateKey) -> Any:
    """Decrypt and JSON-deserialise.  Convenience wrapper around :func:`open_sealed`."""
    return json.loads(open_sealed(envelope, recipient_priv))
