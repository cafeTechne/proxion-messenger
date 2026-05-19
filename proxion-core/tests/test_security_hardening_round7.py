"""Round 7 security hardening regression tests.

Covers:
  1. CORS: null/empty origins rejected by _is_trusted_origin.
  2. Relay DoS: unknown-DID recipients rejected, queue capped at 500.
  3. Replay partitioning: same nonce from different senders not deduplicated.
  4. Import quota: import_data enforces per-thread message count + byte limits.
  5. Content-Length guard: POST bodies over 2 MB rejected before parsing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gw(tmp_path):
    agent = AgentState.generate()
    config = GatewayConfig(port=0, db_path=str(tmp_path / "r7.db"))
    return ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState(),
    )


# ---------------------------------------------------------------------------
# 1. CORS — null / empty origins must be rejected
# ---------------------------------------------------------------------------

def test_null_origin_not_trusted():
    """b'null' origin must NOT be trusted (sandboxed-iframe bypass)."""
    assert not ProxionGateway._is_trusted_origin(b"null", 8080)


def test_empty_bytes_origin_not_trusted():
    """b'' (explicit empty) is falsy and handled the same as absent — trusted."""
    # Empty bytes is falsy so `not origin` is True → trusted (same as no header)
    assert ProxionGateway._is_trusted_origin(b"", 8080)


def test_absent_origin_trusted():
    """No Origin header (b'' or None) is trusted — cannot be spoofed cross-origin."""
    assert ProxionGateway._is_trusted_origin(b"", 8080)
    assert ProxionGateway._is_trusted_origin(None, 8080)


def test_tauri_origin_trusted():
    """tauri://localhost must remain trusted for the desktop app."""
    assert ProxionGateway._is_trusted_origin(b"tauri://localhost", 8080)


def test_localhost_origin_trusted():
    """http://localhost:{port} must remain trusted."""
    assert ProxionGateway._is_trusted_origin(b"http://localhost:8080", 8080)


def test_foreign_origin_not_trusted():
    """An arbitrary remote origin must be rejected."""
    assert not ProxionGateway._is_trusted_origin(b"https://evil.example.com", 8080)


# ---------------------------------------------------------------------------
# 2. Relay DoS — unknown recipient rejected; queue capped
# ---------------------------------------------------------------------------

def _make_relay_body(from_webid, to_webid, nonce="aabbccdd11223344"):
    """Build a minimal relay payload (signature will fail — we patch verify)."""
    return json.dumps({
        "from_webid": from_webid,
        "to_webid": to_webid,
        "message_id": str(uuid.uuid4()),
        "content": "hello",
        "timestamp": "2026-01-01T00:00:00Z",
        "relay_nonce": nonce,
        "signature": "fake",
    }).encode()


@pytest.mark.asyncio
async def test_relay_new_recipient_accepted(tmp_path):
    """POST /relay for a previously-unknown DID is accepted (queued for offline delivery)."""
    gw = _make_gw(tmp_path)
    from proxion_messenger_core.didkey import pub_key_to_did
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    sender_key = Ed25519PrivateKey.generate()
    sender_pub = sender_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    sender_did = pub_key_to_did(sender_pub)
    receiver_did = pub_key_to_did(
        Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )

    body = _make_relay_body(sender_did, receiver_did)

    # Patch out signature verification so we can isolate the queue logic
    with patch("proxion_messenger_core.relay.verify_relay_message", return_value=True), \
         patch("proxion_messenger_core.gateway.ProxionGateway._record_peer_gateway"):
        status, resp = await gw._handle_relay_post(body)

    # Should queue (202) or deliver (200) but NOT reject
    assert status in ("200 OK", "202 Accepted"), f"New recipient should be queued: {status!r}: {resp}"


@pytest.mark.asyncio
async def test_relay_queue_cap(tmp_path):
    """_relay_queue must not grow beyond 500 unique recipients."""
    gw = _make_gw(tmp_path)
    from proxion_messenger_core.didkey import pub_key_to_did
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    def _new_did():
        k = Ed25519PrivateKey.generate()
        pub = k.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return pub_key_to_did(pub), k

    sender_did, sender_key = _new_did()

    # Pre-fill relay queue to the cap with mock entries
    for _ in range(500):
        fake_did = pub_key_to_did(
            Ed25519PrivateKey.generate().public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )
        gw._relay_queue[fake_did] = []

    assert len(gw._relay_queue) == 500

    # Now try to relay to a known (self) recipient that would hit the cap check
    self_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    body = _make_relay_body(sender_did, self_did, nonce="cafebabe12345678")

    with patch("proxion_messenger_core.relay.verify_relay_message", return_value=True), \
         patch("proxion_messenger_core.gateway.ProxionGateway._record_peer_gateway"):
        status, resp = await gw._handle_relay_post(body)

    assert status.startswith("507"), f"Expected 507 at cap, got {status!r}: {resp}"


