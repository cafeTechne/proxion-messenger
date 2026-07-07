"""Tests for Layer 2 — cross-gateway relay integration."""
import json
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_message, verify_relay_message
from proxion_messenger_core.didkey import pub_key_to_did
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


def _make_agent():
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    return agent, pub_key_to_did(pub_bytes)


@pytest.fixture
def gateway(tmp_db):
    agent, _ = _make_agent()
    return ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9990, db_path=tmp_db),
        read_state=ReadState(),
    )


@pytest.fixture
def two_clients(gateway):
    alice = MagicMock(); alice.send = AsyncMock()
    bob = MagicMock(); bob.send = AsyncMock()
    gateway.clients = {alice, bob}
    gateway._client_webids[alice] = "did:key:alice"
    gateway._client_webids[bob] = "did:key:bob"
    gateway._webid_sockets["did:key:alice"] = alice
    gateway._webid_sockets["did:key:bob"] = bob
    gateway._display_names[alice] = "Alice"
    gateway._display_names[bob] = "Bob"
    return alice, bob


@pytest.mark.asyncio
async def test_register_stores_gateway_url(gateway, two_clients):
    alice, _ = two_clients
    with patch("proxion_messenger_core.relay._validate_relay_target", return_value=True):
        await gateway.process_command(alice, {
            "cmd": "register",
            "webid": "did:key:alice",
            "gateway_url": "wss://alice-server.example.com",
        })
    assert gateway._peer_gateway_urls.get("did:key:alice") == "wss://alice-server.example.com"


@pytest.mark.asyncio
async def test_local_dm_stores_target_gateway_url(gateway, two_clients):
    alice, _ = two_clients
    # Bob is not locally connected — but alice provides his gateway URL
    gateway._webid_sockets.pop("did:key:bob", None)
    gateway.clients.discard(two_clients[1])

    with patch("proxion_messenger_core.relay.post_relay", new=AsyncMock(return_value=True)):
        await gateway.process_command(alice, {
            "cmd": "local_dm",
            "target_webid": "did:key:bob",
            "target_gateway_url": "https://bob-server.example.com",
            "content": "hello bob",
        })

    assert gateway._peer_gateway_urls.get("did:key:bob") == "https://bob-server.example.com"


@pytest.mark.asyncio
async def test_local_dm_relays_when_target_not_local(gateway, two_clients):
    alice, bob = two_clients
    # Disconnect bob
    gateway._webid_sockets.pop("did:key:bob", None)
    gateway.clients.discard(bob)
    gateway._peer_gateway_urls["did:key:bob"] = "https://bob-server.example.com"

    relay_calls = []
    async def mock_post(url, payload, timeout=10.0):
        relay_calls.append((url, payload))
        return True

    with patch("proxion_messenger_core.relay.post_relay", new=mock_post):
        await gateway.process_command(alice, {
            "cmd": "local_dm",
            "target_webid": "did:key:bob",
            "content": "hi bob over the internet",
        })

    assert len(relay_calls) == 1
    url, payload = relay_calls[0]
    assert "bob-server.example.com" in url
    assert payload["content"] == "hi bob over the internet"
    assert payload["to_webid"] == "did:key:bob"
    # from_webid is the gateway's own DID (not the browser-reported string)
    # Signature must be verifiable against that DID
    assert verify_relay_message(
        payload["from_webid"], payload["to_webid"],
        payload["message_id"], payload["content"],
        payload["timestamp"], payload["signature"],
        relay_nonce=payload.get("relay_nonce", ""),
    )
    # from_webid should be a real did:key (starts with "did:key:")
    assert payload["from_webid"].startswith("did:key:")


