"""Tests for Track C — Cross-Gateway Federation.

C1: Peer gateway URL persistence and _resolve_peer_gateway
C2: SQLite-backed relay retry queue and _relay_retry_loop
C3: .well-known/proxion emits gateway_http_url; DM send path uses SQLite on failure
"""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.didkey import pub_key_to_did


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_agent():
    key = Ed25519PrivateKey.generate()
    agent = MagicMock(spec=AgentState)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    agent.identity_pub_bytes = pub_bytes
    agent.identity_key = key
    return agent, key, pub_key_to_did(pub_bytes)


def _make_gateway(tmp_path, **cfg_kwargs):
    agent, key, did = _make_agent()
    db = str(tmp_path / "gw.db")
    cfg = GatewayConfig(db_path=db, http_port=8080, host="127.0.0.1", **cfg_kwargs)
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=cfg, read_state=ReadState(),
    )
    return gw, agent, key, did


# ── C1: Peer gateway URL persistence ─────────────────────────────────────────

def test_peer_gateway_urls_loaded_on_init(tmp_path):
    """Peer gateway URLs saved to SQLite are loaded into memory on gateway init."""
    store = LocalStore(str(tmp_path / "store.db"))
    store.save_peer_gateway("did:key:bob", "http://bob-gateway:8080")

    agent, _, _ = _make_agent()
    cfg = GatewayConfig(db_path=str(tmp_path / "store.db"))
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=cfg, read_state=ReadState(),
    )
    assert gw._peer_gateway_urls.get("did:key:bob") == "http://bob-gateway:8080"


def test_resolve_peer_gateway_hits_memory_first(tmp_path):
    """_resolve_peer_gateway returns in-memory value without hitting SQLite."""
    gw, _, _, _ = _make_gateway(tmp_path)
    gw._peer_gateway_urls["did:key:carol"] = "http://carol:8080"
    assert gw._resolve_peer_gateway("did:key:carol") == "http://carol:8080"


def test_resolve_peer_gateway_falls_back_to_sqlite(tmp_path):
    """_resolve_peer_gateway loads from SQLite if not in memory, then caches it."""
    gw, _, _, _ = _make_gateway(tmp_path)
    gw._store.save_peer_gateway("did:key:dave", "http://dave:8080")
    # Not in memory yet
    assert "did:key:dave" not in gw._peer_gateway_urls

    result = gw._resolve_peer_gateway("did:key:dave")
    assert result == "http://dave:8080"
    # Now cached in memory
    assert gw._peer_gateway_urls.get("did:key:dave") == "http://dave:8080"


def test_resolve_peer_gateway_returns_none_when_unknown(tmp_path):
    gw, _, _, _ = _make_gateway(tmp_path)
    assert gw._resolve_peer_gateway("did:key:nobody") is None


@pytest.mark.asyncio
async def test_record_peer_gateway_persists_to_sqlite(tmp_path):
    """_record_peer_gateway saves to both memory and SQLite."""
    gw, _, _, _ = _make_gateway(tmp_path)
    gw._record_peer_gateway("did:key:eve", "http://eve:9000")

    assert gw._peer_gateway_urls["did:key:eve"] == "http://eve:9000"
    assert gw._store.get_peer_gateway("did:key:eve") == "http://eve:9000"


@pytest.mark.asyncio
async def test_record_peer_gateway_survives_restart(tmp_path):
    """After recording a peer gateway, a new gateway instance sees it."""
    db = str(tmp_path / "gw.db")
    agent, key, did = _make_agent()

    gw1 = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(db_path=db), read_state=ReadState(),
    )
    gw1._record_peer_gateway("did:key:frank", "http://frank:7474")

    # Simulate restart with a new gateway instance using the same DB
    gw2 = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(db_path=db), read_state=ReadState(),
    )
    assert gw2._peer_gateway_urls.get("did:key:frank") == "http://frank:7474"


# ── C2: SQLite relay retry queue ──────────────────────────────────────────────

def test_enqueue_and_get_pending_relays(tmp_path):
    store = LocalStore(str(tmp_path / "store.db"))
    payload = {"from_webid": "did:key:a", "to_webid": "did:key:b", "content": "hello"}
    store.enqueue_relay("msg-001", "did:key:b", "http://b-gw:8080", payload)

    pending = store.get_pending_relays()
    assert len(pending) == 1
    assert pending[0]["id"] == "msg-001"
    assert pending[0]["to_webid"] == "did:key:b"
    assert pending[0]["status"] == "pending"
    assert json.loads(pending[0]["payload_json"]) == payload


