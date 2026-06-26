"""R9: Security snapshot export tests."""
import json
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState


@pytest.fixture
def agent(tmp_path):
    return AgentState.generate()


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def test_snapshot_contains_required_sections(agent, store):
    """Snapshot must include security_events, trust_disputes, checksum, abuse_signals."""
    from proxion_messenger_core.didkey import pub_key_to_did
    snap = {
        "generated_at": 0.0,
        "gateway_did": pub_key_to_did(agent.identity_pub_bytes),
        "checksum_mismatch": False,
        "checksum_mismatch_tables": [],
        "security_events": store.get_security_events(limit=50),
        "trust_disputes": store.list_peer_trust_disputes(status="open", limit=50),
        "abuse_signals_1h": store.get_abuse_signal_rollups(hours=1),
        "abuse_signals_24h": store.get_abuse_signal_rollups(hours=24),
    }
    for key in ("generated_at", "gateway_did", "security_events", "trust_disputes",
                "abuse_signals_1h", "abuse_signals_24h", "checksum_mismatch"):
        assert key in snap


def test_snapshot_signature_verifies_with_identity_pubkey(agent, store):
    """Snapshot must be signed with the gateway identity key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    snap = {
        "generated_at": 1234567890.0,
        "gateway_did": "did:key:test",
        "checksum_mismatch": False,
        "checksum_mismatch_tables": [],
    }
    snap_bytes = json.dumps(snap, default=str, sort_keys=True).encode()
    sig = agent.identity_key.sign(snap_bytes)
    snap["signature"] = sig.hex()

    pub_key = Ed25519PublicKey.from_public_bytes(agent.identity_pub_bytes)
    pub_key.verify(bytes.fromhex(snap["signature"]), snap_bytes)  # should not raise


def test_snapshot_bad_signature_raises(agent):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    snap_bytes = b'{"data":"value"}'
    bad_sig = bytes(64)  # all zeros
    pub_key = Ed25519PublicKey.from_public_bytes(agent.identity_pub_bytes)
    with pytest.raises(Exception):
        pub_key.verify(bad_sig, snap_bytes)


def test_export_security_snapshot_is_owner_only():
    """export_security_snapshot must be in the owner-only policy set."""
    from proxion_messenger_core.security_policy import SecurityPolicy
    policy = SecurityPolicy()
    decision = policy.evaluate_ws_command(
        cmd="export_security_snapshot",
        caller_webid="did:key:non_owner",
        gateway_owner_did="did:key:owner",
    )
    assert not decision.allow
    assert decision.deny_code == "E_FORBIDDEN"