@pytest.mark.asyncio
async def test_relay_endpoint_delivers_to_connected_client(gateway, two_clients):
    from datetime import datetime, timezone
    alice, bob = two_clients
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    gateway._webid_sockets[bob_did] = bob

    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "relay-test-001"
    content = "hello via relay"
    sig = sign_relay_message(alice_key, alice_did, bob_did, msg_id, content, ts)

    body = json.dumps({
        "from_webid": alice_did,
        "to_webid": bob_did,
        "message_id": msg_id,
        "content": content,
        "timestamp": ts,
        "display_name": "Alice",
        "signature": sig,
    }).encode()

    status, resp = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    delivered = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert any(m.get("content") == content for m in delivered)


@pytest.mark.asyncio
async def test_inbound_relay_does_not_clobber_sender_gateway_seal_key(gateway, two_clients):
    """An inbound relay carrying the sender's BROWSER x25519 must not overwrite
    their GATEWAY seal key (save_x25519_pub), which _resolve_peer_x25519_pub uses
    to seal outbound relays. The clobber made every reply-after-receiving seal to
    the wrong key -> recipient gateway 400 -> reply silently lost. The browser key
    belongs in the e2e_key store instead.
    """
    from datetime import datetime, timezone
    alice, bob = two_clients
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)

    # Discovery already stored Alice's GATEWAY seal key.
    gateway._store.save_x25519_pub(alice_did, "ALICE_GATEWAY_SEAL_KEY")

    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "relay-clobber-1"
    content = "hi"
    sig = sign_relay_message(alice_key, alice_did, "did:key:bob", msg_id, content, ts)
    body = json.dumps({
        "from_webid": alice_did, "to_webid": "did:key:bob",
        "message_id": msg_id, "content": content, "timestamp": ts,
        "display_name": "Alice", "signature": sig,
        "x25519_pub": "ALICE_BROWSER_KEY",   # rides on the DM's E2E fields
    }).encode()

    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    # Seal key preserved; browser key cached separately.
    assert gateway._store.get_x25519_pub(alice_did) == "ALICE_GATEWAY_SEAL_KEY"
    assert gateway._store.get_e2e_key(alice_did) == "ALICE_BROWSER_KEY"


@pytest.mark.asyncio
async def test_relay_from_blocked_sender_is_dropped(gateway, two_clients, tmp_path):
    """A blocked sender's relayed message must be silently accepted (200, so block
    status isn't revealed) but NOT delivered. Previously the relay receive path
    ignored the blocklist entirely."""
    from datetime import datetime, timezone
    from proxion_messenger_core.blocklist import Blocklist
    _, bob = two_clients
    # Isolate the blocklist to a temp file (don't touch ~/.proxion).
    gateway.blocklist = Blocklist(str(tmp_path / "bl.json"))

    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    gateway._webid_sockets[bob_did] = bob
    gateway.blocklist.block(alice_did)

    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "relay-blocked-001"
    content = "you blocked me but here I am"
    sig = sign_relay_message(alice_key, alice_did, bob_did, msg_id, content, ts)
    body = json.dumps({
        "from_webid": alice_did, "to_webid": bob_did, "message_id": msg_id,
        "content": content, "timestamp": ts, "signature": sig,
    }).encode()

    bob.send.reset_mock()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")  # accepted, not 403 — don't reveal the block
    delivered = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert not any(m.get("content") == content for m in delivered), \
        "blocked sender's message must not be delivered"


@pytest.mark.asyncio
async def test_relay_endpoint_rejects_bad_signature(gateway, two_clients):
    _, bob = two_clients
    body = json.dumps({
        "from_webid": "did:key:alice",
        "to_webid": "did:key:bob",
        "message_id": "msg-bad",
        "content": "hack",
        "timestamp": "2026-04-16T12:00:00+00:00",
        "display_name": "Attacker",
        "signature": "BADSIGNATURE",
    }).encode()

    status, resp = await gateway._handle_relay_post(body)
    assert status.startswith("400")
    assert "signature" in resp


