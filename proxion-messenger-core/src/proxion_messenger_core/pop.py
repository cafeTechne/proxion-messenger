"""Proof-of-Possession (PoP) for Proxion capability tokens.

The spec (§3.3) requires that the Subject prove possession of the private key
bound to the token — mere knowledge of the token bytes is not sufficient.

Protocol
--------
1. **Issuance**: when minting a token, the issuer stores a fingerprint of the
   holder's Ed25519 public key as ``holder_key_fingerprint``.  Use
   :func:`fingerprint` to derive it from raw public-key bytes.

2. **Exercise**: the holder calls :func:`sign_challenge` with their private key
   and a challenge string.  The challenge MUST incorporate the ``token_id`` and
   a per-request nonce so that the proof cannot be replayed across tokens or
   requests.  Use :func:`make_challenge` to build the canonical challenge.

3. **Verification**: the resource server calls :func:`verify_pop` with the
   token and the :class:`PopProof`.  It:

   * re-derives the fingerprint from the supplied public key and checks it
     against ``token.holder_key_fingerprint`` (key-binding check);
   * verifies the Ed25519 signature over the challenge (liveness check).

Envelope
--------
``PopProof`` is a plain dataclass.  Serialise it for transport however suits
the EI (e.g. JSON with base64url fields).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from .errors import CipherError
from .tokens import Token


# ---------------------------------------------------------------------------
# Public key fingerprint
# ---------------------------------------------------------------------------

def fingerprint(public_key_bytes: bytes) -> str:
    """Return the canonical fingerprint for an Ed25519 public key.

    This is the base64url-encoded SHA-256 digest of the 32-byte raw public key,
    without padding — the same format used by the JOSE ``x5t#S256`` thumbprint.

    Use this to populate ``holder_key_fingerprint`` when minting a token.
    """
    digest = hashlib.sha256(public_key_bytes).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def fingerprint_from_key(public_key: Ed25519PublicKey) -> str:
    """Convenience wrapper: derive a fingerprint directly from a key object."""
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return fingerprint(raw)


# ---------------------------------------------------------------------------
# Challenge construction
# ---------------------------------------------------------------------------

def make_challenge(token_id: str, nonce: str) -> bytes:
    """Build the canonical PoP challenge bytes.

    Binds the proof to a specific *token_id* and per-request *nonce*, so it
    cannot be replayed against a different token or request.

    Format: ``proxion-pop:{token_id}:{nonce}`` (UTF-8, no trailing newline).
    """
    return f"proxion-pop:{token_id}:{nonce}".encode("utf-8")


# ---------------------------------------------------------------------------
# PoP proof dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PopProof:
    """A Proof-of-Possession envelope presented by a capability holder.

    Attributes
    ----------
    public_key_bytes:
        The raw 32-byte Ed25519 public key of the holder.
    nonce:
        The per-request nonce used to build the challenge.  Must match the
        ``device_nonce`` in the :class:`~proxion_messenger_core.context.RequestContext`
        so the verifier can reconstruct the exact challenge that was signed.
    signature:
        The 64-byte Ed25519 signature over
        ``make_challenge(token_id, nonce)``.
    """

    public_key_bytes: bytes
    nonce: str
    signature: bytes


# ---------------------------------------------------------------------------
# Sign (holder side)
# ---------------------------------------------------------------------------

def sign_challenge(
    private_key: Ed25519PrivateKey,
    token_id: str,
    nonce: str,
) -> PopProof:
    """Create a :class:`PopProof` by signing the canonical challenge.

    Call this on the *holder* side before presenting the token to a resource
    server.
    """
    challenge = make_challenge(token_id, nonce)
    sig = private_key.sign(challenge)
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return PopProof(public_key_bytes=pub_bytes, nonce=nonce, signature=sig)


# ---------------------------------------------------------------------------
# Verify (resource-server side)
# ---------------------------------------------------------------------------

def verify_pop(token: Token, proof: PopProof) -> bool:
    """Verify a :class:`PopProof` against a capability token.

    Returns ``True`` if and only if:

    1. The fingerprint of ``proof.public_key_bytes`` matches
       ``token.holder_key_fingerprint`` (key-binding check).
    2. The Ed25519 signature is valid over the canonical challenge
       ``make_challenge(token.token_id, proof.nonce)`` (liveness check).

    Returns ``False`` on any failure rather than raising, so callers can treat
    it as a boolean predicate.
    """
    # 1. Key-binding: ensure the supplied public key is the one bound to the token.
    if fingerprint(proof.public_key_bytes) != token.holder_key_fingerprint:
        return False

    # 2. Liveness: verify the signature over the token-specific challenge.
    challenge = make_challenge(token.token_id, proof.nonce)
    try:
        pub_key = Ed25519PublicKey.from_public_bytes(proof.public_key_bytes)
        pub_key.verify(proof.signature, challenge)
    except (InvalidSignature, ValueError):
        return False

    return True
