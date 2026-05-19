"""Round 8: Relay sender binding — sender_webid in canonical payload."""
import pytest
from proxion_messenger_core.relay import (
    sign_relay_message,
    verify_relay_message,
    _canonical,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


# ---------------------------------------------------------------------------
# Canonical string
# ---------------------------------------------------------------------------

def test_canonical_without_sender_webid_unchanged():
    """Without sender_webid, canonical output matches the pre-Round-8 format."""
    payload = {
        "from_webid": "did:key:A",
        "to_webid": "did:key:B",
        "message_id": "mid",
        "content": "hello",
        "timestamp": "2024-01-01T00:00:00+00:00",
    }
    result = _canonical(payload)
    assert "did:key:A" in result
    assert "hello" in result
    # No trailing extra line when sender_webid absent
    parts = result.split("\n")
    assert len(parts) == 5


def test_canonical_with_sender_webid_appended():
    """sender_webid is appended as an extra line when present."""
    payload = {
        "from_webid": "did:key:A",
        "to_webid": "did:key:B",
        "message_id": "mid",
        "content": "hello",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "sender_webid": "did:key:GW",
    }
    result = _canonical(payload)
    parts = result.split("\n")
    assert parts[-1] == "did:key:GW"
    assert len(parts) == 6


def test_canonical_with_nonce_and_sender_webid():
    """Both relay_nonce and sender_webid appended in the right order."""
    payload = {
        "from_webid": "A", "to_webid": "B",
        "message_id": "m", "content": "c",
        "timestamp": "t",
        "relay_nonce": "nonce123",
        "sender_webid": "did:key:GW",
    }
    parts = _canonical(payload).split("\n")
    assert parts[5] == "nonce123"
    assert parts[6] == "did:key:GW"


# ---------------------------------------------------------------------------
# Sign + verify round-trips
# ---------------------------------------------------------------------------

def test_sign_verify_without_sender_webid():
    """Backward-compat: no sender_webid — verifies correctly."""
    key = _key()
    from proxion_messenger_core.didkey import pub_key_to_did
    pub_bytes = key.public_key().public_bytes(
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.Raw,
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.Raw,
    )
    from_webid = pub_key_to_did(pub_bytes)
    ts = _now_iso()
    sig = sign_relay_message(key, from_webid, "did:key:B", "msg-1", "hello", ts)
    assert verify_relay_message(
        from_webid, "did:key:B", "msg-1", "hello", ts, sig,
        clock_skew_window=__import__("datetime").timedelta.max,
    )


def test_sign_verify_with_sender_webid():
    """sender_webid included in sign and verify produces a valid signature."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from datetime import timedelta
    from proxion_messenger_core.didkey import pub_key_to_did

    key = _key()
    pub_bytes = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    from_webid = pub_key_to_did(pub_bytes)
    ts = _now_iso()
    gw_did = "did:key:gateway-xyz"

    sig = sign_relay_message(
        key, from_webid, "did:key:B", "msg-2", "world", ts,
        sender_webid=gw_did,
    )
    assert verify_relay_message(
        from_webid, "did:key:B", "msg-2", "world", ts, sig,
        sender_webid=gw_did,
        clock_skew_window=timedelta.max,
    )


def test_sign_with_sender_webid_verify_without_fails():
    """Signature made with sender_webid must not verify without it."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from datetime import timedelta
    from proxion_messenger_core.didkey import pub_key_to_did

    key = _key()
    pub_bytes = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    from_webid = pub_key_to_did(pub_bytes)
    ts = _now_iso()

    sig = sign_relay_message(
        key, from_webid, "did:key:B", "msg-3", "bound", ts,
        sender_webid="did:key:GW",
    )
    result = verify_relay_message(
        from_webid, "did:key:B", "msg-3", "bound", ts, sig,
        # No sender_webid — canonical string differs → signature mismatch
        clock_skew_window=timedelta.max,
    )
    assert result is False


def test_verify_wrong_sender_webid_fails():
    """verify_relay_message with wrong sender_webid must return False."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from datetime import timedelta
    from proxion_messenger_core.didkey import pub_key_to_did

    key = _key()
    pub_bytes = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    from_webid = pub_key_to_did(pub_bytes)
    ts = _now_iso()

    sig = sign_relay_message(
        key, from_webid, "did:key:B", "msg-4", "test", ts,
        sender_webid="did:key:correct-gw",
    )
    result = verify_relay_message(
        from_webid, "did:key:B", "msg-4", "test", ts, sig,
        sender_webid="did:key:wrong-gw",
        clock_skew_window=timedelta.max,
    )
    assert result is False
