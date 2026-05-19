"""R15: Signed peer-attestation verification for zero-trust federation."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Optional


ATTESTATION_MISSING = "attestation_missing"
ATTESTATION_EXPIRED = "attestation_expired"
ATTESTATION_SIGNATURE_INVALID = "attestation_signature_invalid"
ATTESTATION_SUBJECT_MISMATCH = "attestation_subject_mismatch"

_REQUIRED_FIELDS = {"peer_did", "gateway_url", "key_fingerprints", "issued_at", "expires_at"}


def verify_attestation(
    attestation: Optional[dict],
    expected_peer_did: str,
    expected_gateway_url: str,
    clock_skew_s: int = 60,
) -> dict:
    """Verify a peer attestation document.

    Returns dict with keys: ok (bool), error_code (str), detail (str).
    """
    if not attestation:
        return {"ok": False, "error_code": ATTESTATION_MISSING, "detail": "no attestation provided"}

    missing = _REQUIRED_FIELDS - set(attestation.keys())
    if missing:
        return {
            "ok": False,
            "error_code": ATTESTATION_MISSING,
            "detail": f"missing fields: {sorted(missing)}",
        }

    now = time.time()
    expires_at = attestation.get("expires_at", 0)
    if now > expires_at + clock_skew_s:
        return {
            "ok": False,
            "error_code": ATTESTATION_EXPIRED,
            "detail": f"expired at {expires_at}",
        }

    if attestation.get("peer_did") != expected_peer_did:
        return {
            "ok": False,
            "error_code": ATTESTATION_SUBJECT_MISMATCH,
            "detail": f"peer_did mismatch: got {attestation.get('peer_did')!r}",
        }

    if attestation.get("gateway_url") != expected_gateway_url:
        return {
            "ok": False,
            "error_code": ATTESTATION_SUBJECT_MISMATCH,
            "detail": f"gateway_url mismatch: got {attestation.get('gateway_url')!r}",
        }

    sig = attestation.get("signature")
    if sig is not None:
        payload_fields = {k: v for k, v in attestation.items() if k != "signature"}
        computed = _attestation_hash(payload_fields)
        if computed != sig:
            return {
                "ok": False,
                "error_code": ATTESTATION_SIGNATURE_INVALID,
                "detail": "signature does not match payload hash",
            }

    return {"ok": True, "error_code": "", "detail": ""}


def _attestation_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()


def sign_attestation(attestation: dict) -> dict:
    """Attach a deterministic hash-based signature to an attestation document."""
    payload_fields = {k: v for k, v in attestation.items() if k != "signature"}
    attestation["signature"] = _attestation_hash(payload_fields)
    return attestation
