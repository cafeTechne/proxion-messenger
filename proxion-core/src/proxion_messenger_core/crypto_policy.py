"""R11: Cryptographic algorithm policy registry.

Centralises algorithm acceptance decisions so that dpop.py, relay.py and future
modules all enforce the same policy rather than per-module ad-hoc checks.

Default policy: Ed25519 / EdDSA only.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Maps context name → set of allowed (alg, crv) tuples.
# A "*" context acts as a wildcard fallback.
_ALLOWED: dict[str, set[tuple[str, str]]] = {
    "*": {("EdDSA", "Ed25519")},
    "dpop": {("EdDSA", "Ed25519")},
    "relay": {("EdDSA", "Ed25519")},
}

# Minimum algorithm policy version accepted from remote payloads.
_MIN_POLICY_VERSION = 1


class CryptoPolicyError(ValueError):
    """Raised when an algorithm is rejected by policy."""


def validate_signature_policy(
    alg: str,
    key_meta: Optional[dict] = None,
    context: str = "*",
) -> None:
    """Validate that *alg* (and optional JWK *key_meta*) are permitted.

    Parameters
    ----------
    alg:
        Algorithm string, e.g. "EdDSA".
    key_meta:
        Optional JWK dict with "crv" / "kty" fields.
    context:
        Caller context string for per-context policy lookup ("dpop", "relay", …).

    Raises
    ------
    CryptoPolicyError
        If the algorithm is not allowed by policy.
    """
    crv = (key_meta or {}).get("crv", "")
    pair = (alg, crv) if crv else None

    # Per-context lookup; fall back to wildcard
    allowed = _ALLOWED.get(context) or _ALLOWED.get("*", set())

    if pair is not None and pair not in allowed:
        raise CryptoPolicyError(
            f"unsupported_crypto_policy: alg={alg!r} crv={crv!r} not allowed in context={context!r}"
        )
    if pair is None:
        # No crv to compare — check alg alone
        allowed_algs = {a for a, _ in allowed}
        if alg not in allowed_algs:
            raise CryptoPolicyError(
                f"unsupported_crypto_policy: alg={alg!r} not allowed in context={context!r}"
            )


def get_allowed_algorithms(context: str = "*") -> set[tuple[str, str]]:
    """Return the set of (alg, crv) tuples allowed for *context*."""
    return set(_ALLOWED.get(context) or _ALLOWED.get("*", set()))


def register_algorithm(context: str, alg: str, crv: str) -> None:
    """Extend the registry at runtime (test/extension use only)."""
    _ALLOWED.setdefault(context, set()).add((alg, crv))
