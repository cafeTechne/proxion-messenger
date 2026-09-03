"""Relay authz hardening: DM-edit author check + privileged-relay binding.

Covers two gateway auth fixes:
  - the dm_edit relay must not rewrite a message the peer did not author
    (update_message is a global UPDATE by message_id, like the delete relay);
  - moderation/emoji relays must not be authorized by a first-use TOFU seed of an
    empty relaygw slot (owner-impersonation over /relay).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.relay import sign_relay_envelope


@pytest.fixture
def gateway(tmp_path):
    agent = MagicMock(spec=AgentState)
    agent.identity_pub_bytes = b"\x01" * 32
    agent.identity_key = MagicMock()
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9992, db_path=str(tmp_path / "t.db")),
        read_state=ReadState(),
    )
    gw._store = LocalStore(str(tmp_path / "t.db"))
    return gw


def _did():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pub_key_to_did(pub)


@pytest.mark.asyncio
async def test_dm_edit_relay_rejects_non_author(gateway):
    peer = _did()
    author = _did()
    gateway._store.save_relationship({"certificate_id": "cert-x", "subject": "aa" * 32}, peer_did=peer)
    gateway._store.save_message("m-auth", "cert-x", "local_dm", author, "Author", "original", "2026-01-01T00:00:00Z")
    status, _ = await gateway._handle_dm_edit_relay({
        "from_webid": peer, "to_webid": author, "message_id": "m-auth", "new_content": "HACKED",
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m-auth")["content"] == "original"


@pytest.mark.asyncio
async def test_dm_edit_relay_allows_author(gateway):
    peer = _did()
    gateway._store.save_relationship({"certificate_id": "cert-y", "subject": "bb" * 32}, peer_did=peer)
    gateway._store.save_message("m-own", "cert-y", "local_dm", peer, "Peer", "original", "2026-01-01T00:00:00Z")
    status, _ = await gateway._handle_dm_edit_relay({
        "from_webid": peer, "to_webid": peer, "message_id": "m-own", "new_content": "edited",
    })
    assert status.startswith("200")
    assert gateway._store.get_message("m-own")["content"] == "edited"


def test_privileged_relay_requires_established_binding(gateway):
    owner = _did()
    sig_did = _did()
    # Empty slot + require_established → refused, and NOT seeded.
    assert gateway._relay_sender_gateway_ok(owner, sig_did, require_established=True) is False
    assert not gateway._store.get_identity_key_history("relaygw:" + owner)
    # An ordinary relay seeds the binding (TOFU) ...
    assert gateway._relay_sender_gateway_ok(owner, sig_did) is True
    # ... after which the privileged path accepts that SAME signer ...
    assert gateway._relay_sender_gateway_ok(owner, sig_did, require_established=True) is True
    # ... but still refuses a DIFFERENT signer.
    assert gateway._relay_sender_gateway_ok(owner, _did(), require_established=True) is False


def test_relay_binding_not_seeded_when_may_seed_false(gateway):
    """With may_seed=False an empty slot is NOT seeded (the caller only sets it
    after verifying the signature and authorizing from_webid)."""
    owner = _did()
    sig_did = _did()
    assert gateway._relay_sender_gateway_ok(owner, sig_did, may_seed=False) is False
    assert not gateway._store.get_identity_key_history("relaygw:" + owner)


# ── B1: verify-before-seed + membership-gated seeding at the /relay gate ─────────

def _priv_did():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, pub_key_to_did(pub)


def _mock_member_ws():
    ws = AsyncMock(); ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s); ws.__eq__ = lambda s, o: s is o
    return ws


def _signed_room_message(signer_key, signer_did, room_id, member_did, mid):
    payload = {
        "content_type": "room_message", "room_id": room_id, "thread_id": room_id,
        "from_webid": member_did, "from_display_name": "M", "content": "hello",
        "message_id": mid, "timestamp": datetime.now(timezone.utc).isoformat(),
        "relay_sig_did": signer_did,
        "relay_ts": datetime.now(timezone.utc).isoformat(),
        "relay_nonce": "rn-" + mid,
    }
    payload["signature"] = sign_relay_envelope(signer_key, payload)
    return payload


@pytest.mark.asyncio
async def test_bad_signature_relay_does_not_seed_victim_binding(gateway, monkeypatch):
    """An unauthenticated (bad-signature) relay naming a victim from_webid must NOT
    create a relaygw binding — the pre-fix seed-before-verify let it poison the
    victim's slot and drop the victim's real federated traffic."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    room_id = "room-victim"
    _mkey, victim = _priv_did()
    ws = _mock_member_ws()
    gateway._local_rooms[room_id] = {"name": "R", "members": {ws}}
    gateway._store.add_federated_room_member(room_id, victim, "https://victim-gw.example")
    attacker_key, attacker_did = _priv_did()
    payload = _signed_room_message(attacker_key, attacker_did, room_id, victim, "m-bad")
    payload["signature"] = "AAAA"  # corrupt the signature
    status, _ = await gateway._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")            # no-reveal
    assert ws.send.await_count == 0            # never delivered
    assert not gateway._store.get_identity_key_history("relaygw:" + victim)  # NOT seeded