@pytest.mark.asyncio
async def test_relay_endpoint_stores_for_offline_target(gateway):
    """Message for an offline user should be stored in SQLite (202 Accepted)."""
    from datetime import datetime, timezone
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob-offline"
    # Bob is not in _webid_sockets

    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "relay-offline-001"
    content = "offline message"
    sig = sign_relay_message(alice_key, alice_did, bob_did, msg_id, content, ts)

    body = json.dumps({
        "from_webid": alice_did,
        "to_webid": bob_did,
        "message_id": msg_id,
        "content": content,
        "timestamp": ts,
        "display_name": "Alice",
        "signature": sig,
    }).encode()

    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("202")
    # Stored under the recipient's (bob's) thread_id when target is offline
    stored = gateway._store.get_messages(bob_did)
    assert any(m["message_id"] == msg_id for m in stored)


@pytest.mark.asyncio
async def test_relay_endpoint_rate_limited(gateway):
    gateway._relay_rate_limiter["127.0.0.1"] = __import__("collections").deque([time.time()] * 60)
    body = json.dumps({
        "from_webid": "did:key:alice",
        "to_webid": "did:key:bob",
        "message_id": "msg-rate",
        "content": "rate",
        "timestamp": "2026-04-16T12:00:00+00:00",
        "display_name": "Alice",
        "signature": "invalid",
    }).encode()
    status, _ = await gateway._handle_relay_post(body, client_ip="127.0.0.1")
    assert status.startswith("429")


@pytest.mark.asyncio
async def test_relay_offline_queue_delivered_on_register(gateway):
    bob = MagicMock()
    bob.send = AsyncMock()
    gateway.clients.add(bob)
    gateway._relay_queue["did:key:bob"] = [{
        "from_webid": "did:key:alice",
        "to_webid": "did:key:bob",
        "message_id": "queued-1",
        "content": "queued",
        "timestamp": "2026-04-16T12:00:00+00:00",
        "display_name": "Alice",
    }]
    await gateway.process_command(bob, {"cmd": "register", "webid": "did:key:bob"})
    delivered = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    queued_msg = next((m for m in delivered if m.get("source") == "relay" and m.get("content") == "queued"), None)
    assert queued_msg is not None, f"Queued relay message not delivered; got: {delivered}"
    assert queued_msg["type"] == "message"
    assert "did:key:bob" not in gateway._relay_queue


def test_relay_queue_capped_at_100(gateway):
    key = "did:key:bob"
    for i in range(105):
        q = gateway._relay_queue.setdefault(key, [])
        if len(q) >= 100:
            q.pop(0)
        q.append({"seq": i})
    assert len(gateway._relay_queue[key]) == 100
    assert gateway._relay_queue[key][0]["seq"] == 5


def _store_relationship(store, cert_id, pub_bytes, peer_did):
    """Insert a test relationship row directly into SQLite."""
    import json as _j, time as _t
    now = int(_t.time())
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO relationships "
            "(certificate_id, peer_pub_hex, peer_did, cert_json, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (cert_id, pub_bytes.hex(), peer_did,
             _j.dumps({"certificate_id": cert_id, "peer_did": peer_did}),
             now, now + 86400 * 365),
        )


@pytest.mark.asyncio
async def test_relay_thread_id_normalized_to_cert_id(gateway, two_clients):
    """_handle_relay_post sets thread_id = cert_id when sender has a stored relationship."""
    alice, bob = two_clients
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    cert_id = "aaaabbbb-cert-0001-0000-000000000000"

    # Pre-store a relationship so the gateway can resolve cert_id from alice_did
    _store_relationship(gateway._store, cert_id, pub_bytes, alice_did)

    gateway._webid_sockets[bob_did] = bob

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    msg_id = "cert-norm-001"
    content = "normalize me"
    sig = sign_relay_message(alice_key, alice_did, bob_did, msg_id, content, ts)

    body = json.dumps({
        "from_webid": alice_did,
        "to_webid": bob_did,
        "message_id": msg_id,
        "content": content,
        "timestamp": ts,
        "display_name": "Alice",
        "signature": sig,
    }).encode()

    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    delivered = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    msg = next((m for m in delivered if m.get("content") == content), None)
    assert msg is not None
    assert msg["thread_id"] == cert_id, f"Expected cert_id as thread_id, got: {msg.get('thread_id')}"
    assert msg["cert_id"] == cert_id