def test_mark_relay_delivered_removes_from_pending(tmp_path):
    store = LocalStore(str(tmp_path / "store.db"))
    store.enqueue_relay("msg-002", "did:key:b", "http://b:8080", {})
    store.mark_relay_delivered("msg-002")
    assert store.get_pending_relays() == []


def test_mark_relay_permanently_failed(tmp_path):
    store = LocalStore(str(tmp_path / "store.db"))
    store.enqueue_relay("msg-003", "did:key:b", "http://b:8080", {})
    store.mark_relay_permanently_failed("msg-003")
    assert store.get_pending_relays() == []


def test_increment_relay_attempt(tmp_path):
    store = LocalStore(str(tmp_path / "store.db"))
    store.enqueue_relay("msg-004", "did:key:b", "http://b:8080", {})
    store.increment_relay_attempt("msg-004")
    pending = store.get_pending_relays()
    assert pending[0]["attempt_count"] == 1
    assert pending[0]["last_attempt_at"] is not None


def test_enqueue_relay_idempotent(tmp_path):
    """Same relay_id can't be enqueued twice."""
    store = LocalStore(str(tmp_path / "store.db"))
    store.enqueue_relay("msg-005", "did:key:b", "http://b:8080", {"v": 1})
    store.enqueue_relay("msg-005", "did:key:b", "http://b:8080", {"v": 2})
    pending = store.get_pending_relays()
    assert len(pending) == 1
    assert json.loads(pending[0]["payload_json"])["v"] == 1  # first wins


@pytest.mark.asyncio
async def test_relay_retry_loop_delivers_pending(tmp_path):
    """_relay_retry_loop picks up pending relays and delivers them."""
    gw, _, _, _ = _make_gateway(tmp_path)
    payload = {"from_webid": "did:key:a", "to_webid": "did:key:b",
               "content": "hi", "message_id": "r1", "timestamp": "t", "signature": "s"}
    gw._store.enqueue_relay("r1", "did:key:b", "http://b:8080", payload)

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=True):
        try:
            await gw._relay_retry_loop()
        except asyncio.CancelledError:
            pass

    # After delivery, no pending relays remain
    assert gw._store.get_pending_relays() == []


@pytest.mark.asyncio
async def test_relay_retry_loop_increments_attempt_on_failure(tmp_path):
    """_relay_retry_loop increments attempt_count when delivery fails."""
    gw, _, _, _ = _make_gateway(tmp_path)
    gw._store.enqueue_relay("r2", "did:key:b", "http://b:8080", {
        "from_webid": "did:key:a", "to_webid": "did:key:b",
        "content": "hi", "message_id": "r2", "timestamp": "t", "signature": "s",
    })

    sleep_count = 0

    async def fake_sleep(_):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep), \
         patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=False):
        try:
            await gw._relay_retry_loop()
        except asyncio.CancelledError:
            pass

    pending = gw._store.get_pending_relays()
    assert len(pending) == 1
    assert pending[0]["attempt_count"] == 1


@pytest.mark.asyncio
async def test_relay_retry_loop_exits_immediately_without_store():
    """_relay_retry_loop is a no-op when no SQLite store is configured."""
    agent, _, _ = _make_agent()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(), read_state=ReadState(),
    )
    sleep_called = []
    with patch("asyncio.sleep", side_effect=lambda _: sleep_called.append(True)):
        await gw._relay_retry_loop()
    assert sleep_called == []


# ── C3: .well-known/proxion and DM send path ─────────────────────────────────

def test_gateway_http_url_with_http_port(tmp_path):
    gw, _, _, _ = _make_gateway(tmp_path)  # fixture already sets http_port=8080, host=127.0.0.1
    assert gw._gateway_http_url() == "http://127.0.0.1:8080"


def test_gateway_http_url_empty_without_http_port(tmp_path):
    agent, _, _ = _make_agent()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(http_port=None), read_state=ReadState(),
    )
    assert gw._gateway_http_url() == ""


def test_gateway_http_url_prefers_http_public_url(tmp_path):
    """PROXION_HTTP_PUBLIC_URL overrides the computed internal URL."""
    agent, _, _ = _make_agent()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(http_port=8080, http_public_url="https://chat.example.com/"),
        read_state=ReadState(),
    )
    # Trailing slash must be stripped
    assert gw._gateway_http_url() == "https://chat.example.com"


def test_gateway_http_url_public_url_ignores_ssl_context(tmp_path):
    """When http_public_url is set, ssl_certfile/keyfile are irrelevant to the URL."""
    agent, _, _ = _make_agent()
    gw = ProxionGateway(
        agent=agent, dm_clients=[], room_memberships=[],
        config=GatewayConfig(http_port=443, http_public_url="https://chat.example.com"),
        read_state=ReadState(),
    )
    assert gw._gateway_http_url() == "https://chat.example.com"