@pytest.mark.asyncio
async def test_signed_relay_from_member_seeds_and_delivers(gateway, monkeypatch):
    """First-contact federation: a validly-signed room_message for a registered
    (federated) member seeds the binding and delivers."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    room_id = "room-ok"
    _mkey, member = _priv_did()
    gw_key, gw_did = _priv_did()
    ws = _mock_member_ws()
    gateway._local_rooms[room_id] = {"name": "R", "members": {ws}}
    gateway._store.add_federated_room_member(room_id, member, "https://member-gw.example")
    payload = _signed_room_message(gw_key, gw_did, room_id, member, "m-ok")
    status, _ = await gateway._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")
    assert ws.send.await_count == 1            # delivered on first contact
    hist = gateway._store.get_identity_key_history("relaygw:" + member)
    assert hist and any(h.get("pubkey_hex") for h in hist)   # binding seeded


@pytest.mark.asyncio
async def test_signed_relay_from_non_member_does_not_seed(gateway, monkeypatch):
    """A validly-signed relay whose from_webid is NOT a member of the target room
    must not seed that from_webid's slot (stranger cannot poison a binding)."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    room_id = "room-strict"
    _rkey, real_member = _priv_did()
    _skey, stranger = _priv_did()
    ws = _mock_member_ws()
    gateway._local_rooms[room_id] = {"name": "R", "members": {ws}}
    gateway._store.add_federated_room_member(room_id, real_member, "https://gw.example")
    gw_key, gw_did = _priv_did()
    payload = _signed_room_message(gw_key, gw_did, room_id, stranger, "m-str")
    status, _ = await gateway._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")            # no-reveal
    assert ws.send.await_count == 0            # not delivered
    assert not gateway._store.get_identity_key_history("relaygw:" + stranger)  # NOT seeded


# ── B2: SSRF guard on peer-gateway discovery ────────────────────────────────────

@pytest.mark.asyncio
async def test_discover_peer_gateway_blocks_loopback(gateway, monkeypatch):
    """_discover_peer_gateway must route through the SSRF-safe fetch: a loopback
    gateway_url is blocked before any request reaches the internal host."""
    monkeypatch.delenv("PROXION_ALLOW_PRIVATE_RELAY", raising=False)
    import httpx
    real_stream = httpx.AsyncClient.stream
    fetched = {"hit": False}

    def _spy_stream(self, method, url, **kw):
        fetched["hit"] = True
        return real_stream(self, method, url, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "stream", _spy_stream)
    result = await gateway._discover_peer_gateway("http://127.0.0.1:9/")
    assert result is None
    assert fetched["hit"] is False
