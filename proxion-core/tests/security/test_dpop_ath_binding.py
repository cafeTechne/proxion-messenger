"""Round 8: DPoP proof ath claim (RFC 9449 access token binding)."""
import base64
import hashlib
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.dpop import make_dpop_proof, _b64url


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    assert len(parts) == 3
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


# ---------------------------------------------------------------------------
# make_dpop_proof without access_token (no ath claim)
# ---------------------------------------------------------------------------

def test_no_access_token_no_ath_claim():
    """Without access_token, the ath claim must be absent."""
    key = _key()
    token = make_dpop_proof(key, "GET", "https://pod.example.com/resource")
    payload = _decode_jwt_payload(token)
    assert "ath" not in payload


def test_standard_claims_present():
    """jti, htm, htu, iat, exp must always be present."""
    key = _key()
    token = make_dpop_proof(key, "PUT", "https://pod.example.com/resource")
    payload = _decode_jwt_payload(token)
    for claim in ("jti", "htm", "htu", "iat", "exp"):
        assert claim in payload, f"Missing claim: {claim}"


# ---------------------------------------------------------------------------
# make_dpop_proof with access_token → ath claim included
# ---------------------------------------------------------------------------

def test_access_token_produces_ath_claim():
    """Providing access_token causes ath to appear in the payload."""
    key = _key()
    access_token = "some-opaque-access-token"
    token = make_dpop_proof(
        key, "GET", "https://pod.example.com/resource",
        access_token=access_token,
    )
    payload = _decode_jwt_payload(token)
    assert "ath" in payload


def test_ath_is_correct_sha256_hash():
    """ath must equal base64url(SHA-256(access_token)) per RFC 9449 §4.2."""
    key = _key()
    access_token = "test-bearer-token-value"
    token = make_dpop_proof(
        key, "POST", "https://pod.example.com/resource",
        access_token=access_token,
    )
    payload = _decode_jwt_payload(token)
    expected_ath = _b64url(hashlib.sha256(access_token.encode("ascii")).digest())
    assert payload["ath"] == expected_ath


def test_ath_changes_with_different_token():
    """Different access_token values produce different ath claims."""
    key = _key()
    t1 = make_dpop_proof(key, "GET", "https://example.com/r", access_token="tokenA")
    t2 = make_dpop_proof(key, "GET", "https://example.com/r", access_token="tokenB")
    p1 = _decode_jwt_payload(t1)
    p2 = _decode_jwt_payload(t2)
    assert p1["ath"] != p2["ath"]


def test_ath_does_not_appear_when_token_is_none():
    """access_token=None explicitly must not add ath."""
    key = _key()
    token = make_dpop_proof(key, "GET", "https://pod.example.com/r", access_token=None)
    payload = _decode_jwt_payload(token)
    assert "ath" not in payload


# ---------------------------------------------------------------------------
# DpopSolidClient injects ath on resource requests
# ---------------------------------------------------------------------------

def test_dpop_solid_client_injects_ath():
    """DpopSolidClient._dynamic_headers must include ath in the DPoP proof."""
    from unittest.mock import MagicMock
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from proxion_messenger_core.css_auth import CssClientCredentials, DpopSolidClient
    from proxion_messenger_core.solid import SolidResolver

    key = Ed25519PrivateKey.generate()
    fake_token = "fake-access-token-xyz"

    from proxion_messenger_core.dpop import generate_ec_dpop_key
    creds = MagicMock(spec=CssClientCredentials)
    creds.identity_key = key
    creds._dpop_ec_key = generate_ec_dpop_key()
    creds.get_token.return_value = fake_token

    resolver = MagicMock(spec=SolidResolver)
    client = DpopSolidClient(resolver, creds)

    headers = client._dynamic_headers("GET", "https://pod.example.com/resource")
    dpop_token = headers["DPoP"]

    payload = _decode_jwt_payload(dpop_token)
    expected_ath = _b64url(hashlib.sha256(fake_token.encode("ascii")).digest())
    assert payload.get("ath") == expected_ath