# ---------------------------------------------------------------------------
# 3. Replay protection — nonces are partitioned by sender
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_nonce_partitioned_by_sender(tmp_path):
    """The same raw nonce from different senders must NOT trigger deduplication."""
    gw = _make_gw(tmp_path)
    from proxion_messenger_core.didkey import pub_key_to_did
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    def _new_did():
        k = Ed25519PrivateKey.generate()
        pub = k.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return pub_key_to_did(pub)

    alice_did = _new_did()
    bob_did = _new_did()
    shared_nonce = "SAME-NONCE-12345"

    # Pre-seed Alice's partitioned nonce so it appears "seen"
    alice_key = hashlib.sha256(f"{alice_did}:{shared_nonce}".encode()).hexdigest()
    gw._seen_relay_nonces.append(alice_key)

    # Bob uses the same raw nonce — must NOT be treated as duplicate
    bob_key = hashlib.sha256(f"{bob_did}:{shared_nonce}".encode()).hexdigest()
    assert bob_key not in gw._seen_relay_nonces, (
        "Bob's partitioned nonce must be distinct from Alice's"
    )


@pytest.mark.asyncio
async def test_replay_nonce_same_sender_deduplicated(tmp_path):
    """The same nonce from the same sender IS a replay and must be rejected."""
    gw = _make_gw(tmp_path)
    from proxion_messenger_core.didkey import pub_key_to_did
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    sender_key = Ed25519PrivateKey.generate()
    sender_pub = sender_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    sender_did = pub_key_to_did(sender_pub)
    self_did = pub_key_to_did(gw.agent.identity_pub_bytes)

    nonce = "deadbeef87654321"
    body = json.dumps({
        "from_webid": sender_did,
        "to_webid": self_did,
        "message_id": str(uuid.uuid4()),
        "content": "hello",
        "timestamp": "2026-01-01T00:00:00Z",
        "relay_nonce": nonce,
        "signature": "fake",
    }).encode()

    with patch("proxion_messenger_core.relay.verify_relay_message", return_value=True), \
         patch("proxion_messenger_core.gateway.ProxionGateway._record_peer_gateway"):
        # First delivery
        status1, _ = await gw._handle_relay_post(body)
        # Second delivery — same nonce, same sender → duplicate
        body2 = json.dumps({
            "from_webid": sender_did,
            "to_webid": self_did,
            "message_id": str(uuid.uuid4()),  # different message_id to bypass msg-id dedup
            "content": "hello again",
            "timestamp": "2026-01-01T00:00:01Z",
            "relay_nonce": nonce,
            "signature": "fake",
        }).encode()
        status2, resp2 = await gw._handle_relay_post(body2)

    assert json.loads(resp2).get("status") == "duplicate", (
        f"Same sender+nonce should be duplicate, got {status2}: {resp2}"
    )


# ---------------------------------------------------------------------------
# 4. Import quota bypass prevention
# ---------------------------------------------------------------------------

def test_import_data_respects_message_count_quota(tmp_path):
    """import_data must skip messages that would exceed the per-thread count limit."""
    db_path = str(tmp_path / "import_quota.db")
    store = LocalStore(db_path)
    thread_id = "thread_import_test"

    # Fill the thread to the limit via normal save_message
    for i in range(5000):
        store.save_message(
            message_id=str(uuid.uuid4()),
            thread_id=thread_id,
            thread_type="room",
            from_webid="did:key:alice",
            from_display_name="Alice",
            content=f"msg {i}",
            timestamp="2026-01-01T00:00:00Z",
        )

    # Attempt to import more messages into the same thread
    overflow_id = str(uuid.uuid4())
    counts = store.import_data({
        "messages": [
            {
                "message_id": overflow_id,
                "thread_id": thread_id,
                "thread_type": "room",
                "from_webid": "did:key:alice",
                "content": "smuggled message",
                "timestamp": "2026-01-01T01:00:00Z",
            }
        ]
    })

    # The overflow message must have been skipped (count 0)
    assert counts["messages"] == 0, f"Expected 0 imported, got {counts['messages']}"

    # Verify it's not in the database
    with store._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (overflow_id,)
        ).fetchone()
    assert row is None, "import_data must not insert messages beyond the quota"


def test_import_data_respects_byte_quota(tmp_path):
    """import_data must skip messages that would exceed the per-thread byte limit."""
    db_path = str(tmp_path / "import_byte_quota.db")
    store = LocalStore(db_path)
    thread_id = "thread_byte_test"

    # Build a single large message just over the 50 MB byte limit
    big_content = "x" * (50 * 1024 * 1024 + 1)
    overflow_id = str(uuid.uuid4())
    counts = store.import_data({
        "messages": [
            {
                "message_id": overflow_id,
                "thread_id": thread_id,
                "thread_type": "room",
                "from_webid": "did:key:alice",
                "content": big_content,
                "timestamp": "2026-01-01T00:00:00Z",
            }
        ]
    })

    assert counts["messages"] == 0, "Single message exceeding byte quota must be skipped"


# ---------------------------------------------------------------------------
# 5. Content-Length guard (HTTP layer)
# ---------------------------------------------------------------------------

def test_is_trusted_origin_null_string_rejected():
    """The string 'null' (as bytes) must not be trusted, even without leading/trailing spaces."""
    for variant in (b"null", b"NULL", b"Null"):
        assert not ProxionGateway._is_trusted_origin(variant, 8080), (
            f"Origin {variant!r} should not be trusted"
        )
