"""DPoP proof JWT generation and claim validation (RFC 9449)."""
from __future__ import annotations
import base64
import json
import time
import uuid
from collections import OrderedDict
from typing import Optional, Union
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePrivateKey, SECP256R1, generate_private_key, ECDSA,
)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.backends import default_backend

# Maximum clock skew tolerated when validating iat (seconds)
_CLOCK_SKEW_S = 30


def validate_dpop_claims(payload: dict, now: Optional[int] = None, header: Optional[dict] = None) -> None:
    """Validate DPoP JWT payload claims. Raises ValueError describing the failure.

    Checks performed:
    - ``iat`` and ``exp`` are present
    - ``iat`` is not more than *_CLOCK_SKEW_S* seconds in the future
    - ``exp`` has not passed (with *_CLOCK_SKEW_S* tolerance)
    - ``jti`` is present (uniqueness tracking is left to callers via DpopReplayCache)
    - If ``header`` provided: validates alg=EdDSA and crv=Ed25519

    Parameters
    ----------
    payload : dict
        JWT payload claims
    now : int, optional
        Unix timestamp to use for validation (defaults to current time)
    header : dict, optional
        JWT header; when provided, validates algorithm and curve constraints
    """
    _now = now if now is not None else int(time.time())
    iat = payload.get("iat")
    exp = payload.get("exp")
    if iat is None:
        raise ValueError("DPoP proof missing iat claim")
    if exp is None:
        raise ValueError("DPoP proof missing exp claim")
    if not payload.get("jti"):
        raise ValueError("DPoP proof missing jti claim")
    if iat > _now + _CLOCK_SKEW_S:
        raise ValueError(
            f"DPoP proof iat is {iat - _now}s in the future (max skew {_CLOCK_SKEW_S}s)"
        )
    if exp <= _now - _CLOCK_SKEW_S:
        raise ValueError(f"DPoP proof expired: exp={exp}, now={_now}")

    # Validate JOSE header algorithm and curve via crypto policy registry (R11)
    if header is not None:
        try:
            from .crypto_policy import validate_signature_policy, CryptoPolicyError
            validate_signature_policy(
                alg=header.get("alg", ""),
                key_meta=header.get("jwk"),
                context="dpop",
            )
        except Exception:
            raise ValueError("unsupported_dpop_algorithm")
        jwk = header.get("jwk", {})
        if jwk.get("kty") != "OKP":
            raise ValueError("unsupported_dpop_algorithm")


