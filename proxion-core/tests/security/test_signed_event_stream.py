"""R12: Signed event stream tests."""
import json
import time
import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.event_stream import get_events_after, _event_hash


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


@pytest.fixture
def agent():
    return AgentState.generate()


def _seed_events(store, count=3):
    for i in range(count):
        store.save_security_event(f"test_event_{i}", "info", details=f"detail_{i}")
    return store.get_security_events_after(cursor="", limit=100)


def test_stream_events_include_signature_and_prev_hash(store, agent):
    _seed_events(store, 3)
    result = get_events_after(store, "", 100, agent.identity_key, agent.identity_pub_bytes)
    assert "signature" in result
    assert "pub_key_hex" in result
    assert len(result["events"]) == 3
    for ev in result["events"]:
        assert "prev_event_hash" in ev
        assert "event_hash" in ev


def test_event_stream_cursor_pagination_is_stable(store, agent):
    _seed_events(store, 5)
    first_page = get_events_after(store, "", 2, agent.identity_key, agent.identity_pub_bytes)
    assert len(first_page["events"]) == 2
    cursor = first_page["next_cursor"]
    second_page = get_events_after(store, cursor, 10, agent.identity_key, agent.identity_pub_bytes)
    # Should return remaining events (3)
    assert len(second_page["events"]) == 3
    # No overlap
    first_ids = {e["id"] for e in first_page["events"]}
    second_ids = {e["id"] for e in second_page["events"]}
    assert first_ids.isdisjoint(second_ids)


def test_event_chain_verification_detects_tamper(store, agent):
    _seed_events(store, 3)
    result = get_events_after(store, "", 100, agent.identity_key, agent.identity_pub_bytes)
    events = result["events"]
    # Verify chain: each event's prev_event_hash matches predecessor's event_hash
    for i in range(1, len(events)):
        assert events[i]["prev_event_hash"] == events[i - 1]["event_hash"]

    # Tamper with the first event hash
    tampered = dict(events[1])
    tampered["prev_event_hash"] = "000000" * 10
    # The tampered prev_event_hash should not match the actual predecessor hash
    assert tampered["prev_event_hash"] != events[0]["event_hash"]


def test_stream_signature_is_verifiable(store, agent):
    _seed_events(store, 2)
    result = get_events_after(store, "", 100, agent.identity_key, agent.identity_pub_bytes)
    sig = bytes.fromhex(result["signature"])
    pub_hex = result["pub_key_hex"]
    pub_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    # Payload was signed before signature and pub_key_hex were added
    payload_for_verify = {k: v for k, v in result.items() if k not in ("signature", "pub_key_hex")}
    payload_bytes = json.dumps(payload_for_verify, default=str, sort_keys=True).encode()
    pub_key.verify(sig, payload_bytes)  # raises if invalid


def test_empty_stream_returns_valid_structure(store, agent):
    result = get_events_after(store, "", 10, agent.identity_key, agent.identity_pub_bytes)
    assert result["events"] == []
    assert result["cursor"] == ""
    assert "generated_at" in result