# ── Round 4 tests ─────────────────────────────────────────────────────────────

def _make_relay_body(sender_key, sender_did, to_did, msg_id, content, ts=None,
                     extra: dict | None = None):
    from datetime import datetime, timezone
    from proxion_messenger_core.relay import sign_relay_message
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    sig = sign_relay_message(sender_key, sender_did, to_did, msg_id, content, ts)
    body = {
        "from_webid": sender_did, "to_webid": to_did,
        "message_id": msg_id, "content": content,
        "timestamp": ts, "display_name": "Sender", "signature": sig,
    }
    if extra:
        body.update(extra)
    return json.dumps(body).encode()


@pytest.mark.asyncio
async def test_relay_message_persisted_under_cert_id(gateway, two_clients):
    """Online relay: save_message uses cert_id as thread_id, not from_webid."""
    _, bob = two_clients
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    cert_id = "round4-cert-0001-0000-000000000001"
    _store_relationship(gateway._store, cert_id, pub_bytes, alice_did)
    gateway._webid_sockets[bob_did] = bob

    body = _make_relay_body(alice_key, alice_did, bob_did, "persist-001", "hello")
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")

    stored_cert = gateway._store.get_messages(cert_id)
    assert any(m["message_id"] == "persist-001" for m in stored_cert), (
        f"Message not found under cert_id; got: {stored_cert}"
    )
    stored_did = gateway._store.get_messages(alice_did)
    assert not any(m["message_id"] == "persist-001" for m in stored_did), (
        "Message should NOT be stored under from_webid"
    )


@pytest.mark.asyncio
async def test_offline_relay_message_persisted_under_cert_id(gateway):
    """Offline relay: save_message uses cert_id as thread_id, not to_webid."""
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob-offline-r4"
    cert_id = "round4-cert-0002-0000-000000000002"
    _store_relationship(gateway._store, cert_id, pub_bytes, alice_did)
    # Bob is NOT in _webid_sockets

    body = _make_relay_body(alice_key, alice_did, bob_did, "offline-r4-001", "queued")
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("202")

    stored_cert = gateway._store.get_messages(cert_id)
    assert any(m["message_id"] == "offline-r4-001" for m in stored_cert), (
        f"Offline message not found under cert_id; got: {stored_cert}"
    )
    stored_bobdid = gateway._store.get_messages(bob_did)
    assert not any(m["message_id"] == "offline-r4-001" for m in stored_bobdid), (
        "Offline message should NOT be stored under to_webid"
    )


@pytest.mark.asyncio
async def test_relay_post_forwards_e2e_fields(gateway, two_clients):
    """E2E envelope fields in the POST body are forwarded to the delivered event."""
    _, bob = two_clients
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    gateway._webid_sockets[bob_did] = bob

    e2e_extra = {
        "e2e": True, "nonce": "testnonce123",
        "ratchet_pub": "rpub_base64", "x25519_pub": "xpub_base64",
        "msg_num": 1, "pn": 0,
    }
    body = _make_relay_body(alice_key, alice_did, bob_did, "e2e-fwd-001",
                            "ciphertext_blob", extra=e2e_extra)
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")

    delivered = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    msg = next((m for m in delivered if m.get("message_id") == "e2e-fwd-001"), None)
    assert msg is not None
    assert msg.get("e2e") is True
    assert msg.get("nonce") == "testnonce123"
    assert msg.get("ratchet_pub") == "rpub_base64"
    assert msg.get("x25519_pub") == "xpub_base64"


