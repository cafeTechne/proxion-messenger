"""Per-owner block enforcement on the primary DM/invite delivery paths.

The coarse pre-dispatch gate in _handle_relay_post consults only the legacy
owner-less blocklist. These cover the per-owner store block (R90 B) that must
be enforced in the content handlers where the recipient owner is known: the
plain-DM relay, the sealed-DM relay, the same-gateway local DM, the offline
queue, and inbound federation invites.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.relay import sign_relay_message
from proxion_messenger_core.sealed_relay import seal_relay_payload
from proxion_messenger_core import handshake
from proxion_messenger_core.federation import Capability


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


@pytest.fixture
def gateway(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "block.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "D"})


def _relay_body(sender_key, sender_did, to_did, msg_id, content):
    ts = datetime.now(timezone.utc).isoformat()
    sig = sign_relay_message(sender_key, sender_did, to_did, msg_id, content, ts)
    return {
        "from_webid": sender_did, "to_webid": to_did,
        "message_id": msg_id, "content": content,
        "timestamp": ts, "display_name": "Sender", "signature": sig,
    }


def _sent_contents(ws):
    return [json.loads(c.args[0]).get("content") for c in ws.send.call_args_list]


def _sent_types(ws):
    return [json.loads(c.args[0]).get("type") for c in ws.send.call_args_list]


# ── C1a: plain-DM relay ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plain_relay_per_owner_block_not_delivered(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    sender_key = Ed25519PrivateKey.generate()
    sender_did = _did(sender_key)
    recip = _mock_ws()
    recip_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, recip, recip_did)
    # Per-owner store block only — the legacy global file is untouched, so the
    # coarse owner-less gate does not catch it; the content handler must.
    gateway._store.set_block(recip_did, sender_did, True)

    recip.send.reset_mock()
    body = json.dumps(_relay_body(sender_key, sender_did, recip_did, "blk-1", "hi")).encode()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")   # accepted, not revealed
    assert "hi" not in _sent_contents(recip)
    assert recip_did not in gateway._relay_queue


@pytest.mark.asyncio
async def test_plain_relay_non_blocked_delivered(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    sender_key = Ed25519PrivateKey.generate()
    sender_did = _did(sender_key)
    recip = _mock_ws()
    recip_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, recip, recip_did)

    recip.send.reset_mock()
    body = json.dumps(_relay_body(sender_key, sender_did, recip_did, "ok-1", "hi")).encode()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    assert "hi" in _sent_contents(recip)


@pytest.mark.asyncio
async def test_plain_relay_blocked_offline_not_queued(gateway):
    """A blocked sender's DM to an OFFLINE recipient must not be queued or stored."""
    sender_key = Ed25519PrivateKey.generate()
    sender_did = _did(sender_key)
    recip_did = _did(Ed25519PrivateKey.generate())   # not connected
    gateway._store.set_block(recip_did, sender_did, True)

    body = json.dumps(_relay_body(sender_key, sender_did, recip_did, "blk-off-1", "hi")).encode()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")   # silent accept, NOT 202 stored
    assert recip_did not in gateway._relay_queue
    assert not any(m["message_id"] == "blk-off-1" for m in gateway._store.get_messages(recip_did))


@pytest.mark.asyncio
async def test_plain_relay_non_blocked_offline_is_queued(gateway):
    """Contrast: a non-blocked offline DM is still queued (202)."""
    sender_key = Ed25519PrivateKey.generate()
    sender_did = _did(sender_key)
    recip_did = _did(Ed25519PrivateKey.generate())   # not connected

    body = json.dumps(_relay_body(sender_key, sender_did, recip_did, "ok-off-1", "hi")).encode()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("202")
    assert recip_did in gateway._relay_queue


