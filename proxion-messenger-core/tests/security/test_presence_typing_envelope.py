"""F9: presence/typing ephemeral relays are envelope-verified in _handle_relay_post.

Before the fix, ``presence``/``typing`` were in none of the signed-types sets, so
verify_relay_envelope never ran and the handlers trusted an attacker-controlled
``from_webid``. These tests drive the full inbound /relay path (_handle_relay_post)
so the signed-envelope enforcement runs, and confirm a spoofed (unsigned / forged /
wrong-gateway) relay is dropped while a correctly-signed one still delivers.
"""
from __future__ import annotations
import json
import secrets
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.relay import sign_relay_envelope
from proxion_messenger_core.didkey import pub_key_to_did


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    return ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={},
        config=GatewayConfig(port=9971, db_path=str(tmp_path / "recv.db")),
        read_state=ReadState(),
    )


def _seed_rel(gw, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": "cert-" + peer_did[-4:], "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=peer_did, owner_webid=owner)


def _presence_payload(from_webid, signer_agent, signer_did, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "content_type": "presence",
        "from_webid": from_webid,
        "status": "online",
        "status_message": "",
        "updated_at": now,
        "relay_nonce": secrets.token_hex(8),
        "relay_sig_did": signer_did,
        "relay_ts": now,
    }
    payload.update(overrides)
    payload["signature"] = sign_relay_envelope(signer_agent.identity_key, payload)
    return payload


@pytest.mark.asyncio
async def test_presence_relay_signed_delivers(tmp_path):
    """A correctly-signed presence relay from a contact's gateway is accepted and
    updates the presence cache."""
    gw = _make_gateway(tmp_path)
    contact = AgentState.generate()
    contact_did = pub_key_to_did(contact.identity_pub_bytes)
    peer_gw = AgentState.generate()
    peer_gw_did = pub_key_to_did(peer_gw.identity_pub_bytes)
    _seed_rel(gw, contact_did)

    payload = _presence_payload(contact_did, peer_gw, peer_gw_did)
    status, _ = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")
    assert gw._user_presence.get(contact_did, {}).get("status") == "online"


@pytest.mark.asyncio
async def test_presence_relay_unsigned_rejected(tmp_path):
    """No envelope signature → verify_relay_envelope fails → 200 no-reveal and the
    presence cache is NOT updated (attacker cannot inject presence unsigned)."""
    gw = _make_gateway(tmp_path)
    contact = AgentState.generate()
    contact_did = pub_key_to_did(contact.identity_pub_bytes)
    _seed_rel(gw, contact_did)

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "content_type": "presence", "from_webid": contact_did,
        "status": "online", "status_message": "", "updated_at": now,
    }
    status, _ = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")
    assert contact_did not in gw._user_presence


@pytest.mark.asyncio
async def test_presence_relay_forged_signer_rejected(tmp_path):
    """Signature does not match the claimed relay_sig_did → dropped. The attacker
    signs with its own key but names a different gateway as the signer."""
    gw = _make_gateway(tmp_path)
    contact = AgentState.generate()
    contact_did = pub_key_to_did(contact.identity_pub_bytes)
    attacker = AgentState.generate()
    benign_gw = AgentState.generate()
    benign_gw_did = pub_key_to_did(benign_gw.identity_pub_bytes)
    _seed_rel(gw, contact_did)

    # relay_sig_did claims benign_gw, but the signature is the attacker's.
    payload = _presence_payload(contact_did, attacker, benign_gw_did)
    status, _ = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")
    assert contact_did not in gw._user_presence


@pytest.mark.asyncio
async def test_presence_relay_wrong_gateway_after_binding_rejected(tmp_path):
    """Once a contact's from_webid is bound to one gateway key (TOFU), a validly-
    signed presence claiming the same from_webid from a DIFFERENT gateway is
    rejected — from_webid is bound to the signer."""
    gw = _make_gateway(tmp_path)
    contact = AgentState.generate()
    contact_did = pub_key_to_did(contact.identity_pub_bytes)
    legit_gw = AgentState.generate()
    legit_gw_did = pub_key_to_did(legit_gw.identity_pub_bytes)
    attacker_gw = AgentState.generate()
    attacker_gw_did = pub_key_to_did(attacker_gw.identity_pub_bytes)
    _seed_rel(gw, contact_did)

    # Legit presence seeds the relaygw binding.
    p1 = _presence_payload(contact_did, legit_gw, legit_gw_did, status="online")
    await gw._handle_relay_post(json.dumps(p1).encode())
    assert gw._user_presence.get(contact_did, {}).get("status") == "online"

    # Attacker gateway, validly self-signed, claims the same from_webid.
    p2 = _presence_payload(contact_did, attacker_gw, attacker_gw_did, status="offline")
    status, _ = await gw._handle_relay_post(json.dumps(p2).encode())
    assert status.startswith("200")
    # Binding mismatch → the attacker's "offline" is dropped, prior value stands.
    assert gw._user_presence.get(contact_did, {}).get("status") == "online"


@pytest.mark.asyncio
async def test_typing_relay_signed_dispatches(tmp_path):
    """A correctly-signed typing relay reaches _handle_typing_relay."""
    gw = _make_gateway(tmp_path)
    contact = AgentState.generate()
    contact_did = pub_key_to_did(contact.identity_pub_bytes)
    peer_gw = AgentState.generate()
    peer_gw_did = pub_key_to_did(peer_gw.identity_pub_bytes)
    _seed_rel(gw, contact_did)

    gw._handle_typing_relay = AsyncMock(return_value=("200 OK", '{"status":"ok"}'))
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "content_type": "typing", "from_webid": contact_did, "cert_id": "cert-xyz",
        "relay_nonce": secrets.token_hex(8), "relay_sig_did": peer_gw_did,
        "relay_ts": now,
    }
    payload["signature"] = sign_relay_envelope(peer_gw.identity_key, payload)
    await gw._handle_relay_post(json.dumps(payload).encode())
    gw._handle_typing_relay.assert_awaited_once()


@pytest.mark.asyncio
async def test_typing_relay_unsigned_not_dispatched(tmp_path):
    """An unsigned typing relay never reaches _handle_typing_relay."""
    gw = _make_gateway(tmp_path)
    contact = AgentState.generate()
    contact_did = pub_key_to_did(contact.identity_pub_bytes)
    _seed_rel(gw, contact_did)

    gw._handle_typing_relay = AsyncMock(return_value=("200 OK", '{"status":"ok"}'))
    payload = {"content_type": "typing", "from_webid": contact_did, "cert_id": "cert-xyz"}
    status, _ = await gw._handle_relay_post(json.dumps(payload).encode())
    assert status.startswith("200")
    gw._handle_typing_relay.assert_not_awaited()