@pytest.mark.asyncio
async def test_dm_send_records_peer_gateway_url(tmp_path):
    """Sending a DM with target_gateway_url persists it to SQLite."""
    gw, agent, key, sender_did = _make_gateway(tmp_path)
    ws = MagicMock(); ws.send = AsyncMock()
    gw.clients = {ws}
    gw._client_webids[ws] = sender_did
    gw._display_names[ws] = "Alice"

    target_did = "did:key:bob_target"
    # Simulate that bob is NOT connected (no socket)
    # But his gateway URL is provided
    with patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=True):
        await gw.process_command(ws, {
            "cmd": "local_dm",
            "target_webid": target_did,
            "content": "hello",
            "target_gateway_url": "http://bob-gw:8080",
        })

    assert gw._store.get_peer_gateway(target_did) == "http://bob-gw:8080"


@pytest.mark.asyncio
async def test_dm_send_enqueues_on_relay_failure(tmp_path):
    """When post_relay fails, message is queued in SQLite for retry."""
    gw, agent, key, sender_did = _make_gateway(tmp_path)
    ws = MagicMock(); ws.send = AsyncMock()
    gw.clients = {ws}
    gw._client_webids[ws] = sender_did
    gw._display_names[ws] = "Alice"

    target_did = "did:key:carol_target"
    gw._peer_gateway_urls[target_did] = "http://carol-gw:8080"

    with patch("proxion_messenger_core.relay.post_relay", new_callable=AsyncMock, return_value=False):
        await gw.process_command(ws, {
            "cmd": "local_dm",
            "target_webid": target_did,
            "content": "queued message",
        })

    pending = gw._store.get_pending_relays()
    assert len(pending) == 1
    assert pending[0]["to_webid"] == target_did

    # Sender receives relay_pending notification
    sent_types = [json.loads(c[0][0])["type"] for c in ws.send.call_args_list]
    assert "relay_pending" in sent_types


# ── C4: Cross-gateway relay (inbound) records origin gateway ─────────────────

@pytest.mark.asyncio
async def test_handle_relay_post_records_origin_gateway(tmp_path):
    """Inbound relay records the sender's gateway URL via _record_peer_gateway."""
    gw, agent, key, gw_did = _make_gateway(tmp_path)

    # Build a valid signed relay payload
    sender_key = Ed25519PrivateKey.generate()
    sender_pub = sender_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    sender_did = pub_key_to_did(sender_pub)
    from datetime import datetime as _dt, timezone as _tz
    msg_id = "test-relay-001"
    ts = _dt.now(_tz.utc).isoformat()
    sig = sign_relay_message(sender_key, sender_did, gw_did, msg_id, "hello", ts)

    body = json.dumps({
        "from_webid": sender_did,
        "to_webid": gw_did,
        "message_id": msg_id,
        "content": "hello",
        "timestamp": ts,
        "display_name": "Sender",
        "signature": sig,
        "origin_gateway_url": "http://sender-gw:8080",
    }).encode()

    with patch("proxion_messenger_core._gateway_http._is_safe_gateway_url", return_value=True):
        status, _ = await gw._handle_relay_post(body)
    assert status.startswith("2")

    # Gateway URL should now be persisted
    assert gw._store.get_peer_gateway(sender_did) == "http://sender-gw:8080"
    assert gw._peer_gateway_urls.get(sender_did) == "http://sender-gw:8080"


# ── C5: get_relationships includes x25519_pub ─────────────────────────────────

@pytest.mark.asyncio
async def test_get_relationships_includes_x25519_pub(tmp_path):
    """get_relationships returns x25519_pub per contact when stored."""
    gw, agent, key, owner_did = _make_gateway(tmp_path)
    ws = MagicMock(); ws.send = AsyncMock()
    gw.clients = {ws}
    gw._client_webids[ws] = owner_did

    # Store a relationship and the peer's X25519 pub
    peer_key = Ed25519PrivateKey.generate()
    peer_pub_bytes = peer_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    peer_did = pub_key_to_did(peer_pub_bytes)
    cert_id = "cert-x25519-test-001"
    import json as _j, time as _t
    now = int(_t.time())
    with gw._store._conn() as conn:
        conn.execute(
            "INSERT INTO relationships (certificate_id, peer_pub_hex, peer_did, cert_json, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (cert_id, "aabbcc", peer_did,
             _j.dumps({"certificate_id": cert_id, "peer_did": peer_did}),
             now, now + 86400),
        )
    gw._store.save_x25519_pub(peer_did, "peer_x25519_pub_b64u=")

    await gw.process_command(ws, {"cmd": "get_relationships"})

    sent = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    rel_event = next((m for m in sent if m.get("type") == "relationships"), None)
    assert rel_event is not None, f"No relationships event; got: {[m.get('type') for m in sent]}"
    contacts = rel_event.get("contacts", [])
    contact = next((c for c in contacts if c.get("certificate_id") == cert_id), None)
    assert contact is not None, f"cert_id not in contacts: {contacts}"
    assert contact.get("x25519_pub") == "peer_x25519_pub_b64u=", f"x25519_pub missing or wrong: {contact}"


