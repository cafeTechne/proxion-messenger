"""Tests for peer attestation enforcement (R15)."""
import json
import time
import pytest

from proxion_messenger_core.federation_attest import (
    verify_attestation,
    sign_attestation,
    ATTESTATION_MISSING,
    ATTESTATION_EXPIRED,
    ATTESTATION_SIGNATURE_INVALID,
    ATTESTATION_SUBJECT_MISMATCH,
)


def _make_attestation(peer_did="did:key:alice", gateway_url="https://gw.example", offset=3600):
    attest = {
        "peer_did": peer_did,
        "gateway_url": gateway_url,
        "key_fingerprints": ["sha256:abcd"],
        "issued_at": time.time(),
        "expires_at": time.time() + offset,
    }
    return sign_attestation(attest)


class TestRelayQuarantinedWhenAttestationMissing:
    def test_relay_quarantined_when_attestation_required_and_missing(self):
        result = verify_attestation(None, "did:key:alice", "https://gw.example")
        assert result["ok"] is False
        assert result["error_code"] == ATTESTATION_MISSING

    def test_missing_required_fields_returns_missing_code(self):
        result = verify_attestation({}, "did:key:alice", "https://gw.example")
        assert result["ok"] is False
        assert result["error_code"] == ATTESTATION_MISSING


class TestGatewayChangeRejectedOnInvalidSignature:
    def test_gateway_change_rejected_on_invalid_attestation_signature(self):
        attest = _make_attestation()
        attest["signature"] = "badhash000"
        result = verify_attestation(attest, attest["peer_did"], attest["gateway_url"])
        assert result["ok"] is False
        assert result["error_code"] == ATTESTATION_SIGNATURE_INVALID

    def test_expired_attestation_rejected(self):
        attest = _make_attestation(offset=-100)
        result = verify_attestation(attest, attest["peer_did"], attest["gateway_url"])
        assert result["ok"] is False
        assert result["error_code"] == ATTESTATION_EXPIRED

    def test_subject_mismatch_peer_did(self):
        attest = _make_attestation(peer_did="did:key:alice")
        result = verify_attestation(attest, "did:key:bob", attest["gateway_url"])
        assert result["ok"] is False
        assert result["error_code"] == ATTESTATION_SUBJECT_MISMATCH

    def test_subject_mismatch_gateway_url(self):
        attest = _make_attestation(gateway_url="https://real.example")
        result = verify_attestation(attest, attest["peer_did"], "https://fake.example")
        assert result["ok"] is False
        assert result["error_code"] == ATTESTATION_SUBJECT_MISMATCH


class TestValidAttestationAllowsFederationAction:
    def test_valid_attestation_allows_federation_action(self):
        attest = _make_attestation()
        result = verify_attestation(attest, attest["peer_did"], attest["gateway_url"])
        assert result["ok"] is True
        assert result["error_code"] == ""

    def test_sign_attestation_produces_verifiable_signature(self):
        attest = {
            "peer_did": "did:key:z",
            "gateway_url": "https://z.example",
            "key_fingerprints": [],
            "issued_at": time.time(),
            "expires_at": time.time() + 7200,
        }
        signed = sign_attestation(attest)
        assert "signature" in signed
        result = verify_attestation(signed, signed["peer_did"], signed["gateway_url"])
        assert result["ok"] is True
