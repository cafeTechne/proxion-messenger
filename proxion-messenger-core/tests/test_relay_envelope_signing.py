"""R55: signed-envelope enforcement for ephemeral DM secondary-op relays.

The DM secondary-op relays (reaction/edit/delete/pin/disappear) are POSTed to a
peer gateway's /relay endpoint. Before R55 they were UNSIGNED — the receiver's
relationship/author checks stopped a non-participant, but a related peer gateway
could still forge another gateway's from_webid or tamper with the operation
fields (emoji/action/ms/new_content). Now every ephemeral relay carries a
full-payload Ed25519 envelope signature made by the relaying gateway
(relay_sig_did), and the receiver rejects anything that doesn't verify or whose
relay_sig_did != from_webid.
"""
from __future__ import annotations

import json
import copy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.relay import (
    sign_relay_envelope, verify_relay_envelope,
)
from datetime import datetime, timezone, timedelta


# ── Unit: the primitive ────────────────────────────────────────────────────────

def _signed_payload(key, sig_did):
    payload = {
        "content_type": "dm_reaction",
        "from_webid": sig_did,
        "to_webid": "did:key:zRecipient",
        "message_id": "m-1",
        "emoji": "🔥",
        "action": "add",
        "relay_sig_did": sig_did,
        "relay_ts": datetime.now(timezone.utc).isoformat(),
        "relay_nonce": "abc123",
    }
    payload["signature"] = sign_relay_envelope(key, payload)
    return payload


def test_envelope_roundtrip():
    key = Ed25519PrivateKey.generate()
    did = pub_key_to_did(key.public_key().public_bytes_raw())
    assert verify_relay_envelope(_signed_payload(key, did)) is True


@pytest.mark.parametrize("field", ["emoji", "action", "message_id", "to_webid", "from_webid"])
def test_envelope_tamper_any_field_fails(field):
    key = Ed25519PrivateKey.generate()
    did = pub_key_to_did(key.public_key().public_bytes_raw())
    payload = _signed_payload(key, did)
    payload[field] = payload[field] + "X" if isinstance(payload[field], str) else "X"
    assert verify_relay_envelope(payload) is False


def test_envelope_wrong_signer_fails():
    """A signature made by a different key than relay_sig_did claims is rejected."""
    key = Ed25519PrivateKey.generate()
    did = pub_key_to_did(key.public_key().public_bytes_raw())
    payload = _signed_payload(key, did)
    # Point relay_sig_did at a different identity — the sig no longer matches.
    other = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    payload["relay_sig_did"] = other
    assert verify_relay_envelope(payload) is False


def test_envelope_stale_timestamp_fails():
    key = Ed25519PrivateKey.generate()
    did = pub_key_to_did(key.public_key().public_bytes_raw())
    payload = {
        "content_type": "dm_reaction", "from_webid": did, "to_webid": "did:key:zR",
        "message_id": "m", "emoji": "🔥", "action": "add", "relay_sig_did": did,
        "relay_ts": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "relay_nonce": "n",
    }
    payload["signature"] = sign_relay_envelope(key, payload)
    assert verify_relay_envelope(payload) is False


def test_envelope_missing_fields_fail():
    assert verify_relay_envelope({"signature": "x"}) is False
    assert verify_relay_envelope({}) is False


# ── Integration: enforcement at the /relay dispatch ─────────────────────────────

def _gw(tmp_path, name):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / f"{name}.db")),
    )


def _seed_rel(gw, cert_id, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": cert_id, "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did=peer_did, owner_webid=owner)