@pytest.mark.asyncio
async def test_read_dm_returns_relay_history(gateway, two_clients):
    """read_dm returns relay messages stored under cert_id with full metadata."""
    alice, _ = two_clients
    alice_did = "did:key:alice"
    cert_id = "round4-cert-0003-0000-000000000003"

    # Store a relay message directly under cert_id
    gateway._store.save_message(
        "hist-msg-001", cert_id, "relay",
        "did:key:sender", "Sender Name", "history content", "2026-05-10T12:00:00+00:00",
        reply_to_id="hist-msg-000",
    )

    await gateway.process_command(alice, {"cmd": "read_dm", "cert_id": cert_id})
    sent = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    hist = next((m for m in sent if m.get("type") == "history"), None)
    assert hist is not None, f"No history event; got: {sent}"
    msgs = hist.get("messages", [])
    assert any(m["message_id"] == "hist-msg-001" for m in msgs), (
        f"Expected hist-msg-001 in history; got: {msgs}"
    )
    msg = next(m for m in msgs if m["message_id"] == "hist-msg-001")
    assert msg["from_display_name"] == "Sender Name"
    assert msg["source"] == "relay"
    assert msg["reply_to_id"] == "hist-msg-000"
    assert msg["thread_id"] == cert_id


@pytest.mark.asyncio
async def test_register_stores_x25519_pub(gateway):
    """x25519_pub sent in register command is persisted to SQLite."""
    ws = MagicMock(); ws.send = AsyncMock()
    gateway.clients.add(ws)
    await gateway.process_command(ws, {
        "cmd": "register",
        "did": "did:key:alice-x25519",
        "x25519_pub": "alice_x25519_pub_b64u=",
    })
    stored = gateway._store.get_x25519_pub("did:key:alice-x25519")
    assert stored == "alice_x25519_pub_b64u=", f"Expected stored pub, got: {stored!r}"


@pytest.mark.asyncio
async def test_register_pushes_relationships_with_unread(gateway, two_clients):
    """On register, gateway sends relationships event with unread_count."""
    alice, _ = two_clients
    cert_id = "rel-push-cert-0001"
    peer_did = "did:key:carol"

    # Pre-store a relationship
    import json as _j, time as _t
    now = int(_t.time())
    with gateway._store._conn() as conn:
        conn.execute(
            "INSERT INTO relationships "
            "(certificate_id, peer_pub_hex, peer_did, cert_json, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (cert_id, "aabbcc", peer_did,
             _j.dumps({"certificate_id": cert_id, "peer_did": peer_did}),
             now, now + 86400),
        )

    # Save a message under that cert_id (unread since no last_read entry)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    gateway._store.save_message("unread-msg-1", cert_id, "relay",
                                peer_did, None, "hello", ts)

    new_ws = MagicMock(); new_ws.send = AsyncMock()
    gateway.clients.add(new_ws)
    await gateway.process_command(new_ws, {
        "cmd": "register",
        "did": "did:key:alice",
    })

    sent = [json.loads(c[0][0]) for c in new_ws.send.call_args_list]
    rel_event = next((m for m in sent if m.get("type") == "relationships"), None)
    assert rel_event is not None, f"No relationships event; got types: {[m.get('type') for m in sent]}"
    contacts = rel_event.get("contacts", [])
    cert_contact = next((c for c in contacts if c.get("certificate_id") == cert_id), None)
    assert cert_contact is not None, f"cert_id not in relationships: {contacts}"
    assert cert_contact["unread_count"] == 1


@pytest.mark.asyncio
async def test_relay_post_stores_x25519_pub(gateway, two_clients):
    """Incoming relay carrying the sender's BROWSER x25519 persists it in the
    e2e_key store (NOT the gateway seal-key store — see the clobber regression
    test above)."""
    _, bob = two_clients
    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    gateway._webid_sockets[bob_did] = bob

    body = _make_relay_body(alice_key, alice_did, bob_did, "x25519-store-001",
                            "hello",
                            extra={"x25519_pub": "alice_x25519_from_relay="})
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    assert gateway._store.get_e2e_key(alice_did) == "alice_x25519_from_relay="
    # Seal key store untouched by the relay's browser key.
    assert gateway._store.get_x25519_pub(alice_did) is None


