"""Tests for proxion_messenger_core.dpop — DPoP proof JWT generation."""
import base64
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from proxion_messenger_core.dpop import make_dpop_proof


def _decode_part(part: str) -> dict:
    """Decode a base64url JWT part (add padding then decode JSON)."""
    padded = part + "==" * ((4 - len(part) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


def test_dpop_three_parts(key):
    proof = make_dpop_proof(key, "GET", "https://pod.example/resource")
    parts = proof.split(".")
    assert len(parts) == 3


def test_dpop_header_typ_and_alg(key):
    proof = make_dpop_proof(key, "GET", "https://pod.example/resource")
    header = _decode_part(proof.split(".")[0])
    assert header["typ"] == "dpop+jwt"
    assert header["alg"] == "EdDSA"


def test_dpop_header_jwk_matches_pubkey(key):
    raw_pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    expected_x = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    proof = make_dpop_proof(key, "GET", "https://pod.example/resource")
    header = _decode_part(proof.split(".")[0])
    assert header["jwk"]["kty"] == "OKP"
    assert header["jwk"]["crv"] == "Ed25519"
    assert header["jwk"]["x"] == expected_x


def test_dpop_payload_fields(key):
    url = "https://pod.example/resource"
    proof = make_dpop_proof(key, "put", url, iat=1700000000)
    payload = _decode_part(proof.split(".")[1])
    assert payload["htm"] == "PUT"
    assert payload["htu"] == url
    assert "jti" in payload
    assert payload["iat"] == 1700000000


def test_dpop_unique_jti(key):
    url = "https://pod.example/resource"
    p1 = make_dpop_proof(key, "GET", url)
    p2 = make_dpop_proof(key, "GET", url)
    jti1 = _decode_part(p1.split(".")[1])["jti"]
    jti2 = _decode_part(p2.split(".")[1])["jti"]
    assert jti1 != jti2


def test_dpop_signature_verifies(key):
    url = "https://pod.example/resource"
    proof = make_dpop_proof(key, "GET", url)
    header_b64, payload_b64, sig_b64 = proof.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    # Decode signature (add padding)
    padded = sig_b64 + "==" * ((4 - len(sig_b64) % 4) % 4)
    sig_bytes = base64.urlsafe_b64decode(padded)
    # Verify with public key — raises InvalidSignature on failure
    key.public_key().verify(sig_bytes, signing_input)