@pytest.fixture
def noauth_env(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.mark.asyncio
async def test_unsigned_dm_reaction_relay_rejected(tmp_path, noauth_env):
    """An UNSIGNED dm_reaction relay is dropped (200 no-reveal, not acted on)."""
    gw = _gw(tmp_path, "u")
    sender = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    _seed_rel(gw, "cert-U", sender, owner="")
    status, _ = await gw._handle_relay_post(json.dumps({
        "content_type": "dm_reaction", "from_webid": sender,
        "to_webid": "did:key:zR", "message_id": "m", "emoji": "🔥", "action": "add",
    }).encode())
    assert status.startswith("200")
    # No stored reaction — the op never ran.
    assert not gw._store.get_reactions("cert-U")


@pytest.mark.asyncio
async def test_forged_from_webid_rejected(tmp_path, noauth_env):
    """A gateway that signs with its OWN key but sets from_webid to a DIFFERENT
    peer's did (relay_sig_did != from_webid) is rejected — no from_webid forgery."""
    gw = _gw(tmp_path, "f")
    attacker_key = Ed25519PrivateKey.generate()
    attacker_did = pub_key_to_did(attacker_key.public_key().public_bytes_raw())
    victim_did = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    _seed_rel(gw, "cert-F", victim_did, owner="")
    payload = {
        "content_type": "dm_reaction", "from_webid": victim_did, "to_webid": "did:key:zR",
        "message_id": "m", "emoji": "😈", "action": "add",
        "relay_sig_did": attacker_did,
        "relay_ts": datetime.now(timezone.utc).isoformat(), "relay_nonce": "n1",
    }
    payload["signature"] = sign_relay_envelope(attacker_key, payload)  # validly signed…
    status, _ = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")
    assert not gw._store.get_reactions("cert-F")  # …but from_webid forgery dropped


@pytest.mark.asyncio
async def test_signed_dm_reaction_relay_deduped_on_replay(tmp_path, noauth_env):
    """A valid signed relay is accepted once; a byte-identical replay is a dup."""
    gw = _gw(tmp_path, "d")
    key = Ed25519PrivateKey.generate()
    did = pub_key_to_did(key.public_key().public_bytes_raw())
    _seed_rel(gw, "cert-D", did, owner="")
    payload = {
        "content_type": "dm_reaction", "from_webid": did, "to_webid": "did:key:zR",
        "message_id": "m", "emoji": "🔥", "action": "add", "relay_sig_did": did,
        "relay_ts": datetime.now(timezone.utc).isoformat(), "relay_nonce": "dup-nonce",
    }
    payload["signature"] = sign_relay_envelope(key, payload)
    s1, _ = await gw._handle_relay_post(json.dumps(copy.deepcopy(payload)).encode())
    s2, r2 = await gw._handle_relay_post(json.dumps(copy.deepcopy(payload)).encode())
    assert s1.startswith("200")
    assert "duplicate" in r2


# ── Room relays: TOFU member→gateway binding ────────────────────────────────────

def test_room_sender_gateway_binding_tofu_then_conflict(tmp_path):
    """A member's relays must consistently come from the SAME signing gateway.
    First sighting is trusted (TOFU); the same member relayed by a DIFFERENT
    gateway key is rejected — closing from_webid forgery by a related peer."""
    gw = _gw(tmp_path, "bind")
    member = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    gw_a = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    gw_b = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    assert gw._relay_sender_gateway_ok(member, gw_a) is True    # TOFU: first sighting
    assert gw._relay_sender_gateway_ok(member, gw_a) is True    # same gateway → ok
    assert gw._relay_sender_gateway_ok(member, gw_b) is False   # different gateway → forgery
    # A member whose from_webid IS its own gateway did needs no binding.
    assert gw._relay_sender_gateway_ok(gw_a, gw_a) is True


def _signed_room_message(signer_key, signer_did, room_id, member_did, mid="rm-1"):
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
async def test_signed_room_message_delivered_then_forgery_dropped(tmp_path, noauth_env):
    from unittest.mock import AsyncMock
    gw = _gw(tmp_path, "rm")
    room_id = "room-X"
    member = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    gw_a_key = Ed25519PrivateKey.generate()
    gw_a_did = pub_key_to_did(gw_a_key.public_key().public_bytes_raw())
    gw_b_key = Ed25519PrivateKey.generate()   # a DIFFERENT (attacker) gateway
    gw_b_did = pub_key_to_did(gw_b_key.public_key().public_bytes_raw())
    # A local room with one live member + the remote member registered as federated.
    ws = AsyncMock(); ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s); ws.__eq__ = lambda s, o: s is o
    gw._local_rooms[room_id] = {"name": "R", "members": {ws}}
    gw._store.add_federated_room_member(room_id, member, "https://a.example")

    # 1) Legit: signed by member's gateway (gw_a) → delivered + binds member→gw_a.
    ok = _signed_room_message(gw_a_key, gw_a_did, room_id, member, mid="m1")
    s1, _ = await gw._handle_relay_post(json.dumps(ok).encode())
    assert s1.startswith("200")
    assert ws.send.await_count == 1

    # 2) Forgery: attacker gateway (gw_b) signs a message claiming the SAME member.
    #    Signature is valid for gw_b, membership passes, but the binding rejects it.
    ws.send.reset_mock()
    forged = _signed_room_message(gw_b_key, gw_b_did, room_id, member, mid="m2")
    s2, _ = await gw._handle_relay_post(json.dumps(forged).encode())
    assert s2.startswith("200")            # no-reveal
    assert ws.send.await_count == 0        # …but never delivered
    assert gw._store.get_message("m2") is None