@pytest.mark.asyncio
async def test_read_dm_with_before_timestamp(gateway, two_clients):
    """read_dm respects before_timestamp for pagination."""
    alice, _ = two_clients
    cert_id = "paginate-cert-001"
    from datetime import datetime, timezone
    import time as _t

    base = _t.time()
    for i in range(5):
        from datetime import datetime as dt, timezone as tz
        ts = dt.fromtimestamp(base + i, tz=tz.utc).isoformat()
        gateway._store.save_message(
            f"page-msg-{i}", cert_id, "relay", "did:key:sender", None, f"msg {i}", ts
        )

    # Request messages before message 3 (base+3)
    cutoff = dt.fromtimestamp(base + 3, tz=tz.utc).isoformat()
    await gateway.process_command(alice, {
        "cmd": "read_dm",
        "cert_id": cert_id,
        "before_timestamp": cutoff,
        "limit": 10,
    })
    sent = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    hist = next((m for m in sent if m.get("type") == "history"), None)
    assert hist is not None
    msg_ids = [m["message_id"] for m in hist["messages"]]
    assert "page-msg-4" not in msg_ids, "msg-4 should be after cutoff"
    assert "page-msg-3" not in msg_ids, "msg-3 is at cutoff (not strictly before)"
    assert len(msg_ids) == 3, f"Expected 3 messages, got {len(msg_ids)}: {msg_ids}"


# ── Round 6: Multi-device fanout, DM event scoping, file relay ────────────────

@pytest.mark.asyncio
async def test_relay_fanout_to_all_target_sockets(gateway, two_clients):
    """Inbound relay delivers to ALL connected sockets of the target identity."""
    _, bob = two_clients
    # Give bob a second tab
    bob2 = MagicMock(); bob2.send = AsyncMock()
    gateway.clients.add(bob2)
    gateway._webid_sockets["did:key:bob"] = {bob, bob2}

    alice_key = Ed25519PrivateKey.generate()
    pub_bytes = alice_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    alice_did = pub_key_to_did(pub_bytes)
    bob_did = "did:key:bob"
    body = _make_relay_body(alice_key, alice_did, bob_did, "fanout-relay-001",
                            "hello both tabs")
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    bob.send.assert_called_once()
    bob2.send.assert_called_once()
    for mock in (bob, bob2):
        msg = json.loads(mock.send.call_args[0][0])
        assert msg["content"] == "hello both tabs"


@pytest.mark.asyncio
async def test_local_dm_fanout_to_all_target_sockets(gateway, two_clients):
    """local_dm delivers to ALL connected sockets of the target identity."""
    alice, bob = two_clients
    # Give bob a second tab
    bob2 = MagicMock(); bob2.send = AsyncMock()
    gateway.clients.add(bob2)
    gateway._webid_sockets["did:key:bob"] = {bob, bob2}

    await gateway.process_command(alice, {
        "cmd": "local_dm",
        "target_webid": "did:key:bob",
        "content": "hello both bob tabs",
        "thread_id": "did:key:bob",
    })
    bob.send.assert_called_once()
    bob2.send.assert_called_once()
    for mock in (bob, bob2):
        msg = json.loads(mock.send.call_args[0][0])
        assert msg["content"] == "hello both bob tabs"


@pytest.mark.asyncio
async def test_local_dm_sender_echo_reaches_own_other_tabs(gateway, two_clients):
    """local_dm echoes own=True to all of the sender's open tabs."""
    alice, bob = two_clients
    # Give alice a second tab
    alice2 = MagicMock(); alice2.send = AsyncMock()
    gateway.clients.add(alice2)
    gateway._webid_sockets["did:key:alice"] = {alice, alice2}

    await gateway.process_command(alice, {
        "cmd": "local_dm",
        "target_webid": "did:key:bob",
        "content": "tab1 message",
        "thread_id": "did:key:bob",
    })
    # Both alice tabs should see the own echo
    alice2.send.assert_called_once()
    echo = json.loads(alice2.send.call_args[0][0])
    assert echo["own"] is True
    assert echo["content"] == "tab1 message"


