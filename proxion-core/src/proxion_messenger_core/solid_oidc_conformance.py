"""Solid-OIDC claim and flow conformance validator (Round 14).

Validates ID token claims against Solid-OIDC / OpenID Connect spec requirements.
Returns a deterministic error contract for use by auth adapters.
"""
from __future__ import annotations

import time
from typing import Optional

OIDC_ISSUER_MISMATCH = "oidc_issuer_mismatch"
OIDC_AUDIENCE_MISMATCH = "oidc_audience_mismatch"
OIDC_EXPIRED_TOKEN = "oidc_expired_token"
OIDC_NONCE_MISMATCH = "oidc_nonce_mismatch"
OIDC_MISSING_CLAIM = "oidc_missing_claim"
OIDC_CLOCK_SKEW = "oidc_clock_skew"

_DEFAULT_CLOCK_SKEW_S = 60


def validate_id_token_claims(
    claims: dict,
    expected_iss: str,
    expected_aud: str,
    expected_nonce: Optional[str] = None,
    clock_skew_s: int = _DEFAULT_CLOCK_SKEW_S,
) -> dict:
    """Validate Solid-OIDC ID token claims.

    Parameters
    ----------
    claims:
        Decoded JWT payload dict.
    expected_iss:
        Issuer URL the token must assert.
    expected_aud:
        Client ID / audience the token must assert.
    expected_nonce:
        If provided, ``claims["nonce"]`` must match exactly.
    clock_skew_s:
        Allowed clock drift in seconds (default 60).

    Returns
    -------
    dict with keys:
        ok          bool
        error_code  str  (empty when ok)
        detail      str  (human-readable reason, empty when ok)
    """
    now = time.time()

    for required in ("iss", "sub", "aud", "exp", "iat"):
        if required not in claims:
            return {
                "ok": False,
                "error_code": OIDC_MISSING_CLAIM,
                "detail": f"Required claim '{required}' is missing",
            }

    if claims["iss"] != expected_iss:
        return {
            "ok": False,
            "error_code": OIDC_ISSUER_MISMATCH,
            "detail": f"iss={claims['iss']!r} expected={expected_iss!r}",
        }

    aud = claims["aud"]
    aud_ok = expected_aud in aud if isinstance(aud, list) else aud == expected_aud
    if not aud_ok:
        return {
            "ok": False,
            "error_code": OIDC_AUDIENCE_MISMATCH,
            "detail": f"aud={aud!r} expected={expected_aud!r}",
        }

    exp = claims["exp"]
    if now > exp + clock_skew_s:
        return {
            "ok": False,
            "error_code": OIDC_EXPIRED_TOKEN,
            "detail": f"token expired at {exp}, now={now:.0f}, skew={clock_skew_s}s",
        }

    iat = claims["iat"]
    if iat > now + clock_skew_s:
        return {
            "ok": False,
            "error_code": OIDC_CLOCK_SKEW,
            "detail": f"iat={iat} is in the future (now={now:.0f}, skew={clock_skew_s}s)",
        }

    if expected_nonce is not None:
        if claims.get("nonce") != expected_nonce:
            return {
                "ok": False,
                "error_code": OIDC_NONCE_MISMATCH,
                "detail": "nonce mismatch",
            }

    return {"ok": True, "error_code": "", "detail": ""}