# ── R86/Track 4: single authorization reduction for relayed secondary ops ──────

def test_authorized_relationship_reduces_and_gates(tmp_path):
    """_authorized_relationship is the one gate every secondary-op relay handler uses:
    it returns (cert, our_cert_id) for a known actor and None for unknown/revoked/blocked."""
    from proxion_messenger_core.federation import RelationshipCertificate, Capability

    gw, agent, key, _ = _make_gateway(tmp_path)
    if not hasattr(gw, "_revoked_dids"):
        gw._revoked_dids = set()
    # The gateway now anchors its blocklist to the (tmp) data dir, so it is already
    # isolated from other tests and the machine's ~/.proxion.
    peer_did = "did:key:zPeerAuthzTest"

    # No relationship, and empty input, reduce to None.
    assert gw._authorized_relationship(peer_did) is None
    assert gw._authorized_relationship("") is None

    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(), subject="peerpubhex",
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
    )
    cert.sign(key)
    cert_dict = cert.to_dict()
    gw._store.save_relationship(cert_dict, peer_did=peer_did)

    rel = gw._authorized_relationship(peer_did)
    assert rel is not None
    got_cert, our_cert_id = rel
    assert our_cert_id == cert_dict["certificate_id"]

    # Revoked actor reduces to None.
    gw._revoked_dids.add(peer_did)
    assert gw._authorized_relationship(peer_did) is None
    gw._revoked_dids.discard(peer_did)
    assert gw._authorized_relationship(peer_did) is not None

    # Blocked actor reduces to None.
    gw.blocklist.block(peer_did)
    assert gw._authorized_relationship(peer_did) is None


def test_dm_client_for_target_resolves_and_reduces(tmp_path):
    """_dm_client_for_target is the outbound mirror of _authorized_relationship: it maps a
    DM target to its pod client, resolving the dm_clients cert_id/webid key asymmetry."""
    from proxion_messenger_core.federation import RelationshipCertificate, Capability

    gw, agent, key, _ = _make_gateway(tmp_path)
    target = "did:key:zTargetPod"
    entry = ("CERT_OBJ", "POD_CLIENT")

    # No relationship reduces to None (even with clients present).
    gw.dm_clients = {"unrelated": entry}
    assert gw._dm_client_for_target(target) is None

    cert = RelationshipCertificate(
        issuer=agent.identity_pub_bytes.hex(), subject="tgthex",
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
    )
    cert.sign(key)
    cert_dict = cert.to_dict()
    gw._store.save_relationship(cert_dict, peer_did=target)
    cert_id = cert_dict["certificate_id"]

    # Relationship exists but no matching client entry reduces to None.
    gw.dm_clients = {"unrelated": entry}
    assert gw._dm_client_for_target(target) is None

    # Resolves when the client is keyed by cert_id (federated peer).
    gw.dm_clients = {cert_id: entry}
    res = gw._dm_client_for_target(target)
    assert res is not None
    _cd, _rid, _entry = res
    assert _rid == cert_id and _entry == entry

    # Falls back to the webid key (our own pod).
    gw.dm_clients = {target: entry}
    assert gw._dm_client_for_target(target)[2] == entry


def test_blocklist_anchored_to_data_dir_not_shared(tmp_path):
    """The blocklist lives under the configured data dir, so separate gateways do not
    share block state (fixes the former hardcoded ~/.proxion path)."""
    d1 = tmp_path / "gw1"
    d2 = tmp_path / "gw2"
    d1.mkdir()
    d2.mkdir()
    gw1, *_ = _make_gateway(d1)
    gw2, *_ = _make_gateway(d2)

    assert gw1.blocklist.storage_path.parent == d1
    assert gw2.blocklist.storage_path.parent == d2

    gw1.blocklist.block("did:key:zBlockIsolationTest")
    assert gw1.blocklist.is_blocked("did:key:zBlockIsolationTest")
    assert not gw2.blocklist.is_blocked("did:key:zBlockIsolationTest")  # not shared