# ── C1b: sealed-DM relay ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sealed_relay_per_owner_block_not_delivered(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    sender_key = Ed25519PrivateKey.generate()
    sender_did = _did(sender_key)
    recip = _mock_ws()
    recip_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, recip, recip_did)
    gateway._store.set_block(recip_did, sender_did, True)

    inner = _relay_body(sender_key, sender_did, recip_did, "sealed-blk-1", "secret")
    sealed = seal_relay_payload(inner, gateway._own_x25519_pub_b64)
    body = json.dumps({"content_type": "sealed_dm", "sealed_payload": sealed}).encode()
    recip.send.reset_mock()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    assert "secret" not in _sent_contents(recip)


@pytest.mark.asyncio
async def test_sealed_relay_non_blocked_delivered(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    sender_key = Ed25519PrivateKey.generate()
    sender_did = _did(sender_key)
    recip = _mock_ws()
    recip_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, recip, recip_did)

    inner = _relay_body(sender_key, sender_did, recip_did, "sealed-ok-1", "secret")
    sealed = seal_relay_payload(inner, gateway._own_x25519_pub_b64)
    body = json.dumps({"content_type": "sealed_dm", "sealed_payload": sealed}).encode()
    recip.send.reset_mock()
    status, _ = await gateway._handle_relay_post(body)
    assert status.startswith("200")
    assert "secret" in _sent_contents(recip)


# ── C1c: same-gateway local DM ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_dm_per_owner_block_not_delivered(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    sender, bob = _mock_ws(), _mock_ws()
    sender_did = _did(Ed25519PrivateKey.generate())
    bob_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, sender, sender_did)
    await _register(gateway, bob, bob_did)
    gateway._store.set_block(bob_did, sender_did, True)

    bob.send.reset_mock()
    await gateway.process_command(sender, {
        "cmd": "local_dm", "target_webid": bob_did, "content": "hey",
    })
    assert "hey" not in _sent_contents(bob)


@pytest.mark.asyncio
async def test_local_dm_non_blocked_delivered(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    sender, bob = _mock_ws(), _mock_ws()
    sender_did = _did(Ed25519PrivateKey.generate())
    bob_did = _did(Ed25519PrivateKey.generate())
    await _register(gateway, sender, sender_did)
    await _register(gateway, bob, bob_did)

    bob.send.reset_mock()
    await gateway.process_command(sender, {
        "cmd": "local_dm", "target_webid": bob_did, "content": "hey",
    })
    assert "hey" in _sent_contents(bob)


# ── C2: inbound federation invite ─────────────────────────────────────────────

def _make_invite(display_name="Peer"):
    peer_key = Ed25519PrivateKey.generate()
    peer_pub = peer_key.public_key().public_bytes_raw()
    peer_did = pub_key_to_did(peer_pub)
    invite = handshake.create_invite(
        peer_key, peer_pub,
        [Capability(with_="stash://dm/", can="crud/write")],
        endpoint_hints=["https://peer-gw:8080"],
        display_name=display_name,
    )
    return invite, peer_did


@pytest.mark.asyncio
async def test_invite_from_blocked_issuer_dropped(gateway):
    invite, peer_did = _make_invite()
    gateway._store.set_block(gateway._own_gateway_did(), peer_did, True)

    ws = _mock_ws()
    gateway.clients.add(ws)
    status, _ = await gateway._handle_invite_post(json.dumps(invite.to_dict()).encode())
    assert status.startswith("200")   # accepted, not revealed
    assert "friend_request_received" not in _sent_types(ws)
    # Not persisted as a pending invite either.
    assert gateway._store.get_pending_invite(invite.invitation_id) is None


@pytest.mark.asyncio
async def test_invite_delivered_only_to_owner(gateway, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    owner = _mock_ws()
    await _register(gateway, owner, gateway._own_gateway_did())
    other = _mock_ws()
    await _register(gateway, other, _did(Ed25519PrivateKey.generate()))

    invite, _ = _make_invite()
    owner.send.reset_mock(); other.send.reset_mock()
    status, _ = await gateway._handle_invite_post(json.dumps(invite.to_dict()).encode())
    assert status.startswith("200")
    assert "friend_request_received" in _sent_types(owner)
    assert "friend_request_received" not in _sent_types(other)