class DpopReplayCache:
    """In-memory jti replay cache with TTL-based eviction.

    Keeps jti values for *ttl* seconds (default 120) to detect replayed proofs
    within the validity window. Thread-safe via simple dict operations (GIL).

    Optionally backed by durable storage via hook callables.
    """

    def __init__(
        self,
        ttl: int = 120,
        seen_lookup=None,   # Callable[[str], bool]
        seen_record=None,   # Callable[[str], None]
        prune=None,         # Callable[[float], None]
    ) -> None:
        self._ttl = ttl
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._seen_lookup = seen_lookup
        self._seen_record = seen_record
        self._prune = prune

    def check_and_record(self, jti: str, now: Optional[float] = None) -> None:
        """Raise ValueError if *jti* has been seen within the TTL window.

        Records the jti on first use; evicts expired entries as a side-effect.
        Uses durable hooks if provided, otherwise falls back to in-memory cache.
        """
        _now = now if now is not None else time.time()
        # Check durable store first
        if self._seen_lookup is not None:
            if self._seen_lookup(jti):
                raise ValueError(f"DPoP jti replay detected: {jti!r}")
            if self._seen_record is not None:
                self._seen_record(jti)
            if self._prune is not None:
                self._prune(_now - self._ttl)
            return
        # Fallback: in-memory
        self._evict(_now)
        if jti in self._seen:
            raise ValueError(f"DPoP jti replay detected: {jti!r}")
        self._seen[jti] = _now

    def _evict(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._seen:
            oldest_jti, oldest_ts = next(iter(self._seen.items()))
            if oldest_ts < cutoff:
                self._seen.popitem(last=False)
            else:
                break


def _b64url(data: bytes) -> str:
    """Encode bytes as base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _extract_dpop_nonce(www_authenticate: str) -> Optional[str]:
    """Extract the dpop-nonce value from a WWW-Authenticate header string.

    Returns the nonce string or None if not present.
    """
    import re as _re
    m = _re.search(r'nonce="([^"]+)"', www_authenticate, _re.IGNORECASE)
    return m.group(1) if m else None


def make_dpop_proof(
    identity_key: Ed25519PrivateKey,
    method: str,
    url: str,
    iat: Optional[int] = None,
    nonce: Optional[str] = None,
    access_token: Optional[str] = None,
) -> str:
    """Return a compact DPoP proof JWT (RFC 9449) signed with identity_key.

    Parameters
    ----------
    identity_key : Ed25519PrivateKey — used to sign the JWT
    method : str — HTTP method (e.g. "GET"), will be uppercased
    url : str — full request URL; fragment (#...) is stripped
    iat : int, optional — Unix timestamp; defaults to int(time.time())
    nonce : str, optional — server-issued nonce (RFC 9449 §8); included as
        the ``nonce`` claim when provided (required by Inrupt ESS).
    access_token : str, optional — when provided, include the ``ath`` claim
        (SHA-256 hash of the access token, base64url-encoded) to bind this
        proof to the bearer token (RFC 9449 §4.2).

    Returns a compact JWT: base64url(header).base64url(payload).base64url(sig)
    """
    # Extract raw 32-byte public key
    raw_pub = identity_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    # Build JWK for header
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64url(raw_pub),
    }

    # Build JWT header
    header = {
        "typ": "dpop+jwt",
        "alg": "EdDSA",
        "jwk": jwk,
    }

    # Strip fragment from URL
    url_no_frag = url.split("#")[0]

    # Build JWT payload
    _iat = iat if iat is not None else int(time.time())
    payload = {
        "jti": str(uuid.uuid4()),
        "htm": method.upper(),
        "htu": url_no_frag,
        "iat": _iat,
        "exp": _iat + 60,
    }
    if nonce is not None:
        payload["nonce"] = nonce
    if access_token is not None:
        import hashlib as _hl
        _ath_digest = _hl.sha256(access_token.encode("ascii")).digest()
        payload["ath"] = _b64url(_ath_digest)

    # Encode header and payload as compact JSON
    b64_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    b64_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode())

    # Create signing input
    signing_input = f"{b64_header}.{b64_payload}".encode("ascii")

    # Sign with identity key
    sig = identity_key.sign(signing_input)

    # Return compact JWT
    return f"{b64_header}.{b64_payload}.{_b64url(sig)}"


def generate_ec_dpop_key() -> EllipticCurvePrivateKey:
    """Generate a fresh P-256 private key for ES256 DPoP proofs."""
    return generate_private_key(SECP256R1(), default_backend())


def make_dpop_proof_es256(
    ec_key: EllipticCurvePrivateKey,
    method: str,
    url: str,
    iat: Optional[int] = None,
    nonce: Optional[str] = None,
    access_token: Optional[str] = None,
) -> str:
    """Return a compact DPoP proof JWT signed with an EC P-256 key (ES256).

    CSS v7 rejects EdDSA DPoP proofs despite advertising EdDSA in the
    discovery document; ES256 works correctly. Use this for CSS interactions.
    """
    pub_numbers = ec_key.public_key().public_numbers()
    x = _b64url(pub_numbers.x.to_bytes(32, "big"))
    y = _b64url(pub_numbers.y.to_bytes(32, "big"))

    jwk = {"crv": "P-256", "kty": "EC", "x": x, "y": y}
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}

    url_no_frag = url.split("#")[0]
    _iat = iat if iat is not None else int(time.time())
    payload = {
        "jti": str(uuid.uuid4()),
        "htm": method.upper(),
        "htu": url_no_frag,
        "iat": _iat,
        "exp": _iat + 60,
    }
    if nonce is not None:
        payload["nonce"] = nonce
    if access_token is not None:
        import hashlib as _hl
        payload["ath"] = _b64url(_hl.sha256(access_token.encode("ascii")).digest())

    b64_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    b64_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{b64_header}.{b64_payload}".encode("ascii")

    der_sig = ec_key.sign(signing_input, ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"{b64_header}.{b64_payload}.{_b64url(raw_sig)}"
