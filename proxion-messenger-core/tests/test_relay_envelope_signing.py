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