@pytest.mark.asyncio
async def test_unsigned_room_message_dropped(tmp_path, noauth_env):
    from unittest.mock import AsyncMock
    gw = _gw(tmp_path, "rmu")
    room_id = "room-U"
    member = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    ws = AsyncMock(); ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s); ws.__eq__ = lambda s, o: s is o
    gw._local_rooms[room_id] = {"name": "R", "members": {ws}}
    gw._store.add_federated_room_member(room_id, member, "https://a.example")
    status, _ = await gw._handle_relay_post(json.dumps({
        "content_type": "room_message", "room_id": room_id, "from_webid": member,
        "content": "x", "message_id": "u1", "timestamp": datetime.now(timezone.utc).isoformat(),
    }).encode())
    assert status.startswith("200")
    assert ws.send.await_count == 0


# ── Voice + file: the new content types are gated too ───────────────────────────

@pytest.mark.asyncio
async def test_voice_signal_signed_delivered_unsigned_dropped(tmp_path, noauth_env):
    """voice_signal is self-signed (from_webid == the caller's gateway == signer).
    A signed signal from a related peer is delivered; an unsigned one is dropped."""
    from unittest.mock import AsyncMock
    gw = _gw(tmp_path, "vs")
    caller_key = Ed25519PrivateKey.generate()
    caller_did = pub_key_to_did(caller_key.public_key().public_bytes_raw())
    target = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    _seed_rel(gw, "cert-V", caller_did, owner="")          # recipient knows the caller
    ws = AsyncMock(); ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s); ws.__eq__ = lambda s, o: s is o
    gw.clients.add(ws); gw._client_webids[ws] = target
    gw._webid_sockets[target] = {ws}

    base = {"content_type": "voice_signal", "from_webid": caller_did, "to_webid": target,
            "signal_type": "offer", "signal_data": {"sdp": "x"}, "session_id": "s1",
            "message_id": "vs1", "content": "offer"}
    # Unsigned → dropped at the gate.
    await gw._handle_relay_post(json.dumps(dict(base)).encode())
    assert ws.send.await_count == 0
    # Signed (self-signed: relay_sig_did == from_webid) → delivered.
    signed = dict(base, relay_sig_did=caller_did,
                  relay_ts=datetime.now(timezone.utc).isoformat(), relay_nonce="vn1")
    signed["signature"] = sign_relay_envelope(caller_key, signed)
    await gw._handle_relay_post(json.dumps(signed).encode())
    assert ws.send.await_count == 1


@pytest.mark.asyncio
async def test_file_chunk_forged_sender_dropped(tmp_path, noauth_env):
    """file_* is member-signed (TOFU binding). A file chunk whose from_webid is
    bound to gateway A cannot later be relayed by attacker gateway B."""
    from unittest.mock import AsyncMock
    gw = _gw(tmp_path, "fc")
    member = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    gw_a_key = Ed25519PrivateKey.generate()
    gw_a_did = pub_key_to_did(gw_a_key.public_key().public_bytes_raw())
    gw_b_key = Ed25519PrivateKey.generate()
    gw_b_did = pub_key_to_did(gw_b_key.public_key().public_bytes_raw())
    target = pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    _seed_rel(gw, "cert-FC", member, owner="")
    ws = AsyncMock(); ws.send = AsyncMock()
    ws.__hash__ = lambda s: id(s); ws.__eq__ = lambda s, o: s is o
    gw.clients.add(ws); gw._client_webids[ws] = target
    gw._webid_sockets[target] = {ws}

    def _chunk(signer_key, signer_did, fid):
        p = {"content_type": "file_chunk", "from_webid": member, "to_webid": target,
             "file_id": fid, "seq": 0, "data": "AAAA",
             "relay_sig_did": signer_did, "relay_ts": datetime.now(timezone.utc).isoformat(),
             "relay_nonce": "fn-" + fid}
        p["signature"] = sign_relay_envelope(signer_key, p)
        return p

    # 1) Legit: signed by member's gateway A → delivered + binds member→A.
    await gw._handle_relay_post(json.dumps(_chunk(gw_a_key, gw_a_did, "f1")).encode())
    assert ws.send.await_count == 1
    # 2) Forgery: attacker gateway B signs a chunk claiming the same member → dropped.
    ws.send.reset_mock()
    await gw._handle_relay_post(json.dumps(_chunk(gw_b_key, gw_b_did, "f2")).encode())
    assert ws.send.await_count == 0