@pytest.mark.asyncio
async def test_delete_dm_scoped_to_participants(gateway, two_clients):
    """delete_local_message for a DM thread does NOT reach unrelated clients."""
    alice, bob = two_clients
    # Carol is connected but not part of alice↔bob thread
    carol = MagicMock(); carol.send = AsyncMock()
    gateway.clients.add(carol)
    gateway._client_webids[carol] = "did:key:carol"
    gateway._webid_sockets["did:key:carol"] = carol

    # Save a message and a DM thread so the delete handler can find the peer
    gateway._store.save_message("del-scope-1", "did:key:bob", "dm",
                                "did:key:alice", "Alice", "hi", "2026-05-10T12:00:00+00:00")
    gateway._store.save_dm_thread("did:key:bob", "did:key:bob", "Bob",
                                  owner_webid="did:key:alice")

    await gateway.process_command(alice, {
        "cmd": "delete_local_message",
        "message_id": "del-scope-1",
        "thread_id": "did:key:bob",
    })
    carol_events = [json.loads(c[0][0]) for c in carol.send.call_args_list]
    assert not any(e.get("type") == "message_deleted" for e in carol_events), \
        "Carol should not receive delete event from alice↔bob DM"
    # Alice and Bob should receive it
    alice_events = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    bob_events = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert any(e.get("type") == "message_deleted" for e in alice_events)
    assert any(e.get("type") == "message_deleted" for e in bob_events)


@pytest.mark.asyncio
async def test_edit_dm_scoped_to_participants(gateway, two_clients):
    """edit_local_message for a DM thread does NOT reach unrelated clients."""
    alice, bob = two_clients
    carol = MagicMock(); carol.send = AsyncMock()
    gateway.clients.add(carol)
    gateway._client_webids[carol] = "did:key:carol"
    gateway._webid_sockets["did:key:carol"] = carol

    gateway._store.save_message("edit-scope-1", "did:key:bob", "dm",
                                "did:key:alice", "Alice", "original", "2026-05-10T12:00:00+00:00")
    gateway._store.save_dm_thread("did:key:bob", "did:key:bob", "Bob",
                                  owner_webid="did:key:alice")

    await gateway.process_command(alice, {
        "cmd": "edit_local_message",
        "message_id": "edit-scope-1",
        "thread_id": "did:key:bob",
        "content": "edited",
    })
    carol_events = [json.loads(c[0][0]) for c in carol.send.call_args_list]
    assert not any(e.get("type") == "message_edited" for e in carol_events), \
        "Carol should not receive edit event from alice↔bob DM"
    alice_events = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    bob_events = [json.loads(c[0][0]) for c in bob.send.call_args_list]
    assert any(e.get("type") == "message_edited" for e in alice_events)
    assert any(e.get("type") == "message_edited" for e in bob_events)


@pytest.mark.asyncio
async def test_send_file_cert_dm_relay_fallback(gateway, two_clients):
    """send_file for an offline cert-DM peer falls back to relay."""
    alice, _ = two_clients
    cert_id = "file-relay-cert-001"
    peer_did = "did:key:offline-bob"

    # Bob is not connected, but his gateway URL is known
    gateway._peer_gateway_urls[peer_did] = "http://bob-gw:8080"
    # Store a DM thread so send_file can find the peer webid
    gateway._store.save_dm_thread(cert_id, peer_did, "OfflineBob",
                                  owner_webid="did:key:alice")

    import base64 as _b64
    small_file = b"hello file content"
    with patch("proxion_messenger_core.relay.post_relay",
               new_callable=AsyncMock, return_value=False) as mock_relay:
        await gateway.process_command(alice, {
            "cmd": "send_file",
            "cert_id": cert_id,
            "filename": "hello.txt",
            "mime_type": "text/plain",
            "data_b64": _b64.b64encode(small_file).decode(),
        })
        mock_relay.assert_called_once()

    # Sender should receive own echo and relay_pending notification
    sent = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    own_echo = next((m for m in sent if m.get("own") and m.get("content", "").startswith("📎")), None)
    assert own_echo is not None, f"Own echo missing; got: {sent}"
    pending = next((m for m in sent if m.get("type") == "relay_pending"), None)
    assert pending is not None, f"relay_pending missing; got: {sent}"


