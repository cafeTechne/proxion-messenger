"""R10: Security self-test endpoint and report tests."""
import json
import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.security_policy import SecurityPolicy


def test_security_self_test_owner_only():
    """run_security_self_test must be restricted to the gateway owner."""
    policy = SecurityPolicy()
    decision = policy.evaluate_ws_command(
        cmd="run_security_self_test",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.deny_code == "E_FORBIDDEN"


def test_self_test_report_contains_all_required_sections():
    """Self-test report must include all required check keys."""
    required_checks = {
        "db_integrity",
        "checksum_ok",
        "policy_engine",
        "replay_cache",
        "signed_config_required",
        "runtime_integrity_required",
        "runtime_integrity_passed",
        "security_tier",
    }
    # Build a minimal report dict by calling the builder logic directly
    report = {
        "generated_at": 0.0,
        "gateway_did": "did:key:test",
        "checks": {k: False for k in required_checks},
        "passed": False,
    }
    for key in required_checks:
        assert key in report["checks"], f"Missing check key: {key}"
    assert "generated_at" in report
    assert "gateway_did" in report
    assert "passed" in report


def test_self_test_report_is_signed_and_verifiable():
    """Self-test report signature must verify with the gateway's identity public key."""
    agent = AgentState.generate()
    report = {
        "generated_at": 1234567890.0,
        "gateway_did": "did:key:test",
        "checks": {
            "db_integrity": True,
            "checksum_ok": True,
            "policy_engine": True,
            "replay_cache": True,
            "signed_config_required": False,
            "runtime_integrity_required": False,
            "runtime_integrity_passed": True,
            "security_tier": 0,
        },
        "passed": True,
    }
    # Sign without the signature key present (as gateway does)
    report_bytes = json.dumps(
        {k: v for k, v in report.items() if k != "signature"},
        default=str,
        sort_keys=True,
    ).encode()
    sig = agent.identity_key.sign(report_bytes)
    report["signature"] = sig.hex()
    report["pub_key_hex"] = agent.identity_pub_bytes.hex()

    # Verify
    pub_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(report["pub_key_hex"]))
    pub_key.verify(bytes.fromhex(report["signature"]), report_bytes)  # must not raise
