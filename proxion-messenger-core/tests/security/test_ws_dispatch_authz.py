"""WebSocket-dispatch authorization: a store write keyed by client-supplied
fields must be bound to the connection's authenticated identity.

Covers three handlers whose read side was already scoped to the caller while the
write/leak side was not:

* apply_contact_verification_sync (F3) - forced into the caller's namespace so a
  stranger cannot forge or clobber the owner's safety-number records.
* get_peer_devices / get_peer_device_keys (F14) - gated on an existing
  relationship so a device roster does not leak to an unrelated caller.
* ack_delivered / ack_read (F16) - bound to a thread the caller participates in
  and the message's genuine sender, so a receipt cannot be forged to an
  arbitrary account for an arbitrary message_id.

These are handler-authorization checks: they exercise _client_webids and
_force_auth directly against real store rows, so they construct the gateway in
process (matching tests/security/test_auth_context_binding.py) rather than
driving a live socket. No gateway thread is started.
"""
import asyncio
import json
import time
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def gw(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    agent = AgentState.generate()
    g = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=GatewayConfig(db_path=str(tmp_path / "test.db")),
    )
    g._force_auth = True  # _auth_enforced() -> True; the checks under test apply
    return g


def make_ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.remote_address = ("1.2.3.4", 12345)
    return ws


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def last_json(ws):
    calls = [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]
    return calls[-1] if calls else None


def _cert(cert_id, peer_did, expires_at):
    return {
        "certificate_id": cert_id,
        "subject": "aa" * 32,
        "peer_did": peer_did,
        "created_at": int(time.time()) - 10,
        "expires_at": expires_at,
    }


# ── F3: contact verification sync is bound to the caller ──────────────────────

def test_apply_verification_sync_rejects_forged_verified_by(gw):
    """A stranger's sync naming another account as verified_by is rescoped to the
    caller, never written into the impersonated owner's namespace."""
    ws = make_ws()
    alice = "did:key:zAlice"
    owner = "did:key:zOwner"
    gw._client_webids[ws] = alice
    run(gw._handle_apply_contact_verification_sync(ws, {
        "record": {
            "peer_webid": "did:key:zPeer",
            "safety_numbers": "FORGED",
            "verified_by": owner,          # attempt to write into owner's namespace
            "verification_version": 999,
        }
    }))
    assert gw._store.get_contact_verification("did:key:zPeer", owner) is None
    row = gw._store.get_contact_verification("did:key:zPeer", alice)
    assert row is not None and row["safety_numbers"] == "FORGED"


def test_apply_verification_sync_cannot_clobber_owner_row(gw):
    """An existing owner row survives a forger's higher-version overwrite."""
    owner = "did:key:zOwner"
    gw._store.save_contact_verification("did:key:zPeer", "SN-REAL", owner)
    ws = make_ws()
    gw._client_webids[ws] = "did:key:zMallory"
    run(gw._handle_apply_contact_verification_sync(ws, {
        "record": {
            "peer_webid": "did:key:zPeer",
            "safety_numbers": "SN-EVIL",
            "verified_by": owner,
            "verification_version": 999,
        }
    }))
    assert gw._store.get_contact_verification("did:key:zPeer", owner)["safety_numbers"] == "SN-REAL"


def test_apply_verification_sync_allows_own_records(gw):
    """Legitimate self-sync: the caller writes rows in its own namespace."""
    ws = make_ws()
    alice = "did:key:zAlice"
    gw._client_webids[ws] = alice
    run(gw._handle_apply_contact_verification_sync(ws, {
        "record": {
            "peer_webid": "did:key:zPeer",
            "safety_numbers": "SN-OWN",
            "verified_by": alice,
            "verified_on_device_id": "dev-1",
            "verification_version": 4,
        }
    }))
    rows = gw._store.list_contact_verifications(alice)
    assert len(rows) == 1
    assert rows[0]["safety_numbers"] == "SN-OWN"
    assert rows[0]["verification_version"] == 4


# ── F14: peer device rosters require a relationship ───────────────────────────

def test_get_peer_devices_refused_without_relationship(gw):
    peer = "did:key:zPeer"
    gw._store.register_device("dev-1", peer, "PUBKEY", "ATTEST")
    ws = make_ws()
    gw._client_webids[ws] = "did:key:zStranger"
    run(gw._handle_get_peer_devices(ws, {"peer_webid": peer}))
    resp = last_json(ws)
    assert resp["type"] == "peer_devices"
    assert resp["devices"] == []