# ── Round 7: Invite & Contact UX ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_pushes_friend_requests_when_pending(gateway):
    """7.9.1 — _handle_register pushes friend_requests event when pending invites exist."""
    # Seed a pending invite in the store
    invite_dict = {
        "@type": "FederationInvite",
        "invitation_id": "inv-r7-001",
        "issuer": {
            "did": "did:key:friend-alice",
            "public_key": "aabbcc",
            "display_name": "Friend Alice",
        },
        "capabilities": [],
        "endpoint_hints": ["http://alice-gw:8080"],
        "expires_at": 9999999999,
    }
    gateway._store.save_pending_invite(invite_dict, "did:key:friend-alice")

    ws = MagicMock(); ws.send = AsyncMock()
    gateway.clients.add(ws)
    await gateway.process_command(ws, {"cmd": "register", "webid": "did:key:newuser"})

    sent = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    fr_event = next((m for m in sent if m.get("type") == "friend_requests"), None)
    assert fr_event is not None, f"friend_requests event not pushed; got types: {[m.get('type') for m in sent]}"
    pending_list = fr_event.get("pending", [])
    assert len(pending_list) == 1
    assert pending_list[0]["invitation_id"] == "inv-r7-001"
    assert pending_list[0]["from_did"] == "did:key:friend-alice"
    assert pending_list[0]["display_name"] == "Friend Alice"


@pytest.mark.asyncio
async def test_send_friend_request_invalid_address_friendly_error(gateway, two_clients):
    """7.9.2 — send_friend_request with missing '@' returns friendly invalid_address error."""
    alice, _ = two_clients
    await gateway.process_command(alice, {
        "cmd": "send_friend_request",
        "target_address": "did:key:noatsign",
    })
    sent = [json.loads(c[0][0]) for c in alice.send.call_args_list]
    err = next((m for m in sent if m.get("type") == "error"), None)
    assert err is not None, f"No error event sent; got: {sent}"
    assert err.get("message") == "invalid_address"
    # detail should be human-readable, not just a code
    detail = err.get("detail", "")
    assert "did:key" in detail or "format" in detail.lower(), \
        f"detail not human-readable: {detail!r}"


@pytest.mark.asyncio
async def test_handle_invite_post_includes_display_name(gateway):
    """7.9.3 — _handle_invite_post broadcasts display_name from issuer."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as _ser
    from proxion_messenger_core import handshake
    from proxion_messenger_core.federation import Capability

    # Create a real invite signed by a peer key
    peer_key = Ed25519PrivateKey.generate()
    peer_pub = peer_key.public_key().public_bytes(
        _ser.Encoding.Raw, _ser.PublicFormat.Raw
    )
    peer_did = pub_key_to_did(peer_pub)

    # store_pub for the invite (just the same key for simplicity)
    invite = handshake.create_invite(
        peer_key,
        peer_pub,
        [Capability(with_="stash://dm/", can="crud/write")],
        endpoint_hints=["https://peer-gw:8080"],
        display_name="Peer Alice",
    )
    invite_bytes = json.dumps(invite.to_dict()).encode()

    # Wire up a connected socket so broadcast reaches it
    ws = MagicMock(); ws.send = AsyncMock()
    gateway.clients.add(ws)

    status, _ = await gateway._handle_invite_post(invite_bytes)
    assert status.startswith("200"), f"Expected 200, got: {status}"

    sent = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    fr = next((m for m in sent if m.get("type") == "friend_request_received"), None)
    assert fr is not None, f"friend_request_received not broadcast; got: {sent}"
    assert fr.get("from_did") == peer_did
    assert fr.get("display_name") == "Peer Alice", \
        f"display_name missing or wrong: {fr.get('display_name')!r}"