def test_get_peer_devices_allowed_with_relationship(gw):
    peer = "did:key:zPeer"
    gw._store.register_device("dev-1", peer, "PUBKEY", "ATTEST")
    gw._store.save_relationship(
        _cert("cert-1", peer, int(time.time()) + 86400),
        peer_did=peer, owner_webid="did:key:zAlice",
    )
    ws = make_ws()
    gw._client_webids[ws] = "did:key:zAlice"
    run(gw._handle_get_peer_devices(ws, {"peer_webid": peer}))
    resp = last_json(ws)
    assert [d["device_id"] for d in resp["devices"]] == ["dev-1"]
    # last_seen_at is dropped from the response.
    assert "last_seen_at" not in resp["devices"][0]


def test_get_peer_devices_allowed_for_own_account(gw):
    me = "did:key:zMe"
    gw._store.register_device("dev-me", me, "PUBKEY", "ATTEST")
    ws = make_ws()
    gw._client_webids[ws] = me
    run(gw._handle_get_peer_devices(ws, {"peer_webid": me}))
    resp = last_json(ws)
    assert [d["device_id"] for d in resp["devices"]] == ["dev-me"]


def test_get_peer_device_keys_refused_without_relationship(gw):
    peer = "did:key:zPeer"
    ws = make_ws()
    gw._client_webids[ws] = "did:key:zStranger"
    run(gw._handle_get_peer_device_keys(ws, {"peer_webid": peer}))
    resp = last_json(ws)
    assert resp["type"] == "peer_device_keys"
    assert resp["devices"] == []


# ── F16: delivery/read receipts are bound to a real thread + sender ───────────

def _setup_dm(gw, owner, thread_id, peer, message_id):
    gw._store.save_dm_thread(thread_id, peer, None, owner)
    gw._store.save_message(
        message_id, thread_id, "dm", peer, None, "hi", "2026-01-01T00:00:00Z"
    )


def test_ack_read_persists_for_thread_participant(gw):
    alice, bob = "did:key:zAlice", "did:key:zBob"
    _setup_dm(gw, alice, "thread-1", bob, "msg-1")
    ws = make_ws()
    gw._client_webids[ws] = alice
    run(gw._handle_ack_read(ws, {"message_id": "msg-1", "sender_webid": bob}))
    receipts = gw._store.get_receipts("msg-1")
    assert len(receipts) == 1
    assert receipts[0]["receiver_webid"] == alice
    assert receipts[0]["read_at"]
    assert last_json(ws)["type"] == "ack_read_ok"


def test_ack_read_rejected_when_caller_not_in_thread(gw):
    alice, bob = "did:key:zAlice", "did:key:zBob"
    _setup_dm(gw, alice, "thread-1", bob, "msg-1")  # thread belongs to alice
    ws = make_ws()
    gw._client_webids[ws] = "did:key:zMallory"      # not a participant
    run(gw._handle_ack_read(ws, {"message_id": "msg-1", "sender_webid": bob}))
    assert gw._store.get_receipts("msg-1") == []
    # normal ok shape, not a distinct error (no-leak of thread membership)
    assert last_json(ws)["type"] == "ack_read_ok"


def test_ack_delivered_rejected_for_forged_sender(gw):
    alice, bob = "did:key:zAlice", "did:key:zBob"
    _setup_dm(gw, alice, "thread-1", bob, "msg-1")
    ws = make_ws()
    gw._client_webids[ws] = alice  # a real participant, but names a bogus sender
    run(gw._handle_ack_delivered(ws, {"message_id": "msg-1", "sender_webid": "did:key:zEve"}))
    assert gw._store.get_receipts("msg-1") == []
    assert last_json(ws)["type"] == "ack_delivered_ok"


def test_ack_delivered_persists_for_genuine_sender(gw):
    alice, bob = "did:key:zAlice", "did:key:zBob"
    _setup_dm(gw, alice, "thread-1", bob, "msg-1")
    ws = make_ws()
    gw._client_webids[ws] = alice
    run(gw._handle_ack_delivered(ws, {"message_id": "msg-1", "sender_webid": bob}))
    receipts = gw._store.get_receipts("msg-1")
    assert len(receipts) == 1
    assert receipts[0]["delivered_at"]
